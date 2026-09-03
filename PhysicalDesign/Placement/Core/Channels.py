"""Cluster boundary models, scoring, and feasibility contracts."""

from __future__ import annotations

from dataclasses import (
    dataclass,
)
from hashlib import (
    sha256,
)
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
)
from PhysicalDesign.Geometry.Rotation import TransformDirection
from PhysicalDesign.Geometry.Placement import PlacedGate
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology
from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Resources.ResourceGraph import BuildRoutingEnvelope, LocalRouteClaim, RoutingResourceClaims, RoutingResourceGraph, ValidateLocalRouteClaims
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
class ClusterBoundaryBundle:
    """One logical producer/consumer cluster interface contract."""

    SourceCluster: int
    TargetCluster: int
    Signals: tuple[str, ...]
    FanoutEndpoints: int

    @property
    def RequiredCorridorLanes(self) -> int:
        """Reserve one lane for each distinct crossing signal."""
        return len(self.Signals)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SourceCluster": self.SourceCluster,
            "TargetCluster": self.TargetCluster,
            "Signals": list(self.Signals),
            "FanoutEndpoints": self.FanoutEndpoints,
            "RequiredCorridorLanes": self.RequiredCorridorLanes,
        }

@dataclass(frozen=True)
class CutDrivenClusterRefinementProfile:
    """Bounded structural cohesion applied only to reported exact-cut nets."""

    Signals: tuple[str, ...]
    EdgeWeight: int

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SignalCount": len(self.Signals),
            "EdgeWeight": self.EdgeWeight,
            "StructuralFingerprint": sha256(repr((
                len(self.Signals),
                self.EdgeWeight,
            )).encode("utf-8")).hexdigest(),
        }

@dataclass(frozen=True)
class ClusterBoundaryContractScore:
    """Topology-only capacity score for a tentative cluster-slot assignment."""

    PeakBoundaryDemand: int
    TotalBoundaryDemand: int
    OverflowLanes: int

    def ToDictionary(self) -> dict[str, int]:
        return {
            "PeakBoundaryDemand": self.PeakBoundaryDemand,
            "TotalBoundaryDemand": self.TotalBoundaryDemand,
            "OverflowLanes": self.OverflowLanes,
        }

@dataclass(frozen=True)
class ClusterInterfacePlacementPattern:
    """Topology-only boundary-bank ownership for one tentative placement."""

    SignalBanks: tuple[
        tuple[str, tuple[tuple[int, int, str, str, int, bool], ...]],
        ...,
    ]
    OwnershipFingerprint: str

    def BanksBySignal(
        self,
    ) -> dict[str, frozenset[tuple[int, int, str]]]:
        """Return physical slot-side banks while omitting diagnostic traits."""
        return {
            Signal: frozenset(
                (Column, Row, Side)
                for Column, Row, Side, _Role, _Rotation, _MirrorX in Banks
            )
            for Signal, Banks in self.SignalBanks
        }

@dataclass(frozen=True)
class ClusterInterfaceTopology:
    """Immutable signal-to-cluster interface model reused by a placement beam."""

    SignalEndpoints: tuple[
        tuple[str, int | None, tuple[int, ...], bool],
        ...,
    ]

def BuildClusterInterfaceTopology(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Signals: Iterable[str] | None = None,
) -> ClusterInterfaceTopology:
    """Build endpoint topology for selected signals or every interface."""
    SelectedSignals = (
        frozenset(map(str, Signals))
        if Signals is not None
        else None
    )
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerBySignal = {
        Signal: Gate
        for Gate in Module.Gates
        for Signal in Gate.Outputs
        if SelectedSignals is None or Signal in SelectedSignals
    }
    ConsumersBySignal: dict[str, list[Any]] = {}
    for Gate in Module.Gates:
        for Signal in Gate.Inputs:
            if SelectedSignals is not None and Signal not in SelectedSignals:
                continue
            ConsumersBySignal.setdefault(Signal, []).append(Gate)
    SignalNames = tuple(sorted({
        *ProducerBySignal,
        *ConsumersBySignal,
    }))
    return ClusterInterfaceTopology(SignalEndpoints=tuple(
        (
            Signal,
            (
                ClusterByGate.get(ProducerBySignal[Signal].Name)
                if Signal in ProducerBySignal
                else None
            ),
            tuple(sorted({
                ClusterByGate[Consumer.Name]
                for Consumer in ConsumersBySignal.get(Signal, ())
                if Consumer.Name in ClusterByGate
            })),
            any(
                (
                    Consumer.Kind.value
                    if hasattr(Consumer.Kind, "value")
                    else str(Consumer.Kind)
                ) == "OUTPUT"
                for Consumer in ConsumersBySignal.get(Signal, ())
            ),
        )
        for Signal in SignalNames
    ))

@dataclass(frozen=True)
class ClusterInterfacePlacementScore:
    """Exact-cut score derived from tentative cluster boundary ownership."""

    PairBankConflicts: int
    HigherOrderBankPressure: int
    HigherOrderPeakBankDemand: int
    HigherOrderBankExcessDemand: int
    HigherOrderOverloadedBankCount: int
    FacingMismatches: int
    Pattern: ClusterInterfacePlacementPattern

    def ToDictionary(self) -> dict[str, object]:
        return {
            "PairBankConflicts": self.PairBankConflicts,
            "HigherOrderBankPressure": self.HigherOrderBankPressure,
            "HigherOrderPeakBankDemand": (
                self.HigherOrderPeakBankDemand
            ),
            "HigherOrderBankExcessDemand": (
                self.HigherOrderBankExcessDemand
            ),
            "HigherOrderOverloadedBankCount": (
                self.HigherOrderOverloadedBankCount
            ),
            "FacingMismatches": self.FacingMismatches,
            "SignalCount": len(self.Pattern.SignalBanks),
            "OwnershipFingerprint": self.Pattern.OwnershipFingerprint,
        }

@dataclass(frozen=True)
class HigherOrderPhysicalBankDemandScore:
    """Aggregate capacity pressure for reported higher-order interface cuts."""

    CollisionPairs: int = 0
    PeakDemand: int = 0
    ExcessDemand: int = 0
    OverloadedBankCount: int = 0

def ClusterBoundaryCorridorKey(
    Bank: tuple[int, int, str],
) -> tuple[int, int, str]:
    """Return the shared grid boundary reached through one cluster-side bank."""
    Column, Row, Side = Bank
    if Side == "East":
        return Column, Row, "Vertical"
    if Side == "West":
        return Column - 1, Row, "Vertical"
    if Side == "South":
        return Column, Row, "Horizontal"
    if Side == "North":
        return Column, Row - 1, "Horizontal"
    raise ValueError(f"Unknown cluster boundary side: {Side}")

def ScoreHigherOrderPhysicalBankDemand(
    PhysicalBanksBySignal: dict[
        str,
        frozenset[tuple[int, int, str]],
    ],
    HigherOrderConflictSets: Iterable[Iterable[str]],
) -> HigherOrderPhysicalBankDemandScore:
    """Measure total cut concentration on topology-derived physical banks.

    A maximum-only score cannot distinguish one shared bank from several
    simultaneously shared banks.  Sum pair collisions so concentration grows
    quadratically, then retain peak/excess/overloaded-bank diagnostics for
    deterministic lexicographic placement ranking.
    """
    CollisionPairs = 0
    PeakDemand = 0
    ExcessDemand = 0
    OverloadedBankCount = 0
    CanonicalConflictSets = tuple(sorted({
        tuple(sorted(set(map(str, Signals))))
        for Signals in HigherOrderConflictSets
        if len(set(map(str, Signals))) >= 3
    }))
    for Signals in CanonicalConflictSets:
        CandidateBanks = {
            Bank
            for Signal in Signals
            for Bank in PhysicalBanksBySignal.get(
                Signal,
                frozenset(),
            )
        }
        for Bank in CandidateBanks:
            Demand = sum(
                Bank
                in PhysicalBanksBySignal.get(
                    Signal,
                    frozenset(),
                )
                for Signal in Signals
            )
            PeakDemand = max(PeakDemand, Demand)
            if Demand <= 1:
                continue
            CollisionPairs += Demand * (Demand - 1) // 2
            ExcessDemand += Demand - 1
            OverloadedBankCount += 1
    return HigherOrderPhysicalBankDemandScore(
        CollisionPairs=CollisionPairs,
        PeakDemand=PeakDemand,
        ExcessDemand=ExcessDemand,
        OverloadedBankCount=OverloadedBankCount,
    )

def ScoreClusterInterfaceFacingMismatches(
    Topology: ClusterInterfaceTopology,
    Assignment: Mapping[int, tuple[int, int]],
    Variants: Mapping[int, ClusterLayoutVariant],
) -> int:
    """Count all interface pins that face away from their destination bank.

    This is the hot-path component of cluster-interface scoring.  It avoids
    constructing signal ownership sets and fingerprints for every beam state;
    the complete pattern is still built for the retained diagnostic states.
    """
    def BoundarySide(
        FromSlot: tuple[int, int],
        ToSlot: tuple[int, int],
    ) -> str:
        DeltaColumn = ToSlot[0] - FromSlot[0]
        DeltaRow = ToSlot[1] - FromSlot[1]
        if abs(DeltaColumn) >= abs(DeltaRow):
            return "East" if DeltaColumn >= 0 else "West"
        return "South" if DeltaRow >= 0 else "North"

    def OppositeBoundarySide(Side: str) -> str:
        return {
            "East": "West",
            "West": "East",
            "North": "South",
            "South": "North",
        }[Side]

    Directions = {
        "East": (1, 0, 0),
        "West": (-1, 0, 0),
        "North": (0, 0, -1),
        "South": (0, 0, 1),
    }
    Mismatches = 0

    def AddInterface(
        ClusterIndex: int,
        Side: str,
        Role: str,
    ) -> None:
        nonlocal Mismatches
        Variant = Variants[ClusterIndex]
        PinDirection = TransformDirection(
            (0, 0, 1) if Role == "Source" else (0, 0, -1),
            Variant.Rotation,
            Variant.MirrorX,
        )
        BoundaryDirection = Directions[Side]
        if (
            PinDirection[0] * BoundaryDirection[0]
            + PinDirection[2] * BoundaryDirection[2]
            <= 0
        ):
            Mismatches += 1

    for (
        _Signal,
        SourceCluster,
        TargetClusters,
        HasExternalTarget,
    ) in Topology.SignalEndpoints:
        if SourceCluster is not None:
            for TargetCluster in TargetClusters:
                if TargetCluster == SourceCluster:
                    continue
                SourceSide = BoundarySide(
                    Assignment[SourceCluster],
                    Assignment[TargetCluster],
                )
                AddInterface(SourceCluster, SourceSide, "Source")
                AddInterface(
                    TargetCluster,
                    OppositeBoundarySide(SourceSide),
                    "Target",
                )
            if HasExternalTarget:
                AddInterface(SourceCluster, "East", "Source")
        else:
            for TargetCluster in TargetClusters:
                AddInterface(TargetCluster, "West", "Target")
    return Mismatches

def ScoreClusterInterfaceFacingMismatchesForOrientations(
    Topology: ClusterInterfaceTopology,
    Assignment: Mapping[int, tuple[int, int]],
    Orientations: tuple[int, ...],
    SourceFaces: Mapping[int, tuple[tuple[int, int, int], ...]],
    TargetFaces: Mapping[int, tuple[tuple[int, int, int], ...]],
) -> int:
    """Count facing mismatches using precomputed rigid-transform faces."""
    Directions = {
        "East": (1, 0, 0),
        "West": (-1, 0, 0),
        "North": (0, 0, -1),
        "South": (0, 0, 1),
    }

    def BoundarySide(
        FromSlot: tuple[int, int],
        ToSlot: tuple[int, int],
    ) -> str:
        DeltaColumn = ToSlot[0] - FromSlot[0]
        DeltaRow = ToSlot[1] - FromSlot[1]
        if abs(DeltaColumn) >= abs(DeltaRow):
            return "East" if DeltaColumn >= 0 else "West"
        return "South" if DeltaRow >= 0 else "North"

    Mismatches = 0

    def AddInterface(ClusterIndex: int, Side: str, Role: str) -> None:
        nonlocal Mismatches
        Faces = SourceFaces if Role == "Source" else TargetFaces
        PinDirection = Faces[ClusterIndex][Orientations[ClusterIndex]]
        BoundaryDirection = Directions[Side]
        if (
            PinDirection[0] * BoundaryDirection[0]
            + PinDirection[2] * BoundaryDirection[2]
            <= 0
        ):
            Mismatches += 1

    for (
        _Signal,
        SourceCluster,
        TargetClusters,
        HasExternalTarget,
    ) in Topology.SignalEndpoints:
        if SourceCluster is not None:
            for TargetCluster in TargetClusters:
                if TargetCluster == SourceCluster:
                    continue
                SourceSide = BoundarySide(
                    Assignment[SourceCluster],
                    Assignment[TargetCluster],
                )
                AddInterface(SourceCluster, SourceSide, "Source")
                AddInterface(
                    TargetCluster,
                    {
                        "East": "West",
                        "West": "East",
                        "North": "South",
                        "South": "North",
                    }[SourceSide],
                    "Target",
                )
            if HasExternalTarget:
                AddInterface(SourceCluster, "East", "Source")
        else:
            for TargetCluster in TargetClusters:
                AddInterface(TargetCluster, "West", "Target")
    return Mismatches

def ScoreClusterInterfacePlacement(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Assignment: dict[int, tuple[int, int]],
    Variants: dict[int, ClusterLayoutVariant],
    PairwiseConflictEdges: Iterable[tuple[str, str]] = (),
    HigherOrderConflictSets: Iterable[Iterable[str]] = (),
    Topology: ClusterInterfaceTopology | None = None,
) -> ClusterInterfacePlacementScore:
    """Score cut-signal ownership of topology-derived cluster pin banks.

    This is deliberately a placement model rather than a portal generator.
    It proves whether a reported capacity-one pair is still being presented
    to the same cluster boundary bank.  The authoritative router remains the
    sole judge of concrete portal and electrical legality.
    """
    PairwiseConflictEdges = tuple({
        tuple(sorted((str(First), str(Second))))
        for First, Second in PairwiseConflictEdges
        if str(First) != str(Second)
    })
    HigherOrderConflictSets = tuple(sorted({
        tuple(sorted(set(map(str, Signals))))
        for Signals in HigherOrderConflictSets
        if len(set(map(str, Signals))) >= 3
    }))
    if Topology is None:
        Topology = BuildClusterInterfaceTopology(
            Module,
            Clusters,
            ({
                Signal
                for Edge in PairwiseConflictEdges
                for Signal in Edge
            } | {
                Signal
                for Signals in HigherOrderConflictSets
                for Signal in Signals
            }) or None,
        )

    def BoundarySide(
        FromSlot: tuple[int, int],
        ToSlot: tuple[int, int],
    ) -> str:
        DeltaColumn = ToSlot[0] - FromSlot[0]
        DeltaRow = ToSlot[1] - FromSlot[1]
        if abs(DeltaColumn) >= abs(DeltaRow):
            return "East" if DeltaColumn >= 0 else "West"
        return "South" if DeltaRow >= 0 else "North"

    def OppositeBoundarySide(Side: str) -> str:
        return {
            "East": "West",
            "West": "East",
            "North": "South",
            "South": "North",
        }[Side]

    def DirectionForSide(Side: str) -> tuple[int, int, int]:
        return {
            "East": (1, 0, 0),
            "West": (-1, 0, 0),
            "North": (0, 0, -1),
            "South": (0, 0, 1),
        }[Side]

    BanksBySignal: dict[
        str,
        set[tuple[int, int, str, str, int, bool]],
    ] = {}
    FacingMismatches = 0
    for (
        Signal,
        SourceCluster,
        TargetClusters,
        HasExternalTarget,
    ) in Topology.SignalEndpoints:
        HasExternalSource = SourceCluster is None
        Banks = BanksBySignal.setdefault(Signal, set())

        def AddBank(
            ClusterIndex: int,
            Side: str,
            Role: str,
        ) -> None:
            nonlocal FacingMismatches
            Slot = Assignment[ClusterIndex]
            Variant = Variants[ClusterIndex]
            Banks.add((
                Slot[0],
                Slot[1],
                Side,
                Role,
                Variant.Rotation,
                Variant.MirrorX,
            ))
            PinDirection = TransformDirection(
                (0, 0, 1) if Role == "Source" else (0, 0, -1),
                Variant.Rotation,
                Variant.MirrorX,
            )
            BoundaryDirection = DirectionForSide(Side)
            if (
                PinDirection[0] * BoundaryDirection[0]
                + PinDirection[2] * BoundaryDirection[2]
                <= 0
            ):
                FacingMismatches += 1

        if SourceCluster is not None:
            for TargetCluster in TargetClusters:
                if TargetCluster == SourceCluster:
                    continue
                SourceSide = BoundarySide(
                    Assignment[SourceCluster],
                    Assignment[TargetCluster],
                )
                AddBank(SourceCluster, SourceSide, "Source")
                AddBank(
                    TargetCluster,
                    OppositeBoundarySide(SourceSide),
                    "Target",
                )
            if HasExternalTarget:
                AddBank(SourceCluster, "East", "Source")
        elif HasExternalSource:
            for TargetCluster in TargetClusters:
                AddBank(TargetCluster, "West", "Target")

    SignalBanks = tuple(
        (Signal, tuple(sorted(Banks)))
        for Signal, Banks in sorted(BanksBySignal.items())
        if Banks
    )
    # Names select the cut endpoints, but never enter the topology identity.
    # Slot coordinates plus rigid transforms make renamed/reordered modules
    # produce the same ownership fingerprint.
    StructuralOwnership = tuple(sorted(
        Banks for _Signal, Banks in SignalBanks
    ))
    Pattern = ClusterInterfacePlacementPattern(
        SignalBanks=SignalBanks,
        OwnershipFingerprint=sha256(
            repr(StructuralOwnership).encode("utf-8")
        ).hexdigest(),
    )
    OwnershipBanksBySignal = Pattern.BanksBySignal()
    # Role and transform describe ownership identity, but the physical side of
    # one occupied cluster slot is the shared capacity resource.  Collapse
    # source/target roles before scoring so opposite endpoint roles cannot
    # hide competition for the same pin bank.
    PhysicalBanksBySignal = {
        Signal: frozenset(
            (Bank[0], Bank[1], Bank[2])
            for Bank in Banks
        )
        for Signal, Banks in OwnershipBanksBySignal.items()
    }
    # Facing banks of adjacent clusters feed the same capacity-one boundary
    # corridor.  Score that shared resource rather than treating the two sides
    # as independent merely because their owner slots differ.
    BoundaryCorridorsBySignal = {
        Signal: frozenset(
            ClusterBoundaryCorridorKey(Bank)
            for Bank in Banks
        )
        for Signal, Banks in PhysicalBanksBySignal.items()
    }
    PairBankConflicts = sum(
        bool(
            BoundaryCorridorsBySignal.get(str(First), frozenset())
            .intersection(
                BoundaryCorridorsBySignal.get(str(Second), frozenset())
            )
        )
        for First, Second in PairwiseConflictEdges
    )
    HigherOrderDemand = ScoreHigherOrderPhysicalBankDemand(
        BoundaryCorridorsBySignal,
        HigherOrderConflictSets,
    )
    return ClusterInterfacePlacementScore(
        PairBankConflicts=PairBankConflicts,
        HigherOrderBankPressure=HigherOrderDemand.CollisionPairs,
        HigherOrderPeakBankDemand=HigherOrderDemand.PeakDemand,
        HigherOrderBankExcessDemand=HigherOrderDemand.ExcessDemand,
        HigherOrderOverloadedBankCount=(
            HigherOrderDemand.OverloadedBankCount
        ),
        FacingMismatches=FacingMismatches,
        Pattern=Pattern,
    )

@dataclass(frozen=True)
class ClusterBoundaryLeaseRequest:
    """One packed interface signal requiring an owned pin-access portal."""

    SourceCluster: int
    TargetCluster: int
    Signal: str
    SourceBoundarySide: str
    TargetBoundarySide: str
    SourceTerminal: tuple[int, int, int] | None = None
    TargetTerminals: tuple[tuple[int, int, int], ...] = ()
    CompletePinAccess: bool = False

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SourceCluster": self.SourceCluster,
            "TargetCluster": self.TargetCluster,
            "Signal": self.Signal,
            "SourceBoundarySide": self.SourceBoundarySide,
            "TargetBoundarySide": self.TargetBoundarySide,
            "SourceTerminal": (
                list(self.SourceTerminal)
                if self.SourceTerminal is not None
                else None
            ),
            "TargetTerminals": [list(Value) for Value in self.TargetTerminals],
            "LeaseExtent": (
                "complete-pin-access-to-routing-track"
                if self.CompletePinAccess
                else "first-segment"
            ),
        }

def BuildClusterBoundaryBundles(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
) -> tuple[ClusterBoundaryBundle, ...]:
    """Build name-independent logical interfaces before physical placement."""
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
    SignalsByInterface: dict[tuple[int, int], set[str]] = {}
    EndpointsByInterface: dict[tuple[int, int], int] = {}
    for Gate in Module.Gates:
        TargetCluster = ClusterByGate.get(Gate.Name)
        if TargetCluster is None:
            continue
        for Signal in Gate.Inputs:
            SourceCluster = ProducerClusterBySignal.get(Signal)
            if SourceCluster is None or SourceCluster == TargetCluster:
                continue
            Interface = SourceCluster, TargetCluster
            SignalsByInterface.setdefault(Interface, set()).add(Signal)
            EndpointsByInterface[Interface] = (
                EndpointsByInterface.get(Interface, 0) + 1
            )
    return tuple(
        ClusterBoundaryBundle(
            SourceCluster=Source,
            TargetCluster=Target,
            Signals=tuple(sorted(Signals)),
            FanoutEndpoints=EndpointsByInterface[(Source, Target)],
        )
        for (Source, Target), Signals in sorted(SignalsByInterface.items())
    )

def BuildClusterBoundaryLeaseRequests(
    Bundles: tuple[ClusterBoundaryBundle, ...],
    Assignment: dict[int, tuple[int, int]],
    Module: Any | None = None,
    Clusters: tuple[tuple[str, ...], ...] = (),
    PlacedGates: Iterable[PlacedGate] = (),
    IncludePrimaryTerminals: bool = False,
) -> tuple[ClusterBoundaryLeaseRequest, ...]:
    """Materialize deterministic packed-boundary lease demand from slots.

    The router resolves the actual portal and its first segment against the
    authoritative resource graph.  Placement owns the invariant that every
    inter-cluster signal has one directional request, independent of names or
    synthesis order.
    """
    def BoundarySide(
        FromSlot: tuple[int, int],
        ToSlot: tuple[int, int],
    ) -> str:
        DeltaColumn = ToSlot[0] - FromSlot[0]
        DeltaRow = ToSlot[1] - FromSlot[1]
        if abs(DeltaColumn) >= abs(DeltaRow):
            return "East" if DeltaColumn >= 0 else "West"
        return "South" if DeltaRow >= 0 else "North"

    def PhysicalBoundarySide(
        FromTerminal: tuple[int, int, int],
        ToTerminals: Iterable[tuple[int, int, int]],
    ) -> str:
        Values = tuple(ToTerminals)
        if not Values:
            return "East"
        ToX = sum(Value[0] for Value in Values) / len(Values)
        ToZ = sum(Value[2] for Value in Values) / len(Values)
        DeltaX = ToX - FromTerminal[0]
        DeltaZ = ToZ - FromTerminal[2]
        if abs(DeltaX) >= abs(DeltaZ):
            return "East" if DeltaX >= 0 else "West"
        return "South" if DeltaZ >= 0 else "North"

    def OppositeBoundarySide(Side: str) -> str:
        return {
            "East": "West",
            "West": "East",
            "North": "South",
            "South": "North",
        }[Side]

    GateByName = {Gate.Name: Gate for Gate in PlacedGates}
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerBySignal = {
        Signal: Gate
        for Gate in (Module.Gates if Module is not None else ())
        for Signal in Gate.Outputs
    }
    ConsumersBySignal: dict[str, list[Any]] = {}
    for Gate in (Module.Gates if Module is not None else ()):
        for Signal in Gate.Inputs:
            ConsumersBySignal.setdefault(Signal, []).append(Gate)

    def SignalTerminals(
        Signal: str,
        SourceCluster: int,
        TargetCluster: int,
    ) -> tuple[tuple[int, int, int] | None, tuple[tuple[int, int, int], ...]]:
        # Gate identity is used only to carry placement geometry across the
        # stage boundary.  Selection remains entirely physical and topology
        # driven; names never participate in routing policy.
        SourceGate = ProducerBySignal.get(Signal)
        SourcePlaced = (
            GateByName.get(SourceGate.Name) if SourceGate is not None else None
        )
        SourceTerminal = (
            SourcePlaced.OutputPin
            if SourcePlaced is not None else None
        )
        Targets = []
        for Consumer in ConsumersBySignal.get(Signal, ()):
            TargetPlaced = GateByName.get(Consumer.Name)
            if (
                TargetPlaced is None
                or ClusterByGate.get(Consumer.Name) != TargetCluster
            ):
                continue
            Targets.extend(
                TargetPlaced.InputPins[Index]
                for Index, InputSignal in enumerate(TargetPlaced.Inputs)
                if InputSignal == Signal
            )
        return SourceTerminal, tuple(sorted(set(Targets)))

    Requests: list[ClusterBoundaryLeaseRequest] = []
    for Bundle in Bundles:
        SourceSlot = Assignment.get(Bundle.SourceCluster)
        TargetSlot = Assignment.get(Bundle.TargetCluster)
        if SourceSlot is None or TargetSlot is None:
            continue
        SourceSide = BoundarySide(SourceSlot, TargetSlot)
        TargetSide = BoundarySide(TargetSlot, SourceSlot)
        for Signal in Bundle.Signals:
            SourceTerminal, TargetTerminals = SignalTerminals(
                Signal,
                Bundle.SourceCluster,
                Bundle.TargetCluster,
            )
            Requests.append(ClusterBoundaryLeaseRequest(
                SourceCluster=Bundle.SourceCluster,
                TargetCluster=Bundle.TargetCluster,
                Signal=Signal,
                SourceBoundarySide=SourceSide,
                TargetBoundarySide=TargetSide,
                SourceTerminal=SourceTerminal,
                TargetTerminals=TargetTerminals,
                CompletePinAccess=IncludePrimaryTerminals,
            ))

    # Primary terminals are cluster interfaces too.  Omitting them allowed a
    # capacity-legal cluster-to-cluster lease pattern to collide later with an
    # input/output pin bank during complete portal assignment.
    if Module is not None and IncludePrimaryTerminals:
        for Gate in Module.Gates:
            GateKind = (
                Gate.Kind.value
                if hasattr(Gate.Kind, "value")
                else str(Gate.Kind)
            )
            PlacedGate = GateByName.get(Gate.Name)
            if PlacedGate is None:
                continue
            if GateKind == "INPUT" and PlacedGate.OutputPin is not None:
                for Signal in Gate.Outputs:
                    TargetsByCluster: dict[
                        int, list[tuple[int, int, int]]
                    ] = {}
                    for Consumer in ConsumersBySignal.get(Signal, ()):
                        TargetCluster = ClusterByGate.get(Consumer.Name)
                        TargetPlaced = GateByName.get(Consumer.Name)
                        if TargetCluster is None or TargetPlaced is None:
                            continue
                        TargetsByCluster.setdefault(
                            TargetCluster,
                            [],
                        ).extend(
                            TargetPlaced.InputPins[Index]
                            for Index, InputSignal
                            in enumerate(TargetPlaced.Inputs)
                            if InputSignal == Signal
                        )
                    for TargetCluster, TargetTerminals in sorted(
                        TargetsByCluster.items()
                    ):
                        SourceSide = PhysicalBoundarySide(
                            PlacedGate.OutputPin,
                            TargetTerminals,
                        )
                        Requests.append(ClusterBoundaryLeaseRequest(
                            SourceCluster=-1,
                            TargetCluster=TargetCluster,
                            Signal=Signal,
                            SourceBoundarySide=SourceSide,
                            TargetBoundarySide=OppositeBoundarySide(
                                SourceSide
                            ),
                            SourceTerminal=PlacedGate.OutputPin,
                            TargetTerminals=tuple(sorted(set(
                                TargetTerminals
                            ))),
                            CompletePinAccess=True,
                        ))
            elif GateKind == "OUTPUT":
                for InputIndex, Signal in enumerate(Gate.Inputs):
                    SourceGate = ProducerBySignal.get(Signal)
                    SourceCluster = (
                        ClusterByGate.get(SourceGate.Name)
                        if SourceGate is not None
                        else None
                    )
                    SourcePlaced = (
                        GateByName.get(SourceGate.Name)
                        if SourceGate is not None
                        else None
                    )
                    if (
                        SourceCluster is None
                        or SourcePlaced is None
                        or SourcePlaced.OutputPin is None
                        or InputIndex >= len(PlacedGate.InputPins)
                    ):
                        continue
                    TargetTerminal = PlacedGate.InputPins[InputIndex]
                    SourceSide = PhysicalBoundarySide(
                        SourcePlaced.OutputPin,
                        (TargetTerminal,),
                    )
                    Requests.append(ClusterBoundaryLeaseRequest(
                        SourceCluster=SourceCluster,
                        TargetCluster=-1,
                        Signal=Signal,
                        SourceBoundarySide=SourceSide,
                        TargetBoundarySide=OppositeBoundarySide(SourceSide),
                        SourceTerminal=SourcePlaced.OutputPin,
                        TargetTerminals=(TargetTerminal,),
                        CompletePinAccess=True,
                    ))
    return tuple(sorted(
        Requests,
        key=lambda Value: (
            Value.SourceCluster,
            Value.TargetCluster,
            Value.Signal,
        ),
    ))

def ScoreClusterBoundaryContracts(
    Bundles: tuple[ClusterBoundaryBundle, ...],
    Assignment: dict[int, tuple[int, int]],
    BoundaryCapacity: int,
) -> ClusterBoundaryContractScore:
    """Score whether all logical bundles fit the tentative grid cuts."""
    if BoundaryCapacity < 1:
        raise ValueError("BoundaryCapacity must be positive")
    SignalsByBoundary: dict[tuple[str, int], set[str]] = {}
    for Bundle in Bundles:
        SourceSlot = Assignment.get(Bundle.SourceCluster)
        TargetSlot = Assignment.get(Bundle.TargetCluster)
        if SourceSlot is None or TargetSlot is None:
            continue
        SourceColumn, SourceRow = SourceSlot
        TargetColumn, TargetRow = TargetSlot
        for Boundary in range(
            min(SourceColumn, TargetColumn),
            max(SourceColumn, TargetColumn),
        ):
            SignalsByBoundary.setdefault(("X", Boundary), set()).update(
                Bundle.Signals
            )
        for Boundary in range(
            min(SourceRow, TargetRow),
            max(SourceRow, TargetRow),
        ):
            SignalsByBoundary.setdefault(("Z", Boundary), set()).update(
                Bundle.Signals
            )
    Demands = [len(Signals) for Signals in SignalsByBoundary.values()]
    return ClusterBoundaryContractScore(
        PeakBoundaryDemand=max(Demands, default=0),
        TotalBoundaryDemand=sum(Demands),
        OverflowLanes=sum(
            max(0, Demand - BoundaryCapacity)
            for Demand in Demands
        ),
    )

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

    @property
    def LegalEscapeCandidateCounts(self) -> tuple[tuple[str, int], ...]:
        """Expose exact per-signal escape scarcity without mutable slot sets."""
        return tuple(
            (Signal, len(Slots))
            for Signal, Slots in self.LegalEscapeSlotsBySignal
        )

    @property
    def SingleCandidateBoundarySignals(self) -> tuple[str, ...]:
        """Return signals whose boundary access has exactly one legal choice."""
        return tuple(
            Signal
            for Signal, Count in self.LegalEscapeCandidateCounts
            if Count == 1
        )

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
    CandidateClaimsBySignal: (
        dict[str, list["BoundaryEscapeCandidate"]] | None
    ) = None,
) -> dict[str, set[tuple[int, int, int]]]:
    """Enumerate exact one-primitive exits from immutable terminal access."""
    Result: dict[str, set[tuple[int, int, int]]] = {}
    OrderedSignals = sorted(Signals)
    ForeignFixedClaimsBySignal: dict[str, RoutingResourceClaims] = {}
    for Signal in OrderedSignals:
        WireCells: set[tuple[int, int, int]] = set()
        SupportCells: set[tuple[int, int, int]] = set()
        RequiredAirCells: set[tuple[int, int, int]] = set()
        ElectricalCells: set[tuple[int, int, int]] = set()
        for OtherSignal, OtherClaims in FixedAccessClaimsBySignal.items():
            if OtherSignal == Signal:
                continue
            WireCells.update(OtherClaims.WireCells)
            SupportCells.update(OtherClaims.SupportCells)
            RequiredAirCells.update(OtherClaims.RequiredAirCells)
            ElectricalCells.update(OtherClaims.ElectricalCells)
        ForeignFixedClaimsBySignal[Signal] = RoutingResourceClaims(
            WireCells=frozenset(WireCells),
            SupportCells=frozenset(SupportCells),
            RequiredAirCells=frozenset(RequiredAirCells),
            ElectricalCells=frozenset(ElectricalCells),
        )
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
                ForeignClaims = ForeignFixedClaimsBySignal[Signal]
                if (
                    CandidateClaims.WireCells
                    & ForeignClaims.ElectricalCells
                    or ForeignClaims.WireCells
                    & CandidateClaims.ElectricalCells
                    or CandidateClaims.SupportCells
                    & (
                        ForeignClaims.WireCells
                        | ForeignClaims.RequiredAirCells
                    )
                    or ForeignClaims.SupportCells
                    & (
                        CandidateClaims.WireCells
                        | CandidateClaims.RequiredAirCells
                    )
                    or CandidateClaims.RequiredAirCells
                    & ForeignClaims.WireCells
                    or ForeignClaims.RequiredAirCells
                    & CandidateClaims.WireCells
                ):
                    continue
                LegalSlots.add(Neighbor)
                if CandidateClaimsBySignal is not None:
                    CandidateClaimsBySignal.setdefault(
                        Signal,
                        [],
                    ).append(BoundaryEscapeCandidate(
                        Signal=Signal,
                        Anchor=Anchor,
                        Entrance=Neighbor,
                        Claims=CandidateClaims,
                    ))
        Result[Signal] = LegalSlots
    return Result

@dataclass(frozen=True)
class BoundaryEscapeCandidate:
    """One exact first primitive from a fixed packed pin-access envelope."""

    Signal: str
    Anchor: tuple[int, int, int]
    Entrance: tuple[int, int, int]
    Claims: RoutingResourceClaims

    @property
    def StructuralKey(self) -> tuple[object, ...]:
        """Identify physical ownership without depending on a signal name."""
        return (
            self.Anchor,
            self.Entrance,
            tuple(sorted(map(str, self.Claims.ResourceIds))),
        )

@dataclass(frozen=True)
class CutBoundaryEscapeFeasibility:
    """Exact necessary-condition proof for one higher-order placement cut."""

    Verdict: str
    VariableCount: int
    SignalCount: int
    DomainCounts: tuple[tuple[int, str, int], ...]
    Assignment: tuple[
        tuple[int, str, tuple[int, int, int], tuple[int, int, int]],
        ...,
    ]
    ExpansionCount: int
    MaximumExpansions: int
    MaximumAssignedVariables: int
    ConflictSignals: tuple[str, ...]
    StructuralFingerprint: str

    @property
    def IsInfeasible(self) -> bool:
        return self.Verdict == "infeasible"

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Verdict": self.Verdict,
            "VariableCount": self.VariableCount,
            "SignalCount": self.SignalCount,
            "DomainCounts": [
                {
                    "ClusterId": ClusterId,
                    "Signal": Signal,
                    "CandidateCount": CandidateCount,
                }
                for ClusterId, Signal, CandidateCount in self.DomainCounts
            ],
            "Assignment": [
                {
                    "ClusterId": ClusterId,
                    "Signal": Signal,
                    "Anchor": list(Anchor),
                    "Entrance": list(Entrance),
                }
                for ClusterId, Signal, Anchor, Entrance in self.Assignment
            ],
            "ExpansionCount": self.ExpansionCount,
            "MaximumExpansions": self.MaximumExpansions,
            "MaximumAssignedVariables": self.MaximumAssignedVariables,
            "Deficit": max(
                0,
                self.VariableCount - self.MaximumAssignedVariables,
            ),
            "ConflictSignals": list(self.ConflictSignals),
            "StructuralFingerprint": self.StructuralFingerprint,
        }

def EvaluateCutBoundaryEscapeFeasibility(
    Domains: Mapping[
        tuple[int, str],
        Iterable[BoundaryEscapeCandidate],
    ],
    CutSignals: Iterable[str],
    MaximumExpansions: int = 4_096,
) -> CutBoundaryEscapeFeasibility:
    """Prove a cut's fixed first escapes are jointly capacity-one legal.

    This is a conservative placement prescreen, not a substitute for portal
    generation.  Every retained domain contains all exact one-primitive
    escapes found around the committed local geometry.  An exhaustive failed
    search is therefore a hard placement result; an exhausted search budget
    remains unknown and is left to the authoritative router.
    """
    if MaximumExpansions < 1:
        raise ValueError("MaximumExpansions must be positive")
    SelectedSignals = frozenset(map(str, CutSignals))
    NormalizedDomains = {
        (int(ClusterId), str(Signal)): tuple(sorted(
            {
                Candidate.StructuralKey: Candidate
                for Candidate in Candidates
                if str(Signal) in SelectedSignals
                and Candidate.Signal == str(Signal)
            }.values(),
            key=lambda Candidate: Candidate.StructuralKey,
        ))
        for (ClusterId, Signal), Candidates in Domains.items()
        if str(Signal) in SelectedSignals
    }
    Variables = tuple(sorted(
        NormalizedDomains,
        key=lambda Key: (
            len(NormalizedDomains[Key]),
            Key[0],
            Key[1],
        ),
    ))
    DomainCounts = tuple(
        (ClusterId, Signal, len(NormalizedDomains[(ClusterId, Signal)]))
        for ClusterId, Signal in sorted(NormalizedDomains)
    )

    def ClaimsConflict(
        First: RoutingResourceClaims,
        Second: RoutingResourceClaims,
    ) -> bool:
        return bool(
            (First.WireCells & Second.ElectricalCells)
            or (Second.WireCells & First.ElectricalCells)
            or (
                First.SupportCells
                & (Second.WireCells | Second.RequiredAirCells)
            )
            or (
                Second.SupportCells
                & (First.WireCells | First.RequiredAirCells)
            )
            or (First.RequiredAirCells & Second.WireCells)
            or (Second.RequiredAirCells & First.WireCells)
        )

    Selected: dict[tuple[int, str], BoundaryEscapeCandidate] = {}
    BestAssignment: tuple[
        tuple[int, str, tuple[int, int, int], tuple[int, int, int]],
        ...,
    ] = ()
    ExpansionCount = 0
    BudgetExhausted = False

    def RecordBest() -> None:
        nonlocal BestAssignment
        Candidate = tuple(sorted(
            (
                ClusterId,
                Signal,
                Choice.Anchor,
                Choice.Entrance,
            )
            for (ClusterId, Signal), Choice in Selected.items()
        ))
        if len(Candidate) > len(BestAssignment):
            BestAssignment = Candidate

    def Search(Index: int) -> bool:
        nonlocal ExpansionCount, BudgetExhausted
        RecordBest()
        if Index == len(Variables):
            return True
        if ExpansionCount >= MaximumExpansions:
            BudgetExhausted = True
            return False
        Variable = Variables[Index]
        Signal = Variable[1]
        for Candidate in NormalizedDomains[Variable]:
            ExpansionCount += 1
            if ExpansionCount > MaximumExpansions:
                BudgetExhausted = True
                return False
            if any(
                OtherSignal != Signal
                and ClaimsConflict(Candidate.Claims, Other.Claims)
                for (_OtherCluster, OtherSignal), Other
                in Selected.items()
            ):
                continue
            Selected[Variable] = Candidate
            if Search(Index + 1):
                return True
            del Selected[Variable]
        return False

    Complete = Search(0)
    Verdict = (
        "feasible"
        if Complete
        else "budget-exhausted"
        if BudgetExhausted
        else "infeasible"
    )
    # Signal identifiers are diagnostics.  The fingerprint represents only
    # physical domain structure so renamed/reordered equivalent cuts agree.
    StructuralDomains = tuple(sorted(
        tuple(
            Candidate.StructuralKey
            for Candidate in NormalizedDomains[Variable]
        )
        for Variable in Variables
    ))
    return CutBoundaryEscapeFeasibility(
        Verdict=Verdict,
        VariableCount=len(Variables),
        SignalCount=len({
            Signal for _ClusterId, Signal in Variables
        }),
        DomainCounts=DomainCounts,
        Assignment=BestAssignment,
        ExpansionCount=ExpansionCount,
        MaximumExpansions=MaximumExpansions,
        MaximumAssignedVariables=len(BestAssignment),
        ConflictSignals=(
            tuple(sorted(SelectedSignals))
            if Verdict == "infeasible"
            else ()
        ),
        StructuralFingerprint=sha256(
            repr(StructuralDomains).encode("utf-8")
        ).hexdigest(),
    )

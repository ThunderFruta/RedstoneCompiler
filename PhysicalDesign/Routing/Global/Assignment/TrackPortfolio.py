"""Raw track domains and regeneration portfolios."""

from __future__ import annotations

from ...Planning.ChannelPlanner import RasterizeChannelSegment

from ...Regions.Proofs.Validation import BuildPhysicalPortLocalContractFingerprint

from ...Regions.Proofs.Validation import BuildPhysicalPortSeamContractFingerprint

from ....Contracts.Component import PhysicalComponentPortReservation

from ....Contracts.Core import Position2

from ....Contracts.Core import Position3

from ....Contracts.Placement import TrackAssignmentPreparation

from ....Constraints.BoundaryRelations import BuildRawPortalResourceGeometryFingerprint

from ....Runtime.Reliability import BuildStableFingerprint

from ....Runtime.Reliability import RetainUnaffectedCandidateCache

from ....Resources.ResourceGraph import FindSelfClaimConflicts

from ....Resources.ResourceGraph import IndexedRoutingResourceGraph

from ....Resources.ResourceGraph import LocalRouteClaim

from ....Resources.ResourceGraph import NetRouteCandidate

from ....Resources.ResourceGraph import PinAccessPortal

from ....Resources.ResourceGraph import PortalReservation

from ....Resources.ResourceGraph import RoutingResourceClaims

from ....Redstone.Technology import DefaultRedstoneRoutingTechnology

from collections import Counter

from collections import defaultdict

from collections import deque

from dataclasses import dataclass

from dataclasses import replace

from typing import Any

from typing import Callable

from typing import Iterable

from typing import Mapping

from .AssignmentState import (
    CandidateRequestShapeDescriptor,
    _ClaimsConflict,
)

from ..Leases.BoundaryLeasePlanning import PreRouteLocalClaimChoice

from ..Candidates.CandidateCache import ExtendIndexedRoutingResourceGraph

from ..Ports.Portals import _BuildCandidateGraph, _FindComponentNodes

from ..Orchestration.RunModels import RawTrackAssignmentBaseClaim, RawTrackAssignmentDomain, RawTrackAssignmentValue

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
    NativePairwiseComplete = bool(
        getattr(Result, "PairwiseCompatibilityComplete", False)
    )
    PairwiseEdges = (
        [
            [str(FirstSignal), str(SecondSignal)]
            for FirstSignal, SecondSignal in getattr(
                Result,
                "PairwiseIncompatibleSignals",
                (),
            )
        ]
        if NativePairwiseComplete
        else []
    )
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
    if NativePairwiseComplete:
        CompletedSignalPairs = TotalSignalPairs
    else:
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
    HotspotPositions = {tuple(Value) for Value in Hotspots}
    CongestionCutSignals = sorted(
        Signal
        for Signal, Candidates in CandidatesBySignal.items()
        if any(
            Resource.Position in HotspotPositions
            for Candidate in Candidates
            for Resource in Candidate.Claims.ResourceIds
        )
    )
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
        *CongestionCutSignals,
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
    PairDegrees = Counter(
        Signal
        for Pair in PairwiseEdges
        for Signal in Pair
    )
    MaximumPairDegree = max(PairDegrees.values(), default=0)
    UniversalConflictHubs = {}
    for Signal in sorted(
        Signal
        for Signal, Degree in PairDegrees.items()
        if Degree == MaximumPairDegree and Degree > 0
    )[:3]:
        CandidateClaims = tuple(
            Candidate.Claims
            for Candidate in CandidatesBySignal[Signal]
        )
        CommonWireCells = (
            frozenset.intersection(*(
                Claims.WireCells for Claims in CandidateClaims
            ))
            if CandidateClaims
            else frozenset()
        )
        CommonElectricalCells = (
            frozenset.intersection(*(
                Claims.ElectricalCells for Claims in CandidateClaims
            ))
            if CandidateClaims
            else frozenset()
        )

        def ClaimCountRange(Attribute: str) -> list[int]:
            Counts = [
                len(getattr(Claims, Attribute))
                for Claims in CandidateClaims
            ]
            return [min(Counts), max(Counts)] if Counts else []

        UniversalConflictHubs[Signal] = {
            "PairDegree": PairDegrees[Signal],
            "CandidateCount": len(CandidateClaims),
            "WireCellCountRange": ClaimCountRange("WireCells"),
            "SupportCellCountRange": ClaimCountRange(
                "SupportCells"
            ),
            "ElectricalCellCountRange": ClaimCountRange(
                "ElectricalCells"
            ),
            "RequiredAirCellCountRange": ClaimCountRange(
                "RequiredAirCells"
            ),
            "CommonWireCellCount": len(CommonWireCells),
            "CommonElectricalCellCount": len(
                CommonElectricalCells
            ),
            "CommonWireBounds": (
                [
                    min(Position[Index] for Position in CommonWireCells)
                    for Index in range(3)
                ]
                + [
                    max(Position[Index] for Position in CommonWireCells)
                    for Index in range(3)
                ]
                if CommonWireCells
                else []
            ),
        }
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
        "CongestionCutSignals": CongestionCutSignals,
        "ConflictSignals": ConflictSignals,
        "PairwiseIncompatibleEdges": PairwiseEdges,
        "UniversalConflictHubs": UniversalConflictHubs,
        "ResourceHotspots": Hotspots,
        "PortalReservations": [Value.ToDictionary() for Value in Reservations],
    }

def SelectPlacementRelocationSignals(
    ConflictGraph: dict[str, object],
) -> list[str]:
    """Preserve every contributor identified by the typed congestion cut."""
    Signals: set[str] = set()
    for Key in (
        "NativeConflictSignals",
        "CongestionCutSignals",
        "NoCandidateSignals",
        "ConflictSignals",
    ):
        Values = ConflictGraph.get(Key, ())
        if isinstance(Values, tuple | list):
            Signals.update(str(Value) for Value in Values)
    for Key in ("PairwiseIncompatibleEdges", "StackedConflictPairs"):
        Values = ConflictGraph.get(Key, ())
        if not isinstance(Values, tuple | list):
            continue
        Signals.update(
            str(Signal)
            for Pair in Values
            if isinstance(Pair, tuple | list)
            for Signal in Pair
        )
    FailureNet = ConflictGraph.get("FailureNet")
    if FailureNet:
        Signals.add(str(FailureNet))
    return sorted(Signals)

def SelectPriorityPlacementRelocationSignals(
    ConflictGraph: dict[str, object],
    MaximumSignals: int = 5,
) -> list[str]:
    """Return the smallest exact cut that should drive the next geometry move."""
    if MaximumSignals < 1:
        raise ValueError("MaximumSignals must be positive")
    Signals: set[str] = set()
    for Key in (
        "NativeConflictSignals",
        "CongestionCutSignals",
        "NoCandidateSignals",
    ):
        Values = ConflictGraph.get(Key, ())
        if isinstance(Values, tuple | list):
            Signals.update(str(Value) for Value in Values)
    FailureNet = ConflictGraph.get("FailureNet")
    if FailureNet:
        Signals.add(str(FailureNet))
        RawEdges = ConflictGraph.get("PairwiseIncompatibleEdges", ())
        CandidateCounts = ConflictGraph.get("CandidateCounts", {})
        Counts = (
            {
                str(Signal): max(0, int(Count))
                for Signal, Count in CandidateCounts.items()
            }
            if isinstance(CandidateCounts, dict)
            else {}
        )
        FailureNeighbors = {
            str(Pair[1] if str(Pair[0]) == str(FailureNet) else Pair[0])
            for Pair in RawEdges
            if isinstance(Pair, tuple | list)
            and len(Pair) == 2
            and str(FailureNet) in map(str, Pair)
        }
        # A large pair graph often reflects a few fixed or nearly fixed route
        # domains colliding with the failed net.  Those minimum-domain
        # neighbors are the actionable placement cut; returning every pair
        # endpoint turns one pin-bank repair into a design-wide relocation.
        MinimumDomainNeighbors = sorted(
            (
                Neighbor
                for Neighbor in FailureNeighbors
                if Counts.get(Neighbor, 0) <= 1
            ),
            key=lambda Neighbor: (
                Counts.get(Neighbor, 0),
                Neighbor,
            ),
        )
        Signals.update(
            MinimumDomainNeighbors[:max(0, MaximumSignals - 1)]
        )
    if not Signals:
        Values = ConflictGraph.get("ConflictSignals", ())
        if isinstance(Values, tuple | list):
            Signals.update(str(Value) for Value in Values)
    return sorted(Signals)

def SelectAnonymousMinimumFailurePairRelocationSignals(
    ConflictGraph: object,
    CandidateDomainFingerprints: dict[str, str],
) -> list[str]:
    """Select one exact pair using physical-domain identity, not net names."""
    if not isinstance(ConflictGraph, dict):
        return []
    FailureNet = str(ConflictGraph.get("FailureNet", ""))
    if not FailureNet:
        return []
    CandidateCounts = ConflictGraph.get("CandidateCounts", {})
    Counts = (
        {
            str(Signal): max(0, int(Count))
            for Signal, Count in CandidateCounts.items()
        }
        if isinstance(CandidateCounts, dict)
        else {}
    )
    Neighbors = {
        str(Pair[1] if str(Pair[0]) == FailureNet else Pair[0])
        for Pair in ConflictGraph.get("PairwiseIncompatibleEdges", ())
        if isinstance(Pair, tuple | list)
        and len(Pair) == 2
        and FailureNet in {str(Pair[0]), str(Pair[1])}
    }
    if not Neighbors:
        return [FailureNet]
    Neighbor = min(
        Neighbors,
        key=lambda Signal: (
            Counts.get(Signal, 0),
            CandidateDomainFingerprints.get(Signal, ""),
            # Only resolves physically indistinguishable domains. Their
            # geometry action is equivalent; retain deterministic artifacts.
            Signal,
        ),
    )
    return sorted((FailureNet, Neighbor))

def BuildAnonymousCandidateDomainFingerprint(
    Candidates: tuple[NetRouteCandidate, ...] | list[NetRouteCandidate],
) -> str:
    """Fingerprint physical claims without requiring orderable resource IDs."""
    return BuildStableFingerprint([
        sorted(
            str(Resource)
            for Resource in Candidate.Claims.ResourceIds
        )
        for Candidate in Candidates
    ])

def BuildTrackAssignmentCandidateDomainFingerprint(
    Resources: Any,
    CandidatesBySignal: Mapping[str, Iterable[NetRouteCandidate]],
    LocalChoicesBySignal: Mapping[
        str,
        Iterable[PreRouteLocalClaimChoice],
    ],
) -> str:
    """Identify the complete physical value domain of one assignment solve.

    A frozen selection cannot safely be replayed merely because every
    candidate ID still exists: portal regeneration could otherwise preserve
    an ID while changing the claimed cells.  Keep the immutable resource
    graph identity and every ordinary/local value's exact resource identity
    in one order-independent contract.
    """
    OrdinaryValues = tuple(
        (
            str(Signal),
            str(Candidate.CandidateId),
            tuple(sorted(map(str, Candidate.Claims.ResourceIds))),
        )
        for Signal in sorted(CandidatesBySignal)
        for Candidate in sorted(
            CandidatesBySignal[Signal],
            key=lambda Value: str(Value.CandidateId),
        )
    )
    LocalValues = tuple(
        (
            str(Signal),
            str(Choice.ChoiceId),
            str(Choice.ClaimFingerprint),
            tuple(sorted(map(str, Choice.Claim.Claims.ResourceIds))),
        )
        for Signal in sorted(LocalChoicesBySignal)
        for Choice in sorted(
            LocalChoicesBySignal[Signal],
            key=lambda Value: (
                str(Value.ChoiceId),
                str(Value.ClaimFingerprint),
            ),
        )
    )
    return BuildStableFingerprint({
        "Kind": "track-assignment-candidate-domain-v1",
        "ResourceGraph": BuildRawPortalResourceGeometryFingerprint(
            Resources
        ),
        "OrdinaryValues": OrdinaryValues,
        "LocalValues": LocalValues,
    })

def BuildRawTrackAssignmentPortalDomainFingerprint(
    Portals: Mapping[
        tuple[str, Position3, int],
        Iterable[PinAccessPortal],
    ],
    BoundaryLeaseReservations: Iterable[PortalReservation],
) -> str:
    """Fingerprint the immutable portal/lease handoff behind one domain."""
    return BuildStableFingerprint({
        "Kind": "raw-track-assignment-portal-domain-v1",
        "Portals": tuple(
            (
                str(Signal),
                tuple(Terminal),
                int(Layer),
                tuple(sorted(
                    str(Portal.PortalId) for Portal in Values
                )),
            )
            for (Signal, Terminal, Layer), Values in sorted(
                Portals.items(),
                key=lambda Value: (
                    str(Value[0][0]),
                    tuple(Value[0][1]),
                    int(Value[0][2]),
                ),
            )
        ),
        "BoundaryLeaseReservations": tuple(
            (
                str(Reservation.Signal),
                tuple(Reservation.Terminal),
                int(Reservation.Layer),
                int(Reservation.SlotIndex),
                str(Reservation.PortalId),
                tuple(sorted(map(
                    str,
                    Reservation.Claims.ResourceIds,
                ))),
            )
            for Reservation in sorted(
                BoundaryLeaseReservations,
                key=lambda Value: (
                    str(Value.Signal),
                    tuple(Value.Terminal),
                    int(Value.Layer),
                    int(Value.SlotIndex),
                    str(Value.PortalId),
                ),
            )
        ),
    })

def BuildRawTrackAssignmentDomain(
    *,
    Signals: Iterable[str],
    CandidatesBySignal: Mapping[str, Iterable[NetRouteCandidate]],
    LocalChoicesBySignal: Mapping[
        str,
        Iterable[PreRouteLocalClaimChoice],
    ],
    BaseLocalClaims: Iterable[LocalRouteClaim],
    BoundaryLeaseReservations: Iterable[PortalReservation],
    AssignmentIndexed: IndexedRoutingResourceGraph,
    CandidateDomainFingerprint: str,
    LocalClaimDomainFingerprint: str,
    PlacementFingerprint: str,
    ResourceGraphFingerprint: str,
    PortalDomainFingerprint: str,
    Complete: bool,
    IncompleteReason: str = "",
    MaximumAssignmentExpansions: int = 1,
    MinimizeMaximumRoutingLayer: bool = False,
    Diagnostics: Iterable[tuple[str, object]] = (),
    NativeAssignmentContext: Any | None = None,
) -> RawTrackAssignmentDomain:
    """Freeze the exact Python-side input to one later native assignment.

    This function deliberately does not call the Rust solver.  It makes the
    candidate/value domain, immutable base ownership, canonical resource
    index, and handoff identities available to a higher-level placement
    selector in one pass.
    """
    SignalOrder = tuple(sorted({str(Signal) for Signal in Signals}))
    OrdinaryBySignal = {
        Signal: tuple(sorted(
            CandidatesBySignal.get(Signal, ()),
            key=lambda Value: str(Value.CandidateId),
        ))
        for Signal in SignalOrder
    }
    LocalBySignal = {
        Signal: tuple(sorted(
            LocalChoicesBySignal.get(Signal, ()),
            key=lambda Value: (
                str(Value.ChoiceId),
                str(Value.ClaimFingerprint),
            ),
        ))
        for Signal in SignalOrder
    }
    OrderedBaseLocalClaims = tuple(sorted(
        BaseLocalClaims,
        key=lambda Value: (
            str(Value.Signal),
            int(Value.ClusterId),
            tuple(Value.Root),
            tuple(sorted(Value.Nodes)),
        ),
    ))
    OrderedBoundaryLeases = tuple(sorted(
        BoundaryLeaseReservations,
        key=lambda Value: (
            str(Value.Signal),
            tuple(Value.Terminal),
            int(Value.Layer),
            int(Value.SlotIndex),
            str(Value.PortalId),
        ),
    ))
    Indexed = ExtendIndexedRoutingResourceGraph(
        AssignmentIndexed,
        (
            *(
                Candidate.Claims
                for Values in OrdinaryBySignal.values()
                for Candidate in Values
            ),
            *(
                Choice.Claim.Claims
                for Values in LocalBySignal.values()
                for Choice in Values
            ),
            *(Claim.Claims for Claim in OrderedBaseLocalClaims),
            *(Reservation.Claims for Reservation in OrderedBoundaryLeases),
        ),
    )
    Values = tuple(
        Value
        for Signal in SignalOrder
        for Value in (
            *(
                RawTrackAssignmentValue(
                    Signal=Signal,
                    CandidateId=str(Candidate.CandidateId),
                    Claims=Candidate.Claims,
                    MaterialCost=int(Candidate.MaterialCost),
                    FootprintGrowth=int(Candidate.FootprintGrowth),
                    Length=int(Candidate.Length),
                    BendCount=int(Candidate.BendCount),
                    ViaCount=int(Candidate.ViaCount),
                )
                for Candidate in OrdinaryBySignal[Signal]
            ),
            *(
                RawTrackAssignmentValue(
                    Signal=Signal,
                    CandidateId=str(Choice.ChoiceId),
                    Claims=Choice.Claim.Claims,
                    MaterialCost=int(Choice.MaterialCost),
                    FootprintGrowth=len({
                        (X, Z) for X, _Y, Z in Choice.Claim.Nodes
                    }),
                    Length=len(Choice.Claim.Nodes),
                    BendCount=0,
                    ViaCount=0,
                    ValueKind="local-claim",
                )
                for Choice in LocalBySignal[Signal]
            ),
        )
    )
    BaseClaims = tuple(
        (
            *(
                RawTrackAssignmentBaseClaim(
                    Signal=str(Claim.Signal),
                    ClaimId=(
                        f"local:{Index}:{Claim.Signal}:"
                        f"{Claim.ClusterId}"
                    ),
                    Claims=Claim.Claims,
                )
                for Index, Claim in enumerate(OrderedBaseLocalClaims)
            ),
            *(
                RawTrackAssignmentBaseClaim(
                    Signal=str(Reservation.Signal),
                    ClaimId=(
                        f"lease:{Index}:{Reservation.PortalId}"
                    ),
                    Claims=Reservation.Claims,
                )
                for Index, Reservation in enumerate(OrderedBoundaryLeases)
            ),
        )
    )
    return RawTrackAssignmentDomain(
        ResourcePositions=Indexed.ResourcePositions,
        Values=Values,
        BaseClaims=BaseClaims,
        CandidateCounts=tuple(
            (Signal, len(OrdinaryBySignal[Signal]) + len(LocalBySignal[Signal]))
            for Signal in SignalOrder
        ),
        CandidateDomainFingerprint=CandidateDomainFingerprint,
        LocalClaimDomainFingerprint=LocalClaimDomainFingerprint,
        PlacementFingerprint=PlacementFingerprint,
        ResourceGraphFingerprint=ResourceGraphFingerprint,
        PortalDomainFingerprint=PortalDomainFingerprint,
        Complete=bool(Complete),
        IncompleteReason=IncompleteReason,
        MaximumAssignmentExpansions=max(1, int(MaximumAssignmentExpansions)),
        MinimizeMaximumRoutingLayer=bool(MinimizeMaximumRoutingLayer),
        Diagnostics=tuple(Diagnostics),
        NativeAssignmentContext=NativeAssignmentContext,
    )

def BuildTrackAssignmentPreparationFromRawDomain(
    Domain: RawTrackAssignmentDomain,
    NativeResult: Any,
) -> TrackAssignmentPreparation:
    """Convert one future aggregate-native selection into the live handoff.

    The existing detailed router already knows how to consume a
    ``TrackAssignmentPreparation`` and validate it against regenerated
    candidates.  Reusing that handoff keeps the extraction slice isolated
    from route materialization until the aggregate native solver exists.
    """
    Selected = tuple(sorted(
        (str(Signal), str(CandidateId))
        for Signal, CandidateId in getattr(
            NativeResult,
            "SelectedCandidateIds",
            (),
        )
    ))
    ValuesByKey = {
        (Value.Signal, Value.CandidateId): Value
        for Value in Domain.Values
    }
    Unknown = tuple(
        Value for Value in Selected if Value not in ValuesByKey
    )
    if Unknown:
        raise ValueError(
            "native track-assignment result selected a value outside the "
            "frozen raw domain"
        )
    BudgetExhausted = bool(getattr(NativeResult, "BudgetExhausted", False))
    DeadlineExceeded = bool(getattr(NativeResult, "DeadlineExceeded", False))
    Complete = bool(Domain.Complete and not BudgetExhausted and not DeadlineExceeded)
    IncompleteReason = (
        Domain.IncompleteReason
        if not Domain.Complete
        else "assignment-work-cap"
        if BudgetExhausted
        else "assignment-deadline"
        if DeadlineExceeded
        else ""
    )
    LocalSelections = tuple(
        Value
        for Value in Selected
        if ValuesByKey[Value].ValueKind == "local-claim"
    )
    OrdinarySelections = tuple(
        Value
        for Value in Selected
        if ValuesByKey[Value].ValueKind == "ordinary"
    )
    return TrackAssignmentPreparation(
        Success=bool(getattr(NativeResult, "Success", False) and Complete),
        SelectedCandidateIds=OrdinarySelections,
        CandidateCounts=Domain.CandidateCounts,
        ConflictSignals=tuple(sorted(map(
            str,
            getattr(NativeResult, "ConflictSignals", ()),
        ))),
        ConflictResourceIndices=tuple(sorted(map(
            int,
            getattr(NativeResult, "ConflictResourceIndices", ()),
        ))),
        ExpansionCount=int(getattr(NativeResult, "ExpansionCount", 0)),
        Complete=Complete,
        IncompleteReason=IncompleteReason,
        Diagnostics=(
            ("RawTrackAssignmentDomainFingerprint", Domain.DomainFingerprint),
            ("RawTrackAssignmentResourceCount", len(Domain.ResourcePositions)),
            *Domain.Diagnostics,
        ),
        SelectedLocalClaimChoiceIds=LocalSelections,
        LocalClaimDomainFingerprint=Domain.LocalClaimDomainFingerprint,
        CandidateDomainFingerprint=Domain.CandidateDomainFingerprint,
        SelectedCapacityResourceIds=Domain.SelectedCapacityResourceIds(
            Selected
        ),
    )

def SelectCandidateRegenerationSignals(
    ConflictGraph: dict[str, object],
) -> list[str]:
    """Choose every reported contributor to the exact candidate-domain cut."""
    RawEdges = ConflictGraph.get("PairwiseIncompatibleEdges", ())
    PairSignals = {
        str(Signal)
        for Pair in RawEdges
        if isinstance(Pair, tuple | list) and len(Pair) == 2
        for Signal in Pair
    }
    for Key in (
        "CandidateCoverageRepairSignals",
        "PriorityRelocationSignals",
    ):
        CoverageSignals = ConflictGraph.get(Key, ())
        if isinstance(CoverageSignals, tuple | list):
            PairSignals.update(
                str(Signal) for Signal in CoverageSignals
            )
    CandidateCounts = ConflictGraph.get("CandidateCounts", {})
    Counts = (
        {
            str(Signal): int(Count)
            for Signal, Count in CandidateCounts.items()
        }
        if isinstance(CandidateCounts, dict)
        else {}
    )
    if PairSignals:
        return sorted(
            PairSignals,
            key=lambda Signal: (
                Counts.get(Signal, 0),
                Signal,
            ),
        )
    for Key in ("NoCandidateSignals", "ConflictSignals"):
        Values = ConflictGraph.get(Key, ())
        if isinstance(Values, tuple | list) and Values:
            return sorted(str(Value) for Value in Values)
    return []

def SelectCandidateRegenerationCoverSignals(
    ConflictGraph: dict[str, object],
    PriorSignals: frozenset[str] = frozenset(),
    ExactDomainSignals: frozenset[str] = frozenset(),
) -> list[str]:
    """Batch every fresh endpoint in the current exact conflict cut."""
    CandidateCounts = ConflictGraph.get("CandidateCounts", {})
    Counts = (
        {
            str(Signal): int(Count)
            for Signal, Count in CandidateCounts.items()
        }
        if isinstance(CandidateCounts, dict)
        else {}
    )
    Selected: set[str] = set()
    RawEdges = ConflictGraph.get("PairwiseIncompatibleEdges", ())
    SawPair = False
    for RawPair in RawEdges:
        if not isinstance(RawPair, tuple | list) or len(RawPair) != 2:
            continue
        SawPair = True
        Pair = tuple(str(Signal) for Signal in RawPair)
        Available = tuple(
            Signal for Signal in Pair if Signal not in PriorSignals
        )
        if not Available:
            continue
        Selected.update(Available)
    if (
        ConflictGraph.get("Classification")
        == "relocated-higher-order-conflict"
    ):
        FreshDomainSignals = ExactDomainSignals - PriorSignals
        if FreshDomainSignals:
            Selected.update(FreshDomainSignals)
        else:
            NativeSignals = ConflictGraph.get("NativeConflictSignals", ())
            ConflictSignals = ConflictGraph.get("ConflictSignals", ())
            SourceSignals = (
                NativeSignals
                if isinstance(NativeSignals, tuple | list) and NativeSignals
                else ConflictSignals
            )
            if isinstance(SourceSignals, tuple | list):
                FreshSignals = {
                    str(Signal)
                    for Signal in SourceSignals
                    if str(Signal) not in PriorSignals
                }
                Selected.update(
                    FreshSignals
                    or {str(Signal) for Signal in SourceSignals}
                )
    if Selected:
        return sorted(
            Selected,
            key=lambda Signal: (
                Counts.get(Signal, 0),
                Signal,
            ),
        )
    if SawPair:
        for RawPair in RawEdges:
            if not isinstance(RawPair, tuple | list) or len(RawPair) != 2:
                continue
            Pair = tuple(str(Signal) for Signal in RawPair)
            Selected.add(min(
                Pair,
                key=lambda Signal: (
                    Counts.get(Signal, 0),
                    Signal,
                ),
            ))
        return sorted(
            Selected,
            key=lambda Signal: (
                Counts.get(Signal, 0),
                Signal,
            ),
        )
    for Key in (
        "NativeConflictSignals",
        "NoCandidateSignals",
        "ConflictSignals",
    ):
        ConflictSignals = ConflictGraph.get(Key, ())
        if not isinstance(ConflictSignals, tuple | list) or not ConflictSignals:
            continue
        Available = tuple(
            str(Signal)
            for Signal in ConflictSignals
            if str(Signal) not in PriorSignals
        )
        if not Available:
            Available = tuple(str(Signal) for Signal in ConflictSignals)
        return sorted(
            Available,
            key=lambda Signal: (
                Counts.get(Signal, 0),
                Signal,
            ),
        )
    return []

def SelectConflictAvoidancePositions(
    ConflictGraph: dict[str, object],
    MaximumPositions: int = 32,
) -> frozenset[Position3]:
    """Return a bounded exact-conflict cut for offender route regeneration."""
    Positions: list[Position3] = []
    RawHotspots = ConflictGraph.get("ResourceHotspots", ())
    if not isinstance(RawHotspots, tuple | list):
        return frozenset()
    for RawPosition in RawHotspots:
        if (
            not isinstance(RawPosition, tuple | list)
            or len(RawPosition) != 3
        ):
            continue
        Positions.append(tuple(int(Value) for Value in RawPosition))
        if len(Positions) >= max(0, MaximumPositions):
            break
    return frozenset(Positions)

def MergeSignalScopedAvoidancePositions(
    Existing: dict[str, frozenset[Position3]] | None,
    Signals: frozenset[str],
    Positions: frozenset[Position3],
) -> dict[str, frozenset[Position3]]:
    """Add exact collision positions only to the signals that produced them."""
    Result = {
        str(Signal): frozenset(SignalPositions)
        for Signal, SignalPositions in (Existing or {}).items()
    }
    if not Positions:
        return Result
    for Signal in Signals:
        Result[str(Signal)] = frozenset({
            *Result.get(str(Signal), frozenset()),
            *Positions,
        })
    return Result

def SelectPartialAssignmentAvoidancePositions(
    SelectedCandidateIds: tuple[tuple[str, str], ...] | list[tuple[str, str]],
    CandidatesBySignal: dict[str, list[NetRouteCandidate]],
    RegenerateSignals: frozenset[str],
) -> frozenset[Position3]:
    """Freeze the best compatible partial assignment around its offenders."""
    CandidateById = {
        (Signal, Candidate.CandidateId): Candidate
        for Signal, Candidates in CandidatesBySignal.items()
        if Signal not in RegenerateSignals
        for Candidate in Candidates
    }
    Positions: set[Position3] = set()
    for SignalValue, CandidateIdValue in SelectedCandidateIds:
        Signal = str(SignalValue)
        if Signal in RegenerateSignals:
            continue
        Candidate = CandidateById.get((Signal, str(CandidateIdValue)))
        if Candidate is None:
            continue
        Positions.update(
            Resource.Position
            for Resource in Candidate.Claims.ResourceIds
        )
    return frozenset(Positions)

def CandidateClaimsConflict(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> bool:
    """Return whether two candidate claim sets are physically incompatible."""
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

def SelectPartialAssignmentBlockerSignals(
    SelectedCandidateIds: tuple[tuple[str, str], ...] | list[tuple[str, str]],
    CandidatesBySignal: dict[str, list[NetRouteCandidate]],
    RegenerateSignals: frozenset[str],
    MaximumBlockers: int = 16,
) -> frozenset[str]:
    """Release one bounded selected-route cut per omitted signal."""
    SelectedBySignal = {
        str(Signal): str(CandidateId)
        for Signal, CandidateId in SelectedCandidateIds
        if str(Signal) not in RegenerateSignals
    }
    SelectedCandidates = {
        Signal: Candidate
        for Signal, CandidateId in SelectedBySignal.items()
        for Candidate in CandidatesBySignal.get(Signal, ())
        if Candidate.CandidateId == CandidateId
    }
    Blockers: set[str] = set()
    for Signal in sorted(
        RegenerateSignals,
        key=lambda Value: (
            len(CandidatesBySignal.get(Value, ())),
            Value,
        ),
    ):
        CandidateBlockers = [
            frozenset(
                SelectedSignal
                for SelectedSignal, SelectedCandidate
                in SelectedCandidates.items()
                if CandidateClaimsConflict(
                    Candidate.Claims,
                    SelectedCandidate.Claims,
                )
            )
            for Candidate in CandidatesBySignal.get(Signal, ())
        ]
        if not CandidateBlockers:
            continue
        BestBlockers = min(
            CandidateBlockers,
            key=lambda Values: (
                len(Values - Blockers),
                len(Values),
                tuple(sorted(Values)),
            ),
        )
        for Blocker in sorted(BestBlockers - Blockers):
            if len(Blockers) >= max(0, MaximumBlockers):
                return frozenset(Blockers)
            Blockers.add(Blocker)
    return frozenset(Blockers)

def RetainPartialAssignmentCandidateCache(
    CandidatesBySignal: dict[str, list[NetRouteCandidate]],
    CandidateMetadata: dict[str, dict[str, object]],
    SelectedCandidateIds: tuple[tuple[str, str], ...] | list[tuple[str, str]],
    RegenerateSignals: frozenset[str],
) -> tuple[
    dict[str, tuple[NetRouteCandidate, ...]],
    dict[str, dict[str, object]],
]:
    """Freeze compatible non-offender choices for one focused exact repair."""
    RetainedCandidates, RetainedMetadata = RetainUnaffectedCandidateCache(
        CandidatesBySignal,
        CandidateMetadata,
        RegenerateSignals,
    )
    SelectedBySignal = {
        str(Signal): str(CandidateId)
        for Signal, CandidateId in SelectedCandidateIds
    }
    for Signal, Candidates in tuple(RetainedCandidates.items()):
        SelectedCandidateId = SelectedBySignal.get(Signal)
        if SelectedCandidateId is None:
            continue
        SelectedCandidates = tuple(
            Candidate
            for Candidate in Candidates
            if Candidate.CandidateId == SelectedCandidateId
        )
        if not SelectedCandidates:
            continue
        RetainedCandidates[Signal] = SelectedCandidates
        Metadata = RetainedMetadata.get(Signal, {})
        RetainedMetadata[Signal] = (
            {
                SelectedCandidateId: Metadata[SelectedCandidateId]
            }
            if SelectedCandidateId in Metadata
            else {}
        )
    return RetainedCandidates, RetainedMetadata

def ShouldReleaseFrozenPartialAssignment(
    FreezePartialAssignment: bool,
    NextCandidateDiversityLevel: int,
    FinalCandidateDiversityLevel: int,
) -> bool:
    """Release compatible alternatives for the final higher-order retry."""
    return (
        FreezePartialAssignment
        and NextCandidateDiversityLevel >= FinalCandidateDiversityLevel
    )

def ShouldFreezePartialAssignmentForExactCut(
    Classification: str,
    SelectedSignalCount: int,
    SignalCount: int,
    HasFreshAffectedSignals: bool,
) -> bool:
    """Keep pair-coverage diversification confined to its reported endpoints."""
    if Classification == "portal-coverage-pair-conflict":
        return False
    return (
        SelectedSignalCount > 0
        and (
            not HasFreshAffectedSignals
            or SelectedSignalCount * 10 >= SignalCount * 7
        )
    )

def ShouldRegenerateNewExactConflictSignals(
    Classification: str,
    SignalCount: int,
    PriorSignals: frozenset[str],
    ConflictSignals: frozenset[str],
) -> bool:
    """Allow one same-level exact repair when it targets a genuinely new cut."""
    return (
        Classification
        in {
            "multi-pair-placement-conflict",
            "relocated-larger-matching-failure",
            "relocated-multi-pair-conflict",
            "relocated-higher-order-conflict",
            "relocated-pairwise-incompatibility",
        }
        and 33 <= SignalCount <= 64
        and bool(ConflictSignals - PriorSignals)
    )

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

def BuildForeignElectricalExclusionsBySignal(
    ProtectedNodesBySignal: dict[str, frozenset[Position3]],
    Technology: Any,
    *,
    DeferredPairwiseSignals: frozenset[str] = frozenset(),
) -> dict[str, frozenset[Position3]]:
    """Project foreign electrical keepouts without quadratic recomputation.

    Electrical exclusion distributes over a union of protected nodes. Build
    each signal's contribution once, count which signals contribute each
    cell, and subtract only the active signal's contribution from that count.
    This preserves shared exclusions while reducing whole-design preparation
    from one nearly-global expansion per signal to one local expansion per
    signal.
    """
    ExclusionsBySignal = {
        Signal: frozenset(Technology.BuildElectricalExclusions(set(Nodes)))
        for Signal, Nodes in ProtectedNodesBySignal.items()
    }
    # During physical port-first planning, another selected port is not an
    # immutable obstacle: it is a peer assignment variable.  Its complete
    # candidate claims participate in exact pair compatibility below.  Keep
    # static/frozen signals unary, but defer dynamic port-to-port exclusions
    # so an unchanged port owns a plan-invariant request domain.
    UnaryContributors = frozenset(ProtectedNodesBySignal).difference(
        DeferredPairwiseSignals
    )
    ExclusionSignalCounts: Counter[Position3] = Counter()
    for Signal, Exclusions in ExclusionsBySignal.items():
        if Signal not in UnaryContributors:
            continue
        ExclusionSignalCounts.update(Exclusions)
    return {
        Signal: frozenset(
            Position
            for Position, SignalCount in ExclusionSignalCounts.items()
            if SignalCount > int(
                Signal in UnaryContributors
                and Position in ExclusionsBySignal[Signal]
            )
            and Position not in ProtectedNodesBySignal[Signal]
        )
        for Signal in ProtectedNodesBySignal
    }

def SelectAuthoritativeRouteRequestGuide(
    Terminals: tuple[Position2, ...],
    Axis: str,
    Lane: int,
    *,
    ReservedPhysicalGuide: frozenset[Position2] | None = None,
    AllowPhysicalCorridorVariant: bool = False,
) -> frozenset[Position2]:
    """Select the geometry consumed by native candidate generation.

    A physical assembly corridor is an immutable routing contract, not a
    scoring hint.  Flat nets still synthesize their ordinary Manhattan lane;
    component ports consume the keepout-aware guide selected before local
    compilation.
    """
    if ReservedPhysicalGuide is not None:
        if not ReservedPhysicalGuide:
            raise ValueError("reserved physical route guide is empty")
        if AllowPhysicalCorridorVariant:
            return frozenset((
                *ReservedPhysicalGuide,
                *_BuildGuide(Terminals, Axis, Lane),
            ))
        return ReservedPhysicalGuide
    return _BuildGuide(Terminals, Axis, Lane)

def BuildCandidateRequestGeometryIdentity(
    SourcePortalId: str,
    TargetPortalIds: tuple[str, ...],
    Guide: frozenset[Position2],
    Layer: int,
    Axis: str,
    Lane: int,
    *,
    ImmutablePhysicalGuide: bool,
) -> tuple[object, ...]:
    """Identify native request geometry without flat-only aliases."""
    Identity: tuple[object, ...] = (
        SourcePortalId,
        TargetPortalIds,
        tuple(sorted(Guide)),
        int(Layer),
    )
    if not ImmutablePhysicalGuide:
        Identity = (*Identity, Axis, int(Lane))
    return Identity

def BuildPhysicalCandidateRequestShapeDependencyIdentity(
    Descriptor: CandidateRequestShapeDescriptor,
) -> tuple[object, ...]:
    """Identify the exact immutable request payload, not search ordering.

    Axis, lane, variant rank, and priority choose which equivalent request is
    visited first.  Once a physical guide is frozen, they do not alter the
    portal nodes, guide columns, routing layer, or expansion sent to native
    route-tree generation and therefore cannot invalidate a complete domain.
    """
    return (
        tuple(Descriptor.SourcePortal.Path),
        tuple(
            tuple(Portal.Path) for Portal in Descriptor.TargetPortals
        ),
        tuple(sorted(Descriptor.Guide)),
        int(Descriptor.Layer),
        int(Descriptor.RoutingY),
        int(Descriptor.GuideExpansion),
    )

def InterleavePhysicalPortSeamsByEgressClass(
    Seams: Iterable[Any],
    *,
    BaseKey: Callable[[Any], tuple[object, ...]],
) -> tuple[Any, ...]:
    """Preserve cost order while alternating distinct outward banks."""
    Ordered = tuple(sorted(Seams, key=BaseKey))
    Buckets: dict[tuple[int, int, int], list[Any]] = {}
    for Seam in Ordered:
        Path = tuple(Seam.GlobalPath)
        Direction = (0, 0, 0)
        if len(Path) >= 2:
            Delta = tuple(
                int(Path[1][Index]) - int(Path[0][Index])
                for Index in range(3)
            )
            Direction = tuple(
                0 if Value == 0 else 1 if Value > 0 else -1
                for Value in Delta
            )
        Buckets.setdefault(Direction, []).append(Seam)
    Classes = tuple(Buckets)
    return tuple(
        Buckets[Class][Index]
        for Index in range(
            max((len(Values) for Values in Buckets.values()), default=0)
        )
        for Class in Classes
        if Index < len(Buckets[Class])
    )

def PhysicalPortPathsOwnExclusiveSeam(
    LocalPath: Iterable[Position3],
    GlobalPath: Iterable[Position3],
) -> bool:
    """Require local/global ownership to meet at exactly one attachment."""
    Local = tuple(LocalPath)
    Global = tuple(GlobalPath)
    return bool(
        Local
        and Global
        and Local[-1] == Global[0]
        and frozenset(Local).intersection(Global) == {Local[-1]}
    )

def BuildSeamOnlyPhysicalComponentPortReservation(
    Port: PhysicalComponentPortReservation,
    ResourceGraph: Any,
) -> PhysicalComponentPortReservation:
    """Remove internal terminal witnesses from one physical port contract."""
    CertifiedLocalContractFingerprint = str(getattr(
        Port,
        "CertifiedLocalContractFingerprint",
        "",
    )) or BuildPhysicalPortLocalContractFingerprint(Port)
    CertifiedSeamContractFingerprint = str(getattr(
        Port,
        "CertifiedSeamContractFingerprint",
        "",
    )) or BuildPhysicalPortSeamContractFingerprint(Port)
    CertifiedSupportReservationFingerprint = str(getattr(
        Port,
        "CertifiedSupportReservationFingerprint",
        "",
    )) or Port.ReservationFingerprint
    del ResourceGraph
    LocalClaims = Port.LocalClaims
    GlobalClaims = Port.GlobalClaims
    Claims = Port.Claims
    return replace(
        Port,
        OwnedCandidateFingerprints=(),
        OwnedAccessCandidates=(),
        Claims=Claims,
        LocalClaims=LocalClaims,
        GlobalClaims=GlobalClaims,
        CertifiedLocalContractFingerprint=(
            CertifiedLocalContractFingerprint
        ),
        CertifiedSeamContractFingerprint=(
            CertifiedSeamContractFingerprint
        ),
        CertifiedSupportReservationFingerprint=(
            CertifiedSupportReservationFingerprint
        ),
        ReservationFingerprint=BuildStableFingerprint((
            "physical-port-seam-only-v3",
            BuildPhysicalPortSeamContractFingerprint(Port),
            CertifiedSupportReservationFingerprint,
            tuple(
                tuple(
                    int(Position[Index])
                    - int(Port.FabricAttachment[Index])
                    for Index in range(3)
                )
                for Position in Port.GlobalPath
            ),
        )),
    )

@dataclass(frozen=True)
class InvariantRouteRequestNodePayload:
    """Sorted native node payload shared by one exact portal tuple."""

    RequiredNodeSet: frozenset[Position3]
    RequiredNodes: tuple[Position3, ...]
    BlockedNodes: tuple[Position3, ...]

def BuildInvariantRouteRequestNodePayload(
    FixedRequiredNodes: frozenset[Position3],
    PortalNodes: frozenset[Position3],
    SortedBlockedNodeBase: tuple[Position3, ...],
) -> InvariantRouteRequestNodePayload:
    """Build the exact sorted node lists invariant across guide variants."""
    RequiredNodeSet = FixedRequiredNodes | PortalNodes
    return InvariantRouteRequestNodePayload(
        RequiredNodeSet=RequiredNodeSet,
        RequiredNodes=tuple(sorted(RequiredNodeSet)),
        BlockedNodes=tuple(
            Position
            for Position in SortedBlockedNodeBase
            if Position not in RequiredNodeSet
        ),
    )

def PhysicalRouteRequestFactorHasNecessaryConnectivity(
    Adjacency: Mapping[Position3, Iterable[Position3]],
    RegionNodes: frozenset[Position3],
    RequiredNodes: frozenset[Position3],
    BlockedNodes: frozenset[Position3],
    AllowedColumns: frozenset[Position2],
) -> bool:
    """Prove the required terminals share one allowed exterior component.

    A false result is an exact necessary-condition failure and may prune the
    access/guide factor before native routing.  Missing graph evidence returns
    true so this prescreen can never manufacture an unsatisfiability proof.
    """
    if not RequiredNodes or not RequiredNodes <= RegionNodes:
        return True
    TraversableRequired = RequiredNodes - BlockedNodes
    if TraversableRequired != RequiredNodes:
        return False
    Start = min(RequiredNodes)
    Pending = [Start]
    Seen = {Start}
    while Pending:
        Current = Pending.pop()
        for Neighbor in Adjacency.get(Current, ()):
            if Neighbor in Seen or Neighbor in BlockedNodes:
                continue
            if (
                Neighbor not in RequiredNodes
                and (Neighbor[0], Neighbor[2]) not in AllowedColumns
            ):
                continue
            Seen.add(Neighbor)
            Pending.append(Neighbor)
    return RequiredNodes <= Seen

def PartitionLocalClaimSeedComponents(
    Profile: Any,
    ResourceGraph: Any,
) -> tuple[tuple[Position3, ...], tuple[Position3, ...]]:
    """Separate the rooted pre-route tree from detached fragments to join."""
    Claims = tuple(
        Profile.Seed.LocalClaims
        if Profile.Seed is not None
        else ()
    )
    Nodes = frozenset(
        Position
        for Claim in Claims
        for Position in Claim.Nodes
    )
    if not Nodes:
        return (), ()
    Graph = _BuildCandidateGraph(Nodes, ResourceGraph)
    Remaining = set(Nodes)
    Components = []
    while Remaining:
        Start = min(Remaining)
        Component = _FindComponentNodes(Graph, Start)
        Components.append(frozenset(Component))
        Remaining.difference_update(Component)
    RootAccess = frozenset((
        Profile.Root,
        *Profile.SourceAccessPath,
    ))
    RootComponent = next(
        (
            Component
            for Component in Components
            if Component & RootAccess
        ),
        frozenset(),
    )
    DetachedAnchors = []
    for Component in Components:
        if Component == RootComponent:
            continue
        BoundaryNodes = {
            Position
            for Claim in Claims
            for Position in Claim.BoundaryNodes
            if Position in Component
        }
        Candidates = BoundaryNodes or set(Component)
        DetachedAnchors.append(min(
            Candidates,
            key=lambda Position: (
                abs(Position[0] - Profile.Root[0])
                + abs(Position[1] - Profile.Root[1])
                + abs(Position[2] - Profile.Root[2]),
                Position,
            ),
        ))
    return (
        tuple(sorted(RootComponent)),
        tuple(sorted(DetachedAnchors)),
    )

def BuildDetachedLocalClaimObstacleNodes(
    Profile: Any,
    RootSeedNodes: Iterable[Position3],
    ResourceGraph: Any,
) -> frozenset[Position3]:
    """Keep new wire out of detached pre-route support and air resources."""
    RootNodes = frozenset(RootSeedNodes)
    Claims = tuple(
        Profile.Seed.LocalClaims
        if Profile.Seed is not None
        else ()
    )
    DetachedClaims = tuple(
        Claim
        for Claim in Claims
        if set(Claim.Nodes) - RootNodes
    )
    SeedNodes = frozenset(
        Position
        for Claim in Claims
        for Position in Claim.Nodes
    )
    AnchorIngressNodes = frozenset(
        Neighbor
        for Claim in DetachedClaims
        for Anchor in getattr(Claim, "BoundaryNodes", ())
        for Neighbor in (
            DefaultRedstoneRoutingTechnology.NeighborPositions(
                Anchor
            )
        )
        if (
            ResourceGraph.BuildPrimitive(Anchor, Neighbor)
            is not None
            and not FindSelfClaimConflicts({
                "detached-ingress": (
                    ResourceGraph.BuildRouteClaims(
                        set(Claim.Nodes) | {Neighbor}
                    )
                ),
            })
        )
    )
    return frozenset(
        Position
        for Claim in DetachedClaims
        for Position in (
            Claim.Claims.ElectricalCells
            | Claim.Claims.SupportCells
            | Claim.Claims.RequiredAirCells
        )
        if (
            Position not in SeedNodes
            and Position not in AnchorIngressNodes
        )
    )

def BuildInvariantRouteRequestGuidePayload(
    Guide: frozenset[Position2],
    GuideExpansion: int,
) -> tuple[tuple[Position2, ...], tuple[Position2, ...]]:
    """Build sorted guide columns shared by identical request geometry."""
    CandidateColumns = {
        (GuideX + DeltaX, GuideZ + DeltaZ)
        for GuideX, GuideZ in Guide
        for DeltaX in range(
            -GuideExpansion,
            GuideExpansion + 1,
        )
        for DeltaZ in range(
            -GuideExpansion,
            GuideExpansion + 1,
        )
        if abs(DeltaX) + abs(DeltaZ) <= GuideExpansion
    }
    return (
        tuple(sorted(CandidateColumns)),
        tuple(sorted(Guide)),
    )

def _BuildTargetPortalBranches(
    TargetPortals: tuple[PinAccessPortal, ...],
    TargetAccessPaths: tuple[tuple[Position3, ...], ...] | None = None,
) -> list[list[Position3]]:
    """Orient complete target escapes from their outer endpoint inward.

    A portal and its fixed pin-access path can share an initial segment and
    then split.  Concatenating their reversed position lists in that case
    creates a walk which goes through the terminal and jumps to the other
    branch.  The Rust tree kernel correctly rejects that non-edge, even
    though the immutable union is physically connected.  When the two paths
    share geometry, retain one deterministic simple chain from the portal
    ingress to the terminal; the remaining fixed branch is still carried in
    the required-node payload and restored during materialization.
    """
    if (
        TargetAccessPaths is not None
        and len(TargetAccessPaths) != len(TargetPortals)
    ):
        raise ValueError("target portal/access branch count mismatch")

    def BuildBranch(
        Portal: PinAccessPortal,
        TargetAccessPath: tuple[Position3, ...],
    ) -> list[Position3]:
        PortalPath = tuple(Portal.Path)
        if not PortalPath:
            return list(reversed(TargetAccessPath))
        if not TargetAccessPath:
            return list(reversed(PortalPath))

        SharedNodes = frozenset(PortalPath) & frozenset(TargetAccessPath)
        if not SharedNodes:
            # A generic portal can begin immediately beyond the outer access
            # landing.  In that non-overlapping form, the established joined
            # chain remains the only available representation.
            return list(dict.fromkeys((
                *reversed(PortalPath),
                *reversed(TargetAccessPath),
            )))

        Adjacency: dict[Position3, set[Position3]] = defaultdict(set)
        for Path in (PortalPath, TargetAccessPath):
            for First, Second in zip(Path, Path[1:]):
                Adjacency[First].add(Second)
                Adjacency[Second].add(First)
        Start = PortalPath[-1]
        Terminal = TargetAccessPath[0]
        Pending = deque((Start,))
        Parent: dict[Position3, Position3 | None] = {Start: None}
        while Pending:
            Current = Pending.popleft()
            if Current == Terminal:
                break
            for Next in sorted(Adjacency.get(Current, ())):
                if Next in Parent:
                    continue
                Parent[Next] = Current
                Pending.append(Next)
        if Terminal not in Parent:
            # Preserve the prior conservative representation if a custom
            # portal reports shared cells but not a connected path graph.
            return list(dict.fromkeys((
                *reversed(PortalPath),
                *reversed(TargetAccessPath),
            )))
        Branch = []
        Cursor: Position3 | None = Terminal
        while Cursor is not None:
            Branch.append(Cursor)
            Cursor = Parent[Cursor]
        return list(reversed(Branch))

    return [
        BuildBranch(
            Portal,
            (
                TargetAccessPaths[Index]
                if TargetAccessPaths is not None
                else ()
            ),
        )
        for Index, Portal in enumerate(TargetPortals)
    ]

"""Boundary-portal and negotiated escape planning."""

from __future__ import annotations

from ....Contracts.Core import Position3

from ....Contracts.Results import RoutingResources

from ....Contracts.Failures import RoutingFailure

from ....Contracts.Failures import RoutingFailureReason

from ....Contracts.Failures import RoutingStageError

from ....Interfaces.PhysicalClaims import ClaimConflictPositions

from ....Execution.Reliability import BuildStableFingerprint

from ....Resources.ResourceGraph import FindSelfClaimConflicts

from ....Resources.ResourceGraph import LocalRouteClaim

from ....Resources.ResourceGraph import NetRouteCandidate

from ....Resources.ResourceGraph import PinAccessPortal

from ....Resources.ResourceGraph import PortalReservation

from ....Resources.ResourceGraph import RoutingResourceClaims

from ....Resources.ResourceGraph import RoutingResourceGraph

from ....Resources.ResourceGraph import ValidateLocalRouteClaims

from collections import Counter

from collections import defaultdict

from dataclasses import dataclass

from typing import Any

from typing import Callable

from typing import Iterable

from typing import Mapping

from ..Assignment.AssignmentState import _ClaimsConflict

@dataclass(frozen=True)
class CandidateDomainPairCut:
    """One deterministic incompatible pair in the current bounded domains."""

    Signals: tuple[str, str]
    ConflictPositions: frozenset[Position3]
    CompletedSignalPairs: int
    CandidatePairChecks: int
    TotalSignalPairs: int

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signals": list(self.Signals),
            "ConflictPositions": [
                list(Position)
                for Position in sorted(self.ConflictPositions)
            ],
            "CompletedSignalPairs": self.CompletedSignalPairs,
            "CandidatePairChecks": self.CandidatePairChecks,
            "TotalSignalPairs": self.TotalSignalPairs,
        }

def FindFirstUnavoidableCandidateDomainPairCut(
    CandidatesBySignal: dict[str, list[NetRouteCandidate]],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    CutValidator: Callable[[CandidateDomainPairCut], bool] | None = None,
    MaximumCandidatePairChecks: int | None = None,
    OrderedSignals: tuple[str, ...] | list[str] | None = None,
    PrioritySignals: frozenset[str] = frozenset(),
) -> CandidateDomainPairCut | None:
    """Find the first pair whose complete bounded route domains conflict."""
    if (
        MaximumCandidatePairChecks is not None
        and MaximumCandidatePairChecks < 1
    ):
        raise ValueError("MaximumCandidatePairChecks must be positive")
    AvailableSignals = frozenset(
        Signal
        for Signal, Candidates in CandidatesBySignal.items()
        if Candidates
    )
    Signals = (
        tuple(
            Signal
            for Signal in OrderedSignals
            if Signal in AvailableSignals
        )
        if OrderedSignals is not None
        else tuple(sorted(AvailableSignals))
    )
    MissingSignals = tuple(sorted(
        AvailableSignals - frozenset(Signals)
    ))
    Signals = (*Signals, *MissingSignals)
    TotalSignalPairs = len(Signals) * max(0, len(Signals) - 1) // 2
    SignalPairs = [
        (FirstSignal, SecondSignal)
        for FirstIndex, FirstSignal in enumerate(Signals)
        for SecondSignal in Signals[FirstIndex + 1:]
    ]
    SignalRank = {
        Signal: Index
        for Index, Signal in enumerate(Signals)
    }
    SignalPairs.sort(
        key=lambda Pair: (
            0
            if (
                Pair[0] in PrioritySignals
                and Pair[1] in PrioritySignals
            )
            else 1,
            SignalRank[Pair[0]],
            SignalRank[Pair[1]],
        )
    )
    CompletedSignalPairs = 0
    CandidatePairChecks = 0
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "start",
            "CompletedSignalPairs": CompletedSignalPairs,
            "CandidatePairChecks": CandidatePairChecks,
            "TotalSignalPairs": TotalSignalPairs,
        })
    for FirstSignal, SecondSignal in SignalPairs:
        ConflictPositions: set[Position3] = set()
        Unavoidable = True
        for First in CandidatesBySignal[FirstSignal]:
            for Second in CandidatesBySignal[SecondSignal]:
                if (
                    MaximumCandidatePairChecks is not None
                    and CandidatePairChecks
                    >= MaximumCandidatePairChecks
                ):
                    if WorkCheck is not None:
                        WorkCheck({
                            "Phase": "limit",
                            "CompletedSignalPairs": (
                                CompletedSignalPairs
                            ),
                            "CandidatePairChecks": (
                                CandidatePairChecks
                            ),
                            "MaximumCandidatePairChecks": (
                                MaximumCandidatePairChecks
                            ),
                            "TotalSignalPairs": TotalSignalPairs,
                        })
                    return None
                CandidatePairChecks += 1
                if (
                    WorkCheck is not None
                    and CandidatePairChecks % 64 == 0
                ):
                    WorkCheck({
                        "Phase": "candidate-pairs",
                        "CompletedSignalPairs": CompletedSignalPairs,
                        "CandidatePairChecks": CandidatePairChecks,
                        "TotalSignalPairs": TotalSignalPairs,
                    })
                if not _ClaimsConflict(
                    FirstSignal,
                    First.Claims,
                    SecondSignal,
                    Second.Claims,
                ):
                    Unavoidable = False
                    break
                ConflictPositions.update(
                    First.Claims.WireCells & Second.Claims.WireCells
                )
                ConflictPositions.update(
                    ClaimConflictPositions(
                        First.Claims,
                        Second.Claims,
                    )
                )
            if not Unavoidable:
                break
        CompletedSignalPairs += 1
        if Unavoidable:
            Cut = CandidateDomainPairCut(
                Signals=(FirstSignal, SecondSignal),
                ConflictPositions=frozenset(ConflictPositions),
                CompletedSignalPairs=CompletedSignalPairs,
                CandidatePairChecks=CandidatePairChecks,
                TotalSignalPairs=TotalSignalPairs,
            )
            if CutValidator is not None and not CutValidator(Cut):
                if WorkCheck is not None:
                    WorkCheck({
                        "Phase": "rejected-cut",
                        **Cut.ToDictionary(),
                    })
                continue
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "cut",
                    **Cut.ToDictionary(),
                })
            return Cut
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "complete",
            "CompletedSignalPairs": CompletedSignalPairs,
            "CandidatePairChecks": CandidatePairChecks,
            "TotalSignalPairs": TotalSignalPairs,
        })
    return None

def BuildCandidateMandatoryAccessClaims(
    Signal: str,
    Candidates: list[NetRouteCandidate],
    Profile: Any,
    PortalById: dict[str, PinAccessPortal],
    ResourceGraph: Any,
) -> tuple[RoutingResourceClaims, ...]:
    """Build distinct fixed access/portal claims represented by candidates."""
    AccessNodes = {
        *Profile.SourceAccessPath,
        *(
            Position
            for Path in Profile.TargetAccessPaths.values()
            for Position in Path
        ),
    }
    Claims = []
    SeenClaims = set()
    for Candidate in Candidates:
        CandidatePortals = [
            PortalById.get(Candidate.SourcePortalId),
            *(
                PortalById.get(PortalId)
                for PortalId in Candidate.TargetPortalIds.values()
            ),
        ]
        if any(Portal is None for Portal in CandidatePortals):
            continue
        MandatoryClaims = ResourceGraph.BuildRouteClaims({
            *AccessNodes,
            *(
                Position
                for Portal in CandidatePortals
                if Portal is not None
                for Position in Portal.Path
            ),
        })
        Signature = MandatoryClaims.ResourceIds
        if Signature in SeenClaims:
            continue
        SeenClaims.add(Signature)
        Claims.append(MandatoryClaims)
    return tuple(Claims)

@dataclass(frozen=True)
class LocalClaimReleaseSelection:
    """Deterministic pre-routing ownership choice for packed local trees."""

    ReleasedSignals: frozenset[str]
    SelectorScore: tuple[object, ...]
    ConflictingLocalSignals: frozenset[str]
    CandidateOnlySignals: frozenset[str]
    SearchExpansionCount: int
    SearchExhausted: bool

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ReleasedSignals": sorted(self.ReleasedSignals),
            "SelectorScore": list(self.SelectorScore),
            "ConflictingLocalSignals": sorted(self.ConflictingLocalSignals),
            "CandidateOnlySignals": sorted(self.CandidateOnlySignals),
            "SearchExpansionCount": self.SearchExpansionCount,
            "SearchExhausted": self.SearchExhausted,
        }

@dataclass(frozen=True)
class PreRouteLocalClaimChoice:
    """One complete local tree offered beside ordinary route candidates.

    The choice remains under its real logical signal.  This is important: the
    native capacity solver may merge same-signal ownership, but must reject
    collisions with every other signal.  A synthetic auxiliary signal would
    incorrectly turn that same-signal exception into a foreign conflict.
    """

    Signal: str
    ChoiceId: str
    Claim: LocalRouteClaim
    ClaimFingerprint: str
    MaterialCost: int

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "ChoiceId": self.ChoiceId,
            "ClaimFingerprint": self.ClaimFingerprint,
            "NodeCount": len(self.Claim.Nodes),
            "ResourceCount": len(self.Claim.Claims.ResourceIds),
            "ConnectedTargetCount": len(self.Claim.ConnectedTargets),
            "RepeaterReservationCount": len(
                self.Claim.RepeaterReservations
            ),
            "MaterialCost": self.MaterialCost,
        }

def BuildPreRouteLocalClaimChoices(
    Claims: Iterable[LocalRouteClaim],
    Profiles: Mapping[str, Any],
    ResourceGraph: RoutingResourceGraph,
) -> tuple[
    dict[str, tuple[PreRouteLocalClaimChoice, ...]],
    tuple[dict[str, object], ...],
]:
    """Validate complete local trees as same-signal pre-route values.

    A placement-local tree is eligible only when it roots at, and reaches all
    terminals of, its current logical routing profile.  Invalid or partial
    source fragments are retained as diagnostics but never become a hidden
    base claim or a synthetic candidate.  The ordinary portal domain remains
    available for every signal, so omitting an unsupported optional tree does
    not make the placement unsound.
    """
    ChoicesBySignal: dict[str, list[PreRouteLocalClaimChoice]] = {}
    Rejections: list[dict[str, object]] = []
    SeenChoiceIds: set[str] = set()
    for Claim in sorted(
        Claims,
        key=lambda Value: (
            str(Value.Signal),
            int(Value.ClusterId),
            tuple(sorted(Value.Nodes)),
        ),
    ):
        Signal = str(Claim.Signal)
        Profile = Profiles.get(Signal)
        Rejection: str | None = None
        if Profile is None:
            Rejection = "signal-has-no-routing-profile"
        elif tuple(Claim.Root) != tuple(Profile.Root):
            Rejection = "local-root-does-not-match-profile-root"
        elif not set(Profile.Targets).issubset(Claim.ConnectedTargets):
            Rejection = "local-tree-does-not-cover-profile-targets"
        elif Claim.RepeaterReservations:
            # The first immutable local-choice increment has no separate
            # repeater reservation encoding.  Do not silently discard such
            # reservations: retain the ordinary portal alternative instead.
            Rejection = "local-tree-repeater-reservations-not-supported"
        else:
            try:
                ValidateLocalRouteClaims(ResourceGraph, (Claim,))
            except ValueError as Error:
                Rejection = f"invalid-local-tree:{Error}"
        if Rejection is not None:
            Rejections.append({
                "Signal": Signal,
                "ClusterId": int(Claim.ClusterId),
                "Reason": Rejection,
            })
            continue
        ClaimFingerprint = BuildStableFingerprint({
            "Signal": Signal,
            "Root": tuple(Claim.Root),
            "Targets": tuple(sorted(Claim.ConnectedTargets)),
            "Nodes": tuple(sorted(Claim.Nodes)),
            "Edges": tuple(sorted(Claim.Edges)),
            "Claims": {
                "Wire": tuple(sorted(Claim.Claims.WireCells)),
                "Support": tuple(sorted(Claim.Claims.SupportCells)),
                "Air": tuple(sorted(Claim.Claims.RequiredAirCells)),
                "Electrical": tuple(sorted(Claim.Claims.ElectricalCells)),
            },
        })
        ChoiceId = f"{Signal}:DerivedLocal:{ClaimFingerprint[:16]}"
        if ChoiceId in SeenChoiceIds:
            continue
        SeenChoiceIds.add(ChoiceId)
        ChoicesBySignal.setdefault(Signal, []).append(
            PreRouteLocalClaimChoice(
                Signal=Signal,
                ChoiceId=ChoiceId,
                Claim=Claim,
                ClaimFingerprint=ClaimFingerprint,
                MaterialCost=len(Claim.Claims.ResourceIds),
            )
        )
    return (
        {
            Signal: tuple(sorted(
                Values,
                key=lambda Value: (
                    Value.MaterialCost,
                    Value.ClaimFingerprint,
                    Value.ChoiceId,
                ),
            ))
            for Signal, Values in sorted(ChoicesBySignal.items())
        },
        tuple(Rejections),
    )

def SelectAccessAwareLocalClaimReleases(
    MandatoryClaimsBySignal: dict[str, tuple[RoutingResourceClaims, ...]],
    LocalClaims: tuple[LocalRouteClaim, ...],
    MaximumExpansions: int = 8_192,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> LocalClaimReleaseSelection:
    """Choose the least-cost local claims blocking every access alternative.

    A request is viable once every local claim it conflicts with has been
    released.  The bounded branch-and-bound search selects one viable request
    per constrained signal and minimizes the union of released claim signals.
    """
    if MaximumExpansions < 1:
        raise ValueError("MaximumExpansions must be positive")
    ClaimsBySignal: dict[str, tuple[LocalRouteClaim, ...]] = {
        Signal: tuple(sorted(
            (
                Claim for Claim in LocalClaims if Claim.Signal == Signal
            ),
            key=lambda Claim: (
                Claim.ClusterId,
                tuple(sorted(Claim.Nodes)),
            ),
        ))
        for Signal in sorted({Claim.Signal for Claim in LocalClaims})
    }
    ReleaseCost = {
        Signal: sum(len(Claim.Nodes) for Claim in Values)
        for Signal, Values in ClaimsBySignal.items()
    }
    OptionsBySignal: dict[str, tuple[frozenset[str], ...]] = {}
    CandidateOnlySignals: set[str] = set()
    ConflictingLocalSignals: set[str] = set()
    for Signal, MandatoryValues in sorted(MandatoryClaimsBySignal.items()):
        Options = {
            frozenset(
                LocalSignal
                for LocalSignal, Claims in ClaimsBySignal.items()
                if LocalSignal != Signal
                if any(
                    _ClaimsConflict(Signal, Mandatory, LocalSignal, Claim.Claims)
                    for Claim in Claims
                )
            )
            for Mandatory in MandatoryValues
        }
        if not Options or frozenset() in Options:
            CandidateOnlySignals.add(Signal)
            continue
        OrderedOptions = tuple(sorted(
            Options,
            key=lambda Value: (
                len(Value),
                sum(ReleaseCost[LocalSignal] for LocalSignal in Value),
                tuple(sorted(Value)),
            ),
        ))
        OptionsBySignal[Signal] = OrderedOptions
        for Option in OrderedOptions:
            ConflictingLocalSignals.update(Option)
    if not OptionsBySignal:
        return LocalClaimReleaseSelection(
            ReleasedSignals=frozenset(),
            SelectorScore=(0, 0, ()),
            ConflictingLocalSignals=frozenset(),
            CandidateOnlySignals=frozenset(CandidateOnlySignals),
            SearchExpansionCount=0,
            SearchExhausted=False,
        )

    Best: tuple[tuple[object, ...], frozenset[str]] | None = None
    ExpansionCount = 0
    SearchExhausted = False

    def Score(Released: frozenset[str]) -> tuple[object, ...]:
        return (
            len(Released),
            sum(ReleaseCost[Signal] for Signal in Released),
            tuple(sorted(Released)),
        )

    def Search(Remaining: tuple[str, ...], Released: frozenset[str]) -> None:
        nonlocal Best, ExpansionCount, SearchExhausted
        if not Remaining:
            CandidateScore = Score(Released)
            if Best is None or CandidateScore < Best[0]:
                Best = (CandidateScore, Released)
            return
        if Best is not None and Score(Released) >= Best[0]:
            return
        Pending = tuple(
            Signal for Signal in Remaining
            if not any(Option <= Released for Option in OptionsBySignal[Signal])
        )
        if not Pending:
            Search((), Released)
            return
        Ranked = []
        for Signal in Pending:
            Viable = tuple(
                Option for Option in OptionsBySignal[Signal]
                if not Option <= Released
            )
            if not Viable:
                continue
            Ranked.append((len(Viable), Signal, Viable))
        if not Ranked:
            return
        _Count, Signal, Options = min(Ranked, key=lambda Value: (
            Value[0], Value[1], tuple(tuple(sorted(Option)) for Option in Value[2]),
        ))
        NextRemaining = tuple(Value for Value in Pending if Value != Signal)
        for Option in Options:
            ExpansionCount += 1
            if WorkCheck is not None and ExpansionCount % 64 == 0:
                WorkCheck({
                    "Phase": "local-claim-release-selection",
                    "ExpansionCount": ExpansionCount,
                    "ConstraintCount": len(OptionsBySignal),
                })
            if ExpansionCount > MaximumExpansions:
                SearchExhausted = True
                return
            Search(NextRemaining, frozenset((*Released, *Option)))
            if SearchExhausted:
                return

    Search(tuple(sorted(OptionsBySignal)), frozenset())
    if Best is None or SearchExhausted:
        return LocalClaimReleaseSelection(
            ReleasedSignals=frozenset(),
            SelectorScore=(0, 0, ()),
            ConflictingLocalSignals=frozenset(ConflictingLocalSignals),
            CandidateOnlySignals=frozenset(CandidateOnlySignals),
            SearchExpansionCount=ExpansionCount,
            SearchExhausted=SearchExhausted,
        )
    return LocalClaimReleaseSelection(
        ReleasedSignals=Best[1],
        SelectorScore=Best[0],
        ConflictingLocalSignals=frozenset(ConflictingLocalSignals),
        CandidateOnlySignals=frozenset(CandidateOnlySignals),
        SearchExpansionCount=ExpansionCount,
        SearchExhausted=False,
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
                        Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
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
                            "ConflictGraph": {
                                "Classification": "saturated-boundary-cut",
                                "ConflictSignals": list(Affected),
                                "RelocationSignals": list(Affected),
                            },
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

def ReserveNegotiatedBoundaryEscapes(
    Portals: dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]],
    Profiles: dict[str, Any],
    Resources: RoutingResources,
    ReservationVariant: int = 0,
    MaximumExpansions: int = 50_000,
    MaximumDiagonalVariants: int | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ReservationPurpose: str = "boundary-portal",
    FailureStage: str = "PortalReservation",
) -> tuple[
    dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]],
    tuple[PortalReservation, ...],
]:
    """Match each net's terminals to one claim-compatible routing layer.

    Reserving every terminal on every layer over-constrains boundary capacity,
    while selecting each terminal independently can leave a net with no common
    detailed-routing layer.  Treat a net-wide layer/portal tuple as one domain
    value and match those values across signals with capacity-one claims.
    """
    if MaximumExpansions < 1:
        raise ValueError("MaximumExpansions must be positive")
    if MaximumDiagonalVariants is not None and MaximumDiagonalVariants < 1:
        raise ValueError("MaximumDiagonalVariants must be positive when set")
    KeysBySignal: dict[str, list[tuple[str, Position3, int]]] = defaultdict(list)
    for Key, Values in Portals.items():
        if Values:
            KeysBySignal[Key[0]].append(Key)
    TerminalsBySignal = {
        Signal: tuple(sorted({Key[1] for Key in Keys}))
        for Signal, Keys in KeysBySignal.items()
    }
    Domains: dict[
        str,
        list[tuple[
            int,
            int,
            tuple[tuple[Position3, PinAccessPortal], ...],
            RoutingResourceClaims,
        ]],
    ] = {}
    WorkUnitCount = 0

    def CheckWork(Phase: str, Signal: str | None = None) -> None:
        """Keep exact portal matching inside its caller's routing deadline."""
        nonlocal WorkUnitCount
        WorkUnitCount += 1
        if WorkCheck is not None and WorkUnitCount % 16 == 0:
            WorkCheck({
                "Phase": Phase,
                "Signal": Signal,
                "WorkUnitCount": WorkUnitCount,
                "DomainSignalCount": len(Domains),
            })

    def MergeClaims(
        Signal: str,
        Selection: tuple[tuple[Position3, PinAccessPortal], ...],
    ) -> RoutingResourceClaims:
        Profile = Profiles[Signal]
        MandatoryNodes = {
            Position
            for Terminal, Portal in Selection
            for Position in (
                *(
                    Profile.SourceAccessPath
                    if Terminal == Profile.Root
                    else Profile.TargetAccessPaths[Terminal]
                ),
                *Portal.Path,
            )
        }
        # Rebuild from the complete mandatory union. Unioning claims that were
        # built per stem misses cross-stem stair headroom/support aliases.
        return Resources.ResourceGraph.BuildRouteClaims(MandatoryNodes)
    for Signal in sorted(TerminalsBySignal):
        CheckWork("signal", Signal)
        Terminals = TerminalsBySignal[Signal]
        Layers = sorted({Key[2] for Key in KeysBySignal[Signal]})
        Values = []
        for Layer in Layers:
            TerminalDomains = [
                tuple(sorted(
                    Portals.get((Signal, Terminal, Layer), ()),
                    key=lambda Portal: (Portal.Cost, Portal.PortalId),
                ))
                for Terminal in Terminals
            ]
            if any(not Domain for Domain in TerminalDomains):
                continue
            VariantCount = max(len(Domain) for Domain in TerminalDomains)
            if MaximumDiagonalVariants is not None:
                VariantCount = min(VariantCount, MaximumDiagonalVariants)
            DiagonalValues = []
            for Variant in range(VariantCount):
                CheckWork("diagonal", Signal)
                Selection = tuple(
                    (
                        Terminal,
                        Domain[(Variant + TerminalIndex) % len(Domain)],
                    )
                    for TerminalIndex, (Terminal, Domain) in enumerate(
                        zip(Terminals, TerminalDomains)
                    )
                )
                Claims = MergeClaims(Signal, Selection)
                if FindSelfClaimConflicts({Signal: Claims}):
                    continue
                DiagonalValues.append((
                    sum(Portal.Cost for _Terminal, Portal in Selection),
                    Layer,
                    Selection,
                    Claims,
                ))
            if DiagonalValues:
                Values.extend(DiagonalValues)
                continue
            # A portal is legal in isolation but a net owns all of its portal
            # stems simultaneously.  Build bounded net-wide tuples and reject
            # support/headroom aliases here, before global capacity matching.
            # Run the product fallback only when the cheap diagonal set has no
            # legal tuple; this keeps the common case proportional to the old
            # reservation cost.
            SelectionBeam: list[
                tuple[
                    int,
                    tuple[tuple[Position3, PinAccessPortal], ...],
                    RoutingResourceClaims,
                ]
            ] = [
                (
                    0,
                    (),
                    Resources.ResourceGraph.BuildRouteClaims(()),
                )
            ]
            MaximumSelectionBeam = min(8, MaximumExpansions)
            for Terminal, Domain in zip(Terminals, TerminalDomains):
                NextSelections: dict[
                    tuple[str, ...],
                    tuple[
                        int,
                        tuple[tuple[Position3, PinAccessPortal], ...],
                        RoutingResourceClaims,
                    ],
                ] = {}
                for PreviousCost, PreviousSelection, _PreviousClaims in SelectionBeam:
                    for Portal in Domain:
                        CheckWork("product", Signal)
                        Selection = (*PreviousSelection, (Terminal, Portal))
                        Claims = MergeClaims(Signal, Selection)
                        if FindSelfClaimConflicts({Signal: Claims}):
                            continue
                        PortalIds = tuple(
                            Value.PortalId for _Terminal, Value in Selection
                        )
                        Candidate = (
                            PreviousCost + Portal.Cost,
                            Selection,
                            Claims,
                        )
                        Existing = NextSelections.get(PortalIds)
                        if Existing is None or Candidate[0] < Existing[0]:
                            NextSelections[PortalIds] = Candidate
                SelectionBeam = sorted(
                    NextSelections.values(),
                    key=lambda Value: (
                        Value[0],
                        tuple(
                            Portal.PortalId
                            for _Terminal, Portal in Value[1]
                        ),
                    ),
                )[:MaximumSelectionBeam]
                if not SelectionBeam:
                    break
            Values.extend(
                (Cost, Layer, Selection, Claims)
                for Cost, Selection, Claims in SelectionBeam
                if len(Selection) == len(Terminals)
            )
        if not Values:
            raise RoutingStageError(RoutingFailure(
                Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
                Stage=FailureStage,
                AffectedNets=(Signal,),
                Detail="net terminals have no common legal boundary layer",
                Diagnostics={
                    "ReservationPurpose": ReservationPurpose,
                    "ConflictGraph": {
                        "Classification": "saturated-boundary-cut",
                        "ConflictSignals": [Signal],
                        "RelocationSignals": [Signal],
                    },
                },
            ))
        Domains[Signal] = sorted(
            Values,
            key=lambda Value: (Value[0], Value[1], tuple(
                Portal.PortalId for _Terminal, Portal in Value[2]
            )),
        )

    SignalOrder = tuple(sorted(Domains, key=lambda Signal: (
        len(Domains[Signal]),
        -len(TerminalsBySignal[Signal]),
        Signal,
    )))
    Selected: dict[
        str,
        tuple[
            int,
            int,
            tuple[tuple[Position3, PinAccessPortal], ...],
            RoutingResourceClaims,
        ],
    ] = {}
    ExpansionCount = 0

    def Compatible(
        Signal: str,
        Value: tuple[
            int,
            int,
            tuple[tuple[Position3, PinAccessPortal], ...],
            RoutingResourceClaims,
        ],
    ) -> tuple[bool, set[str]]:
        Blockers: set[str] = set()
        for OtherSignal, OtherValue in Selected.items():
            if _ClaimsConflict(
                Signal,
                Value[3],
                OtherSignal,
                OtherValue[3],
            ):
                Blockers.add(OtherSignal)
        return not Blockers, Blockers

    FailedCut: set[str] = set()
    BudgetExhausted = False

    def Search(Depth: int) -> bool:
        nonlocal ExpansionCount, BudgetExhausted
        if Depth >= len(SignalOrder):
            return True
        Signal = SignalOrder[Depth]
        Values = Domains[Signal]
        Offset = ReservationVariant % len(Values)
        OrderedValues = (*Values[Offset:], *Values[:Offset])
        Cut = {Signal}
        for Value in OrderedValues:
            CheckWork("assignment", Signal)
            ExpansionCount += 1
            if ExpansionCount > MaximumExpansions:
                BudgetExhausted = True
                FailedCut.update(Cut)
                return False
            IsCompatible, Blockers = Compatible(Signal, Value)
            Cut.update(Blockers)
            if not IsCompatible:
                continue
            Selected[Signal] = Value
            ForwardFeasible = True
            for RemainingSignal in SignalOrder[Depth + 1:]:
                RemainingHasValue = False
                RemainingCut = {RemainingSignal}
                for RemainingValue in Domains[RemainingSignal]:
                    CheckWork("forward-check", RemainingSignal)
                    RemainingCompatible, RemainingBlockers = Compatible(
                        RemainingSignal,
                        RemainingValue,
                    )
                    RemainingCut.update(RemainingBlockers)
                    if RemainingCompatible:
                        RemainingHasValue = True
                        break
                if not RemainingHasValue:
                    Cut.update(RemainingCut)
                    ForwardFeasible = False
                    break
            if ForwardFeasible and Search(Depth + 1):
                return True
            Selected.pop(Signal, None)
            if BudgetExhausted:
                return False
        FailedCut.update(Cut)
        return False

    Search(0)

    if len(Selected) != len(SignalOrder):
        Affected = tuple(sorted(FailedCut or set(SignalOrder) - set(Selected)))
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
            Stage=FailureStage,
            AffectedNets=Affected,
            Detail=(
                "no capacity-one net-layer boundary matching within "
                f"{MaximumExpansions} deterministic expansions"
            ),
            Diagnostics={
                "ReservationPurpose": ReservationPurpose,
                "ExpansionCount": ExpansionCount,
                "MaximumExpansions": MaximumExpansions,
                "BudgetExhausted": BudgetExhausted,
                "MatchedSignalCount": len(Selected),
                "SignalCount": len(SignalOrder),
                "ConflictGraph": {
                    "Classification": "saturated-boundary-cut",
                    "ConflictSignals": list(Affected),
                    "RelocationSignals": list(Affected),
                },
            },
        ))

    Filtered = {Key: () for Key in Portals}
    Reservations = []
    for Signal in sorted(Selected):
        _Cost, Layer, Selection, _Claims = Selected[Signal]
        for SlotIndex, (Terminal, Portal) in enumerate(Selection):
            Key = (Signal, Terminal, Layer)
            Filtered[Key] = (Portal,)
            Reservations.append(PortalReservation(
                Signal=Signal,
                Terminal=Terminal,
                Layer=Layer,
                SlotIndex=SlotIndex,
                PortalId=Portal.PortalId,
                Claims=Portal.Claims,
                Purpose=ReservationPurpose,
                FirstSegment=Portal.Path[:2],
            ))
    return Filtered, tuple(Reservations)

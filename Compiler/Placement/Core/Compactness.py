"""Pin-aligned cluster portfolios and weighted compaction."""

from __future__ import annotations

from statistics import (
    median,
)
from typing import (
    Any,
    Callable,
)
from Compiler.Placement.Rotation import (
    RotatedCellSize,
)
from Compiler.Placement.Geometry import (
    BuildPlacedGate,
    PlacedDesign,
)
from Compiler.Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
)
from .Cache import (
    _PinAlignedPackedClusterPortfolioCache,
)
from .Clustering import (
    PcbGatesConflict,
)
from .Constraints import (
    PinAlignedPackedClusterPortfolio,
    PinAlignedPackedClusterState,
)
from .Costs import (
    PlacementCompactKey,
)


def BuildDerivedPinAlignmentOffsets(
    Technology: Any = DefaultRedstoneRoutingTechnology,
) -> tuple[tuple[int, int], ...]:
    """Derive compact pin offsets from connectivity and track geometry."""
    Origin = (0, 0, 0)
    PlanarNeighborOffsets = {
        (Position[0], Position[2])
        for Position in Technology.NeighborPositions(Origin)
        if Position[1] == Origin[1]
    }
    CardinalX = sorted({
        DeltaX for DeltaX, DeltaZ in PlanarNeighborOffsets
        if DeltaX and not DeltaZ
    })
    CardinalZ = sorted({
        DeltaZ for DeltaX, DeltaZ in PlanarNeighborOffsets
        if DeltaZ and not DeltaX
    })
    TrackLandingDistance = max(1, Technology.TrackPitch - 1)
    Offsets = {
        (0, 0),
        *PlanarNeighborOffsets,
        *((DeltaX, DeltaZ) for DeltaX in CardinalX for DeltaZ in CardinalZ),
        *((Sign * TrackLandingDistance, 0) for Sign in (-1, 1)),
        *((0, Sign * TrackLandingDistance) for Sign in (-1, 1)),
    }
    return tuple(sorted(Offsets))

def BuildPinAlignedPackedCluster(
    Names: tuple[str, ...],
    InternalByName: dict[str, Any],
    BeamWidth: int,
    CandidateIndex: int = 0,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[
    dict[str, tuple[int, int]],
    dict[str, int],
    dict[str, bool],
] | None:
    """Materialize one explicitly selected retained graph-core state.

    ``None`` continues to mean that the bounded graph beam could not form any
    legal state.  An index beyond a non-empty retained beam is a caller error,
    not a request to quietly use a row-beam placement instead.
    """
    if CandidateIndex < 0:
        raise ValueError("packed cluster candidate index cannot be negative")
    States = _BuildPinAlignedPackedClusterStates(
        Names,
        InternalByName,
        BeamWidth,
        WorkCheck=WorkCheck,
    )
    if not States:
        return None
    if CandidateIndex >= len(States):
        raise ValueError(
            "packed cluster candidate index exceeds retained state count "
            f"({CandidateIndex} >= {len(States)})"
        )
    return States[CandidateIndex].Materialize()

def BuildPinAlignedPackedClusterPortfolio(
    Names: tuple[str, ...],
    InternalByName: dict[str, Any],
    BeamWidth: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PinAlignedPackedClusterPortfolio:
    """Materialize the finite non-dominated graph-core domain up front.

    States retain their legacy ``CandidateIndex`` values.  A caller that needs
    to invoke :func:`BuildPinAlignedPackedCluster` later must use that explicit
    index, rather than enumerating a guessed contiguous range.
    """
    States = _BuildPinAlignedPackedClusterStates(
        Names,
        InternalByName,
        BeamWidth,
        WorkCheck=WorkCheck,
    )
    NonDominatedStates = tuple(
        State
        for State in States
        if not any(
            Other is not State
            and _PinAlignedPackedClusterStateDominates(Other, State)
            for Other in States
        )
    )
    return PinAlignedPackedClusterPortfolio(
        States=NonDominatedStates,
        RawCandidateCount=len(States),
    )

def CountPinAlignedPackedClusterPortfolio(
    Names: tuple[str, ...],
    InternalByName: dict[str, Any],
    BeamWidth: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> int:
    """Return the exact finite count of valid graph-core portfolio states."""
    return BuildPinAlignedPackedClusterPortfolio(
        Names,
        InternalByName,
        BeamWidth,
        WorkCheck=WorkCheck,
    ).CandidateCount

def _PinAlignedPackedClusterStateDominates(
    First: PinAlignedPackedClusterState,
    Second: PinAlignedPackedClusterState,
) -> bool:
    """Compare graph states on physical objectives, excluding tie-breakers."""
    return (
        all(
            FirstValue <= SecondValue
            for FirstValue, SecondValue in zip(First.Objective, Second.Objective)
        )
        and any(
            FirstValue < SecondValue
            for FirstValue, SecondValue in zip(First.Objective, Second.Objective)
        )
    )

def _BuildPinAlignedPackedClusterStates(
    Names: tuple[str, ...],
    InternalByName: dict[str, Any],
    BeamWidth: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[PinAlignedPackedClusterState, ...]:
    """Build and cache the raw bounded graph beam without selecting a state."""
    if BeamWidth < 1:
        raise ValueError("packed cluster beam width must be positive")
    PortfolioKey = (
        tuple(Names),
        tuple(
            (
                Name,
                tuple(map(str, InternalByName[Name].Inputs)),
                tuple(map(str, InternalByName[Name].Outputs)),
            )
            for Name in sorted(Names)
        ),
        BeamWidth,
        repr(DefaultRedstoneRoutingTechnology),
    )
    CachedPortfolio = _PinAlignedPackedClusterPortfolioCache.get(
        PortfolioKey
    )
    if CachedPortfolio is not None:
        return CachedPortfolio
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
    # A bounded graph beam revisits the same transformed macro pairs across
    # many partial states.  Exact physical conflict checking is authoritative,
    # but recomputing its electrical footprints for every revisit made the
    # finite upfront portfolio dominate the entire small-design deadline.
    # Cache only immutable geometry/signals of this one module; it cannot
    # create a candidate or relax a conflict.
    ConflictCache: dict[
        tuple[
            tuple[str, int, int, int, int, bool],
            tuple[str, int, int, int, int, bool],
        ],
        bool,
    ] = {}

    def ConflictKey(Gate: Any) -> tuple[str, int, int, int, int, bool]:
        return (
            str(Gate.Name),
            int(Gate.X),
            int(Gate.Y),
            int(Gate.Z),
            int(Gate.Rotation),
            bool(Gate.MirrorX),
        )

    def CachedPcbGatesConflict(First: Any, Second: Any) -> bool:
        Key = (ConflictKey(First), ConflictKey(Second))
        Cached = ConflictCache.get(Key)
        if Cached is None:
            Cached = PcbGatesConflict(First, Second)
            ConflictCache[Key] = Cached
        return Cached

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
        AccessPositionsBySignal: dict[
            str,
            set[tuple[int, int, int]],
        ] = {}
        for Gate in State.values():
            if Gate.OutputPin is not None:
                for Signal in Gate.Outputs:
                    Endpoints.setdefault(Signal, []).append(
                        (Gate.OutputPin[0], Gate.OutputPin[2])
                    )
                    PinOwners.append((Gate.OutputPin, Signal))
                    if Gate.OutputDirection is not None:
                        AccessPositionsBySignal.setdefault(
                            str(Signal),
                            set(),
                        ).update(
                            (
                                Gate.OutputPin[0]
                                + Gate.OutputDirection[0] * Offset,
                                Gate.OutputPin[1]
                                + Gate.OutputDirection[1] * Offset,
                                Gate.OutputPin[2]
                                + Gate.OutputDirection[2] * Offset,
                            )
                            for Offset in range(
                                DefaultRedstoneRoutingTechnology.AccessLength
                            )
                        )
            for InputIndex, Signal in enumerate(Gate.Inputs):
                Pin = Gate.InputPins[InputIndex]
                Endpoints.setdefault(Signal, []).append((Pin[0], Pin[2]))
                PinOwners.append((Pin, Signal))
                Direction = Gate.InputDirections[InputIndex]
                AccessPositionsBySignal.setdefault(
                    str(Signal),
                    set(),
                ).update(
                    (
                        Pin[0] + Direction[0] * Offset,
                        Pin[1] + Direction[1] * Offset,
                        Pin[2] + Direction[2] * Offset,
                    )
                    for Offset in range(
                        DefaultRedstoneRoutingTechnology.AccessLength
                    )
                )
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
        Signals = tuple(sorted(AccessPositionsBySignal))
        ElectricalExclusionsBySignal = {
            Signal: DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(
                AccessPositionsBySignal[Signal]
            )
            for Signal in Signals
        }
        AccessConflictPenalty = sum(
            1
            for FirstIndex, FirstSignal in enumerate(Signals)
            for SecondSignal in Signals[FirstIndex + 1 :]
            if (
                AccessPositionsBySignal[FirstSignal]
                & ElectricalExclusionsBySignal[SecondSignal]
            )
            or (
                AccessPositionsBySignal[SecondSignal]
                & ElectricalExclusionsBySignal[FirstSignal]
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
            AccessConflictPenalty,
            CrossElectricalPenalty,
            Width * Depth,
            max(Width, Depth),
            Hpwl,
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
                        for DeltaX, DeltaZ in BuildDerivedPinAlignmentOffsets():
                            CandidateKeys.add(
                                (
                                    ExistingPin[0] + DeltaX - LocalPin[0],
                                    ExistingPin[2] + DeltaZ - LocalPin[2],
                                    Rotation,
                                    MirrorX,
                                )
                            )
            OrderedCandidateKeys = sorted(CandidateKeys)
            for CandidateKeyIndex, (X, Z, Rotation, MirrorX) in enumerate(
                OrderedCandidateKeys
            ):
                if WorkCheck is not None and CandidateKeyIndex % 32 == 0:
                    WorkCheck({
                        "Phase": "graph-beam-candidate",
                        "GateName": Name,
                        "CompletedCandidates": CandidateKeyIndex,
                        "TotalCandidates": len(OrderedCandidateKeys),
                    })
                Candidate = BuildPlacedGate(
                    GateValue, X, 1, Z, Rotation, MirrorX
                )
                if any(
                    CachedPcbGatesConflict(Candidate, Existing)
                    for Existing in State.values()
                ):
                    continue
                CandidateState = dict(State)
                CandidateState[Name] = Candidate
                NextBeam.append((Score(CandidateState), CandidateState))
        if not NextBeam:
            return ()
        NextBeam.sort(key=lambda Value: Value[0])
        Beam = [State for _Key, State in NextBeam[:BeamWidth]]
        PlacedNames.add(Name)

    Portfolio: list[PinAlignedPackedClusterState] = []
    SeenGeometry = set()
    for State in sorted(Beam, key=Score):
        MinimumX = min(Gate.X for Gate in State.values())
        MinimumZ = min(Gate.Z for Gate in State.values())
        Candidate = (
            {
                Name: (Gate.X - MinimumX, Gate.Z - MinimumZ)
                for Name, Gate in State.items()
            },
            {Name: Gate.Rotation for Name, Gate in State.items()},
            {Name: Gate.MirrorX for Name, Gate in State.items()},
        )
        Identity = tuple(
            (
                Name,
                Candidate[0][Name],
                Candidate[1][Name],
                Candidate[2][Name],
            )
            for Name in sorted(State)
        )
        if Identity in SeenGeometry:
            continue
        SeenGeometry.add(Identity)
        CandidateScore = Score(State)
        Portfolio.append(
            PinAlignedPackedClusterState.FromMutableCandidate(
                CandidateIndex=len(Portfolio),
                Candidate=Candidate,
                Objective=tuple(
                    int(Value)
                    for Value in CandidateScore[:5]
                ),
            )
        )
    CachedPortfolio = tuple(Portfolio)
    _PinAlignedPackedClusterPortfolioCache[PortfolioKey] = CachedPortfolio
    return CachedPortfolio

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

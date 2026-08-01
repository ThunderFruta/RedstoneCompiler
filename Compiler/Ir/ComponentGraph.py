"""Topology-derived hierarchical component contracts.

This module deliberately has no placement or routing dependencies.  It turns
the synthesized NAND graph into stable component, port, and global-channel
contracts before physical placement begins.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable


def _StableFingerprint(Value: object) -> str:
    return sha256(repr(Value).encode("utf-8")).hexdigest()[:16]


def _GateKind(Gate: Any) -> str:
    Kind = getattr(Gate, "Kind", "")
    return Kind.value if hasattr(Kind, "value") else str(Kind)


@dataclass(frozen=True)
class ComponentPort:
    """One logical signal crossing a closed component boundary."""

    Signal: str
    Direction: str
    Capacity: int
    InternalTerminalCount: int
    ExternalTerminalCount: int

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "Direction": self.Direction,
            "Capacity": self.Capacity,
            "InternalTerminalCount": self.InternalTerminalCount,
            "ExternalTerminalCount": self.ExternalTerminalCount,
        }


@dataclass(frozen=True)
class TopologyComponent:
    """One name-independent, topologically contiguous logic region."""

    ComponentId: int
    GateNames: tuple[str, ...]
    InternalSignals: tuple[str, ...]
    InputPorts: tuple[ComponentPort, ...]
    OutputPorts: tuple[ComponentPort, ...]
    MinimumLevel: int
    MaximumLevel: int
    QualifyingReconvergentCutCount: int
    StructuralFingerprint: str

    @property
    def BoundarySignals(self) -> tuple[str, ...]:
        return tuple(sorted({
            *(Port.Signal for Port in self.InputPorts),
            *(Port.Signal for Port in self.OutputPorts),
        }))

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ComponentId": self.ComponentId,
            "GateCount": len(self.GateNames),
            "GateNames": list(self.GateNames),
            "InternalSignalCount": len(self.InternalSignals),
            "InputPorts": [
                Port.ToDictionary() for Port in self.InputPorts
            ],
            "OutputPorts": [
                Port.ToDictionary() for Port in self.OutputPorts
            ],
            "MinimumLevel": self.MinimumLevel,
            "MaximumLevel": self.MaximumLevel,
            "QualifyingReconvergentCutCount": (
                self.QualifyingReconvergentCutCount
            ),
            "StructuralFingerprint": self.StructuralFingerprint,
        }


@dataclass(frozen=True)
class ComponentGlobalChannel:
    """One explicit coarse route between closed component ports."""

    Signal: str
    SourceComponentId: int | None
    TargetComponentIds: tuple[int, ...]
    Capacity: int
    FeedthroughComponentIds: tuple[int, ...] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "SourceComponentId": self.SourceComponentId,
            "TargetComponentIds": list(self.TargetComponentIds),
            "Capacity": self.Capacity,
            "FeedthroughComponentIds": list(
                self.FeedthroughComponentIds
            ),
        }


@dataclass(frozen=True)
class ComponentGraph:
    """First-class hierarchy between synthesis and physical placement."""

    Components: tuple[TopologyComponent, ...]
    Channels: tuple[ComponentGlobalChannel, ...]
    GateToComponent: tuple[tuple[str, int], ...]
    StructuralFingerprint: str
    Hierarchical: bool
    MaximumComponentGates: int
    PeakCutwidth: int
    QualifyingReconvergentCutCount: int

    def ComponentForGate(self, GateName: str) -> int | None:
        return dict(self.GateToComponent).get(GateName)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "topology-component-graph-v1",
            "Hierarchical": self.Hierarchical,
            "StructuralFingerprint": self.StructuralFingerprint,
            "MaximumComponentGates": self.MaximumComponentGates,
            "PeakCutwidth": self.PeakCutwidth,
            "QualifyingReconvergentCutCount": (
                self.QualifyingReconvergentCutCount
            ),
            "Components": [
                Component.ToDictionary()
                for Component in self.Components
            ],
            "Channels": [
                Channel.ToDictionary() for Channel in self.Channels
            ],
        }


def _BuildTopologicalLevels(
    Gates: tuple[Any, ...],
    ProducerBySignal: dict[str, int],
) -> dict[int, int]:
    Levels: dict[int, int] = {}
    Pending = set(range(len(Gates)))
    while Pending:
        Advanced = False
        for GateIndex in sorted(Pending):
            Predecessors = {
                ProducerBySignal[str(Signal)]
                for Signal in getattr(Gates[GateIndex], "Inputs", ())
                if (
                    str(Signal) in ProducerBySignal
                    and ProducerBySignal[str(Signal)] != GateIndex
                )
            }
            if not Predecessors.issubset(Levels):
                continue
            Levels[GateIndex] = 1 + max(
                (Levels[Index] for Index in Predecessors),
                default=-1,
            )
            Pending.remove(GateIndex)
            Advanced = True
            break
        if Advanced:
            continue
        # Combinational validation owns cycle rejection.  This deterministic
        # fallback prevents component analysis from becoming a second parser.
        for GateIndex in sorted(Pending):
            Levels[GateIndex] = 0
        break
    return Levels


def _BuildDominators(
    InternalIndexes: tuple[int, ...],
    Predecessors: dict[int, frozenset[int]],
) -> dict[int, frozenset[int]]:
    InternalSet = frozenset(InternalIndexes)
    Dominators: dict[int, frozenset[int]] = {
        Index: (
            frozenset((Index,))
            if not (Predecessors.get(Index, frozenset()) & InternalSet)
            else InternalSet
        )
        for Index in InternalIndexes
    }
    Changed = True
    while Changed:
        Changed = False
        for Index in InternalIndexes:
            Parents = tuple(sorted(
                Predecessors.get(Index, frozenset()) & InternalSet
            ))
            Updated = (
                frozenset((Index,))
                if not Parents
                else frozenset((Index,)).union(
                    set.intersection(*(
                        set(Dominators[Parent])
                        for Parent in Parents
                    ))
                )
            )
            if Updated != Dominators[Index]:
                Dominators[Index] = Updated
                Changed = True
    return Dominators


def _BoundaryCutwidth(
    CutLevel: int,
    Levels: dict[int, int],
    ProducerBySignal: dict[str, int],
    ConsumersBySignal: dict[str, frozenset[int]],
    InternalSet: frozenset[int],
) -> int:
    return sum(
        Levels.get(Producer, 0) <= CutLevel
        and any(
            Consumer in InternalSet
            and Levels.get(Consumer, 0) > CutLevel
            for Consumer in Consumers
        )
        for Signal, Producer in ProducerBySignal.items()
        for Consumers in (ConsumersBySignal.get(Signal, frozenset()),)
        if Producer in InternalSet
    )


def _PartitionLevels(
    LevelGroups: tuple[tuple[int, tuple[int, ...]], ...],
    *,
    MaximumComponentGates: int,
    CutCosts: dict[int, tuple[int, int, int]],
) -> tuple[tuple[int, ...], ...]:
    """Choose deterministic contiguous regions with bounded cutwidth."""
    Count = len(LevelGroups)
    Best: dict[int, tuple[tuple[int, int, int], tuple[tuple[int, ...], ...]]] = {
        0: ((0, 0, 0), ())
    }
    for Start in range(Count):
        if Start not in Best:
            continue
        BaseScore, BaseParts = Best[Start]
        GateIndexes: list[int] = []
        for End in range(Start, Count):
            GateIndexes.extend(LevelGroups[End][1])
            if (
                len(GateIndexes) > MaximumComponentGates
                and End > Start
            ):
                break
            CutLevel = LevelGroups[End][0]
            Cutwidth, ReconvergenceSplits, DominatorBreaks = (
                CutCosts.get(CutLevel, (0, 0, 0))
                if End + 1 < Count
                else (0, 0, 0)
            )
            Score = (
                BaseScore[0] + ReconvergenceSplits,
                BaseScore[1] + DominatorBreaks,
                BaseScore[2] + Cutwidth,
            )
            Parts = (*BaseParts, tuple(sorted(GateIndexes)))
            Existing = Best.get(End + 1)
            CandidateKey = (Score, len(Parts), Parts)
            if Existing is None or CandidateKey < (
                Existing[0],
                len(Existing[1]),
                Existing[1],
            ):
                Best[End + 1] = (Score, Parts)
    return Best[Count][1]


def _PartitionConnectedTopology(
    Gates: tuple[Any, ...],
    InternalIndexes: tuple[int, ...],
    *,
    ProducerBySignal: dict[str, int],
    ConsumersBySignal: dict[str, frozenset[int]],
    Levels: dict[int, int],
    Dominators: dict[int, frozenset[int]],
    QualifyingSignals: frozenset[str],
    ReconvergenceRanges: dict[str, tuple[int, int]],
    MaximumComponentGates: int,
) -> tuple[tuple[int, ...], ...]:
    """Agglomerate connected topology without cutting costly reconvergence."""
    EdgeWeights: dict[frozenset[int], int] = {}
    InternalSet = frozenset(InternalIndexes)
    for Signal, Producer in ProducerBySignal.items():
        if Producer not in InternalSet:
            continue
        for Consumer in ConsumersBySignal.get(
            Signal, frozenset()
        ) & InternalSet:
            if Consumer == Producer:
                continue
            Edge = frozenset((Producer, Consumer))
            EdgeWeights[Edge] = EdgeWeights.get(Edge, 0) + (
                1
                + (12 if Signal in QualifyingSignals else 0)
                + (4 if Signal in ReconvergenceRanges else 0)
                + int(
                    Producer
                    in Dominators.get(Consumer, frozenset())
                )
            )

    Clusters: dict[int, set[int]] = {
        Index: {Index} for Index in InternalIndexes
    }

    def StructuralSignature(
        Values: set[int],
    ) -> tuple[tuple[object, ...], ...]:
        MinimumLevel = min(Levels[Index] for Index in Values)
        return tuple(sorted(
            (
                _GateKind(Gates[Index]),
                len(getattr(Gates[Index], "Inputs", ())),
                len(getattr(Gates[Index], "Outputs", ())),
                Levels[Index] - MinimumLevel,
            )
            for Index in Values
        ))

    while True:
        Candidates = []
        ClusterIds = tuple(sorted(Clusters))
        for FirstOffset, FirstId in enumerate(ClusterIds):
            First = Clusters[FirstId]
            for SecondId in ClusterIds[FirstOffset + 1:]:
                Second = Clusters[SecondId]
                if len(First) + len(Second) > MaximumComponentGates:
                    continue
                CrossWeight = sum(
                    Weight
                    for Edge, Weight in EdgeWeights.items()
                    if len(Edge & First) == 1 and len(Edge & Second) == 1
                )
                if CrossWeight == 0:
                    continue
                Combined = First | Second
                BoundaryWeight = sum(
                    Weight
                    for Edge, Weight in EdgeWeights.items()
                    if len(Edge & Combined) == 1
                )
                ReconvergenceSplits = sum(
                    min(Levels[Index] for Index in Combined)
                    <= Minimum
                    and max(Levels[Index] for Index in Combined)
                    < Maximum
                    for Minimum, Maximum
                    in ReconvergenceRanges.values()
                )
                Candidates.append((
                    (
                        -CrossWeight,
                        ReconvergenceSplits,
                        BoundaryWeight,
                        -len(Combined),
                        StructuralSignature(Combined),
                        FirstId,
                        SecondId,
                    ),
                    FirstId,
                    SecondId,
                ))
        if not Candidates:
            break
        _Score, FirstId, SecondId = min(Candidates)
        Clusters[FirstId].update(Clusters.pop(SecondId))

    return tuple(
        tuple(sorted(Values))
        for _ClusterId, Values in sorted(
            Clusters.items(),
            key=lambda Item: (
                min(Levels[Index] for Index in Item[1]),
                max(Levels[Index] for Index in Item[1]),
                StructuralSignature(Item[1]),
                Item[0],
            ),
        )
    )


def BuildComponentGraph(
    Module: Any,
    *,
    MaximumComponentGates: int = 18,
) -> ComponentGraph:
    """Build a generic dominator/reconvergence/cutwidth component graph."""
    if MaximumComponentGates < 2:
        raise ValueError("MaximumComponentGates must be at least two")
    Gates = tuple(getattr(Module, "Gates", ()))
    ProducerBySignal: dict[str, int] = {}
    ConsumersBySignalMutable: dict[str, set[int]] = {}
    for GateIndex, Gate in enumerate(Gates):
        for Signal in getattr(Gate, "Outputs", ()):
            ProducerBySignal[str(Signal)] = GateIndex
        for Signal in getattr(Gate, "Inputs", ()):
            ConsumersBySignalMutable.setdefault(
                str(Signal), set()
            ).add(GateIndex)
    ConsumersBySignal = {
        Signal: frozenset(Consumers)
        for Signal, Consumers in ConsumersBySignalMutable.items()
    }
    InternalIndexes = tuple(
        Index
        for Index, Gate in enumerate(Gates)
        if _GateKind(Gate) not in {"INPUT", "OUTPUT"}
    )
    InternalSet = frozenset(InternalIndexes)
    Levels = _BuildTopologicalLevels(Gates, ProducerBySignal)
    Successors = {
        Index: frozenset(
            Consumer
            for Signal in getattr(Gates[Index], "Outputs", ())
            for Consumer in ConsumersBySignal.get(
                str(Signal), frozenset()
            )
            if Consumer in InternalSet
        )
        for Index in InternalIndexes
    }
    Predecessors = {
        Index: frozenset(
            ProducerBySignal[str(Signal)]
            for Signal in getattr(Gates[Index], "Inputs", ())
            if (
                str(Signal) in ProducerBySignal
                and ProducerBySignal[str(Signal)] in InternalSet
            )
        )
        for Index in InternalIndexes
    }
    Dominators = _BuildDominators(InternalIndexes, Predecessors)

    DescendantsByGate: dict[int, frozenset[int]] = {}

    def Descendants(Index: int) -> frozenset[int]:
        Cached = DescendantsByGate.get(Index)
        if Cached is not None:
            return Cached
        Seen = {Index}
        Pending = [Index]
        while Pending:
            Current = Pending.pop()
            for Successor in Successors.get(Current, frozenset()):
                if Successor not in Seen:
                    Seen.add(Successor)
                    Pending.append(Successor)
        Result = frozenset(Seen)
        DescendantsByGate[Index] = Result
        return Result

    QualifyingSignals: set[str] = set()
    ReconvergenceRanges: dict[str, tuple[int, int]] = {}
    for Signal, Consumers in ConsumersBySignal.items():
        Branches = tuple(sorted(Consumers & InternalSet))
        if len(Branches) < 2:
            continue
        SharedDescendants = frozenset().union(*(
            Descendants(First) & Descendants(Second)
            for FirstOffset, First in enumerate(Branches)
            for Second in Branches[FirstOffset + 1:]
        ))
        if not SharedDescendants:
            continue
        Range = (
            min(Levels[Index] for Index in Branches),
            max(Levels[Index] for Index in SharedDescendants),
        )
        ReconvergenceRanges[Signal] = Range
        if len(Branches) >= 4:
            QualifyingSignals.add(Signal)

    LevelGroups = tuple(
        (
            Level,
            tuple(sorted(
                (
                    Index
                    for Index in InternalIndexes
                    if Levels.get(Index, 0) == Level
                ),
                key=lambda Index: (
                    _GateKind(Gates[Index]),
                    len(getattr(Gates[Index], "Inputs", ())),
                    len(getattr(Gates[Index], "Outputs", ())),
                    Index,
                ),
            )),
        )
        for Level in sorted({
            Levels.get(Index, 0) for Index in InternalIndexes
        })
    )
    PeakCutwidth = max(
        (
            _BoundaryCutwidth(
                Level,
                Levels,
                ProducerBySignal,
                ConsumersBySignal,
                InternalSet,
            )
            for Level, _Indexes in LevelGroups[:-1]
        ),
        default=0,
    )
    Hierarchical = bool(QualifyingSignals)
    CutCosts: dict[int, tuple[int, int, int]] = {}
    for Level, _Indexes in LevelGroups[:-1]:
        Cutwidth = _BoundaryCutwidth(
            Level,
            Levels,
            ProducerBySignal,
            ConsumersBySignal,
            InternalSet,
        )
        ReconvergenceSplits = sum(
            Minimum <= Level < Maximum
            for Minimum, Maximum in ReconvergenceRanges.values()
        )
        DominatorBreaks = sum(
            Producer not in Dominators.get(Consumer, frozenset())
            for Signal, Producer in ProducerBySignal.items()
            if (
                Producer in InternalSet
                and Levels.get(Producer, 0) <= Level
            )
            for Consumer in ConsumersBySignal.get(
                Signal, frozenset()
            )
            if (
                Consumer in InternalSet
                and Levels.get(Consumer, 0) > Level
            )
        )
        CutCosts[Level] = (
            ReconvergenceSplits,
            DominatorBreaks,
            Cutwidth,
        )

    if not InternalIndexes:
        Partitions: tuple[tuple[int, ...], ...] = ()
    elif not Hierarchical:
        Partitions = (InternalIndexes,)
    else:
        Partitions = _PartitionConnectedTopology(
            Gates,
            InternalIndexes,
            ProducerBySignal=ProducerBySignal,
            ConsumersBySignal=ConsumersBySignal,
            Levels=Levels,
            Dominators=Dominators,
            QualifyingSignals=frozenset(QualifyingSignals),
            ReconvergenceRanges=ReconvergenceRanges,
            MaximumComponentGates=MaximumComponentGates,
        )
    ComponentByGateIndex = {
        GateIndex: ComponentId
        for ComponentId, Partition in enumerate(Partitions)
        for GateIndex in Partition
    }

    Components = []
    for ComponentId, Partition in enumerate(Partitions):
        PartitionSet = frozenset(Partition)
        InternalSignals = []
        InputPorts = []
        OutputPorts = []
        Signals = sorted({
            str(Signal)
            for GateIndex in Partition
            for Signal in (
                *getattr(Gates[GateIndex], "Inputs", ()),
                *getattr(Gates[GateIndex], "Outputs", ()),
            )
        })
        for Signal in Signals:
            Producer = ProducerBySignal.get(Signal)
            Consumers = ConsumersBySignal.get(Signal, frozenset())
            InternalConsumers = Consumers & PartitionSet
            ExternalConsumers = Consumers - PartitionSet
            ProducerInside = Producer in PartitionSet
            if ProducerInside and InternalConsumers and not ExternalConsumers:
                InternalSignals.append(Signal)
            if InternalConsumers and not ProducerInside:
                InputPorts.append(ComponentPort(
                    Signal=Signal,
                    Direction="input",
                    Capacity=max(1, len(InternalConsumers)),
                    InternalTerminalCount=len(InternalConsumers),
                    ExternalTerminalCount=max(1, len(ExternalConsumers)),
                ))
            if ProducerInside and (
                ExternalConsumers
                or not Consumers
                or any(
                    _GateKind(Gates[Index]) == "OUTPUT"
                    for Index in Consumers
                )
            ):
                OutputPorts.append(ComponentPort(
                    Signal=Signal,
                    Direction="output",
                    Capacity=max(1, len(ExternalConsumers)),
                    InternalTerminalCount=1 + len(InternalConsumers),
                    ExternalTerminalCount=max(1, len(ExternalConsumers)),
                ))
        StructuralFingerprint = _StableFingerprint((
            tuple(sorted(
                (
                    _GateKind(Gates[Index]),
                    len(getattr(Gates[Index], "Inputs", ())),
                    len(getattr(Gates[Index], "Outputs", ())),
                    Levels.get(Index, 0)
                    - min(Levels[Value] for Value in Partition),
                )
                for Index in Partition
            )),
            tuple(sorted(
                (
                    Port.Direction,
                    Port.Capacity,
                    Port.InternalTerminalCount,
                    Port.ExternalTerminalCount,
                )
                for Port in (*InputPorts, *OutputPorts)
            )),
        ))
        Components.append(TopologyComponent(
            ComponentId=ComponentId,
            GateNames=tuple(sorted(
                str(getattr(Gates[Index], "Name", Index))
                for Index in Partition
            )),
            InternalSignals=tuple(sorted(InternalSignals)),
            InputPorts=tuple(sorted(
                InputPorts,
                key=lambda Port: (
                    Port.Direction,
                    Port.Signal,
                ),
            )),
            OutputPorts=tuple(sorted(
                OutputPorts,
                key=lambda Port: (
                    Port.Direction,
                    Port.Signal,
                ),
            )),
            MinimumLevel=min(Levels[Index] for Index in Partition),
            MaximumLevel=max(Levels[Index] for Index in Partition),
            QualifyingReconvergentCutCount=sum(
                (
                    ProducerBySignal.get(Signal) in PartitionSet
                    or bool(
                        ConsumersBySignal.get(
                            Signal, frozenset()
                        )
                        & PartitionSet
                    )
                )
                for Signal in QualifyingSignals
            ),
            StructuralFingerprint=StructuralFingerprint,
        ))

    Channels = []
    for Signal in sorted(
        set(ProducerBySignal) | set(ConsumersBySignal)
    ):
        Producer = ProducerBySignal.get(Signal)
        SourceComponent = ComponentByGateIndex.get(Producer)
        TargetComponents = tuple(sorted({
            ComponentByGateIndex[Consumer]
            for Consumer in ConsumersBySignal.get(
                Signal, frozenset()
            )
            if Consumer in ComponentByGateIndex
            and ComponentByGateIndex[Consumer] != SourceComponent
        }))
        if (
            SourceComponent is None
            or TargetComponents
            or (
                Producer is not None
                and any(
                    _GateKind(Gates[Index]) == "OUTPUT"
                    for Index in ConsumersBySignal.get(
                        Signal, frozenset()
                    )
                )
            )
        ):
            Channels.append(ComponentGlobalChannel(
                Signal=Signal,
                SourceComponentId=SourceComponent,
                TargetComponentIds=TargetComponents,
                Capacity=max(1, len(TargetComponents)),
            ))

    StructuralFingerprint = _StableFingerprint((
        tuple(
            Component.StructuralFingerprint
            for Component in Components
        ),
        tuple(sorted(
            (
                (
                    Channel.SourceComponentId,
                    Channel.TargetComponentIds,
                    Channel.Capacity,
                )
                for Channel in Channels
            ),
            key=lambda Value: (
                -1 if Value[0] is None else Value[0],
                Value[1],
                Value[2],
            ),
        )),
        Hierarchical,
        PeakCutwidth,
        len(QualifyingSignals),
    ))
    return ComponentGraph(
        Components=tuple(Components),
        Channels=tuple(Channels),
        GateToComponent=tuple(sorted(
            (
                str(getattr(Gates[Index], "Name", Index)),
                ComponentId,
            )
            for Index, ComponentId in ComponentByGateIndex.items()
        )),
        StructuralFingerprint=StructuralFingerprint,
        Hierarchical=Hierarchical,
        MaximumComponentGates=MaximumComponentGates,
        PeakCutwidth=PeakCutwidth,
        QualifyingReconvergentCutCount=len(QualifyingSignals),
    )

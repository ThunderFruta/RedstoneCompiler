"""Circuit-agnostic PCB-style clustering and weighted gate placement."""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from hashlib import sha256
from itertools import permutations
from math import ceil, sqrt
from statistics import median
from typing import Any

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
from ..Routing.Actions.Validation import ValidateTemplateIsolation
from ..Routing.ResourceGraph import (
    FindClaimConflicts,
    LocalRouteClaim,
    NormalizeRoutingEdge,
    RoutingResourceGraph,
    ValidateLocalRouteClaims,
)


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


@dataclass(frozen=True)
class PcbPlacement:
    """Weighted placement plus global routing metadata."""

    Placed: PlacedDesign
    Clusters: tuple[tuple[str, ...], ...]
    SignalOrder: tuple[str, ...]
    LayerCount: int
    PackedClusters: tuple[PackedNandCluster, ...] = ()


def BuildTopologicalLevels(Module: Any) -> dict[str, int]:
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
    while Pending:
        Remaining = []
        for Gate in Pending:
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
) -> tuple[tuple[str, ...], ...]:
    """Agglomerate strongly connected NAND gates without circuit recognition."""
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
    for Gate in Internal:
        for Signal in Gate.Inputs:
            Producer = Producers.get(Signal)
            if Producer not in InternalNames or Producer == Gate.Name:
                continue
            Key = frozenset((Producer, Gate.Name))
            EdgeWeights[Key] = EdgeWeights.get(Key, 0) + 1
    Levels = BuildTopologicalLevels(Module)

    def BoundaryCount(Names: set[str]) -> int:
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
    while True:
        BestPair = None
        BestScore = None
        ClusterIds = sorted(Clusters)
        for FirstIndex, FirstId in enumerate(ClusterIds):
            for SecondId in ClusterIds[FirstIndex + 1 :]:
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
    for Gate in Module.Gates:
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
    for Name in Names:
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
) -> tuple[str, dict[str, str]] | None:
    """Return an exact structural gate mapping without circuit recognition."""
    Reference = AnalyzeNandClusterStructure(Module, ReferenceNames)
    Candidate = AnalyzeNandClusterStructure(Module, CandidateNames)
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
    for Gate in Module.Gates:
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
        CurrentCost = PlacementCost(Assignment)
        Best = None
        BestCost = CurrentCost
        Occupied = {Slot: Index for Index, Slot in Assignment.items()}
        for ClusterIndex in range(Count):
            CurrentSlot = Assignment[ClusterIndex]
            for Slot in Slots:
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


def PcbGatesConflict(First: Any, Second: Any) -> bool:
    """Reject footprint overlap and access corridors entering another cell."""
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


def BuildPinAlignedPackedCluster(
    Names: tuple[str, ...],
    InternalByName: dict[str, Any],
    BeamWidth: int,
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
        return CrossElectricalPenalty, Hpwl, Width * Depth, max(Width, Depth), Stable

    while PlacedNames != NameSet:
        Name = ChooseNext()
        NextBeam = []
        for State in Beam:
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
                        ):
                            CandidateKeys.add(
                                (
                                    ExistingPin[0] + DeltaX - LocalPin[0],
                                    ExistingPin[2] + DeltaZ - LocalPin[2],
                                    Rotation,
                                    MirrorX,
                                )
                            )
            for X, Z, Rotation, MirrorX in sorted(CandidateKeys):
                Candidate = BuildPlacedGate(
                    GateValue, X, 1, Z, Rotation, MirrorX
                )
                if any(
                    RectanglesOverlap(Candidate, Existing)
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
) -> PlacedDesign:
    """Pull every template toward its nets and compact bounds legally."""
    SourceByName = {Gate.Name: Gate for Gate in Module.Gates}
    Current = Placed
    for _Pass in range(MaximumPasses):
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
        for Gate in list(Current.PlacedGates):
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
            for DeltaX, DeltaZ in Directions:
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
) -> PcbPlacement:
    """Cluster, optimize, legalize, and guide a generic NAND graph."""
    if RoutingSpacing < 0:
        raise ValueError("RoutingSpacing cannot be negative")
    Module = Netlist.Modules[Netlist.Top]
    Levels = BuildTopologicalLevels(Module)
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
    Clusters = BuildConnectivityClusters(
        Module,
        MaximumClusterSize=AdaptiveClusterSize,
        Policy=ClusterPolicy if PackedMode else None,
        MaximumBoundaryTerminals=(
            MaximumBoundaryTerminals if PackedMode else None
        ),
    )
    Assignment, ColumnCount, _RowCount = OptimizeClusterSlots(
        Module,
        Clusters,
        Levels,
    )
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
    SignalProducerNames = {
        Signal: Gate.Name
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    for ClusterIndex, Names in enumerate(Clusters):
        ClusterNames = set(Names)
        ReuseAccepted = False
        if PackedMode:
            StructuralSignature = AnalyzeNandClusterStructure(
                Module, Names
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
                    )
                    if Match is None:
                        continue
                    _Signature, Mapping = Match
                    for ReferenceName, CandidateName in Mapping.items():
                        LocalPositions[CandidateName] = LocalPositions[ReferenceName]
                        LocalRotations[CandidateName] = LocalRotations[ReferenceName]
                        LocalMirrors[CandidateName] = LocalMirrors.get(
                            ReferenceName, False
                        )
                    CandidateGates = [
                        BuildPlacedGate(
                            InternalByName[Name],
                            LocalPositions[Name][0],
                            1,
                            LocalPositions[Name][1],
                            LocalRotations[Name],
                            LocalMirrors[Name],
                        )
                        for Name in Names
                    ]
                    CandidatePlaced = PlacedDesign(
                        Module=Module,
                        PlacedGates=CandidateGates,
                    )
                    try:
                        # Apply the same hard legality used by the generic
                        # graph-beam packer. Pin-access proximity is scored
                        # and later checked by local routing/DRC; it is not a
                        # cell-overlap violation by itself.
                        if any(
                            RectanglesOverlap(First, Second)
                            for Index, First in enumerate(CandidateGates)
                            for Second in CandidateGates[Index + 1 :]
                        ):
                            raise ValueError("reused NAND placement conflicts")
                        BuildPlacedCellGeometry(CandidatePlaced)
                    except ValueError:
                        for CandidateName in Mapping.values():
                            LocalPositions.pop(CandidateName, None)
                            LocalRotations.pop(CandidateName, None)
                            LocalMirrors.pop(CandidateName, None)
                        continue
                    ClusterReuseSources[ClusterIndex] = ReferenceIndex
                    ClusterStructuralMappings[ClusterIndex] = Mapping
                    ReuseAccepted = True
                    break
        LocalLevels: dict[str, int] = {}
        Remaining = set(Names)
        while Remaining:
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
            for Row in range(FoldRows):
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
                        dict[str, int],
                    ]
                ] = [
                    ((0, 0, (), 0, ()), {})
                ]
                for Name in RowNames:
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
                                for ExistingX in Assigned.values()
                            ):
                                continue
                            Candidate = dict(Assigned)
                            Candidate[Name] = CandidateX
                            OrientationOptions = []
                            for MirrorX in (False, True):
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
                            CrossPenalty, Miss, Misses, _MirrorX = min(
                                OrientationOptions
                            )
                            Values = tuple(sorted(Candidate.values()))
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
                PackedXByName.update(RowBeam[0][1])
            MinimumPackedX = min(PackedXByName.values())
            for Name in OrderedNames:
                LocalPositions[Name] = (
                    PackedXByName[Name] - MinimumPackedX,
                    LocalLevels[Name] * CellPitchZ,
                )
                LocalRotations[Name] = PackedRotation
                Gate = InternalByName[Name]
                ParentPositions = [
                    (
                        InputIndex,
                        PackedXByName[Producer] + 1 - MinimumPackedX,
                        LocalLevels[Producer] * CellPitchZ + NandDepth,
                    )
                    if (Producer := SignalProducerNames.get(Signal))
                    in PackedXByName
                    else None
                    for InputIndex, Signal in enumerate(Gate.Inputs)
                ]
                ParentPositions = [
                    Value
                    for Value in ParentPositions
                    if Value is not None
                ]
                NormalPins = (
                    LocalPositions[Name][0],
                    LocalPositions[Name][0] + 2,
                )
                MirrorPins = tuple(reversed(NormalPins))
                InputZ = LocalPositions[Name][1] - 1

                def MirrorKey(Pins: tuple[int, int], MirrorX: bool) -> tuple[Any, ...]:
                    Misses = tuple(
                        abs(ParentX - Pins[InputIndex])
                        for InputIndex, ParentX, _ParentZ in ParentPositions
                    )
                    CrossPenalty = sum(
                        1
                        for InputIndex, ParentX, ParentZ in ParentPositions
                        for OtherIndex, OtherPinX in enumerate(Pins)
                        if OtherIndex != InputIndex
                        and ParentZ == InputZ
                        and abs(ParentX - OtherPinX) <= 1
                    )
                    return CrossPenalty, sum(Misses), Misses, MirrorX

                LocalMirrors[Name] = min(
                    MirrorKey(NormalPins, False),
                    MirrorKey(MirrorPins, True),
                )[-1]
            BeamPacked = (
                BuildPinAlignedPackedCluster(
                    Names,
                    InternalByName,
                    PackingPolicy.BeamWidth,
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
        ClusterSizes[ClusterIndex] = (
            PackedWidth if PackedMode else (FoldColumns - 1) * CellPitchX + NandWidth,
            PackedDepth if PackedMode else (FoldRows - 1) * CellPitchZ + NandDepth,
        )
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
    ColumnOrigins: dict[int, int] = {}
    NextX = 0
    for Column in range(ColumnCount):
        ColumnOrigins[Column] = NextX
        NextX += ColumnWidths[Column] + 3 + RoutingSpacing
    RowOrigins: dict[int, int] = {}
    NextZ = 0
    for Row in sorted(RowDepths):
        RowOrigins[Row] = NextZ
        NextZ += RowDepths[Row] + 2 + RoutingSpacing
    InputMargin = 0
    PlacedGates = []
    for ClusterIndex, Names in enumerate(Clusters):
        SlotX, SlotZ = Assignment[ClusterIndex]
        BaseX = InputMargin + ColumnOrigins[SlotX]
        BaseZ = RowOrigins[SlotZ]
        for Name in Names:
            LocalX, LocalZ = LocalPositions[Name]
            PlacedGates.append(
                BuildPlacedGate(
                    InternalByName[Name],
                    BaseX + LocalX,
                    1,
                    BaseZ + LocalZ,
                    LocalRotations[Name],
                    LocalMirrors.get(Name, False),
                )
            )

    InputGates = [Gate for Gate in Module.Gates if Gate.Kind.value == "INPUT"]
    OutputGates = [Gate for Gate in Module.Gates if Gate.Kind.value == "OUTPUT"]

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
            BankWidth = max(1, 1 + Spacing * (len(Ordered) - 1))
            StartX = CenterX - BankWidth // 2 + (
                PlacementPolicy.TerminalBankOffsetX
                if PlacementPolicy is not None
                and Ordered
                and Ordered[0].Kind.value == "INPUT"
                else 0
            )
            for Setback in range(32):
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

    def PlaceLocalizedTerminals(Gates: list[Any], PortNames: list[str]) -> None:
        """Place packed-mode I/O beside the NAND pins it actually serves."""
        PortIndexes = {Signal: Index for Index, Signal in enumerate(PortNames)}
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

        for Gate in sorted(
            Gates,
            key=lambda Value: (PortIndexes[TerminalSignal(Value)], Value.Name),
        ):
            Signal = TerminalSignal(Gate)
            DesiredPins = (
                Targets.get(Signal, [])
                if Gate.Kind.value == "INPUT"
                else [Producers[Signal].OutputPin]
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
            Options = []
            for Rotation in (0, 90, 180, 270):
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
                        1,
                        PinPosition[2] - LocalPin[2],
                        Rotation,
                        False,
                    )
                    if any(PcbGatesConflict(Candidate, Existing) for Existing in PlacedGates):
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
                    CandidateWidth, CandidateDepth = RotatedCellSize(
                        Candidate.Kind,
                        Candidate.Rotation,
                    )
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
                            (Distance, Width * Depth, max(Width, Depth), Candidate.X, Candidate.Z, Rotation),
                            Candidate,
                        )
                    )
            if not Options:
                raise ValueError(f"Could not place localized terminal {Gate.Name}")
            PlacedGates.append(min(Options, key=lambda Value: Value[0])[1])

    if PackedMode:
        PlaceLocalizedTerminals(InputGates, list(Module.Inputs))
        PlaceLocalizedTerminals(OutputGates, list(Module.Outputs))
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
            for InputIndex, Signal in enumerate(Gate.Inputs):
                TargetsBySignal.setdefault(Signal, []).append(
                    Gate.InputPins[InputIndex]
                )
        FrozenNetWires = {}
        LocalNetBranches = {}
        LocalNetTargets = {}
        LocalRouteClaims = []
        LocalRouteDiagnostics = {}
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
        AccessBySignal: dict[str, set[tuple[int, int, int]]] = {}
        BoundaryAccessBySignal: dict[str, set[tuple[int, int, int]]] = {}
        for Gate in PlacedGates:
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
            while Pending and Target not in Parents:
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
                        and 1 <= Neighbor[1] <= 5
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
            Producer = Producers.get(Signal)
            if Producer is None or not Targets:
                continue
            Root = Producer.OutputPin
            Paths = []
            LocalTargets = []
            for Target in Targets:
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
            if Paths:
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
                ProducerCluster = ClusterByGate.get(Producer.Name)
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
                        Nodes, Targets, LocalTargets
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
                    ValidateContinuationPortal(CandidateClaim, Targets)
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
                            Nodes, Targets, LocalTargets
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
                        ValidateContinuationPortal(CandidateClaim, Targets)
                        ValidateBoundaryEscapes(CandidateClaim)
                        ValidateLocalRouteClaims(
                            LocalResourceGraph,
                            (*LocalRouteClaims, CandidateClaim),
                        )
                    except ValueError:
                        continue
                LocalRouteClaims.append(CandidateClaim)
                LocalRouteDiagnostics.setdefault(Signal, {}).update({
                    "AcceptedTargets": len(set(LocalTargets)),
                    "AcceptedNodes": len(Nodes),
                    "UsedLongRoute": any(
                        len(Path) - 1 > MaximumLength for Path in Paths
                    ),
                })
                LocalNetBranches[Signal] = tuple(sorted(Nodes))
                LocalNetTargets[Signal] = tuple(sorted(LocalTargets))
                if len(LocalTargets) == len(Targets):
                    FrozenNetWires[Signal] = LocalNetBranches[Signal]
        Placed.FrozenNetWires = FrozenNetWires
        Placed.LocalNetBranches = LocalNetBranches
        Placed.LocalNetTargets = LocalNetTargets
        Placed.LocalRouteClaims = tuple(LocalRouteClaims)
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
    for Gate in Module.Gates:
        for Signal in Gate.Inputs:
            ConsumersBySignal.setdefault(Signal, []).append(Gate)
    PackedClusters = []
    ClaimsByCluster: dict[int, list[LocalRouteClaim]] = {}
    for Claim in Placed.LocalRouteClaims:
        ClaimsByCluster.setdefault(Claim.ClusterId, []).append(Claim)
    for ClusterIndex, Names in enumerate(Clusters):
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
            )
        )
    return PcbPlacement(
        Placed=Guided.Placed,
        Clusters=Clusters,
        SignalOrder=Guided.SignalOrder,
        LayerCount=Guided.LayerCount,
        PackedClusters=tuple(PackedClusters) if PackedMode else (),
    )

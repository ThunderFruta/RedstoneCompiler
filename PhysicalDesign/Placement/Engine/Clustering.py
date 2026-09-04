"""Connectivity clustering, structural analysis, and physical transforms."""

from __future__ import annotations

from functools import (
    lru_cache,
)
from hashlib import (
    sha256,
)
from itertools import (
    permutations,
)
from math import (
    ceil,
    sqrt,
)
from statistics import (
    median,
)
from typing import (
    Any,
    Callable,
    Mapping,
)
from PhysicalDesign.Geometry.Rotation import NormalizeRotation, RotatedCellSize, TransformDirection, TransformLocalPosition
from PhysicalDesign.Geometry.Placement import BuildPlacedGate, BuildPlacementPinAccessWitness, RectanglesOverlap
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology
from PhysicalDesign.Policy import ClusteringPolicy
from PhysicalDesign.Redstone.Rules.Geometry import BuildPlacedCellGeometry, BuildPlacedCellGeometryWithKeepOut
from .Clusters import (
    ClusterLayoutVariant,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .Channels import (
        CutDrivenClusterRefinementProfile,
    )


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
    RefinementProfile: CutDrivenClusterRefinementProfile | None = None,
    LogicalComponentByGate: Mapping[str, int] | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[tuple[str, ...], ...]:
    """Agglomerate strongly connected NAND gates without circuit recognition."""
    if WorkCheck is not None:
        WorkCheck({"Phase": "connectivity-clusters-start"})
    Internal = [Gate for Gate in Module.Gates if Gate.Kind.value == "NAND"]
    if not Internal:
        return ()
    InternalNames = {Gate.Name for Gate in Internal}
    LogicalComponentByGate = dict(
        LogicalComponentByGate or {}
    )
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
            EdgeWeights[Key] = (
                EdgeWeights.get(Key, 0)
                + 1
                + (
                    RefinementProfile.EdgeWeight
                    if (
                        RefinementProfile is not None
                        and Signal in RefinementProfile.Signals
                    )
                    else 0
                )
            )
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
                if LogicalComponentByGate and len({
                    LogicalComponentByGate[Name]
                    for Name in (*First, *Second)
                    if Name in LogicalComponentByGate
                }) > 1:
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

def ComposeCellTransform(
    CellRotation: int,
    CellMirrorX: bool,
    ClusterRotation: int,
    ClusterMirrorX: bool,
) -> tuple[int, bool]:
    """Compose a cell transform with one enclosing cluster transform."""
    TargetX = TransformDirection(
        TransformDirection((1, 0, 0), CellRotation, CellMirrorX),
        ClusterRotation,
        ClusterMirrorX,
    )
    TargetZ = TransformDirection(
        TransformDirection((0, 0, 1), CellRotation, CellMirrorX),
        ClusterRotation,
        ClusterMirrorX,
    )
    for Rotation in (0, 90, 180, 270):
        for MirrorX in (False, True):
            if (
                TransformDirection((1, 0, 0), Rotation, MirrorX) == TargetX
                and TransformDirection((0, 0, 1), Rotation, MirrorX) == TargetZ
            ):
                return Rotation, MirrorX
    raise ValueError("Could not compose packed-cell transforms")

def TransformPackedClusterLayout(
    Names: tuple[str, ...],
    LocalPositions: dict[str, tuple[int, int]],
    LocalRotations: dict[str, int],
    LocalMirrors: dict[str, bool],
    Rotation: int,
    MirrorX: bool,
    GatesByName: dict[str, Any] | None = None,
) -> ClusterLayoutVariant:
    """Rigidly transform a local layout using exact template geometry.

    A NAND's placement origin is not a rigid physical anchor: its template
    extends beyond the nominal footprint through pins, electrical exclusions,
    and directional supports.  When logical gates are available, derive each
    transformed origin by matching the transformed actual/electrical template
    sets to a composed target template.  This prevents a nominally mirrored
    rectangle from committing as an electrically overlapping NAND placement.
    """
    Rotation = NormalizeRotation(Rotation)
    BaseWidth = max(
        LocalPositions[Name][0]
        + RotatedCellSize("NAND", LocalRotations[Name])[0]
        for Name in Names
    )
    BaseDepth = max(
        LocalPositions[Name][1]
        + RotatedCellSize("NAND", LocalRotations[Name])[1]
        for Name in Names
    )
    Positions: dict[str, tuple[int, int]] = {}
    Rotations: dict[str, int] = {}
    Mirrors: dict[str, bool] = {}
    ActualGeometry: dict[str, frozenset[tuple[int, int, int]]] = {}
    ElectricalGeometry: dict[str, frozenset[tuple[int, int, int]]] = {}

    def TransformPosition(
        Position: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        return TransformLocalPosition(
            Position,
            (BaseWidth, BaseDepth),
            Rotation,
            MirrorX,
        )

    def TranslateGeometry(
        Geometry: frozenset[tuple[int, int, int]],
        DeltaX: int,
        DeltaZ: int,
    ) -> frozenset[tuple[int, int, int]]:
        return frozenset(
            (X + DeltaX, Y, Z + DeltaZ)
            for X, Y, Z in Geometry
        )

    for Name in Names:
        X, Z = LocalPositions[Name]
        Width, Depth = RotatedCellSize("NAND", LocalRotations[Name])
        Rotations[Name], Mirrors[Name] = ComposeCellTransform(
            LocalRotations[Name],
            LocalMirrors.get(Name, False),
            Rotation,
            MirrorX,
        )
        if GatesByName is None:
            Corners = (
                TransformPosition((X, 0, Z)),
                TransformPosition((X + Width - 1, 0, Z)),
                TransformPosition((X, 0, Z + Depth - 1)),
                TransformPosition((X + Width - 1, 0, Z + Depth - 1)),
            )
            Positions[Name] = (
                min(Value[0] for Value in Corners),
                min(Value[2] for Value in Corners),
            )
            continue
        SourceActual, SourceElectrical = _PhysicalGateGeometry(
            "NAND",
            X,
            1,
            Z,
            LocalRotations[Name],
            LocalMirrors.get(Name, False),
        )
        TransformedActual = frozenset(
            TransformPosition(Position) for Position in SourceActual
        )
        TransformedElectrical = frozenset(
            TransformPosition(Position) for Position in SourceElectrical
        )
        TargetActual, TargetElectrical = _PhysicalGateGeometry(
            "NAND",
            0,
            1,
            0,
            Rotations[Name],
            Mirrors[Name],
        )
        SourceAnchor = min(TransformedActual)
        Match = next(
            (
                (SourceAnchor[0] - TargetAnchor[0], SourceAnchor[2] - TargetAnchor[2])
                for TargetAnchor in sorted(TargetActual)
                if TranslateGeometry(
                    TargetActual,
                    SourceAnchor[0] - TargetAnchor[0],
                    SourceAnchor[2] - TargetAnchor[2],
                ) == TransformedActual
                and TranslateGeometry(
                    TargetElectrical,
                    SourceAnchor[0] - TargetAnchor[0],
                    SourceAnchor[2] - TargetAnchor[2],
                ) == TransformedElectrical
            ),
            None,
        )
        if Match is None:
            return ClusterLayoutVariant(
                Rotation=Rotation,
                MirrorX=MirrorX,
                Positions={},
                Rotations={},
                Mirrors={},
                Width=0,
                Depth=0,
                ActualGeometry={},
                ElectricalGeometry={},
                RejectionReason=(
                    f"TemplateTransformMismatch:Member={Name}:"
                    f"Rotation={Rotation}:MirrorX={MirrorX}"
                ),
            )
        Positions[Name] = Match
        ActualGeometry[Name] = TransformedActual
        ElectricalGeometry[Name] = TransformedElectrical
    MinimumX = min(Value[0] for Value in Positions.values())
    MinimumZ = min(Value[1] for Value in Positions.values())
    Positions = {
        Name: (X - MinimumX, Z - MinimumZ)
        for Name, (X, Z) in Positions.items()
    }
    ActualGeometry = {
        Name: TranslateGeometry(Geometry, -MinimumX, -MinimumZ)
        for Name, Geometry in ActualGeometry.items()
    }
    ElectricalGeometry = {
        Name: TranslateGeometry(Geometry, -MinimumX, -MinimumZ)
        for Name, Geometry in ElectricalGeometry.items()
    }
    Width = max(
        Positions[Name][0] + RotatedCellSize("NAND", Rotations[Name])[0]
        for Name in Names
    )
    Depth = max(
        Positions[Name][1] + RotatedCellSize("NAND", Rotations[Name])[1]
        for Name in Names
    )
    if GatesByName is not None:
        CandidateGates = [
            BuildPlacedGate(
                GatesByName[Name],
                Positions[Name][0],
                1,
                Positions[Name][1],
                Rotations[Name],
                Mirrors[Name],
            )
            for Name in Names
        ]
        Conflict = next(
            (
                (First.Name, Second.Name)
                for Index, First in enumerate(CandidateGates)
                for Second in CandidateGates[Index + 1 :]
                if PcbGatesConflict(First, Second)
            ),
            None,
        )
        if Conflict is not None:
            return ClusterLayoutVariant(
                Rotation=Rotation,
                MirrorX=MirrorX,
                Positions=Positions,
                Rotations=Rotations,
                Mirrors=Mirrors,
                Width=Width,
                Depth=Depth,
                ActualGeometry=ActualGeometry,
                ElectricalGeometry=ElectricalGeometry,
                RejectionReason=(
                    "TemplateConflict:Members="
                    f"{Conflict[0]},{Conflict[1]}:Rotation={Rotation}:"
                    f"MirrorX={MirrorX}"
                ),
            )
    return ClusterLayoutVariant(
        Rotation=Rotation,
        MirrorX=MirrorX,
        Positions=Positions,
        Rotations=Rotations,
        Mirrors=Mirrors,
        Width=Width,
        Depth=Depth,
        ActualGeometry=ActualGeometry,
        ElectricalGeometry=ElectricalGeometry,
    )

def OptimizeClusterSlots(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Levels: dict[str, int],
    LogicalComponentByGate: Mapping[str, int] | None = None,
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
    LogicalComponentByGate = dict(
        LogicalComponentByGate or {}
    )
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
                SharedComponentWeight = (
                    16
                    if (
                        LogicalComponentByGate
                        and LogicalComponentByGate.get(Producer.Name)
                        == LogicalComponentByGate.get(Gate.Name)
                        and Producer.Name in LogicalComponentByGate
                        and Gate.Name in LogicalComponentByGate
                    )
                    else 1
                )
                DirectedWeights[Key] = (
                    DirectedWeights.get(Key, 0)
                    + SharedComponentWeight
                )
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

@lru_cache(maxsize=4096)
def _PhysicalGateElectricalExclusions(
    Kind: str,
    X: int,
    Y: int,
    Z: int,
    Rotation: int,
    MirrorX: bool,
) -> frozenset[tuple[int, int, int]]:
    """Cache the exact electrical keep-out for one transformed macro.

    Packed graph states revisit the same physical macro transforms many times.
    The routing technology owns the keep-out rule, so caching its immutable
    result here changes neither the rule nor a conflict decision.
    """
    _Actual, Electrical = _PhysicalGateGeometry(
        Kind,
        X,
        Y,
        Z,
        Rotation,
        MirrorX,
    )
    Gate = type(
        "CachedPlacedGateKeepOut",
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
    _Actual, _Electrical, _Solid, ExplicitKeepOut = (
        BuildPlacedCellGeometryWithKeepOut(
            type("CachedPlacement", (), {"PlacedGates": [Gate]})()
        )
    )
    return frozenset(
        DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(
            set(Electrical)
        )
        | ExplicitKeepOut
    )


@lru_cache(maxsize=4096)
def _PhysicalGateAccessSignals(
    Kind: str,
    X: int,
    Y: int,
    Z: int,
    Rotation: int,
    MirrorX: bool,
    Outputs: tuple[str, ...],
    Inputs: tuple[str, ...],
    InputPins: tuple[tuple[int, int, int], ...],
    OutputPin: tuple[int, int, int] | None,
    InputDirections: tuple[tuple[int, int, int], ...],
    OutputDirection: tuple[int, int, int] | None,
) -> tuple[tuple[tuple[int, int, int], str], ...]:
    """Cache catalog-derived access rays for repeated slot comparisons."""
    Gate = type(
        "CachedAccessGate",
        (),
        {
            "Name": "CachedAccessGate",
            "Kind": Kind,
            "X": X,
            "Y": Y,
            "Z": Z,
            "Outputs": Outputs,
            "Inputs": Inputs,
            "InputPins": InputPins,
            "OutputPin": OutputPin,
            "Rotation": Rotation,
            "MirrorX": MirrorX,
            "InputDirections": InputDirections,
            "OutputDirection": OutputDirection,
        },
    )()
    Witness = BuildPlacementPinAccessWitness(
        (Gate,),
        AccessLength=DefaultRedstoneRoutingTechnology.AccessLength,
        RequireCatalogMatch=False,
    )
    return tuple(
        (Position, Selection.Signal)
        for Selection in Witness.Selections
        for Position in Selection.Path
    )

def PcbGatesConflict(First: Any, Second: Any) -> bool:
    """Reject footprint, pin-access, and template electrical conflicts."""

    def AccessSignals(
        Gate: Any,
    ) -> tuple[tuple[tuple[int, int, int], str], ...]:
        # Structural fixtures sometimes use minimal synthetic gates; retain
        # their exact rays while strict production boundaries report catalog
        # matches. Slot search revisits these immutable transforms heavily.
        return _PhysicalGateAccessSignals(
            Gate.Kind,
            Gate.X,
            Gate.Y,
            Gate.Z,
            Gate.Rotation,
            Gate.MirrorX,
            tuple(getattr(Gate, "Outputs", ())),
            tuple(getattr(Gate, "Inputs", ())),
            tuple(getattr(Gate, "InputPins", ())),
            tuple(Gate.OutputPin) if Gate.OutputPin is not None else None,
            tuple(getattr(Gate, "InputDirections", ())),
            (
                tuple(Gate.OutputDirection)
                if Gate.OutputDirection is not None
                else None
            ),
        )

    if RectanglesOverlap(First, Second):
        return True
    FirstWidth, FirstDepth = RotatedCellSize(First.Kind, First.Rotation)
    SecondWidth, SecondDepth = RotatedCellSize(Second.Kind, Second.Rotation)
    # Gate access clearance is a routing-technology fact.  Keeping it tied
    # to the same derived access length used by terminal and fabric
    # construction prevents a hidden literal from changing legal compact
    # slot geometry when the technology changes.
    BroadPhaseMargin = DefaultRedstoneRoutingTechnology.AccessLength
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
    FirstActual, _FirstElectrical = _PhysicalGateGeometry(
        First.Kind,
        First.X,
        First.Y,
        First.Z,
        First.Rotation,
        First.MirrorX,
    )
    SecondActual, _SecondElectrical = _PhysicalGateGeometry(
        Second.Kind,
        Second.X,
        Second.Y,
        Second.Z,
        Second.Rotation,
        Second.MirrorX,
    )
    if (
        _PhysicalGateElectricalExclusions(
            First.Kind,
            First.X,
            First.Y,
            First.Z,
            First.Rotation,
            First.MirrorX,
        )
        & SecondActual
    ) or (
        _PhysicalGateElectricalExclusions(
            Second.Kind,
            Second.X,
            Second.Y,
            Second.Z,
            Second.Rotation,
            Second.MirrorX,
        )
        & FirstActual
    ):
        return True
    if abs(First.Y - Second.Y) >= 3:
        return False
    FirstWidth, FirstDepth = RotatedCellSize(First.Kind, First.Rotation)
    SecondWidth, SecondDepth = RotatedCellSize(Second.Kind, Second.Rotation)
    FirstAccess = AccessSignals(First)
    SecondAccess = AccessSignals(Second)
    FirstSignalsByPosition: dict[tuple[int, int, int], set[str]] = {}
    SecondSignalsByPosition: dict[tuple[int, int, int], set[str]] = {}
    for Position, Signal in FirstAccess:
        FirstSignalsByPosition.setdefault(Position, set()).add(Signal)
    for Position, Signal in SecondAccess:
        SecondSignalsByPosition.setdefault(Position, set()).add(Signal)
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

    for FirstPosition, FirstSignal in FirstAccess:
        for SecondPosition, SecondSignal in SecondAccess:
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

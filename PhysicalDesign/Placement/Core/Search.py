"""Joint cluster placement and targeted relocation search."""

from __future__ import annotations

from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
)
from PhysicalDesign.Geometry.Rotation import TransformDirection
from PhysicalDesign.Contracts.Failures import RoutingAssignmentCut
from .Channels import (
    BuildClusterBoundaryBundles,
    BuildClusterInterfaceTopology,
    ClusterBoundaryContractScore,
    ScoreClusterBoundaryContracts,
    ScoreClusterInterfaceFacingMismatchesForOrientations,
    ScoreClusterInterfacePlacement,
)
from .Clustering import (
    OptimizeClusterSlots,
)
from .Constraints import (
    BuildAssignmentCutHigherOrderSignalSet,
    BuildEffectiveAssignmentCutPairwiseEdges,
    PlacementAssignmentConstraintSet,
    SelectPlacementConstraintWorkingSet,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .Clusters import (
        ClusterLayoutVariant,
    )


def ShouldExpandBoundaryEscapeGeometry(
    *,
    PackedMode: bool,
    ClusterIndex: int,
    BoundaryEscapeRelocationClusters: frozenset[int],
    PackedAccessRepairClusters: frozenset[int],
    RequiredRelocationSignals: frozenset[str],
    RelocationVariant: int,
    RelocationPrioritySignalCount: int,
    LocalGeometryRepairClusters: frozenset[int],
    StructuredAssignmentCutRelocation: bool,
) -> bool:
    """Gate the broad boundary shell behind exhausted non-structured repair."""
    return (
        PackedMode
        and ClusterIndex in BoundaryEscapeRelocationClusters
        and ClusterIndex not in PackedAccessRepairClusters
        and (
            not RequiredRelocationSignals
            or RelocationVariant >= 12
        )
        and (
            RelocationPrioritySignalCount > 1
            or ClusterIndex in LocalGeometryRepairClusters
        )
        and not StructuredAssignmentCutRelocation
    )

def ShouldReleasePartialLocalTreeBeforeSearch(
    *,
    ClusterCount: int,
    HasRelocationSignals: bool,
    LocalTargetCount: int,
    TotalTargetCount: int,
) -> bool:
    """Skip a local tree whose final feedback policy must release it."""
    return (
        ClusterCount > 4
        and HasRelocationSignals
        and LocalTargetCount != TotalTargetCount
    )


def JointPlacementSearchRetentionLimit(
    *,
    AvailableStateCount: int,
    PublishedCandidateCount: int,
    EnableClusterInterfacePlacementFeasibility: bool,
) -> int:
    """Bound exact interface screening relative to published candidates."""
    RetentionMultiplier = (
        2 if EnableClusterInterfacePlacementFeasibility else 1
    )
    return min(
        AvailableStateCount,
        PublishedCandidateCount * RetentionMultiplier,
    )

def SelectFocusedCutEpochClusters(
    RankedRelocationClusters: Iterable[int],
    Enabled: bool,
    MaximumClusters: int = 2,
) -> frozenset[int]:
    """Select a bounded cluster-local ECO focus from structural cut ranking."""
    if not Enabled or MaximumClusters <= 0:
        return frozenset()
    return frozenset(
        int(ClusterIndex)
        for ClusterIndex in tuple(RankedRelocationClusters)[
            :MaximumClusters
        ]
    )

def SelectFocusedTopologyFrontierClusters(
    CurrentRankedClusters: Iterable[int],
    PreviousRankedClusters: Iterable[int],
    Enabled: bool,
    MaximumClusters: int = 3,
) -> frozenset[int]:
    """Retain current-cut clusters plus one bounded prior-cut representative."""
    if not Enabled or MaximumClusters <= 0:
        return frozenset()
    Selected: list[int] = []
    for ClusterIndex in CurrentRankedClusters:
        Normalized = int(ClusterIndex)
        if Normalized not in Selected:
            Selected.append(Normalized)
        if len(Selected) >= min(2, MaximumClusters):
            break
    if len(Selected) < MaximumClusters:
        for ClusterIndex in PreviousRankedClusters:
            Normalized = int(ClusterIndex)
            if Normalized not in Selected:
                Selected.append(Normalized)
                break
    return frozenset(Selected[:MaximumClusters])

def SelectFocusedConstraintComponentClusters(
    CurrentFocusedClusters: Iterable[int],
    RankedConstraintClusters: Iterable[int],
    Enabled: bool,
    MaximumClusters: int = 6,
) -> frozenset[int]:
    """Extend one cut ECO across its bounded recurrent cluster component."""
    if not Enabled or MaximumClusters <= 0:
        return frozenset(map(int, CurrentFocusedClusters))
    Selected: list[int] = []
    for Values in (CurrentFocusedClusters, RankedConstraintClusters):
        for ClusterIndex in Values:
            Normalized = int(ClusterIndex)
            if Normalized not in Selected:
                Selected.append(Normalized)
            if len(Selected) >= MaximumClusters:
                return frozenset(Selected)
    return frozenset(Selected)

def SelectInternalPinBankGeometrySignals(
    *,
    Enabled: bool,
    RepairSignals: Iterable[str],
    CoordinatedCandidateDiversificationSignals: Iterable[str],
) -> frozenset[str]:
    """Keep physical ECO focus narrower than cumulative routing diversity."""
    if not Enabled:
        return frozenset()
    ExactRepairSignals = frozenset(map(str, RepairSignals))
    if ExactRepairSignals:
        return ExactRepairSignals
    return frozenset(map(
        str,
        CoordinatedCandidateDiversificationSignals,
    ))

def BuildJointPortfolioBaseRelocationControls(
    *,
    RelocationVariant: int,
    JointPlacementCandidateIndex: int,
    RequiresStructuredJointRelocation: bool,
    PreservePortfolioBaseAssignment: bool,
) -> tuple[int, bool]:
    """Keep one retained portfolio on one immutable base slot assignment."""
    CandidateOffset = (
        JointPlacementCandidateIndex
        if (
            RequiresStructuredJointRelocation
            and not PreservePortfolioBaseAssignment
        )
        else 0
    )
    return (
        max(0, RelocationVariant - 1) + CandidateOffset,
        bool(
            RequiresStructuredJointRelocation
            and JointPlacementCandidateIndex > 0
            and not PreservePortfolioBaseAssignment
        ),
    )


def _BuildJointPlacementCenters(
    SlotsByCluster: tuple[tuple[int, int], ...],
    Orientations: tuple[int, ...],
    *,
    Columns: int,
    Rows: int,
    VariantWidths: Mapping[int, tuple[int, ...]],
    VariantDepths: Mapping[int, tuple[int, ...]],
) -> dict[int, tuple[float, float]]:
    """Materialize the packed geometric center of every cluster state."""
    ColumnWidths = [1] * Columns
    RowDepths = [1] * Rows
    for Index, (Column, Row) in enumerate(SlotsByCluster):
        ColumnWidths[Column] = max(
            ColumnWidths[Column],
            VariantWidths[Index][Orientations[Index]],
        )
        RowDepths[Row] = max(
            RowDepths[Row],
            VariantDepths[Index][Orientations[Index]],
        )
    ColumnOrigins: dict[int, int] = {}
    NextX = 0
    for Column in range(Columns):
        ColumnOrigins[Column] = NextX
        NextX += ColumnWidths[Column] + 2
    RowOrigins: dict[int, int] = {}
    NextZ = 0
    for Row in range(Rows):
        RowOrigins[Row] = NextZ
        NextZ += RowDepths[Row] + 1
    return {
        Index: (
            ColumnOrigins[Slot[0]]
            + VariantWidths[Index][Orientations[Index]] / 2,
            RowOrigins[Slot[1]]
            + VariantDepths[Index][Orientations[Index]] / 2,
        )
        for Index, Slot in enumerate(SlotsByCluster)
    }


def _SelectDiverseJointPlacementBeam(
    OrderedStates: list[
        tuple[
            tuple[object, ...],
            tuple[tuple[int, int], ...],
            tuple[int, ...],
        ]
    ],
    *,
    Limit: int,
    JointOptimizationClusterIndices: tuple[int, ...],
    InitialSlots: tuple[tuple[int, int], ...],
    WorkCheck: Callable[[dict[str, object]], None] | None,
) -> list[
    tuple[
        tuple[object, ...],
        tuple[tuple[int, int], ...],
        tuple[int, ...],
    ]
]:
    """Keep score-competitive orientation representatives at every pass."""
    if not OrderedStates:
        return []
    Retained = [OrderedStates[0]]
    Pending = list(OrderedStates[1:])
    PrimaryOrientations = Retained[0][2]
    DiversityScanCount = 0

    def FindRepresentativeIndex(
        Predicate: Callable[
            [tuple[
                tuple[object, ...],
                tuple[tuple[int, int], ...],
                tuple[int, ...],
            ]],
            bool,
        ],
        ScanPhase: str,
    ) -> int | None:
        nonlocal DiversityScanCount
        for Index, State in enumerate(Pending):
            DiversityScanCount += 1
            if WorkCheck is not None and DiversityScanCount % 512 == 0:
                WorkCheck({
                    "Phase": "joint-cluster-placement-diversity",
                    "ScanPhase": ScanPhase,
                    "ScannedStates": DiversityScanCount,
                    "PendingStates": len(Pending),
                })
            if Predicate(State):
                return Index
        return None

    for ClusterIndex in JointOptimizationClusterIndices:
        RepresentativeIndex = FindRepresentativeIndex(
            lambda State, CurrentCluster=ClusterIndex: (
                State[2][CurrentCluster]
                != PrimaryOrientations[CurrentCluster]
            ),
            "orientation",
        )
        if RepresentativeIndex is not None:
            Retained.append(Pending.pop(RepresentativeIndex))
    for ClusterIndex in JointOptimizationClusterIndices:
        ExistingTransforms = {Existing[2] for Existing in Retained}
        RepresentativeIndex = FindRepresentativeIndex(
            lambda State, CurrentCluster=ClusterIndex: (
                State[1][CurrentCluster] != InitialSlots[CurrentCluster]
                and State[2] not in ExistingTransforms
            ),
            "slot",
        )
        if RepresentativeIndex is not None:
            Retained.append(Pending.pop(RepresentativeIndex))
    Retained.extend(Pending[:max(0, Limit - len(Retained))])
    return Retained

def OptimizeJointClusterPlacement(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Levels: dict[str, int],
    VariantsByCluster: dict[int, tuple[ClusterLayoutVariant, ...]],
    BeamWidth: int,
    PassLimit: int,
    RetainedCandidates: int = 1,
    CandidateIndex: int = 0,
    InitialAssignment: dict[int, tuple[int, int]] | None = None,
    FixedSlotClusters: frozenset[int] = frozenset(),
    AssignmentCut: RoutingAssignmentCut | None = None,
    AssignmentConstraints: PlacementAssignmentConstraintSet = (
        PlacementAssignmentConstraintSet()
    ),
    BoundaryContractCapacity: int = 0,
    EnableClusterInterfacePlacementFeasibility: bool = False,
    FocusedOptimizationClusters: frozenset[int] | None = None,
    FrontierAssignmentCuts: tuple[RoutingAssignmentCut, ...] = (),
    LogicalComponentByGate: Mapping[str, int] | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[
    dict[int, tuple[int, int]],
    dict[int, ClusterLayoutVariant],
    dict[str, object],
]:
    """Jointly optimize packed-cluster grid slots and rigid transforms."""
    Assignment, Columns, Rows = OptimizeClusterSlots(
        Module,
        Clusters,
        Levels,
        LogicalComponentByGate=LogicalComponentByGate,
        WorkCheck=WorkCheck,
    )
    if InitialAssignment is not None:
        Assignment = dict(InitialAssignment)
        Columns = max((Slot[0] for Slot in Assignment.values()), default=-1) + 1
        Rows = max((Slot[1] for Slot in Assignment.values()), default=-1) + 1
    Count = len(Clusters)
    if Count == 0:
        return Assignment, {}, {"Enabled": True, "CandidateCount": 0}
    BoundaryBundles = BuildClusterBoundaryBundles(Module, Clusters)
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
    for Gate in Module.Gates:
        Target = ClusterByGate.get(Gate.Name)
        for Signal in Gate.Inputs:
            Source = ClusterByGate.get(Producers[Signal].Name)
            if Source is None and Target is not None:
                InputWeights[Target] += 1
            elif Source is not None and Target is not None and Source != Target:
                DirectedWeights[Source, Target] = (
                    DirectedWeights.get((Source, Target), 0)
                    + (
                        16
                        if (
                            LogicalComponentByGate
                            and LogicalComponentByGate.get(
                                Producers[Signal].Name
                            )
                            == LogicalComponentByGate.get(Gate.Name)
                            and Producers[Signal].Name
                            in LogicalComponentByGate
                            and Gate.Name in LogicalComponentByGate
                        )
                        else 1
                    )
                )
            elif Source is not None and Gate.Kind.value == "OUTPUT":
                OutputWeights[Source] += 1
    ActiveConstraintWorkingSet = SelectPlacementConstraintWorkingSet(
        AssignmentCut,
        AssignmentConstraints,
        FrontierAssignmentCuts,
        ExpandConnectedComponent=bool(FocusedOptimizationClusters),
    )
    BoundedCompleteProofSignals = (
        frozenset(AssignmentCut.PriorityRelocationSignals)
        if (
            AssignmentCut is not None
            and AssignmentCut.CompleteAssignmentCutProof
            and AssignmentCut.PriorityRelocationSignals
        )
        else frozenset()
    )
    CurrentCutPairwiseEdges = tuple(
        Edge
        for Edge in BuildEffectiveAssignmentCutPairwiseEdges(
            AssignmentCut
        )
        if (
            not BoundedCompleteProofSignals
            or set(Edge).issubset(BoundedCompleteProofSignals)
        )
    )
    FrontierPairwiseEdges = () if BoundedCompleteProofSignals else tuple(
        Edge
        for Cut in FrontierAssignmentCuts
        for Edge in BuildEffectiveAssignmentCutPairwiseEdges(Cut)
    )
    EffectivePairwiseConflictEdges = tuple(sorted({
        *CurrentCutPairwiseEdges,
        *FrontierPairwiseEdges,
        *ActiveConstraintWorkingSet.PairwiseConflictEdges,
    }))
    EffectiveObservedInterfaceConflictEdges = tuple(sorted(
        ActiveConstraintWorkingSet.ObservedInterfaceConflictEdges
    ))
    CurrentCutHigherOrderSignals = (
        BuildAssignmentCutHigherOrderSignalSet(AssignmentCut)
    )
    FrontierHigherOrderSignalSets = () if BoundedCompleteProofSignals else tuple(
        Signals
        for Cut in FrontierAssignmentCuts
        if (
            Signals := BuildAssignmentCutHigherOrderSignalSet(Cut)
        )
    )
    EffectiveHigherOrderConflictSets = tuple(sorted({
        *ActiveConstraintWorkingSet.HigherOrderSignalSets,
        *FrontierHigherOrderSignalSets,
        *((CurrentCutHigherOrderSignals,)
          if CurrentCutHigherOrderSignals else ()),
    }))
    InterfaceConstraintSignals = {
        Signal
        for Edge in EffectivePairwiseConflictEdges
        for Signal in Edge
    } | {
        Signal
        for Edge in EffectiveObservedInterfaceConflictEdges
        for Signal in Edge
    } | {
        Signal
        for Signals in EffectiveHigherOrderConflictSets
        for Signal in Signals
    }
    FocusedConstraintComponentClusters = (
        SelectFocusedConstraintComponentClusters(
            FocusedOptimizationClusters or (),
            PrioritizeRelocationClusters(
                Module,
                Clusters,
                frozenset(InterfaceConstraintSignals),
            ),
            Enabled=bool(FocusedOptimizationClusters),
        )
    )
    ClusterInterfaceTopologyModel = (
        BuildClusterInterfaceTopology(
            Module,
            Clusters,
            None,
        )
        if EnableClusterInterfacePlacementFeasibility
        else None
    )
    CutClusterInterfaceTopologyModel = (
        BuildClusterInterfaceTopology(
            Module,
            Clusters,
            InterfaceConstraintSignals,
        )
        if (
            EnableClusterInterfacePlacementFeasibility
            and InterfaceConstraintSignals
        )
        else None
    )
    ObservedClusterInterfaceTopologyModel = (
        BuildClusterInterfaceTopology(
            Module,
            Clusters,
            {
                Signal
                for Edge in EffectiveObservedInterfaceConflictEdges
                for Signal in Edge
            },
        )
        if (
            EnableClusterInterfacePlacementFeasibility
            and EffectiveObservedInterfaceConflictEdges
        )
        else None
    )
    ExactPairClusterEdges: set[tuple[int, int]] = set()
    if EffectivePairwiseConflictEdges:
        for FirstSignal, SecondSignal in EffectivePairwiseConflictEdges:
            # A capacity-one signal pair proves one competing access
            # interface, not that every producer/consumer endpoint of the
            # two fanout nets must be mutually separated.  Project each
            # signal to its deterministic topology-ranked interface cluster
            # and keep one distinct representative pair.  The former cross
            # product over-constrained high-fanout reconvergent cuts until no
            # exact-legal orientation remained.
            FirstClusters = PrioritizeRelocationClusters(
                Module,
                Clusters,
                frozenset((FirstSignal,)),
            )
            SecondClusters = PrioritizeRelocationClusters(
                Module,
                Clusters,
                frozenset((SecondSignal,)),
            )
            RepresentativePair = next(
                (
                    tuple(sorted((FirstCluster, SecondCluster)))
                    for FirstCluster in FirstClusters
                    for SecondCluster in SecondClusters
                    if FirstCluster != SecondCluster
                ),
                None,
            )
            if RepresentativePair is not None:
                ExactPairClusterEdges.add(RepresentativePair)
    HigherOrderProjectedClusterEdges: set[tuple[int, int]] = set()
    HigherOrderRepresentativeClusters: set[int] = set()
    for Signals in EffectiveHigherOrderConflictSets:
        for Signal in Signals:
            RankedSignalClusters = PrioritizeRelocationClusters(
                Module,
                Clusters,
                frozenset((Signal,)),
            )
            if RankedSignalClusters:
                HigherOrderRepresentativeClusters.add(
                    RankedSignalClusters[0]
                )
        RankedClusters = PrioritizeRelocationClusters(
            Module,
            Clusters,
            frozenset(Signals),
        )
        if len(RankedClusters) >= 2:
            HigherOrderProjectedClusterEdges.add(
                tuple(sorted(RankedClusters[:2]))
            )
    HigherOrderProjectedClusterEdges.difference_update(
        ExactPairClusterEdges
    )
    ExactPairClusterEdgesTuple = tuple(sorted(ExactPairClusterEdges))
    HigherOrderProjectedClusterEdgesTuple = tuple(
        sorted(HigherOrderProjectedClusterEdges)
    )
    CutPairClusterEdges = tuple(sorted({
        *ExactPairClusterEdges,
        *HigherOrderProjectedClusterEdges,
    }))
    ExactPairClusters = frozenset(
        ClusterIndex
        for Edge in ExactPairClusterEdgesTuple
        for ClusterIndex in Edge
    )
    EffectiveFixedSlotClusters = (
        FixedSlotClusters - ExactPairClusters
    )
    CutPairSignals = frozenset(
        Signal
        for Edge in EffectivePairwiseConflictEdges
        for Signal in Edge
    )
    ConstraintSignals = frozenset(
        Signal
        for Signals in EffectiveHigherOrderConflictSets
        for Signal in Signals
    )
    CurrentCutSignals = (
        BoundedCompleteProofSignals
        or frozenset((
            *(
                AssignmentCut.PriorityRelocationSignals
                if AssignmentCut is not None
                else ()
            ),
            *(
                AssignmentCut.RelocationSignals
                if AssignmentCut is not None
                else ()
            ),
            *(
                AssignmentCut.ConflictSignals
                if AssignmentCut is not None
                else ()
            ),
            *(
                AssignmentCut.NoCandidateSignals
                if AssignmentCut is not None
                else ()
            ),
        ))
    )
    CurrentPairSignals = frozenset(
        Signal
        for Edge in CurrentCutPairwiseEdges
        for Signal in Edge
    )
    ResidualCurrentCutSignals = (
        CurrentCutSignals
        - CurrentPairSignals
        - frozenset(CurrentCutHigherOrderSignals)
    )
    StructuredCutClusters = (
        frozenset((
            *ExactPairClusters,
            *HigherOrderRepresentativeClusters,
            *BuildRelocationClusterSet(
                Module,
                Clusters,
                ResidualCurrentCutSignals,
            ),
        ))
        if (
            AssignmentCut is not None
            or AssignmentConstraints.HasActivePlacementConstraints
        )
        else frozenset()
    )
    JointOptimizationClusterIndices = (
        tuple(sorted(
            ClusterIndex
            for ClusterIndex in FocusedConstraintComponentClusters
            if 0 <= ClusterIndex < Count
        ))
        if FocusedConstraintComponentClusters
        else (
            tuple(sorted(StructuredCutClusters))
            if StructuredCutClusters
            else tuple(range(Count))
        )
    )
    if not JointOptimizationClusterIndices:
        JointOptimizationClusterIndices = tuple(range(Count))
    HasStructuredCut = (
        AssignmentCut is not None
        or AssignmentConstraints.HasActivePlacementConstraints
    )
    EffectivePassLimit = (
        min(PassLimit, 2)
        if HasStructuredCut
        else (
            min(PassLimit, 4)
            if EnableClusterInterfacePlacementFeasibility
            else PassLimit
        )
    )
    InitialSlots = tuple(Assignment[Index] for Index in range(Count))
    InitialOrientations = tuple(0 for _ in range(Count))
    Slots = tuple(
        (Column, Row)
        for Column in range(Columns)
        for Row in range(Rows)
    )
    VariantWidths = {
        Index: tuple(Variant.Width for Variant in Variants)
        for Index, Variants in VariantsByCluster.items()
    }
    VariantDepths = {
        Index: tuple(Variant.Depth for Variant in Variants)
        for Index, Variants in VariantsByCluster.items()
    }
    SourceFaces = {
        Index: tuple(
            TransformDirection(
                (0, 0, 1),
                Variant.Rotation,
                Variant.MirrorX,
            )
            for Variant in Variants
        )
        for Index, Variants in VariantsByCluster.items()
    }
    TargetFaces = {
        Index: tuple(
            TransformDirection(
                (0, 0, -1),
                Variant.Rotation,
                Variant.MirrorX,
            )
            for Variant in Variants
        )
        for Index, Variants in VariantsByCluster.items()
    }

    def Score(
        SlotsByCluster: tuple[tuple[int, int], ...],
        Orientations: tuple[int, ...],
    ) -> tuple[object, ...]:
        StateAssignment = {
            ClusterIndex: Slot
            for ClusterIndex, Slot in enumerate(SlotsByCluster)
        }
        StateVariants = (
            {
                ClusterIndex: VariantsByCluster[ClusterIndex][
                    Orientations[ClusterIndex]
                ]
                for ClusterIndex in range(Count)
            }
            if (
                CutClusterInterfaceTopologyModel is not None
                or ObservedClusterInterfaceTopologyModel is not None
            )
            else {}
        )
        CenterByCluster = _BuildJointPlacementCenters(
            SlotsByCluster,
            Orientations,
            Columns=Columns,
            Rows=Rows,
            VariantWidths=VariantWidths,
            VariantDepths=VariantDepths,
        )
        BoundaryContract = (
            ScoreClusterBoundaryContracts(
                BoundaryBundles,
                StateAssignment,
                BoundaryContractCapacity,
            )
            if BoundaryContractCapacity > 0
            else ClusterBoundaryContractScore(0, 0, 0)
        )
        Cost = 0
        for (Source, Target), Weight in DirectedWeights.items():
            SourceX, SourceZ = CenterByCluster[Source]
            TargetX, TargetZ = CenterByCluster[Target]
            DeltaX = TargetX - SourceX
            DeltaZ = TargetZ - SourceZ
            Cost += Weight * int(10 * (abs(DeltaX) + abs(DeltaZ)))
            if DeltaX < 0:
                Cost += Weight * 4
            Direction = (
                (1 if DeltaX >= 0 else -1, 0, 0)
                if abs(DeltaX) >= abs(DeltaZ)
                else (0, 0, 1 if DeltaZ >= 0 else -1)
            )
            SourceFace = SourceFaces[Source][Orientations[Source]]
            TargetFace = TargetFaces[Target][Orientations[Target]]
            if SourceFace[0] * Direction[0] + SourceFace[2] * Direction[2] <= 0:
                Cost += Weight * 8
            if TargetFace[0] * Direction[0] + TargetFace[2] * Direction[2] >= 0:
                Cost += Weight * 8
        MaximumCenterX = max(
            Value[0] for Value in CenterByCluster.values()
        )
        Cost += sum(
            InputWeights[Index] * int(CenterByCluster[Index][0]) * 5
            + OutputWeights[Index]
            * int(MaximumCenterX - CenterByCluster[Index][0])
            * 5
            for Index in range(Count)
        )
        ExactPairAdjacencyViolations = 0
        # A proven capacity-one pair is stronger evidence than the projected
        # leading edge of a higher-order cut.  Order the bounded beam by exact
        # pair separation first, while retaining the original placement cost
        # as the public SearchScore/SelectedScore.
        for FirstCluster, SecondCluster in ExactPairClusterEdgesTuple:
            FirstSlot = SlotsByCluster[FirstCluster]
            SecondSlot = SlotsByCluster[SecondCluster]
            SlotDistance = (
                abs(FirstSlot[0] - SecondSlot[0])
                + abs(FirstSlot[1] - SecondSlot[1])
            )
            if SlotDistance <= 1:
                ExactPairAdjacencyViolations += 1
                Cost += 200
            elif (
                FirstSlot[0] == SecondSlot[0]
                or FirstSlot[1] == SecondSlot[1]
            ):
                Cost += 40
        # Higher-order projections remain a soft distance hint: they summarize
        # one reported cut, but are not themselves proven pair incompatibility.
        # Keep them out of the lexicographic exact-pair objective.
        for FirstCluster, SecondCluster in (
            HigherOrderProjectedClusterEdgesTuple
        ):
            FirstSlot = SlotsByCluster[FirstCluster]
            SecondSlot = SlotsByCluster[SecondCluster]
            SlotDistance = (
                abs(FirstSlot[0] - SecondSlot[0])
                + abs(FirstSlot[1] - SecondSlot[1])
            )
            if SlotDistance <= 1:
                Cost += 200
            elif (
                FirstSlot[0] == SecondSlot[0]
                or FirstSlot[1] == SecondSlot[1]
            ):
                Cost += 40
        InterfaceScore = (
            ScoreClusterInterfacePlacement(
                Module,
                Clusters,
                StateAssignment,
                StateVariants,
                EffectivePairwiseConflictEdges,
                EffectiveHigherOrderConflictSets,
                Topology=CutClusterInterfaceTopologyModel,
            )
            if CutClusterInterfaceTopologyModel is not None
            else None
        )
        ObservedInterfaceBankConflicts = (
            ScoreClusterInterfacePlacement(
                Module,
                Clusters,
                StateAssignment,
                StateVariants,
                EffectiveObservedInterfaceConflictEdges,
                Topology=ObservedClusterInterfaceTopologyModel,
            ).PairBankConflicts
            if ObservedClusterInterfaceTopologyModel is not None
            else 0
        )
        AllInterfaceFacingMismatches = (
            ScoreClusterInterfaceFacingMismatchesForOrientations(
                ClusterInterfaceTopologyModel,
                StateAssignment,
                Orientations,
                SourceFaces,
                TargetFaces,
            )
            if ClusterInterfaceTopologyModel is not None
            else 0
        )
        return (
            (
                (
                    InterfaceScore.PairBankConflicts
                    if InterfaceScore is not None
                    else 0
                ),
                (
                    InterfaceScore.HigherOrderBankPressure
                    if InterfaceScore is not None
                    else 0
                ),
                (
                    InterfaceScore.HigherOrderPeakBankDemand
                    if InterfaceScore is not None
                    else 0
                ),
                (
                    InterfaceScore.HigherOrderBankExcessDemand
                    if InterfaceScore is not None
                    else 0
                ),
                (
                    InterfaceScore.HigherOrderOverloadedBankCount
                    if InterfaceScore is not None
                    else 0
                ),
                ExactPairAdjacencyViolations,
                AllInterfaceFacingMismatches,
                ObservedInterfaceBankConflicts,
            ),
            BoundaryContract.OverflowLanes,
            BoundaryContract.PeakBoundaryDemand,
            Cost,
            SlotsByCluster,
            Orientations,
        )

    Beam = [(Score(InitialSlots, InitialOrientations), InitialSlots, InitialOrientations)]

    CandidateCount = 1
    CandidateEvaluationCount = 0
    for PassIndex in range(EffectivePassLimit):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "joint-cluster-placement",
                "PassIndex": PassIndex,
                "BeamStates": len(Beam),
                "ClusterCount": Count,
            })
        Candidates: dict[
            tuple[tuple[tuple[int, int], ...], tuple[int, ...]],
            tuple[object, ...],
        ] = {}
        for _Score, PreviousSlots, PreviousOrientations in Beam:
            Candidates[PreviousSlots, PreviousOrientations] = _Score
            OccupantBySlot = {
                Slot: Index
                for Index, Slot in enumerate(PreviousSlots)
            }
            for ClusterIndex in JointOptimizationClusterIndices:
                CandidateSlotsForCluster = (
                    (PreviousSlots[ClusterIndex],)
                    if ClusterIndex in EffectiveFixedSlotClusters
                    else Slots
                )
                for Slot in CandidateSlotsForCluster:
                    Occupant = OccupantBySlot.get(Slot)
                    if (
                        Occupant is not None
                        and Occupant != ClusterIndex
                        and Occupant in EffectiveFixedSlotClusters
                    ):
                        continue
                    for OrientationIndex in range(len(VariantsByCluster[ClusterIndex])):
                        CandidateSlots = list(PreviousSlots)
                        CandidateSlots[ClusterIndex] = Slot
                        if Occupant is not None and Occupant != ClusterIndex:
                            CandidateSlots[Occupant] = PreviousSlots[ClusterIndex]
                        CandidateOrientations = list(PreviousOrientations)
                        CandidateOrientations[ClusterIndex] = OrientationIndex
                        Key = tuple(CandidateSlots), tuple(CandidateOrientations)
                        if Key not in Candidates:
                            CandidateEvaluationCount += 1
                            if (
                                WorkCheck is not None
                                and CandidateEvaluationCount % 256 == 0
                            ):
                                WorkCheck({
                                    "Phase": (
                                        "joint-cluster-placement-candidate"
                                    ),
                                    "PassIndex": PassIndex,
                                    "EvaluatedCandidates": (
                                        CandidateEvaluationCount
                                    ),
                                    "CurrentFrontier": len(Candidates),
                                })
                            Candidates[Key] = Score(*Key)
        CandidateCount += len(Candidates)
        Ordered = sorted(
            (ScoreValue, SlotsValue, OrientationValue)
            for (SlotsValue, OrientationValue), ScoreValue in Candidates.items()
        )
        NextBeam = _SelectDiverseJointPlacementBeam(
            Ordered,
            Limit=BeamWidth,
            JointOptimizationClusterIndices=(
                JointOptimizationClusterIndices
            ),
            InitialSlots=InitialSlots,
            WorkCheck=WorkCheck,
        )
        if [(Value[1], Value[2]) for Value in NextBeam] == [
            (Value[1], Value[2]) for Value in Beam
        ]:
            Beam = NextBeam
            break
        Beam = NextBeam
    if CandidateIndex < 0:
        raise ValueError("Joint placement candidate index cannot be negative")
    if RetainedCandidates < 1:
        raise ValueError("Joint placement must retain at least one candidate")

    # The beam is intentionally retained rather than collapsing to one
    # center-score winner.  Final placement materializes every retained state
    # and measures exact access/escape legality before the router receives it.
    # Prefer states that differ in the actual boundary-bank ownership pattern.
    # Rotation labels and slot distance are only tie-breakers: they are not
    # useful diversity when the same terminals still contend for the same
    # capacity-one interface.
    OrderedBeam = sorted(Beam)
    InterfaceFeasibleBeam = [
        State for State in OrderedBeam
        if State[0][0][0] == 0
    ]
    InterfaceRejectedStateCount = 0
    if (
        EnableClusterInterfacePlacementFeasibility
        and EffectivePairwiseConflictEdges
        and InterfaceFeasibleBeam
    ):
        InterfaceRejectedStateCount = (
            len(OrderedBeam) - len(InterfaceFeasibleBeam)
        )
        OrderedBeam = InterfaceFeasibleBeam
    RetainedBeam = [OrderedBeam[0]]
    PendingBeam = list(OrderedBeam[1:])
    SearchRetentionLimit = JointPlacementSearchRetentionLimit(
        AvailableStateCount=len(OrderedBeam),
        PublishedCandidateCount=RetainedCandidates,
        EnableClusterInterfacePlacementFeasibility=(
            EnableClusterInterfacePlacementFeasibility
        ),
    )

    def JointStateDistance(
        First: tuple[
            tuple[object, ...], tuple[tuple[int, int], ...], tuple[int, ...]
        ],
        Second: tuple[
            tuple[object, ...], tuple[tuple[int, int], ...], tuple[int, ...]
        ],
    ) -> int:
        DiversityClusterIndices = JointOptimizationClusterIndices
        return sum(
            First[1][ClusterIndex] != Second[1][ClusterIndex]
            for ClusterIndex in DiversityClusterIndices
        ) + sum(
            First[2][ClusterIndex] != Second[2][ClusterIndex]
            for ClusterIndex in DiversityClusterIndices
        )

    InterfaceOwnershipByState: dict[
        tuple[tuple[tuple[int, int], ...], tuple[int, ...]], str
    ] = {}
    InterfaceOwnershipEvaluationCount = 0

    def InterfaceOwnershipFingerprint(
        State: tuple[
            tuple[object, ...], tuple[tuple[int, int], ...], tuple[int, ...]
        ],
    ) -> str:
        nonlocal InterfaceOwnershipEvaluationCount
        if ClusterInterfaceTopologyModel is None:
            return ""
        _Score, StateSlots, StateOrientations = State
        StateKey = StateSlots, StateOrientations
        CachedFingerprint = InterfaceOwnershipByState.get(StateKey)
        if CachedFingerprint is not None:
            return CachedFingerprint
        InterfaceOwnershipEvaluationCount += 1
        Fingerprint = ScoreClusterInterfacePlacement(
            Module,
            Clusters,
            {
                ClusterIndex: StateSlots[ClusterIndex]
                for ClusterIndex in range(Count)
            },
            {
                ClusterIndex: VariantsByCluster[ClusterIndex][
                    StateOrientations[ClusterIndex]
                ]
                for ClusterIndex in range(Count)
            },
            EffectivePairwiseConflictEdges,
            EffectiveHigherOrderConflictSets,
            Topology=ClusterInterfaceTopologyModel,
        ).Pattern.OwnershipFingerprint
        InterfaceOwnershipByState[StateKey] = Fingerprint
        return Fingerprint

    while (
        PendingBeam
        and len(RetainedBeam) < SearchRetentionLimit
    ):
        ExistingOwnershipFingerprints = {
            InterfaceOwnershipFingerprint(State)
            for State in RetainedBeam
        }
        DistinctInterfaceIndices = []
        for Index, State in enumerate(PendingBeam):
            if WorkCheck is not None and Index % 512 == 0:
                WorkCheck({
                    "Phase": "joint-cluster-placement-retained-scan",
                    "ScannedStates": Index,
                    "PendingStates": len(PendingBeam),
                    "RetainedStates": len(RetainedBeam),
                })
            if (
                InterfaceOwnershipFingerprint(State)
                not in ExistingOwnershipFingerprints
            ):
                DistinctInterfaceIndices.append(Index)
        CandidateIndices = (
            DistinctInterfaceIndices
            if DistinctInterfaceIndices
            else list(range(len(PendingBeam)))
        )
        BestIndex = CandidateIndices[0]
        BestKey: tuple[object, ...] | None = None
        for ScanIndex, Index in enumerate(CandidateIndices, start=1):
            if WorkCheck is not None and ScanIndex % 512 == 0:
                WorkCheck({
                    "Phase": "joint-cluster-placement-retained-rank",
                    "ScannedStates": ScanIndex,
                    "CandidateStates": len(CandidateIndices),
                    "RetainedStates": len(RetainedBeam),
                })
            CandidateKey = (
                -min(
                    JointStateDistance(PendingBeam[Index], Existing)
                    for Existing in RetainedBeam
                ),
                PendingBeam[Index][0],
                PendingBeam[Index][1],
                PendingBeam[Index][2],
            )
            if BestKey is None or CandidateKey < BestKey:
                BestKey = CandidateKey
                BestIndex = Index
        RetainedBeam.append(PendingBeam.pop(BestIndex))
    if CandidateIndex >= len(RetainedBeam):
        raise ValueError(
            "Joint placement candidate index exceeds retained state count "
            f"({CandidateIndex} >= {len(RetainedBeam)})"
        )
    BestScore, BestSlots, BestOrientations = RetainedBeam[CandidateIndex]
    BestAssignment = {
        Index: BestSlots[Index] for Index in range(Count)
    }
    BestVariants = {
        Index: VariantsByCluster[Index][BestOrientations[Index]]
        for Index in range(Count)
    }
    return BestAssignment, BestVariants, {
        "Enabled": True,
        "BeamWidth": BeamWidth,
        "PassLimit": PassLimit,
        "EffectivePassLimit": EffectivePassLimit,
        "StructuredCutClusters": sorted(StructuredCutClusters),
        "FocusedOptimizationClusters": (
            sorted(FocusedOptimizationClusters)
            if FocusedOptimizationClusters
            else []
        ),
        "JointOptimizationClusters": list(
            JointOptimizationClusterIndices
        ),
        "RequestedFixedSlotClusters": sorted(FixedSlotClusters),
        "FixedSlotClusters": sorted(EffectiveFixedSlotClusters),
        "CandidateCount": CandidateCount,
        "SearchRetentionLimit": SearchRetentionLimit,
        "PublishedRetentionLimit": RetainedCandidates,
        "InterfaceOwnershipEvaluationCount": (
            InterfaceOwnershipEvaluationCount
        ),
        "CutPairClusterEdges": [
            list(Edge) for Edge in CutPairClusterEdges
        ],
        "ExactPairClusterEdges": [
            list(Edge) for Edge in ExactPairClusterEdgesTuple
        ],
        "HigherOrderProjectedClusterEdges": [
            list(Edge) for Edge in HigherOrderProjectedClusterEdgesTuple
        ],
        "AssignmentConstraints": AssignmentConstraints.ToDictionary(),
        "ActiveConstraintWorkingSet": (
            ActiveConstraintWorkingSet.ToDictionary()
        ),
        "EffectivePairwiseConflictEdges": [
            list(Edge) for Edge in EffectivePairwiseConflictEdges
        ],
        "EffectiveObservedInterfaceConflictEdges": [
            list(Edge)
            for Edge in EffectiveObservedInterfaceConflictEdges
        ],
        "EffectiveHigherOrderConflictSets": [
            list(Signals)
            for Signals in EffectiveHigherOrderConflictSets
        ],
        "ClusterInterfacePlacementFeasibility": {
            "Enabled": EnableClusterInterfacePlacementFeasibility,
            "ExactPairCount": len(EffectivePairwiseConflictEdges),
            "FeasibleStateCount": len(InterfaceFeasibleBeam),
            "RejectedStateCount": InterfaceRejectedStateCount,
            "AppliedAsHardFilter": bool(
                EnableClusterInterfacePlacementFeasibility
                and EffectivePairwiseConflictEdges
                and InterfaceFeasibleBeam
            ),
        },
        "SelectedCandidateIndex": CandidateIndex,
        "SelectedScore": BestScore[3],
        "SelectedInterfacePairBankConflicts": BestScore[0][0],
        "SelectedHigherOrderBankPressure": BestScore[0][1],
        "SelectedHigherOrderPeakBankDemand": BestScore[0][2],
        "SelectedHigherOrderBankExcessDemand": BestScore[0][3],
        "SelectedHigherOrderOverloadedBankCount": BestScore[0][4],
        "SelectedExactPairAdjacencyViolations": BestScore[0][5],
        "SelectedInterfaceFacingMismatches": BestScore[0][6],
        "SelectedObservedInterfaceBankConflicts": BestScore[0][7],
        "SelectedClusterInterfacePlacement": (
            ScoreClusterInterfacePlacement(
                Module,
                Clusters,
                BestAssignment,
                BestVariants,
                EffectivePairwiseConflictEdges,
                EffectiveHigherOrderConflictSets,
                Topology=ClusterInterfaceTopologyModel,
            ).ToDictionary()
            if ClusterInterfaceTopologyModel is not None
            else None
        ),
        "SelectedBoundaryContract": ScoreClusterBoundaryContracts(
            BoundaryBundles,
            BestAssignment,
            BoundaryContractCapacity,
        ).ToDictionary()
        if BoundaryContractCapacity > 0
        else None,
        "SelectedTransforms": {
            str(Index): {
                "Rotation": BestVariants[Index].Rotation,
                "MirrorX": BestVariants[Index].MirrorX,
            }
            for Index in range(Count)
        },
        "RetainedStates": [
            {
                "CandidateIndex": Index,
                "SearchScore": StateScore[3],
                "InterfaceOwnershipFingerprint": (
                    InterfaceOwnershipFingerprint((
                        StateScore,
                        StateSlots,
                        StateOrientations,
                    ))
                ),
                "InterfacePairBankConflicts": StateScore[0][0],
                "HigherOrderBankPressure": StateScore[0][1],
                "HigherOrderPeakBankDemand": StateScore[0][2],
                "HigherOrderBankExcessDemand": StateScore[0][3],
                "HigherOrderOverloadedBankCount": StateScore[0][4],
                "ExactPairAdjacencyViolations": StateScore[0][5],
                "InterfaceFacingMismatches": StateScore[0][6],
                "ObservedInterfaceBankConflicts": StateScore[0][7],
                "ClusterInterfacePlacement": (
                    ScoreClusterInterfacePlacement(
                        Module,
                        Clusters,
                        {
                            ClusterIndex: StateSlots[ClusterIndex]
                            for ClusterIndex in range(Count)
                        },
                        {
                            ClusterIndex: VariantsByCluster[ClusterIndex][
                                StateOrientations[ClusterIndex]
                            ]
                            for ClusterIndex in range(Count)
                        },
                        EffectivePairwiseConflictEdges,
                        EffectiveHigherOrderConflictSets,
                        Topology=ClusterInterfaceTopologyModel,
                    ).ToDictionary()
                    if ClusterInterfaceTopologyModel is not None
                    else None
                ),
                "BoundaryContract": ScoreClusterBoundaryContracts(
                    BoundaryBundles,
                    {
                        ClusterIndex: StateSlots[ClusterIndex]
                        for ClusterIndex in range(Count)
                    },
                    BoundaryContractCapacity,
                ).ToDictionary()
                if BoundaryContractCapacity > 0
                else None,
                "Slots": {
                    str(ClusterIndex): list(StateSlots[ClusterIndex])
                    for ClusterIndex in range(Count)
                },
                "Transforms": {
                    str(ClusterIndex): {
                        "Rotation": VariantsByCluster[ClusterIndex][
                            StateOrientations[ClusterIndex]
                        ].Rotation,
                        "MirrorX": VariantsByCluster[ClusterIndex][
                            StateOrientations[ClusterIndex]
                        ].MirrorX,
                    }
                    for ClusterIndex in range(Count)
                },
            }
            for Index, (StateScore, StateSlots, StateOrientations) in enumerate(
                RetainedBeam
            )
        ],
    }

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
    RelocationOffset: int = 0,
    RotateExactPortfolioSlots: bool = False,
    ForceDedicatedColumns: bool = False,
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
    if ForceDedicatedColumns:
        # A complete multi-pair access cut can remain infeasible while its
        # owners merely trade the same compact slots.  Give only the reported
        # clusters independent columns; this is a bounded cut-local geometry
        # repair, not a global spacing or routing-limit increase.
        NextColumn = max(
            (Column for Column, _Row in Result.values()),
            default=-1,
        ) + 1 + RelocationOffset
        for Offset, ClusterIndex in enumerate(Pending):
            Result[ClusterIndex] = (NextColumn + Offset, 0)
        return Result, max(ColumnCount, NextColumn + len(Pending))
    if len(Pending) > 1:
        ExistingSlots = tuple(Result[ClusterIndex] for ClusterIndex in Pending)
        if len(set(ExistingSlots)) == len(ExistingSlots):
            if len(Pending) % 2 == 0:
                if RotateExactPortfolioSlots and RelocationOffset:
                    # A retained structured portfolio must vary slot
                    # ownership as well as orientation. Keep the established
                    # adjacent-pair swap at offset zero, but rotate all
                    # measured owners for later exact states.
                    Shift = RelocationOffset % len(Pending)
                    for Offset, ClusterIndex in enumerate(Pending):
                        Result[ClusterIndex] = ExistingSlots[
                            (Offset + Shift) % len(Pending)
                        ]
                    return Result, ColumnCount
                # Compose independent exact-cut repairs without replacing the
                # strongest pair geometry.  Each adjacent ranked pair swaps
                # its established slots, so four selected owners express two
                # measured repairs with no footprint growth.
                for Offset in range(0, len(Pending), 2):
                    First = Pending[Offset]
                    Second = Pending[Offset + 1]
                    Result[First] = ExistingSlots[Offset + 1]
                    Result[Second] = ExistingSlots[Offset]
                return Result, ColumnCount
            # Keep a multi-cluster feedback repair within the established
            # footprint.  RelocationOffset already chooses which ranked
            # clusters participate; using it to move only Pending[0] into a
            # distant column silently discarded the remaining exact cut.
            Shift = 1 + RelocationOffset % (len(Pending) - 1)
            for Offset, ClusterIndex in enumerate(Pending):
                Result[ClusterIndex] = ExistingSlots[
                    (Offset + Shift) % len(Pending)
                ]
            return Result, ColumnCount
        # A suppressed vertical stack leaves multiple clusters in the same
        # logical slot.  Rotating identical slots is a no-op, which would
        # later commit physically overlapping NANDs.  Give each member a
        # deterministic dedicated column so the suppression is real geometry
        # rather than a bookkeeping-only placement variant.
        NextColumn = max(
            (Column for Column, _Row in Result.values()),
            default=-1,
        ) + 1 + RelocationOffset
        for Offset, ClusterIndex in enumerate(Pending):
            Result[ClusterIndex] = (NextColumn + Offset, 0)
        return Result, max(ColumnCount, NextColumn + len(Pending))
    NextColumn = max(
        (Column for Column, _Row in Result.values()),
        default=-1,
    ) + 1 + RelocationOffset
    Result[Pending[0]] = (NextColumn, 0)
    return Result, max(ColumnCount, NextColumn + 1)

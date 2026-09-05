"""Behavior-preserving phases of the final placement commit."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from math import ceil, sqrt
from statistics import median
from Compilation.Ir.ComponentGraph import BuildComponentGraph
from PhysicalDesign.Geometry.Rotation import RotatedCellSize
from PhysicalDesign.Geometry.Placement import BuildPlacedGate, PlacedDesign
from PhysicalDesign.Placement.PreRouteInterface import DerivedPerimeterSlotAssignment, DerivedPerimeterSlotDomain
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology
from PhysicalDesign.Policy import NandPackingPolicy
from PhysicalDesign.Redstone.Rules.Geometry import BuildPlacedCellGeometry
from ..Cache import _ExactStatePlacementGeometryCache, _JointPlacementExactScreenCache, _JointPlacementSearchCache, _PackedClusterBaseLayoutCache, _PlacementTopologyCache
from ..Channels import CutDrivenClusterRefinementProfile, InterClusterGapPlan
from ..Clustering import AnalyzeNandClusterStructure, BuildConnectivityClusters, BuildTopologicalLevels, FindIsomorphicNandClusterMapping, OptimizeClusterSlots, PcbGatesConflict, TransformPackedClusterLayout
from ..Clusters import PcbPlacement
from ..Compactness import BuildPinAlignedPackedCluster, BuildPinAlignedPackedClusterPortfolio
from ..Constraints import BuildAssignmentCutHigherOrderSignalSet, BuildEffectiveAssignmentCutPairwiseEdges, BuildEffectiveStructuredRelocationFocus, BuildJointPlacementSearchCacheKey, ExactJointPlacementScreen, ExactStatePlacedGateGeometry, RequiresStructuredAssignmentCutRelocation, SelectPlacementConstraintWorkingSet
from ..Costs import BuildInterClusterBoundaryDemand, BuildInterClusterGapPlan
from ..MandatoryAccess import MeasureMandatoryAccessConflictProfile, OrderExactStatesForMandatoryAccessCommit, RepairPackedClusterAccess, SelectExactInterfaceCommitStates
from ..Repair import ShouldIncludeNearPortalPackedAccessRepair
from ..Search import BuildJointPortfolioBaseRelocationControls, BuildRelocationClusterSet, OptimizeJointClusterPlacement, PrioritizeRelocationClusters, RelocateClusterSlots, SelectFocusedCutEpochClusters, SelectFocusedTopologyFrontierClusters, SelectInternalPinBankGeometrySignals, ShouldExpandBoundaryEscapeGeometry
from functools import partial

from .CommitState import (
    PlacementCommitState,
    SetPlacementCommitState,
)
from .CommitHelpers import (
    AddCluster,
    BuildClusterLocalRouteTemplateCacheKey,
    CheckMandatoryAccessScreen,
    CheckWork,
    FindExactStateConflict,
    FindLocalPath,
    MergeStacks,
    PlaceLocalizedTerminals,
    PlaceTerminalBank,
    PreferredBoundarySide,
    SelectBoundaryNodes,
    StackEndpoints,
    ValidateBoundaryEscapes,
    ValidateContinuationPortal,
    ValidateLocalPhysicalConnectivity,
    ValidateLocalSignalStrength,
)


def InitializePlacementCommit(Context):
    CheckWork(Context, 'start')
    if Context.RoutingSpacing < 0:
        raise ValueError('RoutingSpacing cannot be negative')
    if Context.DerivedTerminalLayoutVariantIndex < 0:
        raise ValueError('derived terminal layout variant index cannot be negative')
    if Context.DerivedTerminalLayoutVariantIndex and (not Context.UseDerivedPerimeterTerminals):
        raise ValueError('terminal layout variants require derived perimeter terminals')
    Context.Module = Context.Netlist.Modules[Context.Netlist.Top]
    Context.ModuleLayoutFingerprint = tuple(((Gate.Name, Gate.Kind.value if hasattr(Gate.Kind, 'value') else str(Gate.Kind), tuple(Gate.Inputs), tuple(Gate.Outputs)) for Gate in Context.Module.Gates))
    Context.PackedMode = bool(Context.PackingPolicy is not None and Context.PackingPolicy.Enabled)
    Context.NandCount = sum((Gate.Kind.value == 'NAND' for Gate in Context.Module.Gates))
    Context.TerminalPlacementPolicy = Context.PackingPolicy if Context.PackingPolicy is not None else NandPackingPolicy()
    Context.AdaptiveClusterSize = min(Context.PackingPolicy.MaximumClusterCells, max(Context.ClusterPolicy.MinimumCohesiveCells, ceil(Context.ClusterPolicy.CohesiveCellScale * sqrt(max(1, Context.NandCount))))) if Context.PackedMode and Context.ClusterPolicy is not None else Context.PackingPolicy.MaximumClusterCells if Context.PackedMode else 32
    if Context.PackedMode and Context.NandCount > 3 * Context.PackingPolicy.MaximumClusterCells:
        Context.AdaptiveClusterSize = min(Context.AdaptiveClusterSize, max(4, Context.PackingPolicy.MaximumClusterCells - Context.PackingPolicy.MaximumClusterCells // 8))
    Context.LogicalComponentGraph = BuildComponentGraph(Context.Module, MaximumComponentGates=max(4, Context.AdaptiveClusterSize + 4))
    Context.LogicalComponentByGate = dict(Context.LogicalComponentGraph.GateToComponent) if Context.LogicalComponentGraph.Hierarchical else {}
    Context.ClusterRefinementSignals = tuple(sorted(Context.CutDrivenClusterRefinementSignals if Context.CutDrivenClusterRefinementSignals is not None else {Signal for Edge in BuildEffectiveAssignmentCutPairwiseEdges(Context.AssignmentCut) for Signal in Edge}))
    Context.ClusterRefinementProfile = CutDrivenClusterRefinementProfile(Signals=Context.ClusterRefinementSignals, EdgeWeight=max(4, Context.AdaptiveClusterSize)) if Context.EnableClusterInterfacePlacementFeasibility and Context.ClusterRefinementSignals else None
    Context.PlacementTopologyCacheKey = (Context.ModuleLayoutFingerprint, Context.PackedMode, Context.AdaptiveClusterSize, repr(Context.ClusterPolicy if Context.PackedMode else None), Context.MaximumBoundaryTerminals if Context.PackedMode else None, Context.ClusterRefinementProfile.Signals if Context.ClusterRefinementProfile is not None else (), Context.ClusterRefinementProfile.EdgeWeight if Context.ClusterRefinementProfile is not None else 0, Context.LogicalComponentGraph.StructuralFingerprint)
    Context.FixedConnectivityClusters = tuple(tuple(map(str, Cluster)) for Cluster in Context.FixedConnectivityClusters)
    Context.FixedConnectivityClusterNames = tuple(Name for Cluster in Context.FixedConnectivityClusters for Name in Cluster)
    Context.InternalGateNames = frozenset(Gate.Name for Gate in Context.Module.Gates if Gate.Kind.value == 'NAND')
    if Context.FixedConnectivityClusters:
        if len(Context.FixedConnectivityClusterNames) != len(set(Context.FixedConnectivityClusterNames)) or frozenset(Context.FixedConnectivityClusterNames) != Context.InternalGateNames:
            raise ValueError('fixed connectivity clusters must partition every NAND gate exactly once')
        if any((not Cluster) or len(Cluster) > Context.AdaptiveClusterSize for Cluster in Context.FixedConnectivityClusters):
            raise ValueError('fixed connectivity cluster exceeds the active cluster-size contract')
        Context.Levels = BuildTopologicalLevels(Context.Module, WorkCheck=Context.WorkCheck)
        Context.Clusters = Context.FixedConnectivityClusters
        Context.CachedPlacementTopology = None
        CheckWork(Context, 'fixed-connectivity-clusters', GateCount=len(Context.InternalGateNames), ClusterCount=len(Context.Clusters))
    else:
        Context.CachedPlacementTopology = _PlacementTopologyCache.get(Context.PlacementTopologyCacheKey)
    if not Context.FixedConnectivityClusters and Context.CachedPlacementTopology is None:
        Context.Levels = BuildTopologicalLevels(Context.Module, WorkCheck=Context.WorkCheck)
        Context.Clusters = BuildConnectivityClusters(Context.Module, MaximumClusterSize=Context.AdaptiveClusterSize, Policy=Context.ClusterPolicy if Context.PackedMode else None, MaximumBoundaryTerminals=Context.MaximumBoundaryTerminals if Context.PackedMode else None, RefinementProfile=Context.ClusterRefinementProfile, LogicalComponentByGate=Context.LogicalComponentByGate, WorkCheck=Context.WorkCheck)
        _PlacementTopologyCache[Context.PlacementTopologyCacheKey] = (tuple(sorted(Context.Levels.items())), tuple((tuple(Names) for Names in Context.Clusters)))
    elif not Context.FixedConnectivityClusters:
        Context.CachedLevels, Context.CachedClusters = Context.CachedPlacementTopology
        Context.Levels = dict(Context.CachedLevels)
        Context.Clusters = tuple((tuple(Names) for Names in Context.CachedClusters))
        CheckWork(Context, 'placement-topology-cache-hit', GateCount=len(Context.Module.Gates), ClusterCount=len(Context.Clusters))
    Context.ActiveConstraintWorkingSet = SelectPlacementConstraintWorkingSet(Context.AssignmentCut, Context.AssignmentConstraints, Context.TopologyCutFrontier, ExpandConnectedComponent=Context.FocusedCutEpochPlacement)
    Context.EffectivePairwiseConflictEdges = tuple(sorted({*BuildEffectiveAssignmentCutPairwiseEdges(Context.AssignmentCut), *(Edge for Cut in Context.TopologyCutFrontier for Edge in BuildEffectiveAssignmentCutPairwiseEdges(Cut)), *Context.ActiveConstraintWorkingSet.PairwiseConflictEdges}))
    Context.StructuredPairwiseSignals = frozenset((Signal for Edge in Context.EffectivePairwiseConflictEdges for Signal in Edge))
    Context.FrontierHigherOrderSignalSets = tuple((Context.Signals for Cut in Context.TopologyCutFrontier if SetPlacementCommitState(Context, 'Signals', BuildAssignmentCutHigherOrderSignalSet(Cut))))
    Context.StructuredConstraintSignals = frozenset((Signal for Signals in (*Context.ActiveConstraintWorkingSet.HigherOrderSignalSets, *Context.FrontierHigherOrderSignalSets) for Signal in Signals)) | frozenset((Signal for Edge in Context.ActiveConstraintWorkingSet.ObservedInterfaceConflictEdges for Signal in Edge))
    Context.RequiresStructuredJointRelocation = RequiresStructuredAssignmentCutRelocation(Context.AssignmentCut)
    if Context.AssignmentCut is not None:
        Context.RelocationSignals = frozenset((*Context.RelocationSignals, *Context.AssignmentCut.RelocationSignals, *Context.AssignmentCut.ConflictSignals, *Context.AssignmentCut.NoCandidateSignals, *Context.StructuredPairwiseSignals, *Context.StructuredConstraintSignals))
    Context.InternalPinBankGeometrySignals = SelectInternalPinBankGeometrySignals(Enabled=Context.EnableInternalPinBankGeometryRepair, RepairSignals=Context.InternalPinBankGeometryRepairSignals, CoordinatedCandidateDiversificationSignals=Context.CoordinatedCandidateDiversificationSignals)
    if Context.InternalPinBankGeometrySignals:
        Context.RelocationSignals = frozenset((*Context.RelocationSignals, *Context.InternalPinBankGeometrySignals))
        Context.RelocationPrioritySignals = frozenset((*Context.RelocationPrioritySignals, *Context.InternalPinBankGeometrySignals))
        Context.RequiredRelocationSignals = frozenset((*Context.RequiredRelocationSignals, *Context.InternalPinBankGeometrySignals))
    Context.RelocationPrioritySignals, Context.RequiredRelocationSignals = BuildEffectiveStructuredRelocationFocus(Context.AssignmentCut, Context.AssignmentConstraints, Context.RelocationPrioritySignals, Context.RequiredRelocationSignals)
    Context.RelocationClusters = BuildRelocationClusterSet(Context.Module, Context.Clusters, Context.RelocationSignals)
    Context.RequiredRelocationClusters = BuildRelocationClusterSet(Context.Module, Context.Clusters, Context.RequiredRelocationSignals)
    Context.BoundaryEscapeRelocationClusters = BuildRelocationClusterSet(Context.Module, Context.Clusters, Context.RelocationPrioritySignals or Context.RelocationSignals)
    Context.RankedRequiredGeometryClusters = PrioritizeRelocationClusters(Context.Module, Context.Clusters, Context.RelocationPrioritySignals or Context.RequiredRelocationSignals)
    Context.LocalGeometryRepairClusters = frozenset((Context.RankedRequiredGeometryClusters[min(Context.RelocationVariant, 1) % len(Context.RankedRequiredGeometryClusters)],) if Context.PackedMode and Context.PackingPolicy.EnableLocalGeometryRepair and (not Context.RequiresStructuredJointRelocation) and Context.RankedRequiredGeometryClusters else ())
    CheckWork(Context, 'connectivity-clusters', ClusterCount=len(Context.Clusters))
    CheckWork(Context, 'cluster-slots', ClusterCount=len(Context.Clusters))
    Context.Assignment, Context.ColumnCount, Context._RowCount = OptimizeClusterSlots(Context.Module, Context.Clusters, Context.Levels, LogicalComponentByGate=Context.LogicalComponentByGate, WorkCheck=Context.WorkCheck)
    Context.InternalByName = {Gate.Name: Gate for Gate in Context.Module.Gates if Gate.Kind.value == 'NAND'}
    Context.PackedRotation = 0
    Context.DefaultRotation = Context.PackedRotation if Context.PackedMode else 270
    Context.NandWidth, Context.NandDepth = RotatedCellSize('NAND', Context.DefaultRotation)
    Context.CellPitchX = Context.NandWidth + 2 if Context.PackedMode else Context.NandWidth + 3 + Context.RoutingSpacing
    Context.CellPitchZ = Context.NandDepth + 1 if Context.PackedMode else Context.NandDepth + 2 + Context.RoutingSpacing
    Context.LocalPositions: dict[str, tuple[int, int]] = {}
    Context.LocalRotations: dict[str, int] = {}
    Context.LocalMirrors: dict[str, bool] = {}
    Context.ClusterSizes: dict[int, tuple[int, int]] = {}
    Context.ClusterStructuralSignatures: dict[int, str] = {}
    Context.ClusterReuseSources: dict[int, int | None] = {}
    Context.ClusterStructuralMappings: dict[int, dict[str, str]] = {}
    Context.ClusterStackIds: dict[int, int | None] = {}
    Context.ClusterStackLevels: dict[int, int] = {}
    Context.SelectedClusterVariants: dict[int, ClusterLayoutVariant] = {}
    Context.JointPlacementDiagnostics: dict[str, object] = {}
    Context.PackedAccessRepairByCluster: dict[int, dict[str, int]] = {}
    Context.StackSuppressedRelocationClusters: set[int] = set()
    Context.PhysicallyRelocatedClusters: frozenset[int] = frozenset()
    Context.SignalProducerNames = {Signal: Gate.Name for Gate in Context.Module.Gates for Signal in Gate.Outputs}


def BuildClusterPlacementLayouts(Context):
    for Context.ClusterIndex, Context.Names in enumerate(Context.Clusters):
        CheckWork(Context, 'cluster-placement', CompletedClusters=Context.ClusterIndex, TotalClusters=len(Context.Clusters))
        Context.ClusterNames = set(Context.Names)
        Context.CutDrivenRefinementCluster = bool(Context.ClusterRefinementProfile is not None and any((Signal in Context.ClusterRefinementProfile.Signals for Name in Context.Names for Signal in (*Context.InternalByName[Name].Inputs, *Context.InternalByName[Name].Outputs))))
        Context.ReuseAccepted = False
        Context.BaseLayoutCacheKey = (Context.ModuleLayoutFingerprint, tuple(Context.Names), Context.RoutingSpacing, Context.PackingPolicy.BeamWidth if Context.PackedMode else 0, Context.PackingPolicy.GraphBeamEnabled if Context.PackedMode else False, Context.JointPlacementCandidateIndex if Context.PackedMode and Context.PackingPolicy.GraphBeamEnabled else 0, Context.PackingPolicy.EnableStructuralReuse if Context.PackedMode else False, Context.PackingPolicy.MaximumStructuralReuseMappings if Context.PackedMode else 0)
        Context.CachedBaseLayout = _PackedClusterBaseLayoutCache.get(Context.BaseLayoutCacheKey) if Context.PackedMode else None
        if Context.CachedBaseLayout is not None:
            Context.StructuralSignature, Context.ReuseSource, Context.StructuralMapping, Context.CachedPositions, Context.CachedRotations, Context.CachedMirrors, Context.CachedWidth, Context.CachedDepth = Context.CachedBaseLayout
            Context.ClusterStructuralSignatures[Context.ClusterIndex] = Context.StructuralSignature
            Context.ClusterReuseSources[Context.ClusterIndex] = Context.ReuseSource
            if Context.StructuralMapping:
                Context.ClusterStructuralMappings[Context.ClusterIndex] = dict(Context.StructuralMapping)
            Context.LocalPositions.update(Context.CachedPositions)
            Context.LocalRotations.update(Context.CachedRotations)
            Context.LocalMirrors.update(Context.CachedMirrors)
        elif Context.PackedMode:
            Context.StructuralSignature = AnalyzeNandClusterStructure(Context.Module, Context.Names, WorkCheck=Context.WorkCheck)[0]
            Context.ClusterStructuralSignatures[Context.ClusterIndex] = Context.StructuralSignature
            Context.ClusterReuseSources[Context.ClusterIndex] = None
            if Context.PackingPolicy.EnableStructuralReuse:
                for Context.ReferenceIndex in range(Context.ClusterIndex):
                    if Context.ClusterStructuralSignatures.get(Context.ReferenceIndex) != Context.StructuralSignature:
                        continue
                    Context.Match = FindIsomorphicNandClusterMapping(Context.Module, Context.Clusters[Context.ReferenceIndex], Context.Names, Context.PackingPolicy.MaximumStructuralReuseMappings, WorkCheck=Context.WorkCheck)
                    if Context.Match is None:
                        continue
                    Context._Signature, Context.Mapping = Context.Match
                    Context.CandidatePositions = {CandidateName: Context.LocalPositions[ReferenceName] for ReferenceName, CandidateName in Context.Mapping.items()}
                    Context.CandidateRotations = {CandidateName: Context.LocalRotations[ReferenceName] for ReferenceName, CandidateName in Context.Mapping.items()}
                    Context.CandidateMirrors = {CandidateName: Context.LocalMirrors.get(ReferenceName, False) for ReferenceName, CandidateName in Context.Mapping.items()}
                    Context.CandidateGates = [BuildPlacedGate(Context.InternalByName[Name], Context.CandidatePositions[Name][0], 1, Context.CandidatePositions[Name][1], Context.CandidateRotations[Name], Context.CandidateMirrors[Name]) for Name in Context.Names]
                    Context.CandidatePlaced = PlacedDesign(Module=Context.Module, PlacedGates=Context.CandidateGates)
                    try:
                        if any((PcbGatesConflict(First, Second) for Index, First in enumerate(Context.CandidateGates) for Second in Context.CandidateGates[Index + 1:])):
                            raise ValueError('reused NAND placement conflicts')
                        BuildPlacedCellGeometry(Context.CandidatePlaced)
                    except ValueError:
                        continue
                    Context.LocalPositions.update(Context.CandidatePositions)
                    Context.LocalRotations.update(Context.CandidateRotations)
                    Context.LocalMirrors.update(Context.CandidateMirrors)
                    Context.ClusterReuseSources[Context.ClusterIndex] = Context.ReferenceIndex
                    Context.ClusterStructuralMappings[Context.ClusterIndex] = Context.Mapping
                    Context.ReuseAccepted = True
                    break
        Context.LocalLevels: dict[str, int] = {}
        Context.Remaining = set(Context.Names)
        while Context.Remaining:
            CheckWork(Context, 'cluster-ordering', ClusterIndex=Context.ClusterIndex, RemainingGates=len(Context.Remaining))
            Context.Progress = False
            for Context.Name in sorted(Context.Remaining):
                Context.Gate = Context.InternalByName[Context.Name]
                Context.Dependencies = {Context.ProducerName for Signal in Context.Gate.Inputs if SetPlacementCommitState(Context, 'ProducerName', Context.SignalProducerNames.get(Signal)) in Context.ClusterNames}
                if not Context.Dependencies.issubset(Context.LocalLevels):
                    continue
                Context.LocalLevels[Context.Name] = 1 + max((Context.LocalLevels[Dependency] for Dependency in Context.Dependencies), default=-1)
                Context.Remaining.remove(Context.Name)
                Context.Progress = True
            if Context.Progress:
                continue
            for Context.Name in sorted(Context.Remaining):
                Context.LocalLevels[Context.Name] = 0
            break
        Context.OrderedNames = sorted(Context.Names, key=lambda Name: (Context.LocalLevels[Name], Name))
        if Context.PackedMode and Context.CachedBaseLayout is not None:
            Context.FoldColumns = 1
            Context.FoldRows = 1
            Context.PackedWidth = Context.CachedWidth
            Context.PackedDepth = Context.CachedDepth
        elif Context.PackedMode and Context.ReuseAccepted:
            Context.FoldColumns = 1
            Context.FoldRows = 1
            Context.PackedWidth = max((Context.LocalPositions[Name][0] + RotatedCellSize('NAND', Context.LocalRotations[Name])[0] for Name in Context.Names))
            Context.PackedDepth = max((Context.LocalPositions[Name][1] + RotatedCellSize('NAND', Context.LocalRotations[Name])[1] for Name in Context.Names))
        elif Context.PackedMode:
            Context.NamesByLevel: dict[int, list[str]] = {}
            for Context.Name in Context.OrderedNames:
                Context.NamesByLevel.setdefault(Context.LocalLevels[Context.Name], []).append(Context.Name)
            Context.FoldRows = max(Context.NamesByLevel) + 1
            Context.PackedXByName: dict[str, int] = {}
            Context.PackedMirrorByName: dict[str, bool] = {}
            Context.RefinedClusterFallback: tuple[dict[str, tuple[int, int]], dict[str, int], dict[str, bool]] | None = None
            Context.ClusterRowPitchZ = Context.CellPitchZ + 1 if Context.ClusterRefinementProfile is not None else Context.CellPitchZ
            for Context.Row in range(Context.FoldRows):
                CheckWork(Context, 'row-beam', ClusterIndex=Context.ClusterIndex, CompletedRows=Context.Row, TotalRows=Context.FoldRows)
                Context.RowNames = Context.NamesByLevel.get(Context.Row, [])
                Context.RowNames.sort(key=lambda Name: (median([Context.PackedXByName[Context.Producer] + 1 for Signal in Context.InternalByName[Name].Inputs if SetPlacementCommitState(Context, 'Producer', Context.SignalProducerNames.get(Signal)) in Context.PackedXByName] or [0]), Name))
                Context.RowBeam: list[tuple[tuple[int, int, tuple[int, ...], int, tuple[int, ...]], dict[str, tuple[int, bool]]]] = [((0, 0, (), 0, ()), {})]
                for Context.Name in Context.RowNames:
                    CheckWork(Context, 'row-beam-gate', ClusterIndex=Context.ClusterIndex, GateName=Context.Name)
                    Context.ParentItems = [(InputIndex, Context.PackedXByName[Context.Producer] + 1, Context.LocalLevels[Context.Producer] * Context.ClusterRowPitchZ + Context.NandDepth) for InputIndex, Signal in enumerate(Context.InternalByName[Context.Name].Inputs) if SetPlacementCommitState(Context, 'Producer', Context.SignalProducerNames.get(Signal)) in Context.PackedXByName]
                    Context.ParentOutputs = [Value[1] for Value in Context.ParentItems]
                    Context.CandidateXs = {OutputX + InputAlignment for OutputX in Context.ParentOutputs or [0] for InputAlignment in (0, -2)}
                    Context.CandidateXs.update((Value + Shift for Value in tuple(Context.CandidateXs) for Shift in (-10, -5, 5, 10)))
                    if Context.CutDrivenRefinementCluster:
                        Context.RowCenter = int(median(Context.ParentOutputs or [0]))
                        Context.CandidateXs.update((Context.RowCenter + Lane * (Context.NandWidth + 1) for Lane in range(-len(Context.RowNames), len(Context.RowNames) + 1)))
                    Context.NextBeam = []
                    for Context.PreviousKey, Context.Assigned in Context.RowBeam:
                        for Context.CandidateX in sorted(Context.CandidateXs):
                            if any((abs(Context.CandidateX - ExistingX) < 4 for ExistingX, _ExistingMirror in Context.Assigned.values())):
                                continue
                            Context.ExistingGates = [BuildPlacedGate(Context.InternalByName[ExistingName], ExistingX, 1, Context.LocalLevels[ExistingName] * Context.ClusterRowPitchZ, Context.PackedRotation, Context.PackedMirrorByName[ExistingName]) for ExistingName, ExistingX in Context.PackedXByName.items()]
                            Context.ExistingGates.extend((BuildPlacedGate(Context.InternalByName[ExistingName], ExistingX, 1, Context.Row * Context.ClusterRowPitchZ, Context.PackedRotation, ExistingMirror) for ExistingName, (ExistingX, ExistingMirror) in Context.Assigned.items()))
                            Context.OrientationOptions = []
                            for Context.MirrorX in (False, True):
                                Context.CandidateGate = BuildPlacedGate(Context.InternalByName[Context.Name], Context.CandidateX, 1, Context.Row * Context.ClusterRowPitchZ, Context.PackedRotation, Context.MirrorX)
                                if any((PcbGatesConflict(Context.CandidateGate, ExistingGate) for ExistingGate in Context.ExistingGates)):
                                    continue
                                Context.Pins = (Context.CandidateX, Context.CandidateX + 2) if not Context.MirrorX else (Context.CandidateX + 2, Context.CandidateX)
                                Context.Misses = tuple((abs(OutputX - Context.Pins[InputIndex]) for InputIndex, OutputX, _OutputZ in Context.ParentItems))
                                Context.InputZ = Context.Row * Context.ClusterRowPitchZ - 1
                                Context.CrossPenalty = sum((1 for InputIndex, OutputX, OutputZ in Context.ParentItems for OtherIndex, OtherPinX in enumerate(Context.Pins) if OtherIndex != InputIndex and OutputZ == Context.InputZ and (abs(OutputX - OtherPinX) <= 1)))
                                Context.OrientationOptions.append((Context.CrossPenalty, sum(Context.Misses), Context.Misses, Context.MirrorX))
                            if not Context.OrientationOptions:
                                continue
                            Context.CrossPenalty, Context.Miss, Context.Misses, Context.CandidateMirror = min(Context.OrientationOptions)
                            Context.Candidate = dict(Context.Assigned)
                            Context.Candidate[Context.Name] = (Context.CandidateX, Context.CandidateMirror)
                            Context.Values = tuple(sorted((ExistingX for ExistingX, _MirrorX in Context.Candidate.values())))
                            Context.Span = max(Context.Values) - min(Context.Values) + Context.NandWidth
                            Context.NextBeam.append(((Context.PreviousKey[0] + Context.CrossPenalty, Context.PreviousKey[1] + Context.Miss, Context.PreviousKey[2] + Context.Misses, Context.Span, Context.Values), Context.Candidate))
                    Context.NextBeam.sort(key=lambda Value: Value[0])
                    Context.RowBeam = Context.NextBeam[:Context.PackingPolicy.BeamWidth]
                if not Context.RowBeam:
                    if Context.CutDrivenRefinementCluster:
                        Context.GraphCorePortfolio = BuildPinAlignedPackedClusterPortfolio(Context.Names, Context.InternalByName, Context.PackingPolicy.BeamWidth, WorkCheck=Context.WorkCheck)
                        Context.GraphCoreCandidateIndex = Context.JointPlacementCandidateIndex % max(1, Context.GraphCorePortfolio.RawCandidateCount)
                        Context.RefinedClusterFallback = BuildPinAlignedPackedCluster(Context.Names, Context.InternalByName, Context.PackingPolicy.BeamWidth, CandidateIndex=Context.GraphCoreCandidateIndex, WorkCheck=Context.WorkCheck)
                        if Context.RefinedClusterFallback is not None:
                            break
                    raise ValueError(f'Could not pack NAND cluster row {Context.ClusterIndex}:{Context.Row}')
                Context.PackedXByName.update({Name: X for Name, (X, _MirrorX) in Context.RowBeam[0][1].items()})
                Context.PackedMirrorByName.update({Name: MirrorX for Name, (_X, MirrorX) in Context.RowBeam[0][1].items()})
            if Context.RefinedClusterFallback is None:
                Context.MinimumPackedX = min(Context.PackedXByName.values())
                for Context.Name in Context.OrderedNames:
                    Context.LocalPositions[Context.Name] = (Context.PackedXByName[Context.Name] - Context.MinimumPackedX, Context.LocalLevels[Context.Name] * Context.ClusterRowPitchZ)
                    Context.LocalRotations[Context.Name] = Context.PackedRotation
                    Context.LocalMirrors[Context.Name] = Context.PackedMirrorByName[Context.Name]
            if Context.PackingPolicy.GraphBeamEnabled and Context.RefinedClusterFallback is None:
                Context.GraphCorePortfolio = BuildPinAlignedPackedClusterPortfolio(Context.Names, Context.InternalByName, Context.PackingPolicy.BeamWidth, WorkCheck=Context.WorkCheck)
                Context.GraphCoreCandidateIndex = Context.JointPlacementCandidateIndex % max(1, Context.GraphCorePortfolio.RawCandidateCount)
                Context.BeamPacked = BuildPinAlignedPackedCluster(Context.Names, Context.InternalByName, Context.PackingPolicy.BeamWidth, CandidateIndex=Context.GraphCoreCandidateIndex, WorkCheck=Context.WorkCheck)
            else:
                Context.BeamPacked = Context.RefinedClusterFallback
            if Context.BeamPacked is not None:
                Context.BeamPositions, Context.BeamRotations, Context.BeamMirrors = Context.BeamPacked
                Context.LocalPositions.update(Context.BeamPositions)
                Context.LocalRotations.update(Context.BeamRotations)
                Context.LocalMirrors.update(Context.BeamMirrors)
            Context.FoldColumns = 1
            Context.PackedWidth = max((Context.LocalPositions[Name][0] + RotatedCellSize('NAND', Context.LocalRotations[Name])[0] for Name in Context.Names))
            Context.PackedDepth = max((Context.LocalPositions[Name][1] + RotatedCellSize('NAND', Context.LocalRotations[Name])[1] for Name in Context.Names))
        else:
            Context.FoldColumns = max(1, ceil(sqrt(len(Context.OrderedNames))))
            Context.FoldRows = ceil(len(Context.OrderedNames) / Context.FoldColumns)
            for Context.PositionIndex, Context.Name in enumerate(Context.OrderedNames):
                Context.Row = Context.PositionIndex // Context.FoldColumns
                Context.Offset = Context.PositionIndex % Context.FoldColumns
                Context.Column = Context.Offset if Context.Row % 2 == 0 else Context.FoldColumns - 1 - Context.Offset
                Context.LocalPositions[Context.Name] = (Context.Column * Context.CellPitchX, Context.Row * Context.CellPitchZ)
                Context.LocalRotations[Context.Name] = 270 if Context.Row % 2 == 0 else 90
        if Context.PackedMode and Context.CachedBaseLayout is None:
            _PackedClusterBaseLayoutCache[Context.BaseLayoutCacheKey] = (Context.ClusterStructuralSignatures[Context.ClusterIndex], Context.ClusterReuseSources.get(Context.ClusterIndex), dict(Context.ClusterStructuralMappings.get(Context.ClusterIndex, {})), {Name: Context.LocalPositions[Name] for Name in Context.Names}, {Name: Context.LocalRotations[Name] for Name in Context.Names}, {Name: Context.LocalMirrors.get(Name, False) for Name in Context.Names}, Context.PackedWidth, Context.PackedDepth)
        if Context.PackedMode and Context.RequiredRelocationSignals:
            Context.LocalPositions, Context.LocalMirrors, Context.AccessRepairDiagnostics = RepairPackedClusterAccess(Context.Names, Context.InternalByName, Context.LocalPositions, Context.LocalRotations, Context.LocalMirrors, Context.RequiredRelocationSignals, min(Context.PackingPolicy.BeamWidth, 16), IncludeNearPortalConflicts=ShouldIncludeNearPortalPackedAccessRepair(RelocationVariant=Context.RelocationVariant, EnableInternalPinBankGeometryRepair=Context.EnableInternalPinBankGeometryRepair), WorkCheck=Context.WorkCheck)
            if Context.AccessRepairDiagnostics:
                Context.PackedAccessRepairByCluster[Context.ClusterIndex] = Context.AccessRepairDiagnostics
                Context.PackedWidth = max((Context.LocalPositions[Name][0] + RotatedCellSize('NAND', Context.LocalRotations[Name])[0] for Name in Context.Names))
                Context.PackedDepth = max((Context.LocalPositions[Name][1] + RotatedCellSize('NAND', Context.LocalRotations[Name])[1] for Name in Context.Names))
        if ShouldExpandBoundaryEscapeGeometry(PackedMode=Context.PackedMode, ClusterIndex=Context.ClusterIndex, BoundaryEscapeRelocationClusters=Context.BoundaryEscapeRelocationClusters, PackedAccessRepairClusters=frozenset(Context.PackedAccessRepairByCluster), RequiredRelocationSignals=Context.RequiredRelocationSignals, RelocationVariant=Context.RelocationVariant, RelocationPrioritySignalCount=len(Context.RelocationPrioritySignals), LocalGeometryRepairClusters=Context.LocalGeometryRepairClusters, StructuredAssignmentCutRelocation=Context.RequiresStructuredJointRelocation):
            Context.BoundaryEscapeGap = max(Context.PackingPolicy.LocalGeometryRepairColumnGap, Context.RoutingSpacing) if len(Context.Clusters) > 4 else Context.PackingPolicy.LocalGeometryRepairColumnGap
            if Context.RelocationVariant % 2:
                Context.DistinctZ = sorted({Context.LocalPositions[Name][1] for Name in Context.Names})
                Context.ZOffset = {Value: Index * Context.BoundaryEscapeGap for Index, Value in enumerate(Context.DistinctZ)}
                for Context.Name in Context.Names:
                    Context.LocalX, Context.LocalZ = Context.LocalPositions[Context.Name]
                    Context.LocalPositions[Context.Name] = (Context.LocalX, Context.LocalZ + Context.ZOffset[Context.LocalZ])
            else:
                Context.DistinctX = sorted({Context.LocalPositions[Name][0] for Name in Context.Names})
                Context.XOffset = {Value: Index * Context.BoundaryEscapeGap for Index, Value in enumerate(Context.DistinctX)}
                for Context.Name in Context.Names:
                    Context.LocalX, Context.LocalZ = Context.LocalPositions[Context.Name]
                    Context.LocalPositions[Context.Name] = (Context.LocalX + Context.XOffset[Context.LocalX], Context.LocalZ)
            Context.PackedWidth = max((Context.LocalPositions[Name][0] + RotatedCellSize('NAND', Context.LocalRotations[Name])[0] for Name in Context.Names))
            Context.PackedDepth = max((Context.LocalPositions[Name][1] + RotatedCellSize('NAND', Context.LocalRotations[Name])[1] for Name in Context.Names))
        if Context.PackedMode:
            Context.CandidateGates = [BuildPlacedGate(Context.InternalByName[Name], Context.LocalPositions[Name][0], 1, Context.LocalPositions[Name][1], Context.LocalRotations[Name], Context.LocalMirrors.get(Name, False)) for Name in Context.Names]
            if any((PcbGatesConflict(First, Second) for Index, First in enumerate(Context.CandidateGates) for Second in Context.CandidateGates[Index + 1:])):
                raise ValueError(f'Could not pack NAND cluster {Context.ClusterIndex} legally')
        Context.ClusterSizes[Context.ClusterIndex] = (Context.PackedWidth if Context.PackedMode else (Context.FoldColumns - 1) * Context.CellPitchX + Context.NandWidth, Context.PackedDepth if Context.PackedMode else (Context.FoldRows - 1) * Context.CellPitchZ + Context.NandDepth)
    if Context.PackedMode and Context.PackingPolicy.EnableVerticalClusterStacking:
        CheckWork(Context, 'vertical-stacking-start')
        Context.UnrepairedRequiredRelocationClusters: frozenset[int] = frozenset()
        Context.ClusterByGate = {Name: ClusterIndex for ClusterIndex, Names in enumerate(Context.Clusters) for Name in Names}
        Context.InterClusterWeights: dict[tuple[int, int], int] = {}
        for Context.Gate in Context.Module.Gates:
            Context.TargetCluster = Context.ClusterByGate.get(Context.Gate.Name)
            if Context.TargetCluster is None:
                continue
            for Context.Signal in Context.Gate.Inputs:
                Context.SourceCluster = Context.ClusterByGate.get(Context.SignalProducerNames.get(Context.Signal, ''))
                if Context.SourceCluster is None or Context.SourceCluster == Context.TargetCluster:
                    continue
                Context.Edge = (Context.SourceCluster, Context.TargetCluster)
                Context.InterClusterWeights[Context.Edge] = Context.InterClusterWeights.get(Context.Edge, 0) + 1
        Context.MaximumClusterStack = Context.PackingPolicy.MaximumClustersPerStack
        Context.StackByCluster: dict[int, int] = {}
        Context.StackMembers: dict[int, list[int]] = {}
        Context.NextStackId = 0
        Context.RepeatedStructuralClusters = len(Context.Clusters) >= 4 and len({Context.ClusterStructuralSignatures.get(ClusterIndex) for ClusterIndex in range(len(Context.Clusters))}) == 1
        Context.WeakInterClusterChain = bool(Context.InterClusterWeights) and max(Context.InterClusterWeights.values()) <= 2
        Context.PlanarRepeatedClusterPlacement = Context.RepeatedStructuralClusters and Context.WeakInterClusterChain
        if Context.PlanarRepeatedClusterPlacement and (not Context.PackingPolicy.EnableRepeatedStructuralVerticalStacking):
            Context.MaximumClusterStack = 1
        Context.OrderedInterClusterWeights = sorted(Context.InterClusterWeights.items(), key=lambda Value: (-Value[1], Value[0]))
        for Context.EdgeIndex, ((Context.Source, Context.Target), Context.Weight) in enumerate(Context.OrderedInterClusterWeights):
            CheckWork(Context, 'vertical-stacking', CompletedEdges=Context.EdgeIndex, TotalEdges=len(Context.OrderedInterClusterWeights))
            if Context.Source in Context.UnrepairedRequiredRelocationClusters or Context.Target in Context.UnrepairedRequiredRelocationClusters:
                if Context.ClusterStructuralSignatures.get(Context.Source) == Context.ClusterStructuralSignatures.get(Context.Target):
                    Context.StackSuppressedRelocationClusters.update((Context.Source, Context.Target))
                continue
            if Context.Weight < 1 or Context.MaximumClusterStack < 2 or Context.ClusterStructuralSignatures.get(Context.Source) != Context.ClusterStructuralSignatures.get(Context.Target):
                continue
            Context.SourceStack = Context.StackByCluster.get(Context.Source)
            Context.TargetStack = Context.StackByCluster.get(Context.Target)
            if Context.SourceStack is None and Context.TargetStack is None:
                Context.StackId = Context.NextStackId
                Context.NextStackId += 1
                Context.StackMembers[Context.StackId] = [Context.Source, Context.Target]
                Context.StackByCluster[Context.Source] = Context.StackId
                Context.StackByCluster[Context.Target] = Context.StackId
                Context.Assignment[Context.Target] = Context.Assignment[Context.Source]
                continue
            if Context.SourceStack is not None and Context.TargetStack is not None:
                if Context.SourceStack == Context.TargetStack:
                    continue
                Context.SourceFirst, Context.SourceLast = StackEndpoints(Context, Context.SourceStack)
                Context.TargetFirst, Context.TargetLast = StackEndpoints(Context, Context.TargetStack)
                if Context.Source not in (Context.SourceFirst, Context.SourceLast):
                    continue
                if Context.Target not in (Context.TargetFirst, Context.TargetLast):
                    continue
                MergeStacks(Context, SourceStack=Context.SourceStack, SourceEndpoint=Context.Source, RightStack=Context.TargetStack, TargetEndpoint=Context.Target)
                continue
            Context.ActiveStack = Context.SourceStack if Context.SourceStack is not None else Context.TargetStack
            Context.Candidate = Context.Target if Context.SourceStack is not None else Context.Source
            Context.Endpoint = Context.Source if Context.SourceStack is not None else Context.Target
            if len(Context.StackMembers[Context.ActiveStack]) >= Context.MaximumClusterStack:
                continue
            Context.FirstEndpoint, Context.LastEndpoint = StackEndpoints(Context, Context.ActiveStack)
            if Context.Endpoint not in (Context.FirstEndpoint, Context.LastEndpoint):
                continue
            AddCluster(Context, Context.ActiveStack, Context.Endpoint, Context.Candidate)
        for Context.ClusterIndex in range(len(Context.Clusters)):
            Context.StackId = Context.StackByCluster.get(Context.ClusterIndex)
            if Context.StackId is None:
                Context.ClusterStackIds[Context.ClusterIndex] = None
                Context.ClusterStackLevels[Context.ClusterIndex] = 0
            else:
                Context.Members = Context.StackMembers[Context.StackId]
                Context.ClusterStackIds[Context.ClusterIndex] = Context.StackId
                Context.ClusterStackLevels[Context.ClusterIndex] = Context.Members.index(Context.ClusterIndex)
        for Context.ClusterIndex in range(len(Context.Clusters)):
            Context.ClusterStackIds.setdefault(Context.ClusterIndex, None)
            Context.ClusterStackLevels.setdefault(Context.ClusterIndex, 0)
        Context.UsedColumns = sorted({Slot[0] for Slot in Context.Assignment.values()})
        Context.CompactColumn = {Column: Index for Index, Column in enumerate(Context.UsedColumns)}
        Context.Assignment = {ClusterIndex: (Context.CompactColumn[Column], Row) for ClusterIndex, (Column, Row) in Context.Assignment.items()}
        Context.ColumnCount = len(Context.UsedColumns)
    else:
        Context.ClusterStackIds = {Index: None for Index in range(len(Context.Clusters))}
        Context.ClusterStackLevels = {Index: 0 for Index in range(len(Context.Clusters))}


def PrepareExactPlacementSearch(Context):
    Context.RankedRequiredRelocationClusters = tuple((ClusterIndex for ClusterIndex in PrioritizeRelocationClusters(Context.Module, Context.Clusters, Context.RequiredRelocationSignals) if ClusterIndex not in Context.PackedAccessRepairByCluster and ClusterIndex not in Context.LocalGeometryRepairClusters))
    Context.RequiredRelocationLimit = min(6, len(Context.RankedRequiredRelocationClusters)) if Context.RequiresStructuredJointRelocation and len(Context.RequiredRelocationSignals) > 2 else 2 if Context.RequiresStructuredJointRelocation else 1
    Context.RequiredRelocationPriority = Context.RankedRequiredRelocationClusters[:Context.RequiredRelocationLimit]
    if Context.LocalGeometryRepairClusters:
        Context.RequiredRelocationPriority = ()
    Context.CurrentRelocationPriority = PrioritizeRelocationClusters(Context.Module, Context.Clusters, Context.RelocationPrioritySignals or Context.RelocationSignals)
    Context.PreviousFrontierSignals = frozenset((Signal for Cut in Context.TopologyCutFrontier[1:] for Signal in (*Cut.ConflictSignals, *Cut.RelocationSignals, *Cut.PriorityRelocationSignals, *Cut.NoCandidateSignals, *(Signal for Edge in Cut.PairwiseConflictEdges for Signal in Edge))))
    Context.PreviousFrontierPriority = PrioritizeRelocationClusters(Context.Module, Context.Clusters, Context.PreviousFrontierSignals)
    Context.FocusedCutEpochClusters = SelectFocusedTopologyFrontierClusters(Context.CurrentRelocationPriority, Context.PreviousFrontierPriority, Context.FocusedCutEpochPlacement)
    Context.FocusedInternalPinBankClusters = SelectFocusedCutEpochClusters(PrioritizeRelocationClusters(Context.Module, Context.Clusters, Context.InternalPinBankGeometrySignals), bool(Context.InternalPinBankGeometrySignals))
    Context.FocusedJointOptimizationClusters = Context.FocusedInternalPinBankClusters or Context.FocusedCutEpochClusters
    Context.OptionalRelocationPriority = tuple((ClusterIndex for ClusterIndex in Context.CurrentRelocationPriority if ClusterIndex not in Context.StackSuppressedRelocationClusters and ClusterIndex not in Context.RequiredRelocationPriority))
    if not Context.OptionalRelocationPriority:
        Context.OptionalRelocationPriority = tuple((ClusterIndex for ClusterIndex in Context.CurrentRelocationPriority if ClusterIndex not in Context.RequiredRelocationPriority))[:1]
    Context.MaximumOptionalRelocations = min(2, len(Context.OptionalRelocationPriority)) if Context.RelocationVariant > 2 and len(Context.RelocationPrioritySignals) > 2 else 0
    Context.OptionalRelocationClusters = tuple((Context.OptionalRelocationPriority[(Context.RelocationVariant + Offset) % len(Context.OptionalRelocationPriority)] for Offset in range(Context.MaximumOptionalRelocations))) if Context.OptionalRelocationPriority else ()
    Context.RelocationPriority = (*Context.RequiredRelocationPriority, *Context.OptionalRelocationClusters)
    Context.PhysicallyRelocatedClusters = frozenset(Context.RelocationPriority)
    Context.JointPortfolioRelocationOffset, Context.RotateExactPortfolioSlots = BuildJointPortfolioBaseRelocationControls(RelocationVariant=Context.RelocationVariant, JointPlacementCandidateIndex=Context.JointPlacementCandidateIndex, RequiresStructuredJointRelocation=Context.RequiresStructuredJointRelocation, PreservePortfolioBaseAssignment=Context.FocusedCutEpochPlacement)
    Context.BaseAssignment, Context.ColumnCount = RelocateClusterSlots(Context.Assignment, Context.ColumnCount, Context.RelocationPriority, RelocationOffset=Context.JointPortfolioRelocationOffset, RotateExactPortfolioSlots=Context.RotateExactPortfolioSlots, ForceDedicatedColumns=Context.RequiresStructuredJointRelocation and len(Context.RequiredRelocationSignals) > 2 and (Context.RelocationVariant > 0 or Context.JointPlacementCandidateIndex > 0))
    Context.AllVariantsByCluster = {ClusterIndex: tuple((TransformPackedClusterLayout(Names, Context.LocalPositions, Context.LocalRotations, Context.LocalMirrors, Rotation, MirrorX, GatesByName=Context.InternalByName) for Rotation, MirrorX in ((0, False), (0, True), (90, False), (90, True), (180, False), (180, True), (270, False), (270, True)))) for ClusterIndex, Names in enumerate(Context.Clusters)}
    Context.JointVariantDiagnostics = {str(ClusterIndex): [{'Rotation': Variant.Rotation, 'MirrorX': Variant.MirrorX, 'Legal': Variant.IsLegal, 'RejectionReason': Variant.RejectionReason} for Variant in Variants] for ClusterIndex, Variants in Context.AllVariantsByCluster.items()}
    Context.VariantsByCluster = {ClusterIndex: tuple((Variant for Variant in Variants if Variant.IsLegal)) for ClusterIndex, Variants in Context.AllVariantsByCluster.items()}
    Context.MissingLegalVariants = [ClusterIndex for ClusterIndex, Variants in Context.VariantsByCluster.items() if not Variants]
    if Context.MissingLegalVariants:
        raise ValueError('No exact-legal rigid transform for packed NAND cluster(s): ' + ','.join((str(Value) for Value in Context.MissingLegalVariants)))
    if Context.PackedMode and Context.PackingPolicy.EnableJointClusterOrientation:
        Context.JointCacheKey = BuildJointPlacementSearchCacheKey(Context.Module, Context.Clusters, Context.BaseAssignment, Context.PackingPolicy.JointPlacementBeamWidth, Context.PackingPolicy.JointPlacementPassLimit, Context.PackingPolicy.RetainedJointPlacementCandidates, Context.AssignmentCut, Context.AssignmentConstraints, Context.EnableClusterInterfacePlacementFeasibility, Context.FocusedJointOptimizationClusters, Context.TopologyCutFrontier)
        Context.CachedJointSearch = _JointPlacementSearchCache.get(Context.JointCacheKey)
        Context.CachedState = next((State for State in Context.CachedJointSearch['RetainedStates'] if int(State['CandidateIndex']) == Context.JointPlacementCandidateIndex), None) if Context.CachedJointSearch is not None else None
        if Context.CachedState is None:
            Context.Assignment, Context.SelectedClusterVariants, Context.JointPlacementDiagnostics = OptimizeJointClusterPlacement(Context.Module, Context.Clusters, Context.Levels, Context.VariantsByCluster, Context.PackingPolicy.JointPlacementBeamWidth, Context.PackingPolicy.JointPlacementPassLimit, Context.PackingPolicy.RetainedJointPlacementCandidates, Context.JointPlacementCandidateIndex, InitialAssignment=Context.BaseAssignment, FixedSlotClusters=frozenset(Context.RequiredRelocationPriority), AssignmentCut=Context.AssignmentCut, AssignmentConstraints=Context.AssignmentConstraints, BoundaryContractCapacity=max(1, Context.MaximumBoundaryTerminals or len(Context.Clusters)) if Context.RequiresStructuredJointRelocation else 0, EnableClusterInterfacePlacementFeasibility=Context.EnableClusterInterfacePlacementFeasibility, FocusedOptimizationClusters=Context.FocusedJointOptimizationClusters if Context.FocusedJointOptimizationClusters else None, FrontierAssignmentCuts=Context.TopologyCutFrontier, LogicalComponentByGate=Context.LogicalComponentByGate, WorkCheck=Context.WorkCheck)
            _JointPlacementSearchCache[Context.JointCacheKey] = deepcopy(Context.JointPlacementDiagnostics)
        else:
            Context.Assignment = {int(ClusterIndex): tuple(Slot) for ClusterIndex, Slot in dict(Context.CachedState['Slots']).items()}
            Context.SelectedClusterVariants = {ClusterIndex: next((Variant for Variant in Context.VariantsByCluster[ClusterIndex] if (Variant.Rotation, Variant.MirrorX) == (int(dict(Context.CachedState['Transforms'])[str(ClusterIndex)]['Rotation']), bool(dict(Context.CachedState['Transforms'])[str(ClusterIndex)]['MirrorX'])))) for ClusterIndex in range(len(Context.Clusters))}
            Context.JointPlacementDiagnostics = deepcopy(Context.CachedJointSearch)
            Context.JointPlacementDiagnostics['SearchCacheHit'] = True
            Context.JointPlacementDiagnostics['SelectedCandidateIndex'] = Context.JointPlacementCandidateIndex
            Context.JointPlacementDiagnostics['SelectedTransforms'] = deepcopy(Context.CachedState['Transforms'])
            Context.JointPlacementDiagnostics['SelectedScore'] = Context.CachedState.get('SearchScore', Context.JointPlacementDiagnostics.get('SelectedScore'))
            Context.JointPlacementDiagnostics['SelectedExactPairAdjacencyViolations'] = Context.CachedState.get('ExactPairAdjacencyViolations', Context.JointPlacementDiagnostics.get('SelectedExactPairAdjacencyViolations', 0))
            Context.JointPlacementDiagnostics['SelectedInterfacePairBankConflicts'] = Context.CachedState.get('InterfacePairBankConflicts', Context.JointPlacementDiagnostics.get('SelectedInterfacePairBankConflicts', 0))
            Context.JointPlacementDiagnostics['SelectedHigherOrderBankPressure'] = Context.CachedState.get('HigherOrderBankPressure', Context.JointPlacementDiagnostics.get('SelectedHigherOrderBankPressure', 0))
            for Context.MetricName in ('HigherOrderPeakBankDemand', 'HigherOrderBankExcessDemand', 'HigherOrderOverloadedBankCount'):
                Context.JointPlacementDiagnostics[f'Selected{Context.MetricName}'] = Context.CachedState.get(Context.MetricName, Context.JointPlacementDiagnostics.get(f'Selected{Context.MetricName}', 0))
            Context.JointPlacementDiagnostics['SelectedObservedInterfaceBankConflicts'] = Context.CachedState.get('ObservedInterfaceBankConflicts', Context.JointPlacementDiagnostics.get('SelectedObservedInterfaceBankConflicts', 0))
            Context.JointPlacementDiagnostics['SelectedInterfaceFacingMismatches'] = Context.CachedState.get('InterfaceFacingMismatches', Context.JointPlacementDiagnostics.get('SelectedInterfaceFacingMismatches', 0))
            Context.JointPlacementDiagnostics['SelectedClusterInterfacePlacement'] = deepcopy(Context.CachedState.get('ClusterInterfacePlacement', Context.JointPlacementDiagnostics.get('SelectedClusterInterfacePlacement')))
        Context.JointPlacementDiagnostics['ClusterTransformVariants'] = Context.JointVariantDiagnostics
        Context.JointPlacementDiagnostics['AssignmentCut'] = Context.AssignmentCut.ToDictionary() if Context.AssignmentCut is not None else None
        Context.JointPlacementDiagnostics['AssignmentConstraints'] = Context.AssignmentConstraints.ToDictionary()
        Context.JointPlacementDiagnostics['TopologyCutFrontier'] = [{'AssignmentCutFingerprint': Cut.ConflictFingerprint, 'AssignmentCutWorkFingerprint': Cut.EffectiveWorkFingerprint} for Cut in Context.TopologyCutFrontier]
        Context.JointPlacementDiagnostics['FocusedOptimizationClusters'] = sorted(Context.FocusedJointOptimizationClusters)
        Context.ColumnCount = max((Column for Column, _Row in Context.Assignment.values()), default=-1) + 1
        for Context.ClusterIndex, Context.Variant in Context.SelectedClusterVariants.items():
            Context.LocalPositions.update(Context.Variant.Positions)
            Context.LocalRotations.update(Context.Variant.Rotations)
            Context.LocalMirrors.update(Context.Variant.Mirrors)
            Context.ClusterSizes[Context.ClusterIndex] = (Context.Variant.Width, Context.Variant.Depth)
        Context.MirroredRelocationClusters = frozenset()
    else:
        Context.Assignment = Context.BaseAssignment
        Context.SelectedClusterVariants = {ClusterIndex: Variants[0] for ClusterIndex, Variants in Context.VariantsByCluster.items()}
        Context.MirroredRelocationClusters = frozenset((ClusterIndex for ClusterIndex in Context.RelocationPriority if ClusterIndex not in Context.PackedAccessRepairByCluster)) if Context.RelocationVariant > 0 and Context.RelocationPriority else frozenset()
    for Context.ClusterIndex in Context.RequiredRelocationPriority:
        Context.ClusterStackIds[Context.ClusterIndex] = None
        Context.ClusterStackLevels[Context.ClusterIndex] = 0
    Context.ColumnWidths = {Column: max((Context.ClusterSizes[Index][0] for Index, Slot in Context.Assignment.items() if Slot[0] == Column), default=1) for Column in range(Context.ColumnCount)}
    Context.RowDepths = {Row: max((Context.ClusterSizes[Index][1] for Index, Slot in Context.Assignment.items() if Slot[1] == Row), default=1) for Row in range(max((Slot[1] for Slot in Context.Assignment.values()), default=0) + 1)}
    Context.GapPlan = BuildInterClusterGapPlan(BuildInterClusterBoundaryDemand(Context.Module, Context.Clusters, Context.Assignment, WorkCheck=Context.WorkCheck), ColumnCount=Context.ColumnCount, RowCount=len(Context.RowDepths), RoutingSpacing=Context.RoutingSpacing, TrackPitch=Context.PlacementPolicy.DemandAwareBoundaryTrackPitch if Context.PlacementPolicy is not None and Context.PlacementPolicy.DemandAwareBoundaryTrackPitch > 0 else DefaultRedstoneRoutingTechnology.TrackPitch, Enabled=bool(Context.PlacementPolicy is not None and Context.PlacementPolicy.EnableDemandAwareInterClusterSpacing))
    Context.ColumnExtraSpacing = Context.GapPlan.ColumnSpacingByBoundary()
    Context.RowExtraSpacing = Context.GapPlan.RowSpacingByBoundary()
    Context.ColumnOrigins: dict[int, int] = {}
    Context.NextX = 0
    Context.ColumnGap = 2 if Context.PackedMode else 3
    Context.RowGap = 1 if Context.PackedMode else 2
    for Context.Column in range(Context.ColumnCount):
        Context.ColumnOrigins[Context.Column] = Context.NextX
        Context.NextX += Context.ColumnWidths[Context.Column]
        if Context.Column + 1 < Context.ColumnCount:
            Context.NextX += Context.ColumnGap + Context.ColumnExtraSpacing[Context.Column]
    Context.RowOrigins: dict[int, int] = {}
    Context.NextZ = 0
    for Context.Row in sorted(Context.RowDepths):
        Context.RowOrigins[Context.Row] = Context.NextZ
        Context.NextZ += Context.RowDepths[Context.Row]
        if Context.Row + 1 < len(Context.RowDepths):
            Context.NextZ += Context.RowGap + Context.RowExtraSpacing[Context.Row]
    Context.ExactStatePlacementCacheKey: tuple[object, ...] | None = None
    Context.ExactStatePlacementCacheFingerprint = ''
    Context.CachedExactStateGeometry: tuple[ExactStatePlacedGateGeometry, ...] | None = None
    Context.CachedExactStateCoreGeometry: tuple[ExactStatePlacedGateGeometry, ...] | None = None
    Context.SelectedExactMandatoryAccessProfile: MandatoryAccessConflictProfile | None = None


def EvaluateExactPlacementStates(Context):
    if Context.PackedMode and Context.PackingPolicy.EnableJointClusterOrientation:
        Context.VariantByTransform = {ClusterIndex: {(Variant.Rotation, Variant.MirrorX): Variant for Variant in Variants} for ClusterIndex, Variants in Context.VariantsByCluster.items()}
        Context.ExactScreenTrackPitch = Context.PlacementPolicy.DemandAwareBoundaryTrackPitch if Context.PlacementPolicy is not None and Context.PlacementPolicy.DemandAwareBoundaryTrackPitch > 0 else DefaultRedstoneRoutingTechnology.TrackPitch
        Context.ExactScreenDemandSpacing = bool(Context.PlacementPolicy is not None and Context.PlacementPolicy.EnableDemandAwareInterClusterSpacing)
        Context.RawRetainedStates = tuple((deepcopy(State) for State in Context.JointPlacementDiagnostics.get('RetainedStates', ())))
        Context.ExactScreenCacheKey = (Context.ModuleLayoutFingerprint, tuple((tuple(Names) for Names in Context.Clusters)), Context.RoutingSpacing, Context.ColumnGap, Context.RowGap, Context.PackingPolicy.ClusterDeckPitch, Context.ExactScreenTrackPitch, Context.ExactScreenDemandSpacing, tuple(sorted(Context.ClusterStackLevels.items())), tuple(((ClusterIndex, tuple(((Variant.Rotation, Variant.MirrorX, Variant.Width, Variant.Depth, tuple(sorted(Variant.Positions.items())), tuple(sorted(Variant.Rotations.items())), tuple(sorted(Variant.Mirrors.items()))) for Variant in Variants))) for ClusterIndex, Variants in sorted(Context.VariantsByCluster.items()))), tuple(((int(State['CandidateIndex']), State.get('SearchScore'), tuple(sorted(((int(ClusterIndex), tuple(Slot)) for ClusterIndex, Slot in dict(State['Slots']).items()))), tuple(sorted(((int(ClusterIndex), int(Transform['Rotation']), bool(Transform['MirrorX'])) for ClusterIndex, Transform in dict(State['Transforms']).items())))) for State in Context.RawRetainedStates)))
        Context.CachedExactScreen = _JointPlacementExactScreenCache.get(Context.ExactScreenCacheKey)
        Context.ExactScreenCacheHit = Context.CachedExactScreen is not None
        if Context.CachedExactScreen is None:
            Context.ScreenedRetainedStates: list[dict[str, object]] = []
            Context.CoreGeometryByCandidate: list[tuple[int, tuple[ExactStatePlacedGateGeometry, ...]]] = []
            for Context.StateOrdinal, Context.State in enumerate(Context.RawRetainedStates, start=1):
                CheckWork(Context, 'joint-exact-screen-state', CandidateIndex=Context.State['CandidateIndex'], CandidateOrdinal=Context.StateOrdinal, CandidateCount=len(Context.RawRetainedStates))
                Context._StateVariants, Context.Conflict, Context.ExactSpacing, Context.ExactSlots, Context.ExactSlotRepairs, Context.ExactCoreGeometry = FindExactStateConflict(Context, Context.State, Context.StateOrdinal, len(Context.RawRetainedStates))
                if Context.Conflict is None:
                    Context.CoreGeometryByCandidate.append((int(Context.State['CandidateIndex']), Context.ExactCoreGeometry))
                    Context.ScreenedRetainedStates.append({**Context.State, 'Slots': Context.ExactSlots, 'ExactLegal': True, 'ExactSpacing': Context.ExactSpacing, 'ExactSlotRepairs': Context.ExactSlotRepairs})
                    continue
                Context.First, Context.Second = Context.Conflict
                Context.Rejection = {'CandidateIndex': Context.State['CandidateIndex'], 'Reason': 'PcbGatesConflict', 'Members': [Context.First.Name, Context.Second.Name], 'Resource': [Context.First.X, Context.First.Y, Context.First.Z], 'Transforms': Context.State['Transforms'], 'Slots': Context.ExactSlots, 'ExactSlotRepairs': Context.ExactSlotRepairs}
                Context.ScreenedRetainedStates.append({**Context.State, 'Slots': Context.ExactSlots, 'ExactLegal': False, 'ExactSpacing': Context.ExactSpacing, 'ExactSlotRepairs': Context.ExactSlotRepairs, 'ExactRejection': Context.Rejection})
            Context.CachedExactScreen = ExactJointPlacementScreen(RetainedStates=tuple(deepcopy(Context.ScreenedRetainedStates)), CoreGeometryByCandidate=tuple(sorted(Context.CoreGeometryByCandidate)))
            _JointPlacementExactScreenCache[Context.ExactScreenCacheKey] = Context.CachedExactScreen
        else:
            Context.ScreenedRetainedStates = list(deepcopy(Context.CachedExactScreen.RetainedStates))
            CheckWork(Context, 'joint-exact-screen-cache-hit', CandidateCount=len(Context.ScreenedRetainedStates))
        if Context.MandatoryAccessPreScreenOnly and (not Context.CachedExactScreen.MandatoryProfileByCandidate):
            Context.ModuleGateByNameForMandatoryScreen = {Gate.Name: Gate for Gate in Context.Module.Gates}
            Context.MandatoryScreenSignalOrder = tuple(sorted({*Context.Module.Inputs, *Context.Module.Outputs, *(Signal for Gate in Context.Module.Gates for Signal in (*Gate.Inputs, *Gate.Outputs))}))
            Context.ProfilesBySearchCandidate: dict[int, MandatoryAccessConflictProfile] = {}
            for Context.State in Context.ScreenedRetainedStates:
                if not bool(Context.State.get('ExactLegal')):
                    continue
                Context.SearchCandidateIndex = int(Context.State['CandidateIndex'])
                Context.ExactCoreGeometry = Context.CachedExactScreen.CoreGeometry(Context.SearchCandidateIndex)
                if Context.ExactCoreGeometry is None:
                    continue
                CheckWork(Context, 'joint-exact-mandatory-access-screen', CandidateIndex=Context.SearchCandidateIndex, ScreenedCandidateCount=len(Context.ProfilesBySearchCandidate))
                Context.Profile = MeasureMandatoryAccessConflictProfile((Geometry.BuildPlacedGate(Context.ModuleGateByNameForMandatoryScreen[Geometry.Name]) for Geometry in Context.ExactCoreGeometry), Context.MandatoryScreenSignalOrder, WorkCheck=partial(CheckMandatoryAccessScreen, Context), Technology=Context.Technology)
                Context.ProfilesBySearchCandidate[Context.SearchCandidateIndex] = Context.Profile
                if not Context.EnableClusterInterfacePlacementFeasibility and (not Context.Profile.HasConflicts):
                    break
            if Context.EnableClusterInterfacePlacementFeasibility:
                Context.OrderedRetainedStates, Context.InterfacePortfolioAttrition = SelectExactInterfaceCommitStates(Context.ScreenedRetainedStates, Context.ProfilesBySearchCandidate, min(len(Context.ScreenedRetainedStates), Context.PackingPolicy.RetainedJointPlacementCandidates * 2))
            else:
                Context.OrderedRetainedStates = OrderExactStatesForMandatoryAccessCommit(Context.ScreenedRetainedStates, Context.ProfilesBySearchCandidate)
                Context.InterfacePortfolioAttrition = ()
            Context.OrderedCoreGeometryByCandidate = tuple(((int(State['CandidateIndex']), Context.Geometry) for State in Context.OrderedRetainedStates if SetPlacementCommitState(Context, 'Geometry', Context.CachedExactScreen.CoreGeometry(int(State['SearchCandidateIndex']))) is not None))
            Context.OrderedMandatoryProfilesByCandidate = tuple(((int(State['CandidateIndex']), Context.ProfilesBySearchCandidate[int(State['SearchCandidateIndex'])]) for State in Context.OrderedRetainedStates if int(State['SearchCandidateIndex']) in Context.ProfilesBySearchCandidate))
            Context.CachedExactScreen = ExactJointPlacementScreen(RetainedStates=tuple(deepcopy(Context.OrderedRetainedStates)), CoreGeometryByCandidate=Context.OrderedCoreGeometryByCandidate, MandatoryProfileByCandidate=Context.OrderedMandatoryProfilesByCandidate)
            _JointPlacementExactScreenCache[Context.ExactScreenCacheKey] = Context.CachedExactScreen
            Context.ScreenedRetainedStates = list(deepcopy(Context.CachedExactScreen.RetainedStates))
            Context.JointPlacementDiagnostics['InterfacePortfolioAttrition'] = list(Context.InterfacePortfolioAttrition)
            CheckWork(Context, 'joint-exact-mandatory-access-order-complete', ScreenedCandidateCount=len(Context.ProfilesBySearchCandidate), PromotedSearchCandidateIndex=int(Context.ScreenedRetainedStates[0].get('SearchCandidateIndex', Context.ScreenedRetainedStates[0]['CandidateIndex'])) if Context.ScreenedRetainedStates else -1)
        Context.ExactLegalRetainedStates = [State for State in Context.ScreenedRetainedStates if bool(State.get('ExactLegal'))]
        Context.ExactRejections = [deepcopy(State['ExactRejection']) for State in Context.ScreenedRetainedStates if not bool(State.get('ExactLegal')) and 'ExactRejection' in State]
        Context.JointPlacementDiagnostics['RetainedStates'] = Context.ScreenedRetainedStates
        Context.JointPlacementDiagnostics['ExactLegalRetainedStates'] = Context.ExactLegalRetainedStates
        Context.JointPlacementDiagnostics['ExactCandidateRejections'] = Context.ExactRejections
        Context.JointPlacementDiagnostics['ExactScreenCacheHit'] = Context.ExactScreenCacheHit
        Context.ExactScreenFingerprint = sha256(repr(Context.ExactScreenCacheKey).encode('utf-8')).hexdigest()
        Context.JointPlacementDiagnostics['ExactScreenFingerprint'] = Context.ExactScreenFingerprint
        Context.SelectedExactState = next((State for State in Context.ScreenedRetainedStates if int(State['CandidateIndex']) == Context.JointPlacementCandidateIndex), None)
        if Context.SelectedExactState is not None and bool(Context.SelectedExactState.get('ExactLegal')):
            Context.SelectedExactMandatoryAccessProfile = Context.CachedExactScreen.MandatoryProfile(Context.JointPlacementCandidateIndex)
            Context.CachedExactStateCoreGeometry = Context.CachedExactScreen.CoreGeometry(Context.JointPlacementCandidateIndex)
            Context.JointPlacementDiagnostics['SelectedSearchCandidateIndex'] = int(Context.SelectedExactState.get('SearchCandidateIndex', Context.JointPlacementCandidateIndex))
            Context.JointPlacementDiagnostics['SelectedScore'] = Context.SelectedExactState.get('SearchScore', Context.JointPlacementDiagnostics.get('SelectedScore'))
            Context.JointPlacementDiagnostics['SelectedTransforms'] = deepcopy(Context.SelectedExactState.get('Transforms', {}))
            Context.SelectedExactStateFingerprint = sha256(repr((Context.JointPlacementCandidateIndex, Context.SelectedExactState)).encode('utf-8')).hexdigest()
            Context.ExactStatePlacementCacheKey = (Context.ExactScreenFingerprint, Context.JointPlacementCandidateIndex, Context.SelectedExactStateFingerprint, repr(Context.PlacementPolicy), repr(Context.PackingPolicy), bool(Context.MandatoryAccessPreScreenOnly), bool(Context.PreferAccessRingTerminals), bool(Context.UseDerivedPerimeterTerminals), int(Context.DerivedTerminalLayoutVariantIndex), Context.RelocationVariant, tuple(sorted(Context.RelocationSignals)), tuple(sorted(Context.RelocationPrioritySignals)), tuple(sorted(Context.RequiredRelocationSignals)), tuple(sorted(Context.CoordinatedCandidateDiversificationSignals)), (Context.AssignmentCut.ConflictFingerprint, Context.AssignmentCut.EffectiveWorkFingerprint) if Context.AssignmentCut is not None else ('', ''), Context.AssignmentConstraints.Fingerprint)
            Context.ExactStatePlacementCacheFingerprint = sha256(repr(Context.ExactStatePlacementCacheKey).encode('utf-8')).hexdigest()
            Context.CachedExactStateGeometry = None if Context.UseDerivedPerimeterTerminals else _ExactStatePlacementGeometryCache.get(Context.ExactStatePlacementCacheKey)
            Context.JointPlacementDiagnostics['ExactStatePlacementCache'] = {'Key': Context.ExactStatePlacementCacheFingerprint, 'Hit': Context.CachedExactStateGeometry is not None, 'CandidateIndex': Context.JointPlacementCandidateIndex, 'StateFingerprint': Context.SelectedExactStateFingerprint, 'CachedGateCount': len(Context.CachedExactStateGeometry) if Context.CachedExactStateGeometry is not None else 0, 'CoreGeometryAvailable': Context.CachedExactStateCoreGeometry is not None, 'CoreGeometryCacheHit': Context.ExactScreenCacheHit and Context.CachedExactStateCoreGeometry is not None, 'CoreGateCount': len(Context.CachedExactStateCoreGeometry) if Context.CachedExactStateCoreGeometry is not None else 0}
            Context.Assignment = {int(ClusterIndex): tuple(Slot) for ClusterIndex, Slot in dict(Context.SelectedExactState['Slots']).items()}
            Context.SelectedTransforms = dict(Context.SelectedExactState['Transforms'])
            Context.SelectedClusterVariants = {ClusterIndex: Context.VariantByTransform[ClusterIndex][int(Context.SelectedTransforms[str(ClusterIndex)]['Rotation']), bool(Context.SelectedTransforms[str(ClusterIndex)]['MirrorX'])] for ClusterIndex in range(len(Context.Clusters))}
            for Context.ClusterIndex, Context.Variant in Context.SelectedClusterVariants.items():
                Context.LocalPositions.update(Context.Variant.Positions)
                Context.LocalRotations.update(Context.Variant.Rotations)
                Context.LocalMirrors.update(Context.Variant.Mirrors)
                Context.ClusterSizes[Context.ClusterIndex] = (Context.Variant.Width, Context.Variant.Depth)
            Context.ColumnCount = max((Column for Column, _Row in Context.Assignment.values()), default=-1) + 1
            Context.RowCount = max((Row for _Column, Row in Context.Assignment.values()), default=-1) + 1
            Context.ColumnWidths = {Column: max((Context.ClusterSizes[Index][0] for Index, Slot in Context.Assignment.items() if Slot[0] == Column), default=1) for Column in range(Context.ColumnCount)}
            Context.RowDepths = {Row: max((Context.ClusterSizes[Index][1] for Index, Slot in Context.Assignment.items() if Slot[1] == Row), default=1) for Row in range(Context.RowCount)}
            Context.SelectedExactSpacing = dict(Context.SelectedExactState['ExactSpacing'])
            Context.ColumnExtraSpacing = dict(Context.SelectedExactSpacing['Columns'])
            Context.RowExtraSpacing = dict(Context.SelectedExactSpacing['Rows'])
            Context.GapPlan = InterClusterGapPlan(Enabled=Context.ExactScreenDemandSpacing, RoutingSpacing=Context.RoutingSpacing, TrackPitch=Context.ExactScreenTrackPitch, ColumnExtraSpacing=tuple(sorted(Context.ColumnExtraSpacing.items())), RowExtraSpacing=tuple(sorted(Context.RowExtraSpacing.items())), BoundaryDemand=BuildInterClusterBoundaryDemand(Context.Module, Context.Clusters, Context.Assignment, WorkCheck=Context.WorkCheck))
            Context.JointPlacementDiagnostics['SelectedSlots'] = {str(ClusterIndex): list(Slot) for ClusterIndex, Slot in sorted(Context.Assignment.items())}
            Context.JointPlacementDiagnostics['SelectedExactSlotRepairs'] = deepcopy(Context.SelectedExactState.get('ExactSlotRepairs', ()))
            Context.ColumnOrigins = {}
            Context.NextX = 0
            for Context.Column in range(Context.ColumnCount):
                Context.ColumnOrigins[Context.Column] = Context.NextX
                Context.NextX += Context.ColumnWidths[Context.Column]
                if Context.Column + 1 < Context.ColumnCount:
                    Context.NextX += Context.ColumnGap + Context.ColumnExtraSpacing[Context.Column]
            Context.RowOrigins = {}
            Context.NextZ = 0
            for Context.Row in sorted(Context.RowDepths):
                Context.RowOrigins[Context.Row] = Context.NextZ
                Context.NextZ += Context.RowDepths[Context.Row]
                if Context.Row + 1 < len(Context.RowDepths):
                    Context.NextZ += Context.RowGap + Context.RowExtraSpacing[Context.Row]
        if Context.SelectedExactState is None or not bool(Context.SelectedExactState.get('ExactLegal')):
            Context.Rejection = Context.SelectedExactState.get('ExactRejection', {'Reason': 'not retained'}) if Context.SelectedExactState is not None else {'Reason': 'not retained'}
            raise ValueError(f'Exact joint placement candidate rejected: {Context.Rejection}')
    Context.InputMargin = 0
    Context.ModuleGateByName = {Gate.Name: Gate for Gate in Context.Module.Gates}
    if Context.CachedExactStateGeometry is not None:
        CheckWork(Context, 'exact-state-placement-cache-hit', CandidateIndex=Context.JointPlacementCandidateIndex, CachedGateCount=len(Context.CachedExactStateGeometry))
    elif Context.CachedExactStateCoreGeometry is not None:
        CheckWork(Context, 'exact-state-core-geometry-reused', CandidateIndex=Context.JointPlacementCandidateIndex, CachedGateCount=len(Context.CachedExactStateCoreGeometry), ExactScreenCacheHit=Context.ExactScreenCacheHit)
    Context.SelectedPlacedGateGeometry = Context.CachedExactStateGeometry if Context.CachedExactStateGeometry is not None else Context.CachedExactStateCoreGeometry
    Context.PlacedGates = [Geometry.BuildPlacedGate(Context.ModuleGateByName[Geometry.Name]) for Geometry in Context.SelectedPlacedGateGeometry] if Context.SelectedPlacedGateGeometry is not None else []
    if Context.SelectedPlacedGateGeometry is None:
        for Context.ClusterIndex, Context.Names in enumerate(Context.Clusters):
            CheckWork(Context, 'placement-commit', CompletedClusters=Context.ClusterIndex, TotalClusters=len(Context.Clusters))
            Context.SlotX, Context.SlotZ = Context.Assignment[Context.ClusterIndex]
            Context.BaseX = Context.InputMargin + Context.ColumnOrigins[Context.SlotX]
            Context.BaseZ = Context.RowOrigins[Context.SlotZ]
            Context.BaseY = 1 + (Context.ClusterStackLevels[Context.ClusterIndex] * Context.PackingPolicy.ClusterDeckPitch if Context.PackedMode else 0)
            Context.CandidateClusterGates = []
            for Context.Name in Context.Names:
                Context.LocalX, Context.LocalZ = Context.LocalPositions[Context.Name]
                Context.Rotation = Context.LocalRotations[Context.Name]
                Context.MirrorX = Context.LocalMirrors.get(Context.Name, False)
                if Context.ClusterIndex in Context.MirroredRelocationClusters:
                    Context.GateWidth = RotatedCellSize(Context.InternalByName[Context.Name].Kind.value, Context.Rotation)[0]
                    Context.LocalX = Context.ClusterSizes[Context.ClusterIndex][0] - Context.LocalX - Context.GateWidth
                    Context.MirrorX = not Context.MirrorX
                Context.CandidateClusterGates.append(BuildPlacedGate(Context.InternalByName[Context.Name], Context.BaseX + Context.LocalX, Context.BaseY, Context.BaseZ + Context.LocalZ, Context.Rotation, Context.MirrorX))
            if Context.PackedMode and (any((PcbGatesConflict(Candidate, Existing) for Candidate in Context.CandidateClusterGates for Existing in Context.PlacedGates)) or any((PcbGatesConflict(First, Second) for Index, First in enumerate(Context.CandidateClusterGates) for Second in Context.CandidateClusterGates[Index + 1:]))):
                raise ValueError(f'Packed NAND cluster {Context.ClusterIndex} conflicts at placement commit')
            Context.PlacedGates.extend(Context.CandidateClusterGates)

def PrepareTerminalPlacement(Context):
    Context.InputGates = [Gate for Gate in Context.Module.Gates if Gate.Kind.value == 'INPUT']
    Context.OutputGates = [Gate for Gate in Context.Module.Gates if Gate.Kind.value == 'OUTPUT']
    Context.ClusterByGate = {Name: ClusterIndex for ClusterIndex, Names in enumerate(Context.Clusters) for Name in Names} if Context.PackedMode else {}
    Context.TerminalConsumers: dict[str, list[Any]] = {}
    for Context.ModuleGate in Context.Module.Gates:
        for Context.Signal in Context.ModuleGate.Inputs:
            Context.TerminalConsumers.setdefault(Context.Signal, []).append(Context.ModuleGate)
    Context.InternalMinimumX = min((Gate.X for Gate in Context.PlacedGates))
    Context.InternalMaximumX = max((Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1 for Gate in Context.PlacedGates))
    Context.InternalMinimumZ = min((Gate.Z for Gate in Context.PlacedGates))
    Context.InternalMaximumZ = max((Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1 for Gate in Context.PlacedGates))
    Context.UseDerivedSingleComponentPlacement = Context.PackedMode and len(Context.Clusters) == 1 and Context.UseDerivedPerimeterTerminals
    Context.DerivedPerimeterSlotDomainValue: DerivedPerimeterSlotDomain | None = None
    Context.DerivedPerimeterSlotAssignmentValue: DerivedPerimeterSlotAssignment | None = None


def CommitTerminalPlacement(Context):
    if Context.MandatoryAccessPreScreenOnly:
        if Context.ExactStatePlacementCacheKey is not None and Context.CachedExactStateGeometry is None:
            Context.CachedExactStateGeometry = tuple((ExactStatePlacedGateGeometry.FromPlacedGate(Gate) for Gate in Context.PlacedGates))
            _ExactStatePlacementGeometryCache[Context.ExactStatePlacementCacheKey] = Context.CachedExactStateGeometry
            Context.JointPlacementDiagnostics['ExactStatePlacementCache']['CachedGateCount'] = len(Context.CachedExactStateGeometry)
        Context.SignalOrder = tuple(sorted({*Context.Module.Inputs, *Context.Module.Outputs, *(Signal for Gate in Context.Module.Gates for Signal in (*Gate.Inputs, *Gate.Outputs))}))
        Context.PreScreenDiagnostics: dict[str, object] = {'__InterClusterGaps__': Context.GapPlan.ToDictionary(), '__MandatoryAccessPreScreen__': {'Enabled': True, 'TerminalsIncluded': False, 'JointPlacementCandidateIndex': Context.JointPlacementCandidateIndex, 'PlacedGateCount': len(Context.PlacedGates), 'SignalCount': len(Context.SignalOrder)}}
        if Context.JointPlacementDiagnostics:
            Context.PreScreenDiagnostics['__JointClusterPlacement__'] = deepcopy(Context.JointPlacementDiagnostics)
        if Context.PackedAccessRepairByCluster:
            Context.PreScreenDiagnostics['__PackedAccessRepair__'] = {str(ClusterIndex): deepcopy(Diagnostics) for ClusterIndex, Diagnostics in sorted(Context.PackedAccessRepairByCluster.items())}
        return PcbPlacement(Placed=PlacedDesign(Module=Context.Module, PlacedGates=list(Context.PlacedGates), LocalRouteDiagnostics=Context.PreScreenDiagnostics), Clusters=Context.Clusters, SignalOrder=Context.SignalOrder, LayerCount=Context.PlacementPolicy.MaximumRoutingLayers if Context.PlacementPolicy is not None else 0, MandatoryAccessPreScreenProfile=Context.SelectedExactMandatoryAccessProfile)


def BuildCommittedPlacedDesign(Context):
    Context.BasePlacement = list(Context.PlacedGates)
    Context.TerminalPortIndexes = {Signal: Index for Index, Signal in enumerate((*Context.Module.Inputs, *Context.Module.Outputs))}
    Context.PlannedTerminals = PlaceLocalizedTerminals(Context, [*Context.InputGates, *Context.OutputGates], Context.TerminalPortIndexes) if Context.PackingPolicy is not None and Context.CachedExactStateGeometry is None else None
    if Context.UseDerivedSingleComponentPlacement and Context.PlannedTerminals is None:
        raise ValueError('derived single-component terminal domain has no legal assignment')
    if Context.PlannedTerminals is not None:
        Context.CandidatePlacement = Context.BasePlacement + Context.PlannedTerminals
        try:
            if any((PcbGatesConflict(First, Second) for Index, First in enumerate(Context.CandidatePlacement) for Second in Context.CandidatePlacement[Index + 1:])):
                raise ValueError('localized terminal placement conflicts')
            Context._ = BuildPlacedCellGeometry(PlacedDesign(Module=Context.Module, PlacedGates=Context.CandidatePlacement))
            Context.PlacedGates = Context.CandidatePlacement
        except ValueError:
            Context.PlannedTerminals = None
    Context.PlacedGates = Context.BasePlacement + (Context.PlannedTerminals or [])
    Context.PlannedTerminalNames = {Gate.Name for Gate in Context.PlannedTerminals or []}
    Context.RemainingInputGates = [Gate for Gate in Context.InputGates if Context.CachedExactStateGeometry is None and Gate.Name not in Context.PlannedTerminalNames]
    Context.RemainingOutputGates = [Gate for Gate in Context.OutputGates if Context.CachedExactStateGeometry is None and Gate.Name not in Context.PlannedTerminalNames]
    if Context.RemainingInputGates:
        Context.RemainingInputSignals = {Gate.Outputs[0] for Gate in Context.RemainingInputGates}
        PlaceTerminalBank(Context, Context.RemainingInputGates, Context.InternalMinimumZ - 4, -1, [Signal for Signal in Context.Module.Inputs if Signal in Context.RemainingInputSignals], LocalizeByInternalPins=not Context.PackedMode and Context.PackingPolicy is not None)
    if Context.RemainingOutputGates:
        Context.RemainingOutputSignals = {Gate.Inputs[0] for Gate in Context.RemainingOutputGates}
        PlaceTerminalBank(Context, Context.RemainingOutputGates, Context.InternalMaximumZ + 2, 1, [Signal for Signal in Context.Module.Outputs if Signal in Context.RemainingOutputSignals], LocalizeByInternalPins=not Context.PackedMode and Context.PackingPolicy is not None)
    if Context.PackedMode and any((PcbGatesConflict(First, Second) for Index, First in enumerate(Context.PlacedGates) for Second in Context.PlacedGates[Index + 1:])):
        raise ValueError('Packed placement conflicts at final commit')
    if Context.ExactStatePlacementCacheKey is not None and Context.CachedExactStateGeometry is None and (not Context.UseDerivedSingleComponentPlacement):
        Context.CachedExactStateGeometry = tuple((ExactStatePlacedGateGeometry.FromPlacedGate(Gate) for Gate in Context.PlacedGates))
        _ExactStatePlacementGeometryCache[Context.ExactStatePlacementCacheKey] = Context.CachedExactStateGeometry
        Context.JointPlacementDiagnostics['ExactStatePlacementCache']['CachedGateCount'] = len(Context.CachedExactStateGeometry)
    CheckWork(Context, 'terminal-placement-complete', GateCount=len(Context.PlacedGates))
    Context.Placed = PlacedDesign(Module=Context.Module, PlacedGates=Context.PlacedGates, DerivedPerimeterSlotDomain=Context.DerivedPerimeterSlotDomainValue, DerivedPerimeterSlotAssignment=Context.DerivedPerimeterSlotAssignmentValue)

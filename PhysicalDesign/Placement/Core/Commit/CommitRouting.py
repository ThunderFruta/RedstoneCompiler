"""Behavior-preserving phases of the final placement commit."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from PhysicalDesign.Geometry.Rotation import RotatedCellSize
from PhysicalDesign.Geometry.Placement import PlacedDesign
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology
from PhysicalDesign.Redstone.Actions.Geometry import BuildPlacedCellGeometryWithKeepOut
from PhysicalDesign.Redstone.Actions.Validation import ValidateTemplateIsolation
from PhysicalDesign.Resources.ResourceGraph import LocalRouteClaim, NormalizeRoutingEdge, RoutingResourceGraph, ValidateLocalRouteClaims
from ..Cache import _ClusterLocalRouteTemplateCache
from ..Channels import AssignBoundaryDemandSides, BoundaryDemandRecord, BuildBoundaryCapacityRecords, BuildClusterBoundaryBundles, BuildClusterBoundaryLeaseRequests, BuildLegalBoundaryEscapeSlots, EvaluateCutBoundaryEscapeFeasibility, EvaluateHardBoundaryFeasibility, LocalClusterRouteCandidate, SelectJointLocalClusterCandidates, ValidateHardBoundaryFeasibility
from ..Clusters import ClusterLocalRouteTemplate, ClusterLocalRouteTemplateCacheEntry, PackedNandCluster, PcbPlacement, TranslateClusterLocalRouteClaim
from ..Compactness import CompactWeightedPlacement
from ..Constraints import BuildAssignmentCutHigherOrderSignalSet
from ..Costs import AddPcbRoutingGuides
from ..Search import ShouldReleasePartialLocalTreeBeforeSearch
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


def RouteCommittedClusterTemplates(Context):
    if Context.PackedMode:
        Context.Producers = {Signal: Gate for Gate in Context.PlacedGates if Gate.OutputPin is not None for Signal in Gate.Outputs}
        Context.TargetsBySignal: dict[str, list[tuple[int, int, int]]] = {}
        for Context.Gate in Context.PlacedGates:
            CheckWork(Context, 'local-access-geometry', GateName=Context.Gate.Name)
            for Context.InputIndex, Context.Signal in enumerate(Context.Gate.Inputs):
                Context.TargetsBySignal.setdefault(Context.Signal, []).append(Context.Gate.InputPins[Context.InputIndex])
        Context.FrozenNetWires = {}
        Context.LocalNetBranches = {}
        Context.LocalNetTargets = {}
        Context.LocalRouteClaims = []
        Context.LocalRouteDiagnostics = {}
        Context.JointLocalCandidatesByCluster: dict[int, dict[str, list[LocalClusterRouteCandidate]]] = {}
        Context.LocalRouteDiagnostics['__InterClusterGaps__'] = Context.GapPlan.ToDictionary()
        if Context.ClusterRefinementProfile is not None:
            Context.LocalRouteDiagnostics['__CutDrivenClusterRefinement__'] = {**Context.ClusterRefinementProfile.ToDictionary(), 'Signals': list(Context.ClusterRefinementProfile.Signals), 'ClusterCount': len(Context.Clusters)}
        if Context.JointPlacementDiagnostics:
            Context.LocalRouteDiagnostics['__JointClusterPlacement__'] = Context.JointPlacementDiagnostics
        if Context.PackedAccessRepairByCluster:
            Context.LocalRouteDiagnostics['__PackedAccessRepair__'] = {str(ClusterIndex): Diagnostics for ClusterIndex, Diagnostics in sorted(Context.PackedAccessRepairByCluster.items())}
        (
            Context.ActualBlocks,
            Context.ElectricalBlocks,
            Context.SolidBlocks,
            Context.TemplateElectricalKeepOutBlocks,
        ) = BuildPlacedCellGeometryWithKeepOut(Context.Placed)
        Context.LocalResourceGraph = RoutingResourceGraph(
            ActualBlocks=frozenset(Context.ActualBlocks),
            ElectricalBlocks=frozenset(Context.ElectricalBlocks),
            SolidBlocks=frozenset(Context.SolidBlocks),
            StaticKeepOutBlocks=frozenset(
                Context.TemplateElectricalKeepOutBlocks
            ),
        )
        Context.ClusterByGate = {Name: ClusterIndex for ClusterIndex, Names in enumerate(Context.Clusters) for Name in Names}
        Context.GateByInputPin = {Pin: Gate.Name for Gate in Context.PlacedGates for Pin in Gate.InputPins}
        Context.MaximumLength = Context.PackingPolicy.DirectConnectMaximumLength
        Context.MaximumLocalRouteLength = Context.PackingPolicy.MaximumLocalRouteLength
        Context.MinimumRouteX = min((Gate.X for Gate in Context.PlacedGates)) - Context.PackingPolicy.LocalRouteEnvelope
        Context.MaximumRouteX = max((Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] for Gate in Context.PlacedGates)) + Context.PackingPolicy.LocalRouteEnvelope
        Context.MinimumRouteZ = min((Gate.Z for Gate in Context.PlacedGates)) - Context.PackingPolicy.LocalRouteEnvelope
        Context.MaximumRouteZ = max((Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] for Gate in Context.PlacedGates)) + Context.PackingPolicy.LocalRouteEnvelope
        Context.MinimumRouteY = min((Gate.Y for Gate in Context.PlacedGates))
        Context.MaximumRouteY = max((Gate.Y for Gate in Context.PlacedGates)) + Context.PackingPolicy.LocalRouteEnvelope
        Context.AccessBySignal: dict[str, set[tuple[int, int, int]]] = {}
        Context.AccessByClusterSignal: dict[tuple[int, str], set[tuple[int, int, int]]] = {}
        Context.BoundaryAccessBySignal: dict[str, set[tuple[int, int, int]]] = {}
        for Context.Gate in Context.PlacedGates:
            Context.GateCluster = Context.ClusterByGate.get(Context.Gate.Name)
            if Context.Gate.OutputPin is not None and Context.Gate.OutputDirection is not None:
                for Context.Signal in Context.Gate.Outputs:
                    Context.OutputAccess = tuple(((Context.Gate.OutputPin[0] + Context.Gate.OutputDirection[0] * Offset, Context.Gate.OutputPin[1] + Context.Gate.OutputDirection[1] * Offset, Context.Gate.OutputPin[2] + Context.Gate.OutputDirection[2] * Offset) for Offset in range(3)))
                    Context.AccessBySignal.setdefault(Context.Signal, set()).update(Context.OutputAccess)
                    if Context.GateCluster is not None:
                        Context.AccessByClusterSignal.setdefault((Context.GateCluster, Context.Signal), set()).update(Context.OutputAccess)
                    Context.BoundaryAccessBySignal.setdefault(Context.Signal, set()).update(Context.OutputAccess[:2])
            for Context.Signal, Context.Pin, Context.Direction in zip(Context.Gate.Inputs, Context.Gate.InputPins, Context.Gate.InputDirections):
                Context.InputAccess = tuple(((Context.Pin[0] + Context.Direction[0] * Offset, Context.Pin[1] + Context.Direction[1] * Offset, Context.Pin[2] + Context.Direction[2] * Offset) for Offset in range(3)))
                Context.AccessBySignal.setdefault(Context.Signal, set()).update(Context.InputAccess)
                if Context.GateCluster is not None:
                    Context.AccessByClusterSignal.setdefault((Context.GateCluster, Context.Signal), set()).update(Context.InputAccess)
                Context.BoundaryAccessBySignal.setdefault(Context.Signal, set()).update(Context.InputAccess[:2])
        Context.AccessClaimsBySignal = {Signal: Context.LocalResourceGraph.BuildRouteClaims(Positions) for Signal, Positions in Context.BoundaryAccessBySignal.items() if Positions}
        Context.ClusterOrigins = {ClusterIndex: (min((Gate.X for Gate in Context.PlacedGates if Gate.Name in ClusterNames)), min((Gate.Y for Gate in Context.PlacedGates if Gate.Name in ClusterNames)), min((Gate.Z for Gate in Context.PlacedGates if Gate.Name in ClusterNames))) for ClusterIndex, ClusterNames in enumerate(Context.Clusters)}
        Context.ReusedLocalRouteSignals: set[str] = set()
        Context.TemplateReuseDiagnostics: dict[str, object] = {'Enabled': Context.EnableClusterLocalRouteReuse, 'Clusters': {}}
        if Context.EnableClusterLocalRouteReuse and (not Context.PlacementScoringOnly):
            for Context.ClusterIndex, Context.ClusterNames in enumerate(Context.Clusters):
                Context.CacheKey = BuildClusterLocalRouteTemplateCacheKey(Context, Context.ClusterIndex)
                Context.Template = _ClusterLocalRouteTemplateCache.get(Context.CacheKey)
                Context.ClusterDiagnostic: dict[str, object] = {'CacheKey': sha256(repr(Context.CacheKey).encode('utf-8')).hexdigest()}
                Context.TemplateReuseDiagnostics['Clusters'][str(Context.ClusterIndex)] = Context.ClusterDiagnostic
                if Context.Template is None:
                    Context.ClusterDiagnostic.update({'Cache': 'miss'})
                    continue
                Context.Delta = tuple((Context.ClusterOrigins[Context.ClusterIndex][Axis] - Context.Template.Origin[Axis] for Axis in range(3)))
                Context.TranslatedClaims = tuple((TranslateClusterLocalRouteClaim(Claim, Context.Delta) for Claim in Context.Template.Claims))
                try:
                    if not Context.TranslatedClaims:
                        raise ValueError('template has no internal claims')
                    for Context.Claim in Context.TranslatedClaims:
                        Context.Producer = Context.Producers.get(Context.Claim.Signal)
                        if Context.Claim.ClusterId != Context.ClusterIndex or Context.Producer is None or Context.Producer.OutputPin != Context.Claim.Root or any((Context.ClusterByGate.get(Context.GateByInputPin.get(Target)) != Context.ClusterIndex for Target in Context.Claim.ConnectedTargets)):
                            raise ValueError('instantiated local topology differs')
                        ValidateLocalSignalStrength(Context, Context.Claim)
                        ValidateLocalPhysicalConnectivity(Context, Context.Claim)
                        ValidateContinuationPortal(Context, Context.Claim, Context.TargetsBySignal.get(Context.Claim.Signal, []))
                        ValidateBoundaryEscapes(Context, Context.Claim)
                    ValidateLocalRouteClaims(Context.LocalResourceGraph, (*Context.LocalRouteClaims, *Context.TranslatedClaims))
                except ValueError as Error:
                    Context.ClusterDiagnostic.update({'Cache': 'rejected', 'Validation': str(Error)})
                    continue
                Context.LocalRouteClaims.extend(Context.TranslatedClaims)
                Context.ReusedLocalRouteSignals.update((Claim.Signal for Claim in Context.TranslatedClaims))
                Context.ClusterDiagnostic.update({'Cache': 'hit', 'Delta': list(Context.Delta), 'ReusedLocalClaimCount': len(Context.TranslatedClaims), 'RegeneratedBoundarySignals': sorted({Claim.Signal for Claim in Context.TranslatedClaims if not set(Context.TargetsBySignal.get(Claim.Signal, ())).issubset(Claim.ConnectedTargets)}), 'Validation': 'accepted'})
        if Context.EnableClusterLocalRouteReuse:
            Context.LocalRouteDiagnostics['__ClusterLocalRouteTemplates__'] = Context.TemplateReuseDiagnostics
        Context.LocalRouteSignals = () if Context.PlacementScoringOnly else sorted(Context.TargetsBySignal.items(), key=lambda Value: (0 if Context.Producers.get(Value[0]) is not None and Context.Producers[Value[0]].Kind == 'NAND' else 1, -len(set(Value[1])), Value[0]))
        for Context.Signal, Context.Targets in Context.LocalRouteSignals:
            if Context.Signal in Context.ReusedLocalRouteSignals:
                continue
            CheckWork(Context, 'local-route-signal', Signal=Context.Signal, TargetCount=len(Context.Targets))
            Context.Producer = Context.Producers.get(Context.Signal)
            if Context.Producer is None or not Context.Targets:
                continue
            Context.AllTargets = Context.Targets
            Context.ProducerCluster = Context.ClusterByGate.get(Context.Producer.Name)
            if Context.ProducerCluster is not None:
                Context.Targets = [Target for Target in Context.AllTargets if Context.ClusterByGate.get(Context.GateByInputPin.get(Target)) == Context.ProducerCluster]
            if not Context.Targets:
                continue
            if ShouldReleasePartialLocalTreeBeforeSearch(ClusterCount=len(Context.Clusters), HasRelocationSignals=bool(Context.RelocationSignals), LocalTargetCount=len(Context.Targets), TotalTargetCount=len(Context.AllTargets)):
                Context.LocalRouteDiagnostics.setdefault(Context.Signal, {}).update({'ReleasedForGlobalRelocation': Context.ProducerCluster if Context.ProducerCluster is not None else -1, 'ReleasedBeforeLocalSearch': True})
                continue
            Context.Root = Context.Producer.OutputPin
            Context.Paths = []
            Context.LocalTargets = []
            for Context.Target in Context.Targets:
                CheckWork(Context, 'local-route-direct-target', Signal=Context.Signal, Target=Context.Target)
                Context.DeltaX = Context.Target[0] - Context.Root[0]
                Context.DeltaY = Context.Target[1] - Context.Root[1]
                Context.DeltaZ = Context.Target[2] - Context.Root[2]
                Context.Distance = abs(Context.DeltaX) + abs(Context.DeltaY) + abs(Context.DeltaZ)
                if Context.Distance > Context.MaximumLength or sum((Value != 0 for Value in (Context.DeltaX, Context.DeltaY, Context.DeltaZ))) > 1:
                    continue
                Context.Step = (0 if Context.DeltaX == 0 else 1 if Context.DeltaX > 0 else -1, 0 if Context.DeltaY == 0 else 1 if Context.DeltaY > 0 else -1, 0 if Context.DeltaZ == 0 else 1 if Context.DeltaZ > 0 else -1)
                Context.Paths.append(tuple(((Context.Root[0] + Context.Step[0] * Offset, Context.Root[1] + Context.Step[1] * Offset, Context.Root[2] + Context.Step[2] * Offset) for Offset in range(Context.Distance + 1))))
                Context.LocalTargets.append(Context.Target)
            Context.DirectPaths = list(Context.Paths)
            Context.DirectTargets = list(Context.LocalTargets)
            Context.OwnedNodes = {Position for Path in Context.Paths for Position in Path} or {Context.Root}
            Context.RemainingTargets = sorted(set(Context.Targets) - set(Context.LocalTargets), key=lambda Target: (min((abs(Target[0] - Position[0]) + abs(Target[1] - Position[1]) + abs(Target[2] - Position[2]) for Position in Context.OwnedNodes)), Target))
            for Context.Target in Context.RemainingTargets if Context.MaximumLocalRouteLength > Context.MaximumLength else ():
                CheckWork(Context, 'local-route-search-target', Signal=Context.Signal, Target=Context.Target)
                Context.Distance = min((abs(Context.Target[0] - Position[0]) + abs(Context.Target[1] - Position[1]) + abs(Context.Target[2] - Position[2]) for Position in Context.OwnedNodes))
                if Context.Distance > Context.MaximumLocalRouteLength:
                    continue
                Context.Path = FindLocalPath(Context, Context.OwnedNodes, Context.Target, Context.Signal)
                if not Context.Path:
                    continue
                Context.Paths.append(Context.Path)
                Context.OwnedNodes.update(Context.Path)
                Context.LocalTargets.append(Context.Target)
            if not Context.Paths:
                continue
            Context.Nodes = frozenset((Position for Path in Context.Paths for Position in Path))
            Context.Edges = frozenset((NormalizeRoutingEdge(First, Second) for Path in Context.Paths for First, Second in zip(Path, Path[1:])))
            Context.ClusterCandidates = [Context.ClusterByGate[Context.Name] for Target in Context.LocalTargets if SetPlacementCommitState(Context, 'Name', Context.GateByInputPin.get(Target)) in Context.ClusterByGate]
            Context.ClusterId = Context.ProducerCluster if Context.ProducerCluster is not None else min(Context.ClusterCandidates, default=-1)
            Context.CandidateClaim = LocalRouteClaim(Signal=Context.Signal, ClusterId=Context.ClusterId, Root=Context.Root, ConnectedTargets=tuple(sorted(set(Context.LocalTargets))), BoundaryNodes=SelectBoundaryNodes(Context, Context.Nodes, Context.AllTargets, Context.LocalTargets), Nodes=Context.Nodes, Edges=Context.Edges, Claims=Context.LocalResourceGraph.BuildRouteClaims(Context.Nodes), ExactRouteSignalBlocks=len(Context.Nodes), ExactRouteSupportBlocks=len({(X, Y - 1, Z) for X, Y, Z in Context.Nodes} - Context.ActualBlocks))
            Context.TrialClaims = (*Context.LocalRouteClaims, Context.CandidateClaim)
            try:
                ValidateLocalSignalStrength(Context, Context.CandidateClaim)
                ValidateLocalPhysicalConnectivity(Context, Context.CandidateClaim)
                ValidateContinuationPortal(Context, Context.CandidateClaim, Context.AllTargets)
                ValidateBoundaryEscapes(Context, Context.CandidateClaim)
                ValidateLocalRouteClaims(Context.LocalResourceGraph, Context.TrialClaims)
                ValidateTemplateIsolation(
                    {Context.Signal: set(Context.CandidateClaim.Nodes)},
                    Context.ActualBlocks,
                    Context.ElectricalBlocks,
                    Context.SolidBlocks,
                    Context.Producers,
                    Context.TargetsBySignal,
                    Context.AccessBySignal,
                    Context.TemplateElectricalKeepOutBlocks,
                )
            except ValueError as Error:
                Context.LocalRouteDiagnostics[Context.Signal] = {'AttemptedTargets': len(set(Context.LocalTargets)), 'AttemptedNodes': len(Context.Nodes), 'Rejected': str(Error)}
                if not Context.DirectPaths or len(Context.DirectPaths) == len(Context.Paths):
                    continue
                Context.Paths = Context.DirectPaths
                Context.LocalTargets = Context.DirectTargets
                Context.Nodes = frozenset((Position for Path in Context.Paths for Position in Path))
                Context.Edges = frozenset((NormalizeRoutingEdge(First, Second) for Path in Context.Paths for First, Second in zip(Path, Path[1:])))
                Context.CandidateClaim = LocalRouteClaim(Signal=Context.Signal, ClusterId=Context.ClusterId, Root=Context.Root, ConnectedTargets=tuple(sorted(set(Context.LocalTargets))), BoundaryNodes=SelectBoundaryNodes(Context, Context.Nodes, Context.AllTargets, Context.LocalTargets), Nodes=Context.Nodes, Edges=Context.Edges, Claims=Context.LocalResourceGraph.BuildRouteClaims(Context.Nodes), ExactRouteSignalBlocks=len(Context.Nodes), ExactRouteSupportBlocks=len({(X, Y - 1, Z) for X, Y, Z in Context.Nodes} - Context.ActualBlocks))
                try:
                    ValidateLocalSignalStrength(Context, Context.CandidateClaim)
                    ValidateLocalPhysicalConnectivity(Context, Context.CandidateClaim)
                    ValidateContinuationPortal(Context, Context.CandidateClaim, Context.AllTargets)
                    ValidateBoundaryEscapes(Context, Context.CandidateClaim)
                    ValidateLocalRouteClaims(Context.LocalResourceGraph, (*Context.LocalRouteClaims, Context.CandidateClaim))
                except ValueError:
                    continue
            if Context.PackingPolicy.RequireCompleteLocalFanoutClaims and len(Context.Clusters) == 1 and (len(Context.LocalTargets) != len(Context.AllTargets)):
                Context.LocalRouteDiagnostics.setdefault(Context.Signal, {}).update({'ReleasedForCompleteFanout': Context.ClusterId})
                continue
            if len(Context.Clusters) > 4 and Context.RelocationSignals and (len(Context.LocalTargets) != len(Context.AllTargets)):
                Context.LocalRouteDiagnostics.setdefault(Context.Signal, {}).update({'ReleasedForGlobalRelocation': Context.ClusterId})
                continue
            Context.CandidateChoices = Context.JointLocalCandidatesByCluster.setdefault(Context.ClusterId, {}).setdefault(Context.Signal, [])
            Context.CandidateChoices.append(LocalClusterRouteCandidate(CandidateId=f'cluster{Context.ClusterId}:{Context.Signal}:tree:{len(Context.CandidateChoices)}', Claim=Context.CandidateClaim))
            if Context.DirectPaths and tuple(Context.DirectTargets) != tuple(Context.LocalTargets) and (len(Context.CandidateChoices) < Context.PackingPolicy.MaximumLocalRouteCandidatesPerSignal):
                Context.DirectNodes = frozenset((Position for Path in Context.DirectPaths for Position in Path))
                Context.DirectEdges = frozenset((NormalizeRoutingEdge(First, Second) for Path in Context.DirectPaths for First, Second in zip(Path, Path[1:])))
                Context.DirectClaim = LocalRouteClaim(Signal=Context.Signal, ClusterId=Context.ClusterId, Root=Context.Root, ConnectedTargets=tuple(sorted(set(Context.DirectTargets))), BoundaryNodes=SelectBoundaryNodes(Context, Context.DirectNodes, Context.AllTargets, Context.DirectTargets), Nodes=Context.DirectNodes, Edges=Context.DirectEdges, Claims=Context.LocalResourceGraph.BuildRouteClaims(Context.DirectNodes), ExactRouteSignalBlocks=len(Context.DirectNodes), ExactRouteSupportBlocks=len({(X, Y - 1, Z) for X, Y, Z in Context.DirectNodes} - Context.ActualBlocks))
                try:
                    ValidateLocalSignalStrength(Context, Context.DirectClaim)
                    ValidateLocalPhysicalConnectivity(Context, Context.DirectClaim)
                    ValidateContinuationPortal(Context, Context.DirectClaim, Context.AllTargets)
                    ValidateBoundaryEscapes(Context, Context.DirectClaim)
                    ValidateLocalRouteClaims(Context.LocalResourceGraph, (Context.DirectClaim,))
                except ValueError as Error:
                    Context.LocalRouteDiagnostics.setdefault(Context.Signal, {}).setdefault('DirectCandidateRejected', str(Error))
                else:
                    Context.CandidateChoices.append(LocalClusterRouteCandidate(CandidateId=f'cluster{Context.ClusterId}:{Context.Signal}:direct:{len(Context.CandidateChoices)}', Claim=Context.DirectClaim))
            Context.LocalRouteDiagnostics.setdefault(Context.Signal, {}).update({'AcceptedTargets': len(set(Context.LocalTargets)), 'AcceptedNodes': len(Context.Nodes), 'UsedLongRoute': any((len(Path) - 1 > Context.MaximumLength for Path in Context.Paths))})
        if Context.PlacementScoringOnly:
            Context.LocalRouteDiagnostics['__DeferredLocalRouting__'] = {'Enabled': True, 'ScoringOnly': True, 'TerminalsIncluded': True, 'FixedPinAccessClaimsIncluded': True, 'LocalRouteCandidateSearchDeferred': True, 'LocalRoutePathSearchDeferred': True}
        elif Context.PackingPolicy.EnableJointLocalRouting:
            Context.JointDiagnostics: dict[str, object] = {'Enabled': True, 'CandidateLimitPerSignal': Context.PackingPolicy.MaximumLocalRouteCandidatesPerSignal, 'AssignmentExpansionLimit': Context.PackingPolicy.MaximumLocalClusterAssignmentExpansions, 'Clusters': {}}
            for Context.ClusterId, Context.CandidateMap in sorted(Context.JointLocalCandidatesByCluster.items()):
                Context.BaseClaims = tuple(Context.LocalRouteClaims)
                Context.LimitedCandidateMap = {Signal: tuple(Candidates[:Context.PackingPolicy.MaximumLocalRouteCandidatesPerSignal]) for Signal, Candidates in sorted(Context.CandidateMap.items())}
                Context.Selection = SelectJointLocalClusterCandidates(Context.LocalResourceGraph, Context.BaseClaims, Context.LimitedCandidateMap, Context.PackingPolicy.MaximumLocalClusterAssignmentExpansions)
                Context.SelectedClaims = tuple((Candidate.Claim for Candidate in Context.Selection.Candidates))
                Context.LocalRouteClaims.extend(Context.SelectedClaims)
                Context.JointDiagnostics['Clusters'][str(Context.ClusterId)] = {'AttemptedSignals': len(Context.LimitedCandidateMap), 'AttemptedCandidates': sum((len(Candidates) for Candidates in Context.LimitedCandidateMap.values())), 'SelectedCandidates': len(Context.Selection.Candidates), 'LocalizedTargets': sum((Candidate.LocalizedTargetCount for Candidate in Context.Selection.Candidates)), 'LocalRepeaters': sum((Candidate.RepeaterCount for Candidate in Context.Selection.Candidates)), 'RouteAndSupportBlocks': sum((Candidate.RouteAndSupportBlocks for Candidate in Context.Selection.Candidates)), 'AssignmentExpansions': Context.Selection.AssignmentExpansions, 'BudgetExhausted': Context.Selection.BudgetExhausted, 'RejectionCounts': Context.Selection.RejectionCounts}
            Context.FullyLocalizedSignals = {Claim.Signal for Claim in Context.LocalRouteClaims if set(Context.TargetsBySignal.get(Claim.Signal, ())).issubset(Claim.ConnectedTargets)}
            Context.JointDiagnostics['Aggregate'] = {'CandidateCount': sum((sum((len(Candidates) for Candidates in CandidateMap.values())) for CandidateMap in Context.JointLocalCandidatesByCluster.values())), 'LocalClaimCoverageBefore': 0, 'LocalClaimCoverageAfter': sum((len(Claim.Claims.ResourceIds) for Claim in Context.LocalRouteClaims)), 'SelectedClaimCount': len(Context.LocalRouteClaims), 'LocalizedTargetCount': sum((len(Claim.ConnectedTargets) for Claim in Context.LocalRouteClaims)), 'GlobalNetCountBefore': len(Context.TargetsBySignal), 'GlobalNetCountAfter': len(Context.TargetsBySignal) - len(Context.FullyLocalizedSignals), 'GlobalNetCountReduction': len(Context.FullyLocalizedSignals), 'EstimatedLocalVolume': sum((LocalClusterRouteCandidate('selected', Claim).FullVolume for Claim in Context.LocalRouteClaims))}
            Context.LocalRouteDiagnostics['__JointLocalRouting__'] = Context.JointDiagnostics
        else:
            for Context.CandidateMap in Context.JointLocalCandidatesByCluster.values():
                for Context.Candidates in Context.CandidateMap.values():
                    if Context.Candidates:
                        Context.LocalRouteClaims.append(Context.Candidates[0].Claim)
        for Context.CandidateClaim in Context.LocalRouteClaims:
            Context.Signal = Context.CandidateClaim.Signal
            Context.LocalNetBranches[Context.Signal] = tuple(sorted(Context.CandidateClaim.Nodes))
            Context.LocalNetTargets[Context.Signal] = tuple(sorted(Context.CandidateClaim.ConnectedTargets))
            if len(Context.CandidateClaim.ConnectedTargets) == len(Context.TargetsBySignal[Context.Signal]) and len(Context.CandidateClaim.Nodes) <= Context.PackingPolicy.MaximumFrozenLocalNetNodes and (len(Context.CandidateClaim.ConnectedTargets) <= Context.PackingPolicy.MaximumFrozenLocalTargets):
                Context.FrozenNetWires[Context.Signal] = Context.LocalNetBranches[Context.Signal]
        Context.Placed.FrozenNetWires = Context.FrozenNetWires
        Context.Placed.LocalNetBranches = Context.LocalNetBranches
        Context.Placed.LocalNetTargets = Context.LocalNetTargets
        Context.Placed.LocalRouteClaims = tuple(Context.LocalRouteClaims)
        if Context.RelocationSignals or Context.AssignmentCut is not None:
            Context.LocalRouteDiagnostics['__PlacementRelocation__'] = {'Signals': sorted(Context.RelocationSignals), 'PrioritySignals': sorted(Context.RelocationPrioritySignals), 'RequiredSignals': sorted(Context.RequiredRelocationSignals), 'Variant': Context.RelocationVariant, 'Clusters': sorted(Context.PhysicallyRelocatedClusters), 'MirroredClusters': sorted(Context.MirroredRelocationClusters), 'AssignmentCut': Context.AssignmentCut.ToDictionary() if Context.AssignmentCut is not None else None, 'ActivePlacementConstraints': Context.AssignmentConstraints.ToDictionary(), 'CoordinatedCandidateDiversificationSignals': sorted(Context.CoordinatedCandidateDiversificationSignals), 'CoordinatedCandidateDiversityLevel': 1 if Context.CoordinatedCandidateDiversificationSignals else 0, 'InternalPinBankGeometryRepair': {'Enabled': Context.EnableInternalPinBankGeometryRepair, 'Signals': sorted(Context.InternalPinBankGeometrySignals)}}
        Context.Placed.LocalRouteDiagnostics = Context.LocalRouteDiagnostics


def FinalizePlacementCommit(Context):
    if Context.RoutingSpacing == 0:
        Context.Placed = CompactWeightedPlacement(Context.Module, Context.Placed, MaximumPasses=Context.PlacementPolicy.CompactPassLimit if Context.PlacementPolicy is not None else 32, WorkCheck=Context.WorkCheck)
    Context.Guided = AddPcbRoutingGuides(Context.Placed, MaximumLayerCount=Context.PlacementPolicy.MaximumRoutingLayers if Context.PlacementPolicy is not None else 0)
    Context.GateByName = {Gate.Name: Gate for Gate in Context.PlacedGates}
    Context.ConsumersBySignal: dict[str, list[Any]] = {}
    Context.ProducersBySignal = {Signal: Gate for Gate in Context.Module.Gates for Signal in Gate.Outputs}
    for Context.Gate in Context.Module.Gates:
        for Context.Signal in Context.Gate.Inputs:
            Context.ConsumersBySignal.setdefault(Context.Signal, []).append(Context.Gate)
    Context.PackedClusters = []
    Context.ClaimsByCluster: dict[int, list[LocalRouteClaim]] = {}
    for Context.Claim in Context.Placed.LocalRouteClaims:
        Context.ClaimsByCluster.setdefault(Context.Claim.ClusterId, []).append(Context.Claim)
    Context.CutBoundaryEscapeSignals = frozenset(BuildAssignmentCutHigherOrderSignalSet(Context.AssignmentCut)) if Context.EnableClusterInterfacePlacementFeasibility else frozenset()
    Context.CutBoundaryEscapeDomains: dict[tuple[int, str], tuple[BoundaryEscapeCandidate, ...]] = {}
    for Context.ClusterIndex, Context.Names in enumerate(Context.Clusters):
        CheckWork(Context, 'boundary-capacity', CompletedClusters=Context.ClusterIndex, TotalClusters=len(Context.Clusters))
        Context.NameSet = set(Context.Names)
        Context.Produced = {Signal for Name in Context.Names for Signal in Context.InternalByName[Name].Outputs}
        Context.InternalSignals = {Signal for Signal in Context.Produced if any((Gate.Name in Context.NameSet for Gate in Context.ConsumersBySignal.get(Signal, ()))) and all((Gate.Name in Context.NameSet for Gate in Context.ConsumersBySignal.get(Signal, ())))}
        Context.BoundarySignals = {Signal for Name in Context.Names for Signal in (*Context.InternalByName[Name].Inputs, *Context.InternalByName[Name].Outputs) if Signal not in Context.InternalSignals}
        Context.DirectConnections = []
        for Context.Signal in sorted(Context.InternalSignals):
            Context.Producer = next((Context.GateByName[Name] for Name in Context.Names if Context.Signal in Context.GateByName[Name].Outputs))
            if any((Context.Producer.OutputPin in Consumer.InputPins for Consumer in (Context.GateByName[Gate.Name] for Gate in Context.ConsumersBySignal[Context.Signal]))):
                Context.DirectConnections.append(Context.Signal)
        Context.BaseX = min((Context.GateByName[Name].X for Name in Context.Names))
        Context.BaseZ = min((Context.GateByName[Name].Z for Name in Context.Names))
        Context.MaximumClusterX = max((Context.GateByName[Name].X + RotatedCellSize(Context.GateByName[Name].Kind, Context.GateByName[Name].Rotation)[0] - 1 for Name in Context.Names))
        Context.MaximumClusterZ = max((Context.GateByName[Name].Z + RotatedCellSize(Context.GateByName[Name].Kind, Context.GateByName[Name].Rotation)[1] - 1 for Name in Context.Names))
        Context.ClusterCenterX = (Context.BaseX + Context.MaximumClusterX) / 2
        Context.ClusterCenterZ = (Context.BaseZ + Context.MaximumClusterZ) / 2
        Context.BoundaryDemand = {Signal: max(1, sum((Consumer.Name not in Context.NameSet for Consumer in Context.ConsumersBySignal.get(Signal, ())))) for Signal in sorted(Context.BoundarySignals)}
        Context.BoundaryDemandRecords = tuple((BoundaryDemandRecord(Signal=Signal, UnresolvedTargets=Context.BoundaryDemand[Signal], RequiredPortalSlots=1, RequiredCorridorLanes=1, PreferredBoundarySide=PreferredBoundarySide(Context, Signal)) for Signal in sorted(Context.BoundarySignals)))
        Context.BoundaryPitch = Context.PlacementPolicy.DemandAwareBoundaryTrackPitch if Context.PlacementPolicy is not None and Context.PlacementPolicy.EnableDemandAwareInterClusterSpacing and (Context.PlacementPolicy.DemandAwareBoundaryTrackPitch > 0) else DefaultRedstoneRoutingTechnology.TrackPitch
        Context.BoundaryLayerCapacity = Context.PlacementPolicy.MaximumRoutingLayers if Context.PlacementPolicy is not None and Context.PlacementPolicy.MaximumRoutingLayers > 0 else DefaultRedstoneRoutingTechnology.MaximumRoutableLayerCount
        Context.GeometricCapacity = {'West': max(1, (Context.MaximumClusterZ - Context.BaseZ + 1) // Context.BoundaryPitch) * Context.BoundaryLayerCapacity, 'East': max(1, (Context.MaximumClusterZ - Context.BaseZ + 1) // Context.BoundaryPitch) * Context.BoundaryLayerCapacity, 'North': max(1, (Context.MaximumClusterX - Context.BaseX + 1) // Context.BoundaryPitch) * Context.BoundaryLayerCapacity, 'South': max(1, (Context.MaximumClusterX - Context.BaseX + 1) // Context.BoundaryPitch) * Context.BoundaryLayerCapacity}
        Context.LegalPortalSlotsBySide = dict(Context.GeometricCapacity)
        Context.LegalEscapeCandidateCounts: tuple[tuple[str, int], ...] = ()
        if Context.PackedMode:
            Context.AccessPositionsBySignal = {Signal: set(Context.AccessByClusterSignal.get((Context.ClusterIndex, Signal), ())) for Signal in Context.BoundarySignals}
            Context.BoundaryEscapeCandidatesBySignal: dict[str, list[BoundaryEscapeCandidate]] = {}
            Context.LegalEscapeSlotsBySignal = BuildLegalBoundaryEscapeSlots(Context.BoundarySignals, Context.AccessPositionsBySignal, Context.LocalResourceGraph, Context.AccessClaimsBySignal, WorkCheck=Context.WorkCheck, CandidateClaimsBySignal=Context.BoundaryEscapeCandidatesBySignal if Context.CutBoundaryEscapeSignals else None)
            for Context.Signal in sorted(Context.CutBoundaryEscapeSignals.intersection(Context.BoundarySignals)):
                Context.CutBoundaryEscapeDomains[Context.ClusterIndex, Context.Signal] = tuple(Context.BoundaryEscapeCandidatesBySignal.get(Context.Signal, ()))
            Context.HardBoundary = EvaluateHardBoundaryFeasibility(Context.ClusterIndex, Context.BoundaryDemandRecords, Context.LegalEscapeSlotsBySignal)
            Context.LegalEscapeCandidateCounts = Context.HardBoundary.LegalEscapeCandidateCounts
            ValidateHardBoundaryFeasibility(Context.HardBoundary)
            Context.SlotsBySide = {'West': set(), 'East': set(), 'North': set(), 'South': set()}
            for Context.X, Context.Y, Context.Z in {Slot for Slots in Context.LegalEscapeSlotsBySignal.values() for Slot in Slots}:
                Context.Side = min(((abs(Context.X - Context.BaseX), 'West'), (abs(Context.X - Context.MaximumClusterX), 'East'), (abs(Context.Z - Context.BaseZ), 'North'), (abs(Context.Z - Context.MaximumClusterZ), 'South')))[1]
                Context.SlotsBySide[Context.Side].add((Context.X, Context.Y, Context.Z))
            Context.LegalPortalSlotsBySide = {Side: len(Slots) for Side, Slots in Context.SlotsBySide.items()}
            Context.BoundaryDemandRecords = AssignBoundaryDemandSides(Context.BoundaryDemandRecords, Context.LegalEscapeSlotsBySignal, (Context.BaseX, Context.MaximumClusterX, Context.BaseZ, Context.MaximumClusterZ), {Side: min(Context.GeometricCapacity[Side], Context.LegalPortalSlotsBySide[Side]) for Side in Context.GeometricCapacity})
        Context.BoundaryCapacityRecords = BuildBoundaryCapacityRecords(Context.BoundaryDemandRecords, Context.GeometricCapacity, Context.LegalPortalSlotsBySide)
        Context.BoundaryOverflow = sum((Record.Overflow for Record in Context.BoundaryCapacityRecords))
        Context.ScarceSides = {Record.BoundarySide for Record in Context.BoundaryCapacityRecords if Record.Overflow > 0}
        Context.PinScarcityCount = sum((Record.PreferredBoundarySide in Context.ScarceSides for Record in Context.BoundaryDemandRecords))
        Context.LocalClaimTargets = sum((len(Claim.ConnectedTargets) for Claim in Context.ClaimsByCluster.get(Context.ClusterIndex, ())))
        Context.BoundaryTargetCount = sum(Context.BoundaryDemand.values())
        Context.PackedClusters.append(PackedNandCluster(ClusterId=Context.ClusterIndex, MemberNands=tuple(Context.Names), BoundarySignals=tuple(sorted(Context.BoundarySignals)), InternalSignals=tuple(sorted(Context.InternalSignals)), RelativePlacements={Name: (Context.GateByName[Name].X - Context.BaseX, Context.GateByName[Name].Z - Context.BaseZ, Context.GateByName[Name].Rotation, Context.GateByName[Name].MirrorX) for Name in Context.Names}, DirectConnections=tuple(Context.DirectConnections), LocalClaimSignals=tuple(sorted({Claim.Signal for Claim in Context.ClaimsByCluster.get(Context.ClusterIndex, ())})), BoundaryTerminals=tuple(sorted({Position for Claim in Context.ClaimsByCluster.get(Context.ClusterIndex, ()) for Position in Claim.BoundaryNodes})), ExactLocalRoutingBlocks=sum((Claim.ExactRoutingBlocks for Claim in Context.ClaimsByCluster.get(Context.ClusterIndex, ()))), GlobalEntrances=len(Context.BoundarySignals), StructuralSignature=Context.ClusterStructuralSignatures.get(Context.ClusterIndex, ''), ReusedFromClusterId=Context.ClusterReuseSources.get(Context.ClusterIndex), StructuralMapping=Context.ClusterStructuralMappings.get(Context.ClusterIndex), StackId=Context.ClusterStackIds.get(Context.ClusterIndex), StackLevel=Context.ClusterStackLevels.get(Context.ClusterIndex, 0), BaseY=1 if not Context.PackedMode else 1 + Context.ClusterStackLevels.get(Context.ClusterIndex, 0) * Context.PackingPolicy.ClusterDeckPitch, BoundaryDemand=Context.BoundaryDemand, EstimatedCorridorLanes=sum(Context.BoundaryDemand.values()), LocalClaimCoverage=Context.LocalClaimTargets / max(1, Context.LocalClaimTargets + Context.BoundaryTargetCount), BoundaryDemandRecords=Context.BoundaryDemandRecords, BoundaryCapacityRecords=Context.BoundaryCapacityRecords, BoundaryOverflow=Context.BoundaryOverflow, PinScarcityCount=Context.PinScarcityCount, LegalEscapeCandidateCounts=Context.LegalEscapeCandidateCounts, OrientationRotation=Context.SelectedClusterVariants[Context.ClusterIndex].Rotation, OrientationMirrorX=Context.SelectedClusterVariants[Context.ClusterIndex].MirrorX))
    if Context.CutBoundaryEscapeSignals:
        Context.CutBoundaryEscapeFeasibility = EvaluateCutBoundaryEscapeFeasibility(Context.CutBoundaryEscapeDomains, Context.CutBoundaryEscapeSignals)
        Context.Guided.Placed.LocalRouteDiagnostics.setdefault('__CutBoundaryEscapeFeasibility__', Context.CutBoundaryEscapeFeasibility.ToDictionary())
    if Context.Guided.Placed.LocalRouteDiagnostics is None:
        Context.Guided.Placed.LocalRouteDiagnostics = {}
    Context.Guided.Placed.LocalRouteDiagnostics['__ComponentGraph__'] = Context.LogicalComponentGraph.ToDictionary()
    CheckWork(Context, 'complete', ClusterCount=len(Context.Clusters))
    Context.BoundaryLeaseRequests = BuildClusterBoundaryLeaseRequests(BuildClusterBoundaryBundles(Context.Module, Context.Clusters), Context.Assignment, Module=Context.Module, Clusters=Context.Clusters, PlacedGates=Context.Guided.Placed.PlacedGates, IncludePrimaryTerminals=Context.EnableClusterInterfacePlacementFeasibility) if Context.PackedMode and Context.EnableClusterBoundaryLeases else ()
    Context.ClusterLocalRouteTemplates = tuple((ClusterLocalRouteTemplate(ClusterId=Cluster.ClusterId, StructuralSignature=Cluster.StructuralSignature, Rotation=Cluster.OrientationRotation, MirrorX=Cluster.OrientationMirrorX, Origin=(min((Gate.X for Gate in Context.Guided.Placed.PlacedGates if Gate.Name in Cluster.MemberNands)), Cluster.BaseY, min((Gate.Z for Gate in Context.Guided.Placed.PlacedGates if Gate.Name in Cluster.MemberNands))), LocalClaimFingerprint=sha256(repr(tuple(sorted(((Claim.Signal, Claim.Root, Claim.ConnectedTargets, Claim.BoundaryNodes, tuple(sorted(Claim.Nodes)), tuple(sorted(Claim.Edges))) for Claim in Context.Guided.Placed.LocalRouteClaims if Claim.ClusterId == Cluster.ClusterId)))).encode('utf-8')).hexdigest(), BoundaryTerminalFingerprint=sha256(repr(Cluster.BoundaryTerminals).encode('utf-8')).hexdigest(), ClaimCount=sum((Claim.ClusterId == Cluster.ClusterId for Claim in Context.Guided.Placed.LocalRouteClaims)), BoundaryTerminalCount=len(Cluster.BoundaryTerminals)) for Cluster in Context.PackedClusters)) if Context.PackedMode else ()
    if Context.PackedMode and (not Context.PlacementScoringOnly):
        for Context.ClusterIndex, Context.Cluster in enumerate(Context.PackedClusters):
            Context.Claims = tuple((Claim for Claim in Context.Guided.Placed.LocalRouteClaims if Claim.ClusterId == Context.ClusterIndex and all((Context.ClusterByGate.get(Context.GateByInputPin.get(Target)) == Context.ClusterIndex for Target in Claim.ConnectedTargets))))
            if not Context.Claims:
                continue
            Context.CacheKey = BuildClusterLocalRouteTemplateCacheKey(Context, Context.ClusterIndex)
            Context.LocalClaimFingerprint = sha256(repr(tuple(sorted(((Claim.Signal, Claim.Root, Claim.ConnectedTargets, Claim.BoundaryNodes, tuple(sorted(Claim.Nodes)), tuple(sorted(Claim.Edges))) for Claim in Context.Claims)))).encode('utf-8')).hexdigest()
            _ClusterLocalRouteTemplateCache[Context.CacheKey] = ClusterLocalRouteTemplateCacheEntry(CacheKey=Context.CacheKey, Origin=Context.ClusterOrigins[Context.ClusterIndex], Claims=Context.Claims, LocalClaimFingerprint=Context.LocalClaimFingerprint)
        if Context.EnableClusterLocalRouteReuse:
            Context.Guided.Placed.LocalRouteDiagnostics.setdefault('__ClusterLocalRouteTemplates__', {})['CacheEntryCount'] = len(_ClusterLocalRouteTemplateCache)
    return PcbPlacement(Placed=PlacedDesign(Module=Context.Guided.Placed.Module, PlacedGates=Context.Guided.Placed.PlacedGates, RouteGuides=Context.Guided.Placed.RouteGuides, RouteLayers=Context.Guided.Placed.RouteLayers, FrozenNetWires=Context.Guided.Placed.FrozenNetWires, LocalNetBranches=Context.Guided.Placed.LocalNetBranches, LocalNetTargets=Context.Guided.Placed.LocalNetTargets, LocalRouteClaims=Context.Guided.Placed.LocalRouteClaims, LocalRouteDiagnostics=Context.Guided.Placed.LocalRouteDiagnostics, DerivedPerimeterSlotDomain=Context.Guided.Placed.DerivedPerimeterSlotDomain, DerivedPerimeterSlotAssignment=Context.Guided.Placed.DerivedPerimeterSlotAssignment, ClusterBoundaryLeaseRequests=Context.BoundaryLeaseRequests, CompleteClusterInterfaceAccess=Context.EnableClusterInterfacePlacementFeasibility, ComponentGraph=Context.LogicalComponentGraph), Clusters=Context.Clusters, SignalOrder=Context.Guided.SignalOrder, LayerCount=Context.Guided.LayerCount, PackedClusters=tuple(Context.PackedClusters) if Context.PackedMode else (), ClusterBoundaryLeaseRequests=Context.BoundaryLeaseRequests, ClusterLocalRouteTemplates=Context.ClusterLocalRouteTemplates, CompleteClusterInterfaceAccess=Context.EnableClusterInterfacePlacementFeasibility, DerivedPerimeterSlotDomain=Context.Guided.Placed.DerivedPerimeterSlotDomain, DerivedPerimeterSlotAssignment=Context.Guided.Placed.DerivedPerimeterSlotAssignment, ComponentGraph=Context.LogicalComponentGraph)

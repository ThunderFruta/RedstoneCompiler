"""Cohesive preparation and finalization phases for one domain."""

from __future__ import annotations

from ..Components.Access import ValidateComponentAccessCertificateIdentity
from ..Components.Fabric import BuildComponentFabricAdjacency
from ..Components.Feedthroughs import BuildDeclaredComponentFeedthroughDomains
from ..Contracts.Component import PhysicalComponentChannelReservation
from ..Contracts.Component import PreparedPhysicalComponentFeedthroughEndpointDomain
from ..Failures import RoutingFailure
from ..Failures import RoutingFailureReason
from ..Failures import RoutingStageError
from ..Reliability import BuildStableFingerprint
from ..Technology import DefaultRedstoneRoutingTechnology
from collections import Counter
from dataclasses import replace
from typing import Any
from .PhysicalGuides import BuildComponentKeepoutAvoidingGlobalGuides, BuildComponentKeepoutGuideCellsByLayer, BuildExplicitPhysicalComponentFeedthrough, ExpandPhysicalComponentGuideChannels, PreparePhysicalComponentFeedthroughEndpointDomain, RemoveClosedComponentInternalGuides
from functools import partial
from .PortPreparationState import PortPreparationState, SetPortPreparationState
from .PortPreparationHelpers import BuildGlobalPathToGuide, DeduplicateCertifiedAccessCandidates, PrepareCertifiedPhysicalPortLocalSeams
from ..Contracts.Component import PhysicalExteriorApertureFabric
from ..Contracts.Core import Position2
from ..Contracts.Core import Position3
from ..Contracts.PhysicalInterface import PhysicalPortLaneFactor
from ..ResourceGraph import RoutingResourceClaims
from collections import defaultdict
from time import monotonic
from .ExteriorConnectors import FrozenPhysicalExteriorConnectorSearchRequest, PhysicalExteriorConnectorDistanceField, PhysicalExteriorConnectorPathResult, PreparedPhysicalGlobalApertureStaticContract, SearchFrozenPhysicalExteriorConnectorBatch
from .PhysicalGuides import BuildPhysicalExteriorApertureFabric, BuildPhysicalExteriorResourceGraphFingerprint

def ValidatePhysicalPortPreparation(Context):
    """Prepare and freeze the complete pre-assignment port factor domain."""
    if Context.Problem.Interface is None:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalComponentAssemblyPlanning', Detail='component preparation did not produce a closed interface'))
    Context.EffectiveLayerCount = max(1, int(Context.LayerCount if Context.LayerCount is not None else max(getattr(Context.CoarsePlan, 'Layers', {}).values(), default=0) + 1))
    Context.CoarsePlan = ExpandPhysicalComponentGuideChannels(Context.CoarsePlan, Context.EffectiveLayerCount)
    Context.ComponentPortSignals = frozenset((Value.Signal for Value in Context.Problem.Interface.Ports))
    Context.ComponentInternalSignals = frozenset(Context.Problem.ComponentSignals) - Context.ComponentPortSignals
    Context.CoarsePlan = RemoveClosedComponentInternalGuides(Context.CoarsePlan, Context.ComponentInternalSignals)
    Context.GuideOverflow = dict(getattr(Context.CoarsePlan, 'Overflow', {}) or {})
    Context.ComponentGuideOverflow = {Position: Count for Position, Count in Context.GuideOverflow.items() if any((int(Context.CoarsePlan.Layers.get(Signal, 0)) == Position[0] and (Position[1], Position[2]) in Context.CoarsePlan.Guides.get(Signal, ()) for Signal in Context.ComponentPortSignals))}
    Context.MissingGuideSignals = tuple(sorted((Signal for Signal in Context.ComponentPortSignals if not getattr(Context.CoarsePlan, 'Guides', {}).get(Signal))))
    Context.GuideFeasible = bool(Context.CoarsePlan is not None and (not Context.MissingGuideSignals))
    if Context.CoarsePlan is None or not Context.GuideFeasible:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentChannelCapacityUnsatisfiable, Stage='PhysicalComponentAssemblyPlanning', AffectedNets=tuple(Context.Problem.ComponentSignals), Detail='the authoritative global guide has no complete capacity-one component corridor assignment', Diagnostics={'GlobalGuidePrepared': Context.CoarsePlan is not None, 'GlobalGuideFeasible': bool(Context.GuideFeasible), 'GlobalGuideOverflowCount': len(Context.GuideOverflow), 'ComponentGuideOverflowCount': len(Context.ComponentGuideOverflow), 'MissingGuideSignals': list(Context.MissingGuideSignals), 'ComponentFabricConstructionComplete': True, 'OwnershipSearchComplete': True, 'ImplicitForeignTransitDomainCount': 0}))
    Context.ResourceGraph = Context.Resources.ResourceGraph
    if Context.ResourceGraph is None:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalComponentAssemblyPlanning', Detail='physical assembly planning requires a resource graph'))
    Context.PoweredSeamFabricAdjacency = BuildComponentFabricAdjacency(Context.Problem.Fabric)
    Context.PoweredSeamFabricParentCache: dict[Any, Any] = {}
    Context.PoweredSeamRouteClaimsCache: dict[Any, Any] = {}
    Context.PoweredSeamTreeRepeaterSubproblemCache: dict[Any, Any] = {}
    Context.PoweredSeamTreeRepeaterCacheStatistics: dict[str, int] = {}
    Context.PreliminaryComponentKeepoutNodes = frozenset((*Context.Problem.Fabric.Nodes, *(Position for Claim in Context.Problem.LocalClaims for Position in Claim.Nodes)))
    Context.PreliminaryEnvelopeMinimum = tuple((min((Value[Index] for Value in Context.PreliminaryComponentKeepoutNodes)) for Index in range(3))) if Context.PreliminaryComponentKeepoutNodes else (0, 0, 0)
    Context.PreliminaryEnvelopeMaximum = tuple((max((Value[Index] for Value in Context.PreliminaryComponentKeepoutNodes)) for Index in range(3))) if Context.PreliminaryComponentKeepoutNodes else (0, 0, 0)
    Context.KeepoutDetouredGlobalSignals: tuple[str, ...] = ()
    Context.ComponentGraph = getattr(Context.Placed, 'ComponentGraph', None)
    Context.ComponentGraphFingerprint = str(getattr(Context.ComponentGraph, 'StructuralFingerprint', ''))
    if Context.AccessCertificate is None:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentAccessCertificateIdentityMismatch, Stage='PhysicalComponentAssemblyPlanning', AffectedNets=tuple(Context.Problem.ComponentSignals), Detail='physical assembly planning requires an immutable component access certificate'))
    try:
        ValidateComponentAccessCertificateIdentity(Context.AccessCertificate, Context.Problem, Context.ResourceGraph, ComponentGraphFingerprint=Context.ComponentGraphFingerprint)
    except ValueError as Error:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentAccessCertificateIdentityMismatch, Stage='PhysicalComponentAssemblyPlanning', AffectedNets=tuple(Context.Problem.ComponentSignals), Detail=str(Error), Diagnostics={'AccessCertificateFingerprint': Context.AccessCertificate.CertificateFingerprint})) from Error
    if not Context.AccessCertificate.Complete or not Context.AccessCertificate.Feasible:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentAccessCertificationIncomplete if not Context.AccessCertificate.Complete else RoutingFailureReason.ComponentTerminalAccessUnsatisfiable, Stage='ComponentAccessCertification', AffectedNets=Context.AccessCertificate.AffectedSignals, Detail='component access certificate is incomplete' if not Context.AccessCertificate.Complete else 'component access certificate is infeasible', Diagnostics=Context.AccessCertificate.ToDictionary()))
    Context.CertifiedPortDomainBySignal = {Domain.Signal: Domain for Domain in Context.AccessCertificate.PortDomains}
    Context.AlignedGuideLayers = dict(Context.CoarsePlan.Layers)
    Context.CertifiedGuideLayerReassignmentsBySignal = {}
    Context.CertifiedPolicyLayerEmptySignals = []
    Context.CertifiedCandidateLayersBySignal = {}
    for Context.Port in sorted(Context.Problem.Interface.Ports, key=lambda Value: Value.Signal):
        Context.Domain = Context.CertifiedPortDomainBySignal.get(Context.Port.Signal)
        if Context.Domain is None or not Context.Domain.Complete:
            continue
        Context.CertifiedLayers = tuple(sorted({int(Candidate.Layer) for Candidate in Context.Domain.Candidates}))
        Context.CertifiedCandidateLayersBySignal[Context.Port.Signal] = Context.CertifiedLayers
        Context.AvailableLayers = tuple((Layer for Layer in Context.CertifiedLayers if 0 <= Layer < Context.EffectiveLayerCount))
        if not Context.AvailableLayers:
            Context.CertifiedPolicyLayerEmptySignals.append(Context.Port.Signal)
            continue
        Context.PreferredLayer = int(Context.AlignedGuideLayers.get(Context.Port.Signal, 0))
        Context.SelectedLayer = min(Context.AvailableLayers, key=lambda Layer: (abs(Layer - Context.PreferredLayer), Layer))
        if Context.SelectedLayer == Context.PreferredLayer:
            continue
        Context.AlignedGuideLayers[Context.Port.Signal] = Context.SelectedLayer
        Context.CertifiedGuideLayerReassignmentsBySignal[Context.Port.Signal] = {'OriginalLayer': Context.PreferredLayer, 'AssignedLayer': Context.SelectedLayer, 'CertifiedLayers': list(Context.AvailableLayers)}
    if Context.CertifiedPolicyLayerEmptySignals:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable, Stage='PhysicalComponentEligibility', AffectedNets=tuple(sorted(Context.CertifiedPolicyLayerEmptySignals)), Detail='the complete certified perimeter domain requires a layer outside the authoritative routing policy', Diagnostics={'Complete': True, 'EffectiveLayerCount': Context.EffectiveLayerCount, 'EmptyPortSignals': sorted(Context.CertifiedPolicyLayerEmptySignals), 'CertifiedCandidateLayersBySignal': {Signal: list(Context.CertifiedCandidateLayersBySignal[Signal]) for Signal in sorted(Context.CertifiedPolicyLayerEmptySignals)}, 'AccessCertificateFingerprint': Context.AccessCertificate.CertificateFingerprint, 'ComponentFabricConstructionComplete': True, 'OwnershipSearchComplete': True, 'ImplicitForeignTransitDomainCount': 0}))
    if Context.AlignedGuideLayers != Context.CoarsePlan.Layers:
        Context.CoarsePlan = replace(Context.CoarsePlan, Layers=Context.AlignedGuideLayers)

def BuildPhysicalPortChannelReservations(Context):
    Context.PortBankTrackPitch = int(getattr(getattr(Context.Resources.ResourceGraph, 'Technology', None), 'TrackPitch', DefaultRedstoneRoutingTechnology.TrackPitch))
    Context.PreliminaryKeepoutClaims = Context.ResourceGraph.BuildRouteClaims(Context.PreliminaryComponentKeepoutNodes)
    Context.MinimumPlacementY = min((int(Gate.Y) for Gate in getattr(Context.Placed, 'PlacedGates', ())), default=0)
    Context.ComponentKeepoutGuideCellsByLayer = BuildComponentKeepoutGuideCellsByLayer(Context.PreliminaryKeepoutClaims, Context.ResourceGraph, MinimumPlacementY=Context.MinimumPlacementY, LayerCount=Context.EffectiveLayerCount, WorkCheck=Context.WorkCheck)
    Context.ExplicitFeedthroughsBySignal = {Value.Signal: Value for Value in Context.Problem.Interface.Feedthroughs}
    Context.FeedthroughEndpointDomainsBySignal: dict[str, PreparedPhysicalComponentFeedthroughEndpointDomain] = {}
    Context.KeepoutDetouredGlobalSignals: tuple[str, ...] = ()
    while True:
        try:
            Context.CoarsePlan, Context.KeepoutDetouredGlobalSignals = BuildComponentKeepoutAvoidingGlobalGuides(Context.CoarsePlan, ComponentPortSignals=Context.ComponentPortSignals, EnvelopeMinimum=Context.PreliminaryEnvelopeMinimum, EnvelopeMaximum=Context.PreliminaryEnvelopeMaximum, TrackPitch=Context.PortBankTrackPitch, ReservedPortGuideCells=frozenset(), ComponentKeepoutGuideCellsByLayer=Context.ComponentKeepoutGuideCellsByLayer, DeclaredFeedthroughSignals=frozenset(Context.ExplicitFeedthroughsBySignal), WorkCheck=Context.WorkCheck)
            break
        except RoutingStageError as Error:
            Context.Failure = Error.Failure
            Context.Signal = str(Context.Failure.Diagnostics.get('Signal', ''))
            if Context.Failure.Reason != RoutingFailureReason.ComponentChannelCapacityUnsatisfiable or int(Context.Failure.Diagnostics.get('ExteriorGuideComponentCount', 0)) < 2 or (not Context.Signal) or (Context.Signal in Context.ExplicitFeedthroughsBySignal):
                raise
            Context.Layer = int(Context.CoarsePlan.Layers.get(Context.Signal, 0))
            Context.KeepoutCore = Context.ComponentKeepoutGuideCellsByLayer.get(Context.Layer, frozenset())
            Context.KeepoutHalo = frozenset(((X + DeltaX, Z + DeltaZ) for X, Z in Context.KeepoutCore for DeltaX, DeltaZ in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1))))
            Context.EndpointDomain = PreparePhysicalComponentFeedthroughEndpointDomain(Context.Signal, Context.Layer, FabricNodes=frozenset(Context.Problem.Fabric.Nodes), FabricEdges=frozenset(Context.Problem.Fabric.Edges), FabricIngressNodes=frozenset(Context.Problem.Fabric.IngressNodes), FabricFingerprint=Context.Problem.Fabric.FabricFingerprint, ResourceGraph=Context.ResourceGraph, MinimumPlacementY=Context.MinimumPlacementY, WorkCheck=Context.WorkCheck)
            Context.FeedthroughEndpointDomainsBySignal[Context.Signal] = Context.EndpointDomain
            Context.Contract, Context.UpdatedGuide = BuildExplicitPhysicalComponentFeedthrough(Context.Signal, Context.Layer, frozenset(Context.CoarsePlan.Guides.get(Context.Signal, ())), ComponentKeepoutGuideCells=Context.KeepoutHalo, ReservedPortAccessGuideCells=frozenset(), FabricNodes=frozenset(Context.Problem.Fabric.Nodes), FabricEdges=frozenset(Context.Problem.Fabric.Edges), FabricIngressNodes=frozenset(Context.Problem.Fabric.IngressNodes), ResourceGraph=Context.ResourceGraph, MinimumPlacementY=Context.MinimumPlacementY, PreparedEndpointDomain=Context.EndpointDomain, WorkCheck=Context.WorkCheck)
            Context.ExplicitFeedthroughsBySignal[Context.Signal] = Context.Contract
            Context.Guides = dict(Context.CoarsePlan.Guides)
            Context.Guides[Context.Signal] = Context.UpdatedGuide
            Context.PlanFields = getattr(Context.CoarsePlan, '__dataclass_fields__', {})
            Context.Changes: dict[str, object] = {'Guides': Context.Guides}
            if 'Usage' in Context.PlanFields:
                Context.Changes['Usage'] = dict(Counter(((int(Context.CoarsePlan.Layers.get(GuideSignal, 0)), X, Z) for GuideSignal, Cells in Context.Guides.items() for X, Z in Cells)))
            if 'Overflow' in Context.PlanFields:
                Context.Usage = Context.Changes.get('Usage', {})
                Context.Changes['Overflow'] = {Position: Count - 1 for Position, Count in Context.Usage.items() if Count > 1}
            if 'CorridorUsage' in Context.PlanFields:
                Context.Changes['CorridorUsage'] = dict(Counter((Position for Cells in Context.Guides.values() for Position in Cells)))
            Context.CoarsePlan = replace(Context.CoarsePlan, **Context.Changes)
    if tuple(Context.ExplicitFeedthroughsBySignal.values()) != Context.Problem.Interface.Feedthroughs:
        Context.Feedthroughs = tuple((Context.ExplicitFeedthroughsBySignal[Signal] for Signal in sorted(Context.ExplicitFeedthroughsBySignal)))
        Context.Interface = replace(Context.Problem.Interface, Feedthroughs=Context.Feedthroughs)
        Context.FeedthroughProblem = replace(Context.Problem, Interface=Context.Interface)
        Context.ForeignTransitDomains = BuildDeclaredComponentFeedthroughDomains(Context.FeedthroughProblem, Context.Feedthroughs)
        Context.Problem = replace(Context.FeedthroughProblem, ProblemFingerprint=BuildStableFingerprint((Context.Problem.ProblemFingerprint, tuple((Value.ReservationFingerprint for Value in Context.Feedthroughs)))), ForeignTransitDomains=Context.ForeignTransitDomains, DomainComplete=bool(Context.Problem.DomainComplete and all((Domain.Complete and Domain.Candidates for Domain in Context.ForeignTransitDomains))))
    Context.GraphChannelsBySignal = {str(Value.Signal): Value for Value in getattr(Context.ComponentGraph, 'Channels', ())}
    Context.ChannelReservations = []
    Context.PortBySignal = {Port.Signal: Port for Port in Context.Problem.Interface.Ports}
    for Context.Signal in sorted(Context.CoarsePlan.Guides):
        Context.Port = Context.PortBySignal.get(Context.Signal)
        Context.GuideCells = tuple(sorted(Context.CoarsePlan.Guides.get(Context.Signal, ())))
        Context.Layer = int(Context.CoarsePlan.Layers.get(Context.Signal, 0))
        Context.GuideNodes = tuple(((int(X), Context.ResourceGraph.Technology.RoutingY(Context.MinimumPlacementY, Context.Layer), int(Z)) for X, Z in Context.GuideCells if Context.Port is None and (int(X), int(Z)) not in Context.ComponentKeepoutGuideCellsByLayer.get(Context.Layer, frozenset())))
        Context.CorridorClaims = Context.ResourceGraph.BuildRouteClaims(Context.GuideNodes)
        Context.ResourceIds = tuple(sorted(Context.CorridorClaims.ResourceIds, key=str))
        if not Context.GuideCells:
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentChannelCapacityUnsatisfiable, Stage='PhysicalComponentAssemblyPlanning', AffectedNets=(Context.Signal,), Detail='an exported component signal has no reserved global guide corridor', Diagnostics={'Signal': Context.Signal, 'GuideCellCount': len(Context.GuideCells), 'ResourceCount': 0, 'ComponentFabricConstructionComplete': True, 'OwnershipSearchComplete': True, 'ImplicitForeignTransitDomainCount': 0}))
        Context.LogicalChannel = Context.GraphChannelsBySignal.get(Context.Signal)
        Context.DeclaredFeedthroughComponentIds = ((int(Context.Problem.Interface.ComponentId),) if Context.Problem.Interface.ComponentId is not None else ()) if Context.Signal in Context.Problem.Interface.DeclaredFeedthroughSignals else ()
        Context.ChannelIdentity = (Context.Signal, Context.Layer, Context.GuideCells, tuple(map(str, Context.ResourceIds)), tuple(sorted({*getattr(Context.LogicalChannel, 'FeedthroughComponentIds', ()), *Context.DeclaredFeedthroughComponentIds}, key=str)))
        Context.ChannelReservations.append(PhysicalComponentChannelReservation(Signal=Context.Signal, Layer=Context.Layer, GuideCells=Context.GuideCells, ResourceIds=tuple(map(str, Context.ResourceIds)), Claims=Context.CorridorClaims, Capacity=Context.Port.Capacity if Context.Port is not None else 1, FeedthroughComponentIds=tuple(sorted({*getattr(Context.LogicalChannel, 'FeedthroughComponentIds', ()), *Context.DeclaredFeedthroughComponentIds}, key=str)), ReservationFingerprint=BuildStableFingerprint(Context.ChannelIdentity)))

def BuildPhysicalPortFabricTopology(Context):
    """Freeze component-local geometry before any exterior construction."""
    Context.ChannelClaimsBySignal = {Value.Signal: Value.Claims for Value in Context.ChannelReservations}
    Context.FabricOrigin = (min((Value[0] for Value in Context.Problem.Fabric.Nodes)), min((Value[1] for Value in Context.Problem.Fabric.Nodes)), min((Value[2] for Value in Context.Problem.Fabric.Nodes))) if Context.Problem.Fabric.Nodes else (0, 0, 0)
    Context.FabricMaximum = (max((Value[0] for Value in Context.Problem.Fabric.Nodes)), max((Value[1] for Value in Context.Problem.Fabric.Nodes)), max((Value[2] for Value in Context.Problem.Fabric.Nodes))) if Context.Problem.Fabric.Nodes else (0, 0, 0)
    Context.ComponentKeepoutNodes = frozenset((*Context.Problem.Fabric.Nodes, *(Position for Claim in Context.Problem.LocalClaims for Position in Claim.Nodes)))
    Context.ComponentEnvelopeMinimum = (min((Value[0] for Value in Context.ComponentKeepoutNodes)), min((Value[1] for Value in Context.ComponentKeepoutNodes)), min((Value[2] for Value in Context.ComponentKeepoutNodes))) if Context.ComponentKeepoutNodes else Context.FabricOrigin
    Context.ComponentEnvelopeMaximum = (max((Value[0] for Value in Context.ComponentKeepoutNodes)), max((Value[1] for Value in Context.ComponentKeepoutNodes)), max((Value[2] for Value in Context.ComponentKeepoutNodes))) if Context.ComponentKeepoutNodes else Context.FabricMaximum
    Context.ExteriorFabrics: tuple[PhysicalExteriorApertureFabric, ...] = ()
    Context.ExteriorFabricSetFingerprint = ''
    Context.ExteriorCapacityLedgerFingerprint = ''
    Context.ResourceGraphFingerprint = BuildPhysicalExteriorResourceGraphFingerprint(Context.ResourceGraph, Context.AuthoritativeRegionFingerprint, Context.AuthoritativeRegion)
    Context.FabricAdjacency: dict[tuple[int, int, int], set[tuple[int, int, int]]] = defaultdict(set)
    for Context.First, Context.Second in Context.Problem.Fabric.Edges:
        Context.FabricAdjacency[tuple(Context.First)].add(tuple(Context.Second))
        Context.FabricAdjacency[tuple(Context.Second)].add(tuple(Context.First))
    Context.FabricComponentByNode: dict[tuple[int, int, int], int] = {}
    for Context.Start in sorted(Context.Problem.Fabric.Nodes):
        if Context.Start in Context.FabricComponentByNode:
            continue
        Context.ComponentIndex = len(set(Context.FabricComponentByNode.values()))
        Context.PendingNodes = [Context.Start]
        Context.FabricComponentByNode[Context.Start] = Context.ComponentIndex
        while Context.PendingNodes:
            Context.Current = Context.PendingNodes.pop()
            for Context.Neighbor in sorted(Context.FabricAdjacency.get(Context.Current, ())):
                if Context.Neighbor in Context.FabricComponentByNode:
                    continue
                Context.FabricComponentByNode[Context.Neighbor] = Context.ComponentIndex
                Context.PendingNodes.append(Context.Neighbor)
    Context.FabricNodesByComponent = {
        ComponentIndex: frozenset(
            Node
            for Node, NodeComponentIndex
            in Context.FabricComponentByNode.items()
            if NodeComponentIndex == ComponentIndex
        )
        for ComponentIndex in sorted(set(
            Context.FabricComponentByNode.values()
        ))
    }
    Context.FabricEnvelopeBoundsByComponent = {
        ComponentIndex: (
            tuple(
                min(Node[Index] for Node in ComponentNodes)
                for Index in range(3)
            ),
            tuple(
                max(Node[Index] for Node in ComponentNodes)
                for Index in range(3)
            ),
        )
        for ComponentIndex, ComponentNodes
        in Context.FabricNodesByComponent.items()
    }


def BuildPhysicalPortExteriorFabrics(Context):
    if not hasattr(Context, 'FabricComponentByNode'):
        BuildPhysicalPortFabricTopology(Context)
    if Context.AuthoritativeRegion is not None:
        if not Context.AuthoritativeRegionFingerprint:
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalExteriorFabricPreparation', Detail='authoritative exterior region has no stable identity', Diagnostics={'ImplicitForeignTransitDomainCount': 0}))
        RegionNodesByY = defaultdict(set)
        for NodeIndex, Node in enumerate(
            Context.AuthoritativeRegion.Nodes,
            start=1,
        ):
            RegionNodesByY[int(Node[1])].add(Node)
            if Context.WorkCheck is not None and NodeIndex % 16_384 == 0:
                Context.WorkCheck({
                    "Stage": "physical-exterior-region-partition",
                    "Phase": "nodes",
                    "CompletedNodes": NodeIndex,
                    "TotalNodes": len(Context.AuthoritativeRegion.Nodes),
                })
        RegionEdgesByY = defaultdict(set)
        for EdgeIndex, (First, Second) in enumerate(
            Context.AuthoritativeRegion.Edges,
            start=1,
        ):
            if First[1] == Second[1]:
                RegionEdgesByY[int(First[1])].add((First, Second))
            if Context.WorkCheck is not None and EdgeIndex % 16_384 == 0:
                Context.WorkCheck({
                    "Stage": "physical-exterior-region-partition",
                    "Phase": "edges",
                    "CompletedEdges": EdgeIndex,
                    "TotalEdges": len(Context.AuthoritativeRegion.Edges),
                })
        Context.ExteriorFabricValues = []
        for Context.Layer in sorted({int(Context.CoarsePlan.Layers.get(Port.Signal, 0)) for Port in Context.Problem.Interface.Ports}):
            Context.LayerSignals = frozenset((Port.Signal for Port in Context.Problem.Interface.Ports if int(Context.CoarsePlan.Layers.get(Port.Signal, 0)) == Context.Layer))
            Context.GuideCellsBySignal = {Signal: frozenset(Context.CoarsePlan.Guides.get(Signal, ())) for Signal in sorted(Context.LayerSignals)}
            Context.IngressNodesBySignal = {Signal: tuple(sorted({tuple(Candidate.Attachment) for Candidate in (Context.CertifiedPortDomainBySignal[Signal].Candidates if Signal in Context.CertifiedPortDomainBySignal else ()) if int(Candidate.Layer) == Context.Layer})) for Signal in sorted(Context.LayerSignals)}
            try:
                Context.IngressEnvelopeBoundsByNode = defaultdict(set)
                for Context.Signal in sorted(Context.LayerSignals):
                    for Context.Candidate in (
                        Context.CertifiedPortDomainBySignal[
                            Context.Signal
                        ].Candidates
                        if Context.Signal
                        in Context.CertifiedPortDomainBySignal
                        else ()
                    ):
                        if int(Context.Candidate.Layer) != Context.Layer:
                            continue
                        Context.CandidateComponentIndex = (
                            Context.FabricComponentByNode.get(
                                tuple(Context.Candidate.FabricAttachment)
                            )
                        )
                        Context.CandidateEnvelopeBounds = (
                            Context.FabricEnvelopeBoundsByComponent.get(
                                Context.CandidateComponentIndex
                            )
                        )
                        if Context.CandidateEnvelopeBounds is None:
                            raise ValueError(
                                "certified portal ingress has no connected "
                                "fabric component envelope: "
                                f"{Context.Candidate.Attachment}"
                            )
                        Context.IngressNode = tuple(
                            Context.Candidate.Attachment
                        )
                        Context.IngressEnvelopeBoundsByNode[
                            Context.IngressNode
                        ].add(Context.CandidateEnvelopeBounds)
                RoutingY = Context.ResourceGraph.Technology.RoutingY(
                    Context.MinimumPlacementY,
                    Context.Layer,
                )
                Context.ExteriorFabricValues.append(BuildPhysicalExteriorApertureFabric(Context.ComponentEnvelopeMinimum, Context.ComponentEnvelopeMaximum, Context.GuideCellsBySignal, Context.IngressNodesBySignal, Technology=Context.ResourceGraph.Technology, MinimumPlacementY=Context.MinimumPlacementY, Layer=Context.Layer, KeepoutColumns=Context.ComponentKeepoutGuideCellsByLayer.get(Context.Layer, frozenset()), KeepoutNodes=Context.ComponentKeepoutNodes, DeclaredPortalIngressEnvelopeBoundsByNode=Context.IngressEnvelopeBoundsByNode, RegionNodes=RegionNodesByY.get(RoutingY, ()), RegionEdges=RegionEdgesByY.get(RoutingY, ()), RegionFingerprint=Context.AuthoritativeRegionFingerprint, ResourceGraphFingerprint=Context.ResourceGraphFingerprint, Complete=True))
            except ValueError as Error:
                raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalExteriorFabricPreparation', AffectedNets=tuple(sorted(Context.LayerSignals)), Detail=str(Error), Diagnostics={'ExteriorRegionFingerprint': Context.AuthoritativeRegionFingerprint, 'Layer': Context.Layer, 'ImplicitForeignTransitDomainCount': 0})) from Error
        Context.ExteriorFabrics = tuple(Context.ExteriorFabricValues)
        Context.ExteriorFabricSetFingerprint = BuildStableFingerprint(('physical-exterior-fabric-set-v1', Context.AuthoritativeRegionFingerprint, tuple((Value.FabricFingerprint for Value in Context.ExteriorFabrics)), tuple((Value.SignalBindingFingerprint for Value in Context.ExteriorFabrics))))
        Context.ExteriorCapacityLedgerFingerprint = BuildStableFingerprint(('physical-exterior-capacity-ledger-v2', Context.ExteriorFabricSetFingerprint, tuple(((Value.Layer, Value.FabricFingerprint, len(Value.AllowedEdges), 1) for Value in Context.ExteriorFabrics))))
    Context.ExteriorFabricByLayer = {Value.Layer: Value for Value in Context.ExteriorFabrics}

def PreparePhysicalPortConnectorSearch(
    Context,
    Signals: frozenset[str] | None = None,
    *,
    Initialize: bool = True,
):
    if Initialize:
        Context.LaneFactorsBySignal: dict[str, tuple[PhysicalPortLaneFactor, ...]] = {}
        Context.ExteriorFactorPreparationStartedAt = monotonic()
        Context.LaneFactorExpansionCount = 0
        Context.AccessFactorExpansionCount = 0
        Context.SeamFactorExpansionCount = 0
        Context.GlobalConnectorSearchCount = 0
        Context.GlobalConnectorExpansionCount = 0
        Context.GlobalConnectorCacheHitCount = 0
        Context.GlobalConnectorPortableCacheHitCount = 0
        Context.GlobalConnectorPortableCacheValidationRejectCount = 0
        Context.GlobalConnectorPortableCacheStoreCount = 0
        Context.GlobalGuideFieldBuildCount = 0
        Context.GlobalGuideFieldExpansionCount = 0
        Context.GlobalGuideFieldHitCount = 0
        Context.GlobalGuideFieldCanonicalPathCount = 0
        Context.GlobalGuideFieldFallbackCount = 0
        Context.GlobalApertureTargetContextBuildCount = 0
        Context.GlobalApertureTargetDiagnosticsBySignal: dict[
            str,
            dict[str, object],
        ] = {}
        Context.CertifiedStraightExteriorTargetCountBySignal: dict[str, int] = {}
        Context.GlobalApertureStaticContractBuildCount = 0
        Context.GlobalPathRejectionCountsBySignal: dict[str, dict[str, int]] = {}
        Context.NativeConnectorSearchResults: dict[tuple[object, ...], PhysicalExteriorConnectorPathResult] = {}
        Context.NativeConnectorBatchWorkItems = 0
        Context.NativeConnectorBatchActiveWorkerCount = 0
        Context.NativeConnectorResultHitCount = 0
        Context.NativeConnectorEmptyResultCount = 0
        Context.NativeConnectorAcceptedPathCount = 0
        Context.NativeConnectorValidationRejectCount = 0
        Context.GlobalConnectorCache: dict[tuple[str, tuple[int, int, int], tuple[int, int, int], int, frozenset[tuple[int, int]], str], tuple[tuple[int, int, int], ...]] = {}
        Context.GlobalGuideFieldCache: dict[tuple[object, ...], PhysicalExteriorConnectorDistanceField] = {}
        Context.GlobalConnectorForeignClaimsCache: dict[tuple[str, str], RoutingResourceClaims] = {}
        Context.GlobalConnectorForeignEdgeLegalityCache: dict[tuple[str, str, Position3, Position3], bool] = {}
        Context.GlobalApertureTargetsCache: dict[tuple[object, ...], frozenset[Position3]] = {}
        Context.GlobalApertureGuideProjectionCache: dict[
            tuple[object, ...],
            tuple[
                tuple[Position3, ...],
                tuple[Position3, ...],
                tuple[Position3, ...],
            ],
        ] = {}
        Context.GlobalApertureStaticContractCache: dict[tuple[str, Position3, int, int, frozenset[Position2], str], PreparedPhysicalGlobalApertureStaticContract] = {}
        Context.LaneFactorDiagnosticsBySignal: dict[str, dict[str, object]] = {}
    PrepareCertifiedPhysicalPortLocalSeams(
        Context,
        Signals,
        Initialize=(
            Initialize
            and not hasattr(
                Context,
                'CertifiedPhysicalPortLocalSeamsByCandidate',
            )
        ),
    )
    Context.NativeConnectorSearchRequests: dict[tuple[object, ...], FrozenPhysicalExteriorConnectorSearchRequest] = {}
    for Context.Port in Context.Problem.Interface.Ports:
        if Signals is not None and Context.Port.Signal not in Signals:
            continue
        Context.CertifiedDomain = Context.CertifiedPortDomainBySignal.get(Context.Port.Signal)
        if Context.CertifiedDomain is None or not Context.CertifiedDomain.Candidates:
            continue
        Context.GuideCells = frozenset(Context.CoarsePlan.Guides.get(Context.Port.Signal, ()))
        Context.PortLayer = int(Context.CoarsePlan.Layers.get(Context.Port.Signal, 0))
        for Context.CertifiedCandidate in Context.CertifiedDomain.Candidates:
            Context.PreparedCertifiedLocalSeam = (
                Context.CertifiedPhysicalPortLocalSeamsByCandidate.get((
                    str(Context.Port.Signal),
                    str(Context.CertifiedCandidate.CandidateFingerprint),
                ))
            )
            if (
                Context.PreparedCertifiedLocalSeam is None
                or not Context.PreparedCertifiedLocalSeam.Complete
                or not Context.PreparedCertifiedLocalSeam.Feasible
            ):
                continue
            if Context.CertifiedCandidate.Layer != Context.PortLayer:
                continue
            Context.LocalPath = tuple(Context.CertifiedCandidate.LocalPath)
            if len(Context.LocalPath) < 2:
                continue
            BuildGlobalPathToGuide(Context, Context.LocalPath[-1], tuple((Context.LocalPath[-1][Index] - Context.LocalPath[-2][Index] for Index in range(3))), Context.GuideCells, Context.Port.Signal, Context.PortLayer, {}, FabricAttachment=Context.CertifiedCandidate.FabricAttachment, CollectNativeConnectorRequest=True)
    if Context.NativeConnectorSearchRequests:
        Context.NativeConnectorKeys = tuple(Context.NativeConnectorSearchRequests)
        if Context.WorkCheck is not None:
            Context.NativeConnectorFields = {
                Request.Field.FieldFingerprint: Request.Field
                for Request in Context.NativeConnectorSearchRequests.values()
            }
            Context.WorkCheck({
                "Stage": "physical-port-native-connector-batch",
                "Phase": "start",
                "NativeConnectorRequestCount": len(
                    Context.NativeConnectorSearchRequests
                ),
                "NativeConnectorFieldCount": len(
                    Context.NativeConnectorFields
                ),
                "MaximumNativeConnectorTargetCount": max(
                    len(Field.Targets)
                    for Field in Context.NativeConnectorFields.values()
                ),
                "MaximumNativeConnectorAllowedNodeCount": max(
                    len(Field.AllowedNodes)
                    for Field in Context.NativeConnectorFields.values()
                ),
                "ImplicitForeignTransitDomainCount": 0,
            })
        Context.NativeConnectorResults, Context.NativeConnectorBatchCurrentActiveWorkerCount = SearchFrozenPhysicalExteriorConnectorBatch((Context.NativeConnectorSearchRequests[Key] for Key in Context.NativeConnectorKeys))
        Context.NativeConnectorBatchActiveWorkerCount = max(Context.NativeConnectorBatchActiveWorkerCount, Context.NativeConnectorBatchCurrentActiveWorkerCount)
        Context.NativeConnectorBatchWorkItems += len(Context.NativeConnectorResults)
        Context.NativeConnectorSearchResults.update(zip(Context.NativeConnectorKeys, Context.NativeConnectorResults))

"""Cohesive preparation and finalization phases for one domain."""

from __future__ import annotations

from ...Regions.Interfaces.Fabric import BuildComponentEgressPaths
from ...Regions.Interfaces.Fabric import FilterExternalSourcePoweredSeamCandidateDomains
from ....Contracts.PhysicalInterface import PhysicalPortLaneFactor
from ....Contracts.PhysicalInterface import PhysicalPortLocalAccessFactor
from ....Contracts.PhysicalInterface import PhysicalPortSeamFactor
from ....Execution.Reliability import BuildStableFingerprint
from ....Resources.ResourceGraph import FindSelfClaimConflicts
from ....Resources.ResourceGraph import RoutingResourceClaims
from collections import Counter
from collections import defaultdict
from dataclasses import replace
from ..Guides.PhysicalGuides import CertifyPhysicalPortExteriorFixedClaims, DecomposePhysicalPortLaneFactors, FindSignalClaimConflicts
from ..Assignment.TrackPortfolio import PhysicalPortPathsOwnExclusiveSeam
from functools import partial
from .PortPreparationState import PortPreparationState, SetPortPreparationState
from .PortPreparationHelpers import BuildGlobalPathToGuide, DeduplicateCertifiedAccessCandidates
from ....Contracts.PhysicalInterface import PhysicalPortLocalApertureSupport
from ....Contracts.PhysicalInterface import PreparedPhysicalComponentPortFactorDomain
from time import monotonic
from ..Guides.PhysicalGuides import BuildPhysicalComponentBoundaryPortReservation
def BuildPhysicalPortLaneFactors(
    Context,
    Signals: frozenset[str] | None = None,
):
    for Context.Port in Context.Problem.Interface.Ports:
        if Signals is not None and Context.Port.Signal not in Signals:
            continue
        Context.PortLayer = int(Context.CoarsePlan.Layers.get(Context.Port.Signal, 0))
        Context.Domains = tuple(sorted((Domain for Domain in Context.Problem.OwnedTerminalDomains if Domain.Signal == Context.Port.Signal), key=lambda Value: Value.Terminal))
        Context.SignalLaneDiagnostics: dict[str, object] = {'OwnedTerminalDomainCount': len(Context.Domains), 'EmptyOwnedTerminalCount': sum((1 for Domain in Context.Domains if not Domain.Candidates)), 'CandidateCountByTerminal': [{'Terminal': list(Domain.Terminal), 'CandidateCount': len(Domain.Candidates)} for Domain in Context.Domains], 'CertifiedCandidateCount': 0, 'CertifiedLayerMatchCount': 0, 'CertifiedLayerMismatchCount': 0, 'CertifiedCandidateLayerCounts': {}, 'RequiredPortLayer': Context.PortLayer, 'CertifiedGuideLayerReassignment': Context.CertifiedGuideLayerReassignmentsBySignal.get(Context.Port.Signal), 'CertifiedMissingOwnedCandidateFingerprintCount': 0, 'CertifiedIncompleteCandidateDomainMappingCount': 0, 'CertifiedShortLocalPathCount': 0, 'CertifiedInvalidOutwardPrimitiveCount': 0, 'CertifiedGuideDisconnectedCount': 0, 'CertifiedNonExclusiveSeamPathCount': 0, 'CertifiedSelfClaimConflictCount': 0, 'CertifiedForeignCorridorConflictCount': 0, 'CertifiedUnarySeamInfeasibleCount': 0, 'CertifiedStraightExteriorTargetCount': Context.CertifiedStraightExteriorTargetCountBySignal.get(Context.Port.Signal, 0), 'ForeignCorridorConflictSignals': {}, 'ForeignCorridorConflictResources': [], 'ForeignCorridorConflictSamples': [], 'CertifiedLaneFactorCount': 0, 'CertifiedCandidateDomainProjectionBuildCount': 0, 'CertifiedCandidateDomainProjectionHitCount': 0, 'CommonFabricComponentCount': 0, 'FabricIngressNodeCount': 0, 'EgressPathCount': 0, 'EnvelopeExitPathCount': 0, 'InvalidPrimitivePathCount': 0, 'NonExclusiveSeamPathCount': 0, 'SelfClaimConflictPathCount': 0, 'ForeignCorridorConflictPathCount': 0, 'UnarySeamInfeasiblePathCount': 0, 'GeneratedSeamCount': 0, 'LaneFactorCount': 0, 'GuideCellCount': len(Context.CoarsePlan.Guides.get(Context.Port.Signal, ())), 'ExteriorFabricLayerAvailable': Context.PortLayer in Context.ExteriorFabricByLayer, 'GlobalPathRejectionCounts': Context.GlobalPathRejectionCountsBySignal.setdefault(Context.Port.Signal, {}), 'GlobalApertureTargetDiagnostics': Context.GlobalApertureTargetDiagnosticsBySignal.setdefault(Context.Port.Signal, {}), 'NativeConnectorBatchWorkItems': Context.NativeConnectorBatchWorkItems, 'NativeConnectorBatchActiveWorkerCount': Context.NativeConnectorBatchActiveWorkerCount, 'Reason': 'unclassified'}
        Context.UnarySeamEligibilityDiagnostics = (
            getattr(
                Context,
                "PhysicalLocalSeamEligibilityDiagnosticsBySignal",
                {},
            ).setdefault(str(Context.Port.Signal), {})
        )
        Context.SignalLaneDiagnostics[
            "CertifiedUnarySeamEligibilityDiagnostics"
        ] = Context.UnarySeamEligibilityDiagnostics
        Context.LaneFactorDiagnosticsBySignal[Context.Port.Signal] = Context.SignalLaneDiagnostics
        if not Context.Domains or any((not Domain.Candidates for Domain in Context.Domains)):
            Context.LaneFactorsBySignal[Context.Port.Signal] = ()
            Context.SignalLaneDiagnostics['Reason'] = 'no-owned-terminal-domains' if not Context.Domains else 'owned-terminal-domain-empty'
            continue
        Context.GuideCells = frozenset(Context.CoarsePlan.Guides.get(Context.Port.Signal, ()))
        Context.ExternalTerminals = tuple((Terminal for Signal, Terminal, _Role in Context.Problem.ExternalContinuationTerminals if Signal == Context.Port.Signal))
        Context.ForeignCorridorClaims: dict[str, RoutingResourceClaims] = {}
        Context.CertifiedDomain = Context.CertifiedPortDomainBySignal.get(Context.Port.Signal)
        if Context.CertifiedDomain is not None and Context.CertifiedDomain.Candidates:
            Context.SignalLaneDiagnostics['CertifiedCandidateCount'] = len(Context.CertifiedDomain.Candidates)
            Context.CertifiedLaneFactors = []
            Context.CandidateByFingerprint = {Candidate.CandidateFingerprint: Candidate for Domain in Context.Domains for Candidate in Domain.Candidates}
            Context.CandidateDomainsByFabricComponentIndex = {ComponentIndex: tuple((DeduplicateCertifiedAccessCandidates(Context, (Candidate for Candidate in Domain.Candidates if Context.FabricComponentByNode.get(Candidate.Attachment) == ComponentIndex)) for Domain in Context.Domains)) for ComponentIndex in sorted({Context.FabricComponentByNode[Candidate.Attachment] for Domain in Context.Domains for Candidate in Domain.Candidates if Candidate.Attachment in Context.FabricComponentByNode})}
            Context.SignalLaneDiagnostics['CertifiedCandidateDomainProjectionBuildCount'] = len(Context.CandidateDomainsByFabricComponentIndex)
            for Context.CertifiedCandidate in Context.CertifiedDomain.Candidates:
                Context.CandidateLayerCounts = Context.SignalLaneDiagnostics['CertifiedCandidateLayerCounts']
                assert isinstance(Context.CandidateLayerCounts, dict)
                Context.CandidateLayerKey = str(Context.CertifiedCandidate.Layer)
                Context.CandidateLayerCounts[Context.CandidateLayerKey] = int(Context.CandidateLayerCounts.get(Context.CandidateLayerKey, 0)) + 1
                if Context.CertifiedCandidate.Layer != Context.PortLayer:
                    Context.SignalLaneDiagnostics['CertifiedLayerMismatchCount'] = int(Context.SignalLaneDiagnostics['CertifiedLayerMismatchCount']) + 1
                    continue
                Context.SignalLaneDiagnostics['CertifiedLayerMatchCount'] = int(Context.SignalLaneDiagnostics['CertifiedLayerMatchCount']) + 1
                if Context.CertifiedCandidate.OwnedCandidateFingerprints:
                    Context.SelectedCandidateDomains = tuple(((Context.CandidateByFingerprint.get(Fingerprint),) for Fingerprint in Context.CertifiedCandidate.OwnedCandidateFingerprints))
                    if len(Context.SelectedCandidateDomains) != len(Context.Domains) or any((Values[0] is None for Values in Context.SelectedCandidateDomains)):
                        if any((Values[0] is None for Values in Context.SelectedCandidateDomains)):
                            Context.SignalLaneDiagnostics['CertifiedMissingOwnedCandidateFingerprintCount'] = int(Context.SignalLaneDiagnostics['CertifiedMissingOwnedCandidateFingerprintCount']) + 1
                        else:
                            Context.SignalLaneDiagnostics['CertifiedIncompleteCandidateDomainMappingCount'] = int(Context.SignalLaneDiagnostics['CertifiedIncompleteCandidateDomainMappingCount']) + 1
                        continue
                else:
                    Context.CertifiedComponentIndex = Context.FabricComponentByNode.get(Context.CertifiedCandidate.FabricAttachment)
                    Context.SelectedCandidateDomains = Context.CandidateDomainsByFabricComponentIndex.get(Context.CertifiedComponentIndex, ())
                    Context.SignalLaneDiagnostics['CertifiedCandidateDomainProjectionHitCount'] = int(Context.SignalLaneDiagnostics['CertifiedCandidateDomainProjectionHitCount']) + 1
                    if len(Context.SelectedCandidateDomains) != len(Context.Domains) or any((not Values for Values in Context.SelectedCandidateDomains)):
                        Context.SignalLaneDiagnostics['CertifiedIncompleteCandidateDomainMappingCount'] = int(Context.SignalLaneDiagnostics['CertifiedIncompleteCandidateDomainMappingCount']) + 1
                        continue
                Context.LocalPath = tuple(Context.CertifiedCandidate.LocalPath)
                if len(Context.LocalPath) < 2:
                    Context.SignalLaneDiagnostics['CertifiedShortLocalPathCount'] = int(Context.SignalLaneDiagnostics['CertifiedShortLocalPathCount']) + 1
                    continue
                # Prove the component-local powered seam before constructing
                # its exterior connector.  This filter depends only on the
                # certified local path and owned terminal domains, so running
                # it first preserves the exact factor domain while avoiding
                # global guide searches for locally impossible seams.
                Context.PreparedCertifiedLocalSeam = getattr(
                    Context,
                    'CertifiedPhysicalPortLocalSeamsByCandidate',
                    {},
                ).get((
                    str(Context.Port.Signal),
                    str(Context.CertifiedCandidate.CandidateFingerprint),
                ))
                Context.PoweredCandidateDomains = (
                    Context.PreparedCertifiedLocalSeam.PoweredCandidateDomains
                    if Context.PreparedCertifiedLocalSeam is not None
                    else FilterExternalSourcePoweredSeamCandidateDomains(Context.Problem, Context.Port.Signal, Context.Domains, Context.SelectedCandidateDomains, tuple(Context.LocalPath), FabricAdjacency=Context.PoweredSeamFabricAdjacency, FabricParentCache=Context.PoweredSeamFabricParentCache, RouteClaimsCache=Context.PoweredSeamRouteClaimsCache, TreeRepeaterSubproblemCache=Context.PoweredSeamTreeRepeaterSubproblemCache, TreeRepeaterCacheStatistics=Context.PoweredSeamTreeRepeaterCacheStatistics, CandidateEligibilityDiagnostics=Context.UnarySeamEligibilityDiagnostics)
                )
                if any((not Values for Values in Context.PoweredCandidateDomains)):
                    Context.SignalLaneDiagnostics['CertifiedUnarySeamInfeasibleCount'] = int(Context.SignalLaneDiagnostics['CertifiedUnarySeamInfeasibleCount']) + 1
                    continue
                Context.BoundCandidates = (
                    Context.PreparedCertifiedLocalSeam.BoundCandidates
                    if Context.PreparedCertifiedLocalSeam is not None
                    else tuple((Values[0] for Values in Context.PoweredCandidateDomains)) if all((len(Values) == 1 for Values in Context.PoweredCandidateDomains)) else ()
                )
                Context.GlobalPath = BuildGlobalPathToGuide(Context, Context.LocalPath[-1], tuple((Context.LocalPath[-1][Index] - Context.LocalPath[-2][Index] for Index in range(3))), Context.GuideCells, Context.Port.Signal, Context.PortLayer, Context.ForeignCorridorClaims, FabricAttachment=Context.CertifiedCandidate.FabricAttachment)
                if not Context.GlobalPath:
                    Context.SignalLaneDiagnostics['CertifiedGuideDisconnectedCount'] = int(Context.SignalLaneDiagnostics['CertifiedGuideDisconnectedCount']) + 1
                    continue
                if not PhysicalPortPathsOwnExclusiveSeam(Context.LocalPath, Context.GlobalPath):
                    Context.SignalLaneDiagnostics['CertifiedNonExclusiveSeamPathCount'] = int(Context.SignalLaneDiagnostics['CertifiedNonExclusiveSeamPathCount']) + 1
                    continue
                if any((Context.ResourceGraph.BuildPrimitive(First, Second) is None or (Context.PortLayer in Context.ExteriorFabricByLayer and (not Context.ExteriorFabricByLayer[Context.PortLayer].AllowsEdge(First, Second))) for First, Second in zip(Context.GlobalPath, Context.GlobalPath[1:]))):
                    Context.SignalLaneDiagnostics['CertifiedInvalidOutwardPrimitiveCount'] = int(Context.SignalLaneDiagnostics['CertifiedInvalidOutwardPrimitiveCount']) + 1
                    continue
                Context.SeamClaims = Context.ResourceGraph.BuildRouteClaims(frozenset((*Context.LocalPath, *Context.GlobalPath)))
                if FindSelfClaimConflicts({Context.Port.Signal: Context.SeamClaims}):
                    Context.SignalLaneDiagnostics['CertifiedSelfClaimConflictCount'] = int(Context.SignalLaneDiagnostics['CertifiedSelfClaimConflictCount']) + 1
                    continue
                Context.ForeignConflicts = FindSignalClaimConflicts({**Context.ForeignCorridorClaims, Context.Port.Signal: Context.SeamClaims}, Context.Port.Signal)
                if Context.ForeignConflicts:
                    Context.SignalLaneDiagnostics['CertifiedForeignCorridorConflictCount'] = int(Context.SignalLaneDiagnostics['CertifiedForeignCorridorConflictCount']) + 1
                    Context.ConflictSignalCounts = Counter((Signal for Signals in Context.ForeignConflicts.values() for Signal in Signals if Signal != Context.Port.Signal))
                    Context.ExistingConflictCounts = Context.SignalLaneDiagnostics['ForeignCorridorConflictSignals']
                    assert isinstance(Context.ExistingConflictCounts, dict)
                    for Context.Signal, Context.Count in Context.ConflictSignalCounts.items():
                        Context.ExistingConflictCounts[Context.Signal] = int(Context.ExistingConflictCounts.get(Context.Signal, 0)) + Context.Count
                    Context.ConflictSamples = Context.SignalLaneDiagnostics['ForeignCorridorConflictSamples']
                    assert isinstance(Context.ConflictSamples, list)
                    if len(Context.ConflictSamples) < 8:
                        Context.ConflictSamples.extend(({'Kind': Resource.Kind.value, 'Position': list(Resource.Position), 'Signals': list(Signals)} for Resource, Signals in sorted(Context.ForeignConflicts.items(), key=lambda Value: (Value[0].Kind.value, Value[0].Position))[:8 - len(Context.ConflictSamples)]))
                    continue
                Context.SeamFingerprint = BuildStableFingerprint((Context.CertifiedCandidate.CandidateFingerprint, tuple((tuple((Position[Index] - Context.FabricOrigin[Index] for Index in range(3))) for Position in Context.LocalPath)), tuple((tuple((Position[Index] - Context.FabricOrigin[Index] for Index in range(3))) for Position in Context.GlobalPath))))
                Context.CertifiedLaneFactors.append(PhysicalPortLaneFactor(Signal=Context.Port.Signal, Direction=Context.Port.Direction, Capacity=Context.Port.Capacity, OwnedTerminals=tuple((Domain.Terminal for Domain in Context.Domains)), Domains=Context.Domains, CandidateDomains=Context.SelectedCandidateDomains, FabricDomainFingerprint=Context.CertifiedCandidate.FabricDomainFingerprint, Seams=(PhysicalPortSeamFactor(FabricAttachment=Context.CertifiedCandidate.FabricAttachment, Attachment=Context.CertifiedCandidate.Attachment, LocalPath=Context.LocalPath, GlobalPath=Context.GlobalPath, Claims=Context.SeamClaims, SeamFingerprint=Context.SeamFingerprint, OwnedCandidateFingerprints=tuple((Candidate.CandidateFingerprint for Candidate in Context.BoundCandidates))),), GuideCells=Context.GuideCells, ExternalTerminals=Context.ExternalTerminals))
            Context.SignalLaneDiagnostics['CertifiedLaneFactorCount'] = len(Context.CertifiedLaneFactors)
            if Context.CertifiedLaneFactors:
                Context.MergedCertifiedLaneFactors = []
                for Context.FabricFingerprint in sorted({Value.FabricDomainFingerprint for Value in Context.CertifiedLaneFactors}):
                    Context.Values = tuple((Value for Value in Context.CertifiedLaneFactors if Value.FabricDomainFingerprint == Context.FabricFingerprint))
                    Context.FirstValue = Context.Values[0]
                    Context.CandidateDomains = tuple((tuple(({Candidate.CandidateFingerprint: Candidate for Value in Context.Values for Candidate in Value.CandidateDomains[Index]}[Fingerprint] for Fingerprint in sorted({Candidate.CandidateFingerprint for Value in Context.Values for Candidate in Value.CandidateDomains[Index]}))) for Index in range(len(Context.FirstValue.Domains))))
                    Context.SeamsByFingerprint = {Seam.SeamFingerprint: Seam for Value in Context.Values for Seam in Value.Seams}
                    Context.MergedCertifiedLaneFactors.append(replace(Context.FirstValue, CandidateDomains=Context.CandidateDomains, Seams=tuple(sorted(Context.SeamsByFingerprint.values(), key=lambda Seam: (0 if Seam.FabricAttachment in Context.FirstValue.OwnedTerminals else 1, tuple((Seam.FabricAttachment[Index] - Context.FabricOrigin[Index] for Index in range(3))), Seam.SeamFingerprint)))))
                Context.LaneFactorsBySignal[Context.Port.Signal] = tuple(Context.MergedCertifiedLaneFactors)
                Context.SignalLaneDiagnostics['GeneratedSeamCount'] = sum((len(Value.Seams) for Value in Context.MergedCertifiedLaneFactors))
                Context.SignalLaneDiagnostics['LaneFactorCount'] = len(Context.MergedCertifiedLaneFactors)
                Context.SignalLaneDiagnostics['Reason'] = 'available-certified'
                continue
        if Context.CertifiedDomain is not None and Context.CertifiedDomain.Complete and Context.AccessCertificate.Complete:
            Context.LaneFactorsBySignal[Context.Port.Signal] = ()
            Context.SignalLaneDiagnostics['GeneratedFallbackSuppressed'] = True
            Context.SignalLaneDiagnostics['Reason'] = 'complete-certified-domain-empty-after-physical-projection'
            continue
        Context.LaneFactors = []
        Context.CandidateComponentsByDomain = tuple(({Context.FabricComponentByNode[Candidate.Attachment] for Candidate in Domain.Candidates if Candidate.Attachment in Context.FabricComponentByNode} for Domain in Context.Domains))
        Context.CommonComponentIndexes = set.intersection(*map(set, Context.CandidateComponentsByDomain)) if Context.CandidateComponentsByDomain else set()
        Context.SignalLaneDiagnostics['CandidateFabricComponentCountByTerminal'] = [len(Values) for Values in Context.CandidateComponentsByDomain]
        Context.SignalLaneDiagnostics['CommonFabricComponentCount'] = len(Context.CommonComponentIndexes)
        for Context.ComponentIndex in sorted(Context.CommonComponentIndexes):
            Context.ComponentCandidatesByDomain = tuple((tuple(sorted((Candidate for Candidate in Domain.Candidates if Context.FabricComponentByNode.get(Candidate.Attachment) == Context.ComponentIndex), key=lambda Value: (Value.Cost, Value.CandidateFingerprint))) for Domain in Context.Domains))
            Context.ComponentNodes = frozenset((Node for Node, CandidateComponentIndex in Context.FabricComponentByNode.items() if CandidateComponentIndex == Context.ComponentIndex))
            Context.FabricDomainFingerprint = BuildStableFingerprint(tuple(sorted((tuple((Position[Index] - Context.FabricOrigin[Index] for Index in range(3))) for Position in Context.ComponentNodes))))
            Context.FabricIngressNodes = tuple(sorted(Context.ComponentNodes))
            Context.SignalLaneDiagnostics['FabricIngressNodeCount'] = int(Context.SignalLaneDiagnostics['FabricIngressNodeCount']) + len(Context.FabricIngressNodes)
            Context.Seams = []
            for Context.FabricAttachment in Context.FabricIngressNodes:
                Context.LaneFactorExpansionCount += 1
                if Context.WorkCheck is not None:
                    Context.WorkCheck({'Stage': 'physical-port-lane-assignment', 'Signal': Context.Port.Signal, 'ComponentIndex': Context.ComponentIndex, 'LaneFactorExpansionCount': Context.LaneFactorExpansionCount, 'AccessFactorExpansionCount': Context.AccessFactorExpansionCount, 'SeamFactorExpansionCount': Context.SeamFactorExpansionCount, 'ConnectorSearchCount': Context.GlobalConnectorSearchCount, 'ConnectorExpansionCount': Context.GlobalConnectorExpansionCount, 'ConnectorCacheHitCount': Context.GlobalConnectorCacheHitCount, 'GuideFieldBuildCount': Context.GlobalGuideFieldBuildCount, 'GuideFieldExpansionCount': Context.GlobalGuideFieldExpansionCount, 'GuideFieldHitCount': Context.GlobalGuideFieldHitCount, 'GuideFieldCanonicalPathCount': Context.GlobalGuideFieldCanonicalPathCount, 'GuideFieldFallbackCount': Context.GlobalGuideFieldFallbackCount, 'PoweredSeamRouteClaimsCacheSize': len(Context.PoweredSeamRouteClaimsCache)})
                for Context.LocalPath in BuildComponentEgressPaths(Context.FabricAttachment, TargetY=Context.ResourceGraph.Technology.RoutingY(Context.MinimumPlacementY, Context.PortLayer), EnvelopeMinimum=Context.ComponentEnvelopeMinimum, EnvelopeMaximum=Context.ComponentEnvelopeMaximum):
                    Context.SignalLaneDiagnostics['EgressPathCount'] = int(Context.SignalLaneDiagnostics['EgressPathCount']) + 1
                    Context.LocalDirection = (Context.LocalPath[1][0] - Context.LocalPath[0][0], Context.LocalPath[1][2] - Context.LocalPath[0][2])
                    Context.LocalEndpoint = Context.LocalPath[-1]
                    Context.ExitsFabricEnvelope = Context.LocalDirection == (-1, 0) and Context.LocalEndpoint[0] < Context.ComponentEnvelopeMinimum[0] or (Context.LocalDirection == (1, 0) and Context.LocalEndpoint[0] > Context.ComponentEnvelopeMaximum[0]) or (Context.LocalDirection == (0, -1) and Context.LocalEndpoint[2] < Context.ComponentEnvelopeMinimum[2]) or (Context.LocalDirection == (0, 1) and Context.LocalEndpoint[2] > Context.ComponentEnvelopeMaximum[2])
                    if not Context.ExitsFabricEnvelope:
                        continue
                    Context.SignalLaneDiagnostics['EnvelopeExitPathCount'] = int(Context.SignalLaneDiagnostics['EnvelopeExitPathCount']) + 1
                    Context.PoweredCandidateDomains = FilterExternalSourcePoweredSeamCandidateDomains(Context.Problem, Context.Port.Signal, Context.Domains, Context.ComponentCandidatesByDomain, tuple(Context.LocalPath), FabricAdjacency=Context.PoweredSeamFabricAdjacency, FabricParentCache=Context.PoweredSeamFabricParentCache, RouteClaimsCache=Context.PoweredSeamRouteClaimsCache, TreeRepeaterSubproblemCache=Context.PoweredSeamTreeRepeaterSubproblemCache, TreeRepeaterCacheStatistics=Context.PoweredSeamTreeRepeaterCacheStatistics, CandidateEligibilityDiagnostics=Context.UnarySeamEligibilityDiagnostics)
                    if any((not Values for Values in Context.PoweredCandidateDomains)):
                        Context.SignalLaneDiagnostics['UnarySeamInfeasiblePathCount'] = int(Context.SignalLaneDiagnostics['UnarySeamInfeasiblePathCount']) + 1
                        continue
                    Context.GlobalPath = BuildGlobalPathToGuide(Context, Context.LocalEndpoint, (Context.LocalDirection[0], Context.LocalPath[-1][1] - Context.LocalPath[-2][1], Context.LocalDirection[1]), Context.GuideCells, Context.Port.Signal, Context.PortLayer, Context.ForeignCorridorClaims, FabricAttachment=Context.FabricAttachment)
                    if not Context.GlobalPath:
                        continue
                    if not PhysicalPortPathsOwnExclusiveSeam(Context.LocalPath, Context.GlobalPath):
                        Context.SignalLaneDiagnostics['NonExclusiveSeamPathCount'] = int(Context.SignalLaneDiagnostics['NonExclusiveSeamPathCount']) + 1
                        continue
                    if any((Context.ResourceGraph.BuildPrimitive(First, Second) is None or (Context.PortLayer in Context.ExteriorFabricByLayer and (not Context.ExteriorFabricByLayer[Context.PortLayer].AllowsEdge(First, Second))) for First, Second in zip(Context.GlobalPath, Context.GlobalPath[1:]))):
                        Context.SignalLaneDiagnostics['InvalidPrimitivePathCount'] = int(Context.SignalLaneDiagnostics['InvalidPrimitivePathCount']) + 1
                        continue
                    Context.SeamClaims = Context.ResourceGraph.BuildRouteClaims(frozenset(Context.LocalPath) | frozenset(Context.GlobalPath))
                    if FindSelfClaimConflicts({Context.Port.Signal: Context.SeamClaims}):
                        Context.SignalLaneDiagnostics['SelfClaimConflictPathCount'] = int(Context.SignalLaneDiagnostics['SelfClaimConflictPathCount']) + 1
                        continue
                    Context.ForeignConflicts = FindSignalClaimConflicts({**Context.ForeignCorridorClaims, Context.Port.Signal: Context.SeamClaims}, Context.Port.Signal)
                    if Context.ForeignConflicts:
                        Context.SignalLaneDiagnostics['ForeignCorridorConflictPathCount'] = int(Context.SignalLaneDiagnostics['ForeignCorridorConflictPathCount']) + 1
                        Context.ConflictSignalCounts = Counter((Signal for Signals in Context.ForeignConflicts.values() for Signal in Signals if Signal != Context.Port.Signal))
                        Context.ExistingConflictCounts = Context.SignalLaneDiagnostics['ForeignCorridorConflictSignals']
                        assert isinstance(Context.ExistingConflictCounts, dict)
                        for Context.Signal, Context.Count in Context.ConflictSignalCounts.items():
                            Context.ExistingConflictCounts[Context.Signal] = int(Context.ExistingConflictCounts.get(Context.Signal, 0)) + Context.Count
                        Context.ConflictResources = Context.SignalLaneDiagnostics['ForeignCorridorConflictResources']
                        assert isinstance(Context.ConflictResources, list)
                        if len(Context.ConflictResources) < 8:
                            Context.ConflictResources.extend(({'Resource': str(Resource), 'Signals': list(Signals)} for Resource, Signals in sorted(Context.ForeignConflicts.items(), key=lambda Value: str(Value[0]))[:8 - len(Context.ConflictResources)]))
                        continue
                    Context.SeamFingerprint = BuildStableFingerprint((Context.FabricDomainFingerprint, tuple((tuple((Position[Index] - Context.FabricOrigin[Index] for Index in range(3))) for Position in Context.LocalPath)), tuple((tuple((Position[Index] - Context.FabricOrigin[Index] for Index in range(3))) for Position in Context.GlobalPath))))
                    Context.Seams.append(PhysicalPortSeamFactor(FabricAttachment=Context.FabricAttachment, Attachment=Context.LocalPath[-1], LocalPath=tuple(Context.LocalPath), GlobalPath=tuple(Context.GlobalPath), Claims=Context.SeamClaims, SeamFingerprint=Context.SeamFingerprint))
                    Context.SignalLaneDiagnostics['GeneratedSeamCount'] = int(Context.SignalLaneDiagnostics['GeneratedSeamCount']) + 1
            if not Context.Seams:
                continue
            Context.LaneFactors.append(PhysicalPortLaneFactor(Signal=Context.Port.Signal, Direction=Context.Port.Direction, Capacity=Context.Port.Capacity, OwnedTerminals=tuple((Domain.Terminal for Domain in Context.Domains)), Domains=Context.Domains, CandidateDomains=Context.ComponentCandidatesByDomain, FabricDomainFingerprint=Context.FabricDomainFingerprint, Seams=tuple(Context.Seams), GuideCells=Context.GuideCells, ExternalTerminals=Context.ExternalTerminals))
        Context.LaneFactorsBySignal[Context.Port.Signal] = tuple(Context.LaneFactors)
        Context.SignalLaneDiagnostics['LaneFactorCount'] = len(Context.LaneFactors)
        if Context.LaneFactors:
            Context.SignalLaneDiagnostics['Reason'] = 'available-generated'
        elif int(Context.SignalLaneDiagnostics['CertifiedCandidateCount']) > 0 and int(Context.SignalLaneDiagnostics['CertifiedLayerMatchCount']) == 0:
            Context.SignalLaneDiagnostics['Reason'] = 'all-certified-candidates-mismatch-required-port-layer'
        elif not Context.CommonComponentIndexes:
            Context.SignalLaneDiagnostics['Reason'] = 'no-common-fabric-component'
        elif not int(Context.SignalLaneDiagnostics['EnvelopeExitPathCount']):
            Context.SignalLaneDiagnostics['Reason'] = 'no-envelope-exiting-egress-path'
        elif int(Context.SignalLaneDiagnostics['InvalidPrimitivePathCount']) == int(Context.SignalLaneDiagnostics['EnvelopeExitPathCount']):
            Context.SignalLaneDiagnostics['Reason'] = 'all-envelope-exits-have-invalid-global-primitive'
        elif int(Context.SignalLaneDiagnostics['SelfClaimConflictPathCount']) == int(Context.SignalLaneDiagnostics['EnvelopeExitPathCount']) - int(Context.SignalLaneDiagnostics['InvalidPrimitivePathCount']):
            Context.SignalLaneDiagnostics['Reason'] = 'all-valid-primitive-egress-paths-self-conflict'
        elif int(Context.SignalLaneDiagnostics['ForeignCorridorConflictPathCount']) == int(Context.SignalLaneDiagnostics['EnvelopeExitPathCount']) - int(Context.SignalLaneDiagnostics['InvalidPrimitivePathCount']) - int(Context.SignalLaneDiagnostics['SelfClaimConflictPathCount']):
            Context.SignalLaneDiagnostics['Reason'] = 'all-remaining-egress-paths-conflict-with-foreign-corridor'
        else:
            Context.SignalLaneDiagnostics['Reason'] = 'no-legal-generated-seam-mixed-rejections'

def CertifyPhysicalPortFactors(Context):
    Context.GuideFingerprint = BuildStableFingerprint((tuple(((Signal, tuple(sorted(Values))) for Signal, Values in sorted(Context.CoarsePlan.Guides.items()))), tuple(sorted(Context.CoarsePlan.Layers.items()))))
    Context.LocalAccessFactorsBySignal, Context.ApertureFactorsBySignal, Context.LocalApertureSupportBySignal = DecomposePhysicalPortLaneFactors(Context.LaneFactorsBySignal, Context.ChannelReservations, Context.ResourceGraph, FabricOrigin=Context.FabricOrigin)
    Context.ExteriorFixedClaimCertificates = CertifyPhysicalPortExteriorFixedClaims(Context.Problem, Context.Profiles, dict(Context.ApertureFactorsBySignal), Context.ResourceGraph, Context.FrozenComponentClaims, TechnologyFingerprint=Context.TechnologyFingerprint or BuildStableFingerprint(repr(getattr(Context.ResourceGraph, 'Technology', None))), ResourceGraphIdentityFingerprint=Context.ResourceGraphFingerprint) if Context.Profiles is not None else ()
    Context.RejectedApertureFingerprints = frozenset((Certificate.ApertureOptionFingerprint for Certificate in Context.ExteriorFixedClaimCertificates if Certificate.Complete and (not Certificate.Feasible)))
    if Context.RejectedApertureFingerprints:
        Context.RejectedSeamFingerprints = frozenset((Support.SourceSeamFingerprint for _Signal, Supports in Context.LocalApertureSupportBySignal for Support in Supports if Support.ApertureOptionFingerprint in Context.RejectedApertureFingerprints))
        Context.LaneFactorsBySignal = {Signal: tuple((replace(LaneFactor, Seams=tuple((Seam for Seam in LaneFactor.Seams if Seam.SeamFingerprint not in Context.RejectedSeamFingerprints))) for LaneFactor in Values if any((Seam.SeamFingerprint not in Context.RejectedSeamFingerprints for Seam in LaneFactor.Seams)))) for Signal, Values in Context.LaneFactorsBySignal.items()}
        Context.LocalAccessFactorsBySignal, Context.ApertureFactorsBySignal, Context.LocalApertureSupportBySignal = DecomposePhysicalPortLaneFactors(Context.LaneFactorsBySignal, Context.ChannelReservations, Context.ResourceGraph, FabricOrigin=Context.FabricOrigin)
        Context.CertificatesBySignal: dict[str, list[dict[str, object]]] = defaultdict(list)
        for Context.Certificate in Context.ExteriorFixedClaimCertificates:
            if not Context.Certificate.Feasible:
                Context.CertificatesBySignal[Context.Certificate.Signal].append(Context.Certificate.ToDictionary())
        for Context.Signal, Context.Certificates in Context.CertificatesBySignal.items():
            Context.Diagnostics = Context.LaneFactorDiagnosticsBySignal.setdefault(Context.Signal, {})
            Context.Diagnostics['ExteriorFixedClaimRejectedApertureCount'] = len(Context.Certificates)
            Context.Diagnostics['ExteriorFixedClaimCertificates'] = Context.Certificates
            Context.Diagnostics['Reason'] = 'all-exterior-apertures-conflict-with-immutable-claims' if not Context.LaneFactorsBySignal.get(Context.Signal) else 'exterior-fixed-claim-apertures-pruned'

def CachePhysicalPortLocalFactors(Context):
    """Finalize authoritative factors without eager derived-domain publication."""
    Context.LocalFactorPreparationStartedAt = monotonic()
    Context.LocalFactorDomainsBySignal = {}
    Context.LocalFactorCacheHitSignals: list[str] = []
    Context.LocalFactorRebuiltSignals: list[str] = []
    Context.LocalFactorPreparationElapsedSeconds = monotonic() - Context.LocalFactorPreparationStartedAt
    Context.ExteriorFactorPreparationElapsedSeconds = Context.LocalFactorPreparationStartedAt - Context.ExteriorFactorPreparationStartedAt
    Context.EmptySignals = tuple(sorted((Signal for Signal, Values in Context.LaneFactorsBySignal.items() if not Values)))
    Context.BoundaryPortReservationsBySignal = tuple(((Signal, tuple((BuildPhysicalComponentBoundaryPortReservation(Value) for Value in Values))) for Signal, Values in Context.ApertureFactorsBySignal))
    Context.FactorDomainIdentity = tuple(((Signal, tuple(((Value.FabricDomainFingerprint, tuple((Candidate.CandidateFingerprint for Candidates in Value.CandidateDomains for Candidate in Candidates)), tuple((Seam.SeamFingerprint for Seam in Value.Seams))) for Value in Values))) for Signal, Values in sorted(Context.LaneFactorsBySignal.items())))
    Context.FactorDomainFingerprint = BuildStableFingerprint((Context.Problem.PlacementFingerprint, Context.ComponentGraphFingerprint, Context.ResourceGraphFingerprint, Context.GuideFingerprint, Context.AccessCertificate.CertificateFingerprint, Context.ExteriorFabricSetFingerprint, str(Context.AuthoritativeRegionFingerprint), Context.ExteriorCapacityLedgerFingerprint, tuple((Value.ReservationFingerprint for Value in Context.ChannelReservations)), tuple((Domain.DomainFingerprint for Domain in (Context.FeedthroughEndpointDomainsBySignal[Signal] for Signal in sorted(Context.FeedthroughEndpointDomainsBySignal)))), Context.FactorDomainIdentity, tuple(((Signal, tuple((Value.LocalAccessFingerprint for Value in Values))) for Signal, Values in Context.LocalAccessFactorsBySignal)), tuple(((Signal, tuple((Value.ApertureOptionFingerprint for Value in Values))) for Signal, Values in Context.ApertureFactorsBySignal)), tuple(((Signal, tuple((Value.SupportFingerprint for Value in Values))) for Signal, Values in Context.LocalApertureSupportBySignal)), tuple((Value.CertificateFingerprint for Value in Context.ExteriorFixedClaimCertificates))))
    Context.LocalApertureSupportsByOptionValues: dict[tuple[str, str], list[PhysicalPortLocalApertureSupport]] = defaultdict(list)
    for Context.Signal, Context.SupportsForSignal in Context.LocalApertureSupportBySignal:
        for Context.Support in Context.SupportsForSignal:
            Context.LocalApertureSupportsByOptionValues[Context.Signal, Context.Support.ApertureOptionFingerprint].append(Context.Support)
    Context.LocalApertureSupportsByOption = tuple(((Key, tuple(sorted(Values, key=lambda Value: Value.SupportFingerprint))) for Key, Values in sorted(Context.LocalApertureSupportsByOptionValues.items())))

def FinalizePhysicalPortPreparation(Context):
    Context.Preparation = PreparedPhysicalComponentPortFactorDomain(DomainFingerprint=Context.FactorDomainFingerprint, PlacementFingerprint=Context.Problem.PlacementFingerprint, ComponentGraphFingerprint=Context.ComponentGraphFingerprint, ResourceGraphFingerprint=Context.ResourceGraphFingerprint, GuideFingerprint=Context.GuideFingerprint, AccessCertificateFingerprint=Context.AccessCertificate.CertificateFingerprint, AccessCertificatePlacementFingerprint=Context.AccessCertificate.PlacementFingerprint, AccessCertificateResourceGraphFingerprint=Context.AccessCertificate.ResourceGraphFingerprint, AccessCertificateComponentGraphFingerprint=Context.AccessCertificate.ComponentGraphFingerprint, Problem=Context.Problem, CoarsePlan=Context.CoarsePlan, AccessCertificate=Context.AccessCertificate, ChannelReservations=tuple(Context.ChannelReservations), LaneFactorsBySignal=tuple(sorted(Context.LaneFactorsBySignal.items())), DiagnosticsBySignal=tuple(((Signal, Context.LaneFactorDiagnosticsBySignal[Signal]) for Signal in sorted(Context.LaneFactorDiagnosticsBySignal))), FabricOrigin=Context.FabricOrigin, MinimumPlacementY=Context.MinimumPlacementY, ComponentEnvelopeMinimum=Context.ComponentEnvelopeMinimum, ComponentEnvelopeMaximum=Context.ComponentEnvelopeMaximum, FabricAdjacency=tuple(((Node, tuple(sorted(Neighbors))) for Node, Neighbors in sorted(Context.FabricAdjacency.items()))), ComponentKeepoutNodes=Context.ComponentKeepoutNodes, ComponentKeepoutGuideCellsByLayer=tuple(sorted(Context.ComponentKeepoutGuideCellsByLayer.items())), LaneFactorExpansionCount=Context.LaneFactorExpansionCount, AccessFactorExpansionCount=Context.AccessFactorExpansionCount, SeamFactorExpansionCount=Context.SeamFactorExpansionCount, GlobalConnectorSearchCount=Context.GlobalConnectorSearchCount, GlobalConnectorCacheHitCount=Context.GlobalConnectorCacheHitCount, GlobalConnectorPortableCacheHitCount=Context.GlobalConnectorPortableCacheHitCount, GlobalConnectorPortableCacheValidationRejectCount=Context.GlobalConnectorPortableCacheValidationRejectCount, GlobalConnectorPortableCacheStoreCount=Context.GlobalConnectorPortableCacheStoreCount, GlobalConnectorExpansionCount=Context.GlobalConnectorExpansionCount, GlobalGuideFieldBuildCount=Context.GlobalGuideFieldBuildCount, GlobalGuideFieldExpansionCount=Context.GlobalGuideFieldExpansionCount, GlobalGuideFieldHitCount=Context.GlobalGuideFieldHitCount, GlobalGuideFieldCanonicalPathCount=Context.GlobalGuideFieldCanonicalPathCount, GlobalGuideFieldFallbackCount=Context.GlobalGuideFieldFallbackCount, NativeConnectorBatchWorkItems=Context.NativeConnectorBatchWorkItems, NativeConnectorBatchActiveWorkerCount=Context.NativeConnectorBatchActiveWorkerCount, Complete=bool(all((Domain.Complete for Domain in Context.FeedthroughEndpointDomainsBySignal.values())) and (Context.Profiles is None or all((Certificate.Complete for Certificate in Context.ExteriorFixedClaimCertificates)))), Feasible=bool(not Context.EmptySignals and all((Domain.Candidates for Domain in Context.FeedthroughEndpointDomainsBySignal.values()))), LocalAccessFactorsBySignal=Context.LocalAccessFactorsBySignal, ApertureFactorsBySignal=Context.ApertureFactorsBySignal, LocalApertureSupportBySignal=Context.LocalApertureSupportBySignal, LocalApertureSupportsByOption=Context.LocalApertureSupportsByOption, SignalLocalFactorDomains=tuple(sorted(Context.LocalFactorDomainsBySignal.items())), LocalFactorCacheHitSignals=tuple(sorted(Context.LocalFactorCacheHitSignals)), LocalFactorRebuiltSignals=tuple(sorted(Context.LocalFactorRebuiltSignals)), LocalFactorPreparationElapsedSeconds=Context.LocalFactorPreparationElapsedSeconds, ExteriorFactorPreparationElapsedSeconds=Context.ExteriorFactorPreparationElapsedSeconds, FactorPreparationTimings=tuple(sorted(Context.FactorPreparationTimings.items())), PhysicalLocalSeamEligibilityCacheHitCount=int(Context.PhysicalLocalSeamEligibilityCacheStatistics.get('HitCount', 0)), PhysicalLocalSeamEligibilityCacheMissCount=int(Context.PhysicalLocalSeamEligibilityCacheStatistics.get('MissCount', 0)), PhysicalLocalSeamEligibilityCacheStoreCount=int(Context.PhysicalLocalSeamEligibilityCacheStatistics.get('StoreCount', 0)), ExteriorFixedClaimCertificates=Context.ExteriorFixedClaimCertificates, BoundaryPortReservationsBySignal=Context.BoundaryPortReservationsBySignal, FeedthroughEndpointDomains=tuple((Context.FeedthroughEndpointDomainsBySignal[Signal] for Signal in sorted(Context.FeedthroughEndpointDomainsBySignal))), ExteriorFabricSetFingerprint=Context.ExteriorFabricSetFingerprint, ExteriorRegionFingerprint=str(Context.AuthoritativeRegionFingerprint), ExteriorCapacityLedgerFingerprint=Context.ExteriorCapacityLedgerFingerprint, ExteriorFabrics=Context.ExteriorFabrics)
    Context.Resources.PreparedPhysicalComponentPortFactorDomain = Context.Preparation
    return Context.Preparation

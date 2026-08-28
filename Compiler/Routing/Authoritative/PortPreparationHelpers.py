"""Importable helpers for exact physical-port factor preparation."""

from __future__ import annotations

from ..Contracts.Core import Position3
from ..Contracts.PhysicalInterface import PhysicalGlobalAperturePathTemplate
from ..Reliability import BuildStableFingerprint
from ..ResourceGraph import FindClaimConflicts
from ..ResourceGraph import FindSelfClaimConflicts
from ..ResourceGraph import RoutingResourceClaims
from ..Technology import DefaultRedstoneRoutingTechnology
from typing import Any
from typing import Iterable
from typing import Mapping
from .ExteriorConnectors import BuildPhysicalExteriorConnectorDistanceField, BuildPhysicalGlobalApertureSearchKey, BuildPortablePhysicalGlobalApertureContract, FrozenPhysicalExteriorConnectorSearchRequest, MaterializePhysicalGlobalAperturePath, NormalizePhysicalGlobalAperturePath, PreparePhysicalGlobalApertureStaticContract, RetainPhysicalGlobalAperturePathTemplate, SelectPhysicalExteriorConnectorPath
from .PhysicalGuides import FindSignalClaimConflicts
from functools import partial

from .PortPreparationState import (
    PortPreparationState,
    SetPortPreparationState,
)


def BuildGlobalPathToGuide(Context, SeamAttachment: tuple[int, int, int], Direction: tuple[int, int, int], GuideCells: frozenset[tuple[int, int]], Signal: str, Layer: int, ForeignCorridorClaims: Mapping[str, RoutingResourceClaims], *, CollectNativeConnectorRequest: bool=False) -> tuple[tuple[int, int, int], ...]:
    if not GuideCells:
        return ()
    ExteriorFabric = Context.ExteriorFabricByLayer.get(int(Layer))
    if Context.AuthoritativeRegion is not None and ExteriorFabric is None:
        return ()

    def ExteriorEdgeIsLegal(First: Position3, Second: Position3) -> bool:
        return bool(ExteriorFabric.AllowsEdge(First, Second) if ExteriorFabric is not None else Context.ResourceGraph.BuildPrimitive(First, Second) is not None)
    ForeignClaimsFingerprint = BuildStableFingerprint(tuple(((ForeignSignal, tuple(sorted(Claims.WireCells)), tuple(sorted(Claims.SupportCells)), tuple(sorted(Claims.RequiredAirCells)), tuple(sorted(Claims.ElectricalCells))) for ForeignSignal, Claims in sorted(ForeignCorridorClaims.items()))))
    CacheKey = BuildPhysicalGlobalApertureSearchKey(Signal, SeamAttachment, Direction, Layer, GuideCells, ForeignClaimsFingerprint)
    if CacheKey in Context.GlobalConnectorCache:
        Context.GlobalConnectorCacheHitCount += 1
        return Context.GlobalConnectorCache[CacheKey]
    TargetContextKey = (Signal, int(Layer), int(SeamAttachment[1]), GuideCells)
    Targets = Context.GlobalApertureTargetsCache.get(TargetContextKey)
    if Targets is None:
        Context.GlobalApertureTargetContextBuildCount += 1
        Targets = frozenset(((X, SeamAttachment[1], Z) for X, Z in GuideCells if not (Context.ComponentEnvelopeMinimum[0] <= X <= Context.ComponentEnvelopeMaximum[0] and Context.ComponentEnvelopeMinimum[2] <= Z <= Context.ComponentEnvelopeMaximum[2]) and (ExteriorFabric is None or ExteriorFabric.AllowsNode((X, SeamAttachment[1], Z)))))
        Context.GlobalApertureTargetsCache[TargetContextKey] = Targets
    if not Targets:
        return ()
    HasForeignCorridorClaims = bool(ForeignCorridorClaims)
    ForeignClaimsCacheKey = (Signal, ForeignClaimsFingerprint)
    ForeignClaims = Context.GlobalConnectorForeignClaimsCache.get(ForeignClaimsCacheKey)
    if HasForeignCorridorClaims and ForeignClaims is None:
        ForeignClaims = RoutingResourceClaims(WireCells=frozenset((Position for Claims in ForeignCorridorClaims.values() for Position in Claims.WireCells)), SupportCells=frozenset((Position for Claims in ForeignCorridorClaims.values() for Position in Claims.SupportCells)), RequiredAirCells=frozenset((Position for Claims in ForeignCorridorClaims.values() for Position in Claims.RequiredAirCells)), ElectricalCells=frozenset((Position for Claims in ForeignCorridorClaims.values() for Position in Claims.ElectricalCells)))
        Context.GlobalConnectorForeignClaimsCache[ForeignClaimsCacheKey] = ForeignClaims

    def ForeignEdgeIsLegal(Current: Position3, Neighbor: Position3) -> bool:
        if not HasForeignCorridorClaims:
            return True
        Edge = tuple(sorted((Current, Neighbor)))
        EdgeKey = (Signal, ForeignClaimsFingerprint, Edge[0], Edge[1])
        Cached = Context.GlobalConnectorForeignEdgeLegalityCache.get(EdgeKey)
        if Cached is not None:
            return Cached
        EdgeClaims = Context.ResourceGraph.BuildRouteClaims((Current, Neighbor))
        assert ForeignClaims is not None
        Cached = not FindClaimConflicts({'ForeignCorridors': ForeignClaims, Signal: EdgeClaims})
        Context.GlobalConnectorForeignEdgeLegalityCache[EdgeKey] = Cached
        return Cached
    MinimumStemLength = max(1, int(getattr(Context.ResourceGraph.Technology, 'TrackPitch', DefaultRedstoneRoutingTechnology.TrackPitch)))
    DeltaX = int(Direction[0])
    DeltaZ = int(Direction[2])
    if not (DeltaX or DeltaZ):
        return ()
    BlockedGuideCells = frozenset(Context.ComponentKeepoutGuideCellsByLayer.get(int(Layer), frozenset()))

    def ConnectorClaimsAreLegal(GlobalPath: tuple[Position3, ...]) -> bool:
        CandidateClaims = Context.ResourceGraph.BuildRouteClaims(frozenset(GlobalPath))
        return not (FindSelfClaimConflicts({Signal: CandidateClaims}) or FindSignalClaimConflicts({**ForeignCorridorClaims, Signal: CandidateClaims}, Signal))

    def PortablePathIsValid(PortablePath: tuple[Position3, ...]) -> bool:
        return bool(len(PortablePath) >= 2 and PortablePath[0] == SeamAttachment and (tuple((PortablePath[1][Index] - PortablePath[0][Index] for Index in range(3))) == Direction) and (PortablePath[-1] in Targets) and (not any((Context.ComponentEnvelopeMinimum[0] <= Position[0] <= Context.ComponentEnvelopeMaximum[0] and Context.ComponentEnvelopeMinimum[2] <= Position[2] <= Context.ComponentEnvelopeMaximum[2] or (Position[0], Position[2]) in BlockedGuideCells for Position in PortablePath[1:]))) and all((ExteriorEdgeIsLegal(First, Second) and ForeignEdgeIsLegal(First, Second) for First, Second in zip(PortablePath, PortablePath[1:]))) and ConnectorClaimsAreLegal(PortablePath))
    StaticContractKey = (Signal, tuple(Direction), int(Layer), int(SeamAttachment[1]), GuideCells, ForeignClaimsFingerprint)
    PreparedStaticContract = Context.GlobalApertureStaticContractCache.get(StaticContractKey)
    if PreparedStaticContract is None:
        Context.GlobalApertureStaticContractBuildCount += 1
        PreparedStaticContract = PreparePhysicalGlobalApertureStaticContract(Direction, Layer, Targets, Context.ComponentEnvelopeMinimum, Context.ComponentEnvelopeMaximum, BlockedGuideCells, ForeignClaims, Context.ResourceGraph.Technology)
        Context.GlobalApertureStaticContractCache[StaticContractKey] = PreparedStaticContract
    PortableContractFingerprint, PortableCanonicalContract, PortableCanonicalTransform = BuildPortablePhysicalGlobalApertureContract(SeamAttachment, Direction, Layer, Targets, Context.ComponentEnvelopeMinimum, Context.ComponentEnvelopeMaximum, BlockedGuideCells, ForeignClaims, Context.ResourceGraph.Technology, PreparedStaticContract=PreparedStaticContract)
    PortableTemplate = Context.Resources.PhysicalGlobalApertureTemplateCache.get(PortableContractFingerprint)
    if isinstance(PortableTemplate, PhysicalGlobalAperturePathTemplate) and PortableTemplate.CanonicalContract == PortableCanonicalContract:
        PortablePath = MaterializePhysicalGlobalAperturePath(PortableTemplate.CanonicalPath, SeamAttachment, PortableCanonicalTransform)
        if PortablePathIsValid(PortablePath):
            Context.GlobalConnectorPortableCacheHitCount += 1
            Context.GlobalConnectorCache[CacheKey] = PortablePath
            return PortablePath
        Context.GlobalConnectorPortableCacheValidationRejectCount += 1

    def RetainPortablePath(Path: tuple[Position3, ...]) -> None:
        RetainPhysicalGlobalAperturePathTemplate(Context.Resources.PhysicalGlobalApertureTemplateCache, PhysicalGlobalAperturePathTemplate(ContractFingerprint=PortableContractFingerprint, CanonicalContract=PortableCanonicalContract, CanonicalPath=NormalizePhysicalGlobalAperturePath(Path, SeamAttachment, PortableCanonicalTransform), SourcePlacementFingerprint=Context.Problem.PlacementFingerprint))
        Context.GlobalConnectorPortableCacheStoreCount += 1
    MaximumStemLength = max(MinimumStemLength, Context.ComponentEnvelopeMaximum[0] - Context.ComponentEnvelopeMinimum[0] + Context.ComponentEnvelopeMaximum[2] - Context.ComponentEnvelopeMinimum[2] + 2 * MinimumStemLength + 4)
    StemLength = MinimumStemLength
    while StemLength <= MaximumStemLength:
        CurrentCell = (SeamAttachment[0] + StemLength * DeltaX, SeamAttachment[2] + StemLength * DeltaZ)
        NextCell = (CurrentCell[0] + DeltaX, CurrentCell[1] + DeltaZ)
        if CurrentCell not in BlockedGuideCells and NextCell not in BlockedGuideCells:
            break
        StemLength += 1
    if StemLength > MaximumStemLength:
        return ()
    StemPath = (SeamAttachment, *(tuple((SeamAttachment[Index] + Distance * Direction[Index] for Index in range(3))) for Distance in range(1, StemLength + 1)))
    if any((not ExteriorEdgeIsLegal(First, Second) or not ForeignEdgeIsLegal(First, Second) for First, Second in zip(StemPath, StemPath[1:]))):
        return ()
    FirstGuideIndex = next((Index for Index, Position in enumerate(StemPath) if Position in Targets), None)
    if FirstGuideIndex is not None:
        Result = StemPath[:max(2, FirstGuideIndex + 1)]
        CandidateClaims = Context.ResourceGraph.BuildRouteClaims(frozenset(Result))
        if not (FindSelfClaimConflicts({Signal: CandidateClaims}) or FindSignalClaimConflicts({**ForeignCorridorClaims, Signal: CandidateClaims}, Signal)):
            Context.GlobalConnectorCache[CacheKey] = Result
            RetainPortablePath(Result)
            return Result
        Context.GlobalConnectorCache[CacheKey] = ()
        return ()
    Start = StemPath[-1]
    Margin = max(2, MinimumStemLength + 1)
    BlockedLocalNodes = frozenset(StemPath[:-1])
    FieldMinimumX = min(Context.ComponentEnvelopeMinimum[0] - MaximumStemLength, *(Value[0] for Value in Targets)) - Margin
    FieldMaximumX = max(Context.ComponentEnvelopeMaximum[0] + MaximumStemLength, *(Value[0] for Value in Targets)) + Margin
    FieldMinimumZ = min(Context.ComponentEnvelopeMinimum[2] - MaximumStemLength, *(Value[2] for Value in Targets)) - Margin
    FieldMaximumZ = max(Context.ComponentEnvelopeMaximum[2] + MaximumStemLength, *(Value[2] for Value in Targets)) + Margin
    FieldBounds = (FieldMinimumX, FieldMaximumX, FieldMinimumZ, FieldMaximumZ)
    FieldKey = (int(Layer), Targets, Context.ComponentEnvelopeMinimum, Context.ComponentEnvelopeMaximum, BlockedGuideCells, ForeignClaimsFingerprint, Context.ResourceGraphFingerprint, ExteriorFabric.FabricFingerprint if ExteriorFabric is not None else '', FieldBounds)
    Field = Context.GlobalGuideFieldCache.get(FieldKey)
    if Field is None:
        Context.GlobalGuideFieldBuildCount += 1
        Field = BuildPhysicalExteriorConnectorDistanceField(Context.ResourceGraph, Targets, EnvelopeMinimum=Context.ComponentEnvelopeMinimum, EnvelopeMaximum=Context.ComponentEnvelopeMaximum, BlockedGuideCells=BlockedGuideCells, Margin=Margin, EdgeIsLegal=ForeignEdgeIsLegal, Bounds=FieldBounds, ResourceGraphFingerprint=Context.ResourceGraphFingerprint, ForeignClaimsFingerprint=ForeignClaimsFingerprint, ExteriorFabric=ExteriorFabric, WorkCheck=(lambda Details: Context.WorkCheck({'Signal': Signal, 'GuideFieldBuildCount': Context.GlobalGuideFieldBuildCount, **Details})) if Context.WorkCheck is not None else None)
        Context.GlobalGuideFieldCache[FieldKey] = Field
        Context.GlobalGuideFieldExpansionCount += Field.BuildExpansionCount
    else:
        Context.GlobalGuideFieldHitCount += 1
    NativeSearchKey = tuple(CacheKey)
    NativeEligible = bool(Field.Complete and (not HasForeignCorridorClaims) and (_SearchExteriorConnectorsBatchWithTelemetry is not None))
    if CollectNativeConnectorRequest:
        if NativeEligible:
            Context.NativeConnectorSearchRequests.setdefault(NativeSearchKey, FrozenPhysicalExteriorConnectorSearchRequest(Field=Field, Start=Start, BlockedLocalNodes=BlockedLocalNodes))
        return ()
    Context.GlobalConnectorSearchCount += 1
    if Context.WorkCheck is not None and (Context.GlobalConnectorSearchCount == 1 or Context.GlobalConnectorSearchCount % 64 == 0):
        Context.WorkCheck({'Stage': 'physical-port-global-connector', 'Signal': Signal, 'ConnectorSearchCount': Context.GlobalConnectorSearchCount, 'ConnectorExpansionCount': Context.GlobalConnectorExpansionCount, 'ConnectorCacheHitCount': Context.GlobalConnectorCacheHitCount, 'ConnectorPortableCacheHitCount': Context.GlobalConnectorPortableCacheHitCount, 'ConnectorPortableCacheValidationRejectCount': Context.GlobalConnectorPortableCacheValidationRejectCount, 'ConnectorPortableCacheStoreCount': Context.GlobalConnectorPortableCacheStoreCount, 'GuideFieldBuildCount': Context.GlobalGuideFieldBuildCount, 'GuideFieldExpansionCount': Context.GlobalGuideFieldExpansionCount, 'GuideFieldHitCount': Context.GlobalGuideFieldHitCount, 'GuideFieldCanonicalPathCount': Context.GlobalGuideFieldCanonicalPathCount, 'GuideFieldFallbackCount': Context.GlobalGuideFieldFallbackCount, 'NativeConnectorBatchWorkItems': Context.NativeConnectorBatchWorkItems, 'NativeConnectorBatchActiveWorkerCount': Context.NativeConnectorBatchActiveWorkerCount, 'ApertureTargetContextBuildCount': Context.GlobalApertureTargetContextBuildCount, 'ApertureStaticContractBuildCount': Context.GlobalApertureStaticContractBuildCount, 'GuideCellCount': len(GuideCells)})
    NativePathResult = Context.NativeConnectorSearchResults.get(NativeSearchKey) if NativeEligible else None
    if NativePathResult is not None and NativePathResult.Path:
        Candidate = tuple((*StemPath[:-1], *NativePathResult.Path))
        if ConnectorClaimsAreLegal(Candidate):
            if NativePathResult.UsedCanonicalField:
                Context.GlobalGuideFieldCanonicalPathCount += 1
            if NativePathResult.UsedFallback:
                Context.GlobalGuideFieldFallbackCount += 1
                Context.GlobalConnectorExpansionCount += NativePathResult.FallbackExpansionCount
            Context.GlobalConnectorCache[CacheKey] = Candidate
            RetainPortablePath(Candidate)
            return Candidate
    PathResult = SelectPhysicalExteriorConnectorPath(Field, Context.ResourceGraph, Start, BlockedLocalNodes=BlockedLocalNodes, EdgeIsLegal=ForeignEdgeIsLegal, ValidateCandidate=lambda Suffix: ConnectorClaimsAreLegal(tuple((*StemPath[:-1], *Suffix))), WorkCheck=(lambda Details: Context.WorkCheck({'Signal': Signal, 'ConnectorSearchCount': Context.GlobalConnectorSearchCount, **Details})) if Context.WorkCheck is not None else None)
    if PathResult.UsedCanonicalField:
        Context.GlobalGuideFieldCanonicalPathCount += 1
    if PathResult.UsedFallback:
        Context.GlobalGuideFieldFallbackCount += 1
        Context.GlobalConnectorExpansionCount += PathResult.FallbackExpansionCount
    if not PathResult.Path:
        Context.GlobalConnectorCache[CacheKey] = ()
        return ()
    Result = tuple((*StemPath[:-1], *PathResult.Path))
    Context.GlobalConnectorCache[CacheKey] = Result
    RetainPortablePath(Result)
    return Result


def DeduplicateCertifiedAccessCandidates(Context, Candidates: Iterable[Any]) -> tuple[Any, ...]:
    ByGeometry = {}
    for Candidate in sorted(Candidates, key=lambda Value: Value.CandidateFingerprint):
        Key = (Candidate.Attachment, tuple(Candidate.Path), Candidate.Layer, Candidate.Claims.WireCells, Candidate.Claims.SupportCells, Candidate.Claims.RequiredAirCells, Candidate.Claims.ElectricalCells)
        ByGeometry.setdefault(Key, Candidate)
    return tuple(ByGeometry.values())

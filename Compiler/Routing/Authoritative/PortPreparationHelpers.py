"""Importable helpers for exact physical-port factor preparation."""

from __future__ import annotations

from ..Contracts.Core import Position3
from ..Contracts.PhysicalInterface import PhysicalGlobalAperturePathTemplate
from ..Reliability import BuildStableFingerprint
from ..ResourceGraph import FindClaimConflicts
from ..ResourceGraph import FindSelfClaimConflicts
from ..ResourceGraph import RoutingResourceClaims
from ..Technology import DefaultRedstoneRoutingTechnology
from ..Components.Fabric import FilterExternalSourcePoweredSeamCandidateDomains
from dataclasses import dataclass
from typing import Any
from typing import Iterable
from typing import Mapping
from .ExteriorConnectors import BuildPhysicalExteriorConnectorDistanceField, BuildPhysicalGlobalApertureSearchKey, BuildPortablePhysicalGlobalApertureContract, FrozenPhysicalExteriorConnectorSearchRequest, MaterializePhysicalGlobalAperturePath, NativeExteriorConnectorSearchAvailable, NormalizePhysicalGlobalAperturePath, PreparePhysicalGlobalApertureStaticContract, RetainPhysicalGlobalAperturePathTemplate, SelectPhysicalExteriorConnectorPath
from .PhysicalGuides import FindSignalClaimConflicts
from functools import partial

from .PortPreparationState import (
    PortPreparationState,
    SetPortPreparationState,
)


@dataclass(frozen=True)
class PreparedCertifiedPhysicalPortLocalSeam:
    """Exact powered-local projection of one certified perimeter seam."""

    Signal: str
    CandidateFingerprint: str
    PoweredCandidateDomains: tuple[tuple[Any, ...], ...]
    BoundCandidates: tuple[Any, ...]
    Complete: bool
    Feasible: bool


def RecordPhysicalGlobalPathRejection(
    Context,
    Signal: str,
    Reason: str,
) -> None:
    Counts = Context.GlobalPathRejectionCountsBySignal.setdefault(Signal, {})
    Counts[Reason] = int(Counts.get(Reason, 0)) + 1


def RecordPhysicalGlobalApertureTargetProjection(
    Context,
    Signal: str,
    *,
    SeamAttachment: Position3,
    FabricAttachment: Position3 | None,
    EnvelopeMinimum: Position3,
    EnvelopeMaximum: Position3,
    GuideNodes: tuple[Position3, ...],
    OutsideEnvelopeGuideNodes: tuple[Position3, ...],
    ExteriorAllowedGuideNodes: tuple[Position3, ...],
    StraightFallbackTargets: frozenset[Position3],
    FinalTargets: frozenset[Position3],
) -> None:
    """Publish bounded evidence for exact exterior-target projection."""
    DiagnosticsBySignal = getattr(
        Context,
        "GlobalApertureTargetDiagnosticsBySignal",
        None,
    )
    if DiagnosticsBySignal is None:
        DiagnosticsBySignal = {}
        Context.GlobalApertureTargetDiagnosticsBySignal = DiagnosticsBySignal
    Diagnostics = DiagnosticsBySignal.setdefault(
        Signal,
        {
            "TargetContextBuildCount": 0,
            "EmptyFinalTargetContextCount": 0,
            "GuideCellCountTotal": 0,
            "OutsideEnvelopeGuideCellCountTotal": 0,
            "ExteriorAllowedGuideCellCountTotal": 0,
            "StraightFallbackTargetCountTotal": 0,
            "FinalTargetCountTotal": 0,
            "EmptyTargetSamples": [],
        },
    )
    Counts = {
        "GuideCellCount": len(GuideNodes),
        "OutsideEnvelopeGuideCellCount": len(OutsideEnvelopeGuideNodes),
        "ExteriorAllowedGuideCellCount": len(ExteriorAllowedGuideNodes),
        "StraightFallbackTargetCount": len(StraightFallbackTargets),
        "FinalTargetCount": len(FinalTargets),
    }
    Diagnostics["TargetContextBuildCount"] = (
        int(Diagnostics["TargetContextBuildCount"]) + 1
    )
    if not FinalTargets:
        Diagnostics["EmptyFinalTargetContextCount"] = (
            int(Diagnostics["EmptyFinalTargetContextCount"]) + 1
        )
    for Name, Count in Counts.items():
        TotalName = f"{Name}Total"
        Diagnostics[TotalName] = int(Diagnostics[TotalName]) + Count
        MinimumName = f"Minimum{Name}"
        MaximumName = f"Maximum{Name}"
        Diagnostics[MinimumName] = min(
            int(Diagnostics.get(MinimumName, Count)),
            Count,
        )
        Diagnostics[MaximumName] = max(
            int(Diagnostics.get(MaximumName, Count)),
            Count,
        )
    Samples = Diagnostics["EmptyTargetSamples"]
    assert isinstance(Samples, list)
    if not FinalTargets and len(Samples) < 8:
        ExteriorAllowedGuideNodeSet = frozenset(ExteriorAllowedGuideNodes)
        ExteriorRejectedGuideNodes = tuple(
            Node
            for Node in OutsideEnvelopeGuideNodes
            if Node not in ExteriorAllowedGuideNodeSet
        )
        Samples.append({
            "SeamAttachment": list(SeamAttachment),
            "FabricAttachment": (
                list(FabricAttachment)
                if FabricAttachment is not None
                else None
            ),
            "ComponentEnvelopeMinimum": list(EnvelopeMinimum),
            "ComponentEnvelopeMaximum": list(EnvelopeMaximum),
            **Counts,
            "OutsideEnvelopeGuideCellSamples": [
                list(Node) for Node in OutsideEnvelopeGuideNodes[:8]
            ],
            "ExteriorRejectedGuideCellSamples": [
                list(Node) for Node in ExteriorRejectedGuideNodes[:8]
            ],
            "ExteriorAllowedGuideCellSamples": [
                list(Node) for Node in ExteriorAllowedGuideNodes[:8]
            ],
            "StraightFallbackTargetSamples": [
                list(Node) for Node in sorted(StraightFallbackTargets)[:8]
            ],
        })


def SelectCertifiedStraightExteriorTargets(
    AccessCertificate,
    Signal: str,
    SeamAttachment: Position3,
    Direction: Position3,
    EnvelopeMinimum: Position3,
    EnvelopeMaximum: Position3,
    ExteriorFabric,
) -> frozenset[Position3]:
    """Extend only an exact certified local seam beyond an enclosing box."""
    if not (
        AccessCertificate is not None
        and AccessCertificate.Complete
        and AccessCertificate.Feasible
    ):
        return frozenset()
    Certified = any(
        Candidate.Attachment == SeamAttachment
        and len(Candidate.LocalPath) >= 2
        and tuple(
            Candidate.LocalPath[-1][Index]
            - Candidate.LocalPath[-2][Index]
            for Index in range(3)
        ) == tuple(Direction)
        for Domain in AccessCertificate.PortDomains
        if Domain.Signal == Signal
        for Candidate in Domain.Candidates
    )
    if not Certified or not (Direction[0] or Direction[2]):
        return frozenset()
    MaximumDistance = (
        EnvelopeMaximum[0] - EnvelopeMinimum[0]
        + EnvelopeMaximum[2] - EnvelopeMinimum[2]
        + 4
    )
    for Distance in range(1, MaximumDistance + 1):
        Target = tuple(
            SeamAttachment[Index] + Distance * Direction[Index]
            for Index in range(3)
        )
        Outside = not (
            EnvelopeMinimum[0] <= Target[0] <= EnvelopeMaximum[0]
            and EnvelopeMinimum[2] <= Target[2] <= EnvelopeMaximum[2]
        )
        ExteriorOwned = (
            ExteriorFabric.AllowsNode(Target)
            if ExteriorFabric is not None
            else Outside
        )
        if ExteriorOwned:
            return frozenset((Target,))
    return frozenset()


def BuildGlobalPathToGuide(Context, SeamAttachment: tuple[int, int, int], Direction: tuple[int, int, int], GuideCells: frozenset[tuple[int, int]], Signal: str, Layer: int, ForeignCorridorClaims: Mapping[str, RoutingResourceClaims], *, FabricAttachment: Position3 | None = None, CollectNativeConnectorRequest: bool=False) -> tuple[tuple[int, int, int], ...]:
    if not GuideCells:
        RecordPhysicalGlobalPathRejection(Context, Signal, 'missing-guide-cells')
        return ()
    ExteriorFabric = Context.ExteriorFabricByLayer.get(int(Layer))
    if Context.AuthoritativeRegion is not None and ExteriorFabric is None:
        RecordPhysicalGlobalPathRejection(Context, Signal, 'missing-exterior-fabric-layer')
        return ()
    ComponentEnvelopeMinimum = Context.ComponentEnvelopeMinimum
    ComponentEnvelopeMaximum = Context.ComponentEnvelopeMaximum
    FabricComponentIndex = (
        Context.FabricComponentByNode.get(tuple(FabricAttachment))
        if FabricAttachment is not None
        else None
    )
    FabricEnvelopeBounds = getattr(
        Context,
        "FabricEnvelopeBoundsByComponent",
        {},
    ).get(FabricComponentIndex)
    if FabricEnvelopeBounds is not None:
        ComponentEnvelopeMinimum, ComponentEnvelopeMaximum = (
            FabricEnvelopeBounds
        )

    def ExteriorEdgeIsLegal(First: Position3, Second: Position3) -> bool:
        return bool(ExteriorFabric.AllowsEdge(First, Second) if ExteriorFabric is not None else Context.ResourceGraph.BuildPrimitive(First, Second) is not None)
    ForeignClaimsFingerprint = BuildStableFingerprint(tuple(((ForeignSignal, tuple(sorted(Claims.WireCells)), tuple(sorted(Claims.SupportCells)), tuple(sorted(Claims.RequiredAirCells)), tuple(sorted(Claims.ElectricalCells))) for ForeignSignal, Claims in sorted(ForeignCorridorClaims.items()))))
    CacheKey = (
        *BuildPhysicalGlobalApertureSearchKey(
            Signal,
            SeamAttachment,
            Direction,
            Layer,
            GuideCells,
            ForeignClaimsFingerprint,
        ),
        ComponentEnvelopeMinimum,
        ComponentEnvelopeMaximum,
    )
    if CacheKey in Context.GlobalConnectorCache:
        Context.GlobalConnectorCacheHitCount += 1
        return Context.GlobalConnectorCache[CacheKey]
    TargetContextKey = (
        Signal,
        int(Layer),
        int(SeamAttachment[1]),
        GuideCells,
        tuple(SeamAttachment),
        tuple(Direction),
        ComponentEnvelopeMinimum,
        ComponentEnvelopeMaximum,
        ExteriorFabric.FabricFingerprint if ExteriorFabric is not None else "",
    )
    Targets = Context.GlobalApertureTargetsCache.get(TargetContextKey)
    if Targets is None:
        Context.GlobalApertureTargetContextBuildCount += 1
        ProjectionCache = getattr(
            Context,
            "GlobalApertureGuideProjectionCache",
            None,
        )
        if ProjectionCache is None:
            ProjectionCache = {}
            Context.GlobalApertureGuideProjectionCache = ProjectionCache
        ProjectionKey = (
            Signal,
            int(Layer),
            int(SeamAttachment[1]),
            GuideCells,
            ComponentEnvelopeMinimum,
            ComponentEnvelopeMaximum,
            ExteriorFabric.FabricFingerprint if ExteriorFabric is not None else "",
        )
        Projection = ProjectionCache.get(ProjectionKey)
        if Projection is None:
            GuideNodes = tuple(sorted(
                (X, SeamAttachment[1], Z)
                for X, Z in GuideCells
            ))
            OutsideEnvelopeGuideNodes = tuple(
                Node
                for Node in GuideNodes
                if not (
                    ComponentEnvelopeMinimum[0]
                    <= Node[0]
                    <= ComponentEnvelopeMaximum[0]
                    and ComponentEnvelopeMinimum[2]
                    <= Node[2]
                    <= ComponentEnvelopeMaximum[2]
                )
            )
            ExteriorAllowedGuideNodes = tuple(
                Node
                for Node in GuideNodes
                if (
                    ExteriorFabric.AllowsNode(Node)
                    if ExteriorFabric is not None
                    else Node in OutsideEnvelopeGuideNodes
                )
            )
            Projection = (
                GuideNodes,
                OutsideEnvelopeGuideNodes,
                ExteriorAllowedGuideNodes,
            )
            ProjectionCache[ProjectionKey] = Projection
        (
            GuideNodes,
            OutsideEnvelopeGuideNodes,
            ExteriorAllowedGuideNodes,
        ) = Projection
        Targets = frozenset(ExteriorAllowedGuideNodes)
        StraightFallbackTargets: frozenset[Position3] = frozenset()
        if not Targets:
            StraightFallbackTargets = SelectCertifiedStraightExteriorTargets(
                Context.AccessCertificate,
                Signal,
                SeamAttachment,
                Direction,
                ComponentEnvelopeMinimum,
                ComponentEnvelopeMaximum,
                ExteriorFabric,
            )
            Targets = StraightFallbackTargets
            if Targets:
                Context.CertifiedStraightExteriorTargetCountBySignal[
                    Signal
                ] = (
                    Context.CertifiedStraightExteriorTargetCountBySignal.get(
                        Signal,
                        0,
                    )
                    + len(Targets)
                )
        RecordPhysicalGlobalApertureTargetProjection(
            Context,
            Signal,
            SeamAttachment=SeamAttachment,
            FabricAttachment=FabricAttachment,
            EnvelopeMinimum=ComponentEnvelopeMinimum,
            EnvelopeMaximum=ComponentEnvelopeMaximum,
            GuideNodes=GuideNodes,
            OutsideEnvelopeGuideNodes=OutsideEnvelopeGuideNodes,
            ExteriorAllowedGuideNodes=ExteriorAllowedGuideNodes,
            StraightFallbackTargets=StraightFallbackTargets,
            FinalTargets=Targets,
        )
        Context.GlobalApertureTargetsCache[TargetContextKey] = Targets
    if not Targets:
        RecordPhysicalGlobalPathRejection(Context, Signal, 'no-exterior-guide-targets')
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
        RecordPhysicalGlobalPathRejection(Context, Signal, 'non-horizontal-seam-direction')
        return ()
    BlockedGuideCells = frozenset(Context.ComponentKeepoutGuideCellsByLayer.get(int(Layer), frozenset()))

    def ConnectorClaimsAreLegal(GlobalPath: tuple[Position3, ...]) -> bool:
        CandidateClaims = Context.ResourceGraph.BuildRouteClaims(frozenset(GlobalPath))
        return not (FindSelfClaimConflicts({Signal: CandidateClaims}) or FindSignalClaimConflicts({**ForeignCorridorClaims, Signal: CandidateClaims}, Signal))

    def PortablePathNodeIsLegal(Position: Position3) -> bool:
        ExteriorOwned = (
            ExteriorFabric.AllowsNode(Position)
            if ExteriorFabric is not None
            else not (
                ComponentEnvelopeMinimum[0]
                <= Position[0]
                <= ComponentEnvelopeMaximum[0]
                and ComponentEnvelopeMinimum[2]
                <= Position[2]
                <= ComponentEnvelopeMaximum[2]
            )
        )
        return bool(
            ExteriorOwned
            and (Position[0], Position[2]) not in BlockedGuideCells
        )

    def PortablePathIsValid(PortablePath: tuple[Position3, ...]) -> bool:
        return bool(len(PortablePath) >= 2 and PortablePath[0] == SeamAttachment and (tuple((PortablePath[1][Index] - PortablePath[0][Index] for Index in range(3))) == Direction) and (PortablePath[-1] in Targets) and all((PortablePathNodeIsLegal(Position) for Position in PortablePath[1:])) and all((ExteriorEdgeIsLegal(First, Second) and ForeignEdgeIsLegal(First, Second) for First, Second in zip(PortablePath, PortablePath[1:]))) and ConnectorClaimsAreLegal(PortablePath))
    PortableContractFingerprint = ""
    PortableCanonicalContract: tuple[object, ...] = ()
    PortableCanonicalTransform = ""
    PreparePortableContract = bool(
        not CollectNativeConnectorRequest
        or Context.Resources.PhysicalGlobalApertureTemplateCache
    )
    if PreparePortableContract:
        StaticContractKey = (Signal, tuple(Direction), int(Layer), int(SeamAttachment[1]), GuideCells, ComponentEnvelopeMinimum, ComponentEnvelopeMaximum, ForeignClaimsFingerprint)
        PreparedStaticContract = Context.GlobalApertureStaticContractCache.get(StaticContractKey)
        if PreparedStaticContract is None:
            Context.GlobalApertureStaticContractBuildCount += 1
            PreparedStaticContract = PreparePhysicalGlobalApertureStaticContract(Direction, Layer, Targets, ComponentEnvelopeMinimum, ComponentEnvelopeMaximum, BlockedGuideCells, ForeignClaims, Context.ResourceGraph.Technology)
            Context.GlobalApertureStaticContractCache[StaticContractKey] = PreparedStaticContract
        PortableContractFingerprint, PortableCanonicalContract, PortableCanonicalTransform = BuildPortablePhysicalGlobalApertureContract(SeamAttachment, Direction, Layer, Targets, ComponentEnvelopeMinimum, ComponentEnvelopeMaximum, BlockedGuideCells, ForeignClaims, Context.ResourceGraph.Technology, PreparedStaticContract=PreparedStaticContract)
        PortableTemplate = Context.Resources.PhysicalGlobalApertureTemplateCache.get(PortableContractFingerprint)
        if isinstance(PortableTemplate, PhysicalGlobalAperturePathTemplate) and PortableTemplate.CanonicalContract == PortableCanonicalContract:
            PortablePath = MaterializePhysicalGlobalAperturePath(PortableTemplate.CanonicalPath, SeamAttachment, PortableCanonicalTransform)
            if PortablePathIsValid(PortablePath):
                Context.GlobalConnectorPortableCacheHitCount += 1
                Context.GlobalConnectorCache[CacheKey] = PortablePath
                return PortablePath
            Context.GlobalConnectorPortableCacheValidationRejectCount += 1

    def RetainPortablePath(Path: tuple[Position3, ...]) -> None:
        if CollectNativeConnectorRequest:
            return
        RetainPhysicalGlobalAperturePathTemplate(Context.Resources.PhysicalGlobalApertureTemplateCache, PhysicalGlobalAperturePathTemplate(ContractFingerprint=PortableContractFingerprint, CanonicalContract=PortableCanonicalContract, CanonicalPath=NormalizePhysicalGlobalAperturePath(Path, SeamAttachment, PortableCanonicalTransform), SourcePlacementFingerprint=Context.Problem.PlacementFingerprint))
        Context.GlobalConnectorPortableCacheStoreCount += 1
    MaximumStemLength = max(MinimumStemLength, ComponentEnvelopeMaximum[0] - ComponentEnvelopeMinimum[0] + ComponentEnvelopeMaximum[2] - ComponentEnvelopeMinimum[2] + 2 * MinimumStemLength + 4)
    StemLength = MinimumStemLength
    while StemLength <= MaximumStemLength:
        CurrentCell = (SeamAttachment[0] + StemLength * DeltaX, SeamAttachment[2] + StemLength * DeltaZ)
        NextCell = (CurrentCell[0] + DeltaX, CurrentCell[1] + DeltaZ)
        if CurrentCell not in BlockedGuideCells and NextCell not in BlockedGuideCells:
            break
        StemLength += 1
    if StemLength > MaximumStemLength:
        RecordPhysicalGlobalPathRejection(Context, Signal, 'blocked-straight-stem')
        return ()
    StemPath = (SeamAttachment, *(tuple((SeamAttachment[Index] + Distance * Direction[Index] for Index in range(3))) for Distance in range(1, StemLength + 1)))
    if any((not ExteriorEdgeIsLegal(First, Second) or not ForeignEdgeIsLegal(First, Second) for First, Second in zip(StemPath, StemPath[1:]))):
        RecordPhysicalGlobalPathRejection(Context, Signal, 'illegal-straight-stem')
        return ()
    if (
        not CollectNativeConnectorRequest
        and not ConnectorClaimsAreLegal(StemPath)
    ):
        RecordPhysicalGlobalPathRejection(
            Context,
            Signal,
            'claim-illegal-straight-stem',
        )
        return ()
    FirstGuideIndex = next((Index for Index, Position in enumerate(StemPath) if Position in Targets), None)
    if FirstGuideIndex is not None:
        if CollectNativeConnectorRequest:
            # This prepass freezes geometry-only native work.  Claim legality
            # remains mandatory in the materialization pass before a direct
            # guide path can enter either the factor domain or path cache.
            return ()
        Result = StemPath[:max(2, FirstGuideIndex + 1)]
        CandidateClaims = Context.ResourceGraph.BuildRouteClaims(frozenset(Result))
        if not (FindSelfClaimConflicts({Signal: CandidateClaims}) or FindSignalClaimConflicts({**ForeignCorridorClaims, Signal: CandidateClaims}, Signal)):
            Context.GlobalConnectorCache[CacheKey] = Result
            RetainPortablePath(Result)
            return Result
        Context.GlobalConnectorCache[CacheKey] = ()
        RecordPhysicalGlobalPathRejection(Context, Signal, 'direct-guide-claim-conflict')
        return ()
    Start = StemPath[-1]
    Margin = max(2, MinimumStemLength + 1)
    BlockedLocalNodes = frozenset(StemPath[:-1])
    FieldMinimumX = min(ComponentEnvelopeMinimum[0] - MaximumStemLength, *(Value[0] for Value in Targets)) - Margin
    FieldMaximumX = max(ComponentEnvelopeMaximum[0] + MaximumStemLength, *(Value[0] for Value in Targets)) + Margin
    FieldMinimumZ = min(ComponentEnvelopeMinimum[2] - MaximumStemLength, *(Value[2] for Value in Targets)) - Margin
    FieldMaximumZ = max(ComponentEnvelopeMaximum[2] + MaximumStemLength, *(Value[2] for Value in Targets)) + Margin
    FieldBounds = (FieldMinimumX, FieldMaximumX, FieldMinimumZ, FieldMaximumZ)
    FieldKey = (int(Layer), Targets, ComponentEnvelopeMinimum, ComponentEnvelopeMaximum, BlockedGuideCells, ForeignClaimsFingerprint, Context.ResourceGraphFingerprint, ExteriorFabric.FabricFingerprint if ExteriorFabric is not None else '', FieldBounds)
    Field = Context.GlobalGuideFieldCache.get(FieldKey)
    if Field is None:
        Context.GlobalGuideFieldBuildCount += 1
        Field = BuildPhysicalExteriorConnectorDistanceField(Context.ResourceGraph, Targets, EnvelopeMinimum=ComponentEnvelopeMinimum, EnvelopeMaximum=ComponentEnvelopeMaximum, BlockedGuideCells=BlockedGuideCells, Margin=Margin, EdgeIsLegal=ForeignEdgeIsLegal, Bounds=FieldBounds, ResourceGraphFingerprint=Context.ResourceGraphFingerprint, ForeignClaimsFingerprint=ForeignClaimsFingerprint, ExteriorFabric=ExteriorFabric, WorkCheck=(lambda Details: Context.WorkCheck({'Signal': Signal, 'GuideFieldBuildCount': Context.GlobalGuideFieldBuildCount, **Details})) if Context.WorkCheck is not None else None)
        Context.GlobalGuideFieldCache[FieldKey] = Field
        Context.GlobalGuideFieldExpansionCount += Field.BuildExpansionCount
    else:
        Context.GlobalGuideFieldHitCount += 1
    NativeSearchKey = tuple(CacheKey)
    NativeEligible = bool(Field.Complete and (not HasForeignCorridorClaims) and NativeExteriorConnectorSearchAvailable())
    if CollectNativeConnectorRequest:
        if NativeEligible:
            Context.NativeConnectorSearchRequests.setdefault(NativeSearchKey, FrozenPhysicalExteriorConnectorSearchRequest(Field=Field, Start=Start, BlockedLocalNodes=BlockedLocalNodes))
        return ()
    Context.GlobalConnectorSearchCount += 1
    if Context.WorkCheck is not None and (Context.GlobalConnectorSearchCount == 1 or Context.GlobalConnectorSearchCount % 64 == 0):
        Context.WorkCheck({'Stage': 'physical-port-global-connector', 'Signal': Signal, 'ConnectorSearchCount': Context.GlobalConnectorSearchCount, 'ConnectorExpansionCount': Context.GlobalConnectorExpansionCount, 'ConnectorCacheHitCount': Context.GlobalConnectorCacheHitCount, 'ConnectorPortableCacheHitCount': Context.GlobalConnectorPortableCacheHitCount, 'ConnectorPortableCacheValidationRejectCount': Context.GlobalConnectorPortableCacheValidationRejectCount, 'ConnectorPortableCacheStoreCount': Context.GlobalConnectorPortableCacheStoreCount, 'GuideFieldBuildCount': Context.GlobalGuideFieldBuildCount, 'GuideFieldExpansionCount': Context.GlobalGuideFieldExpansionCount, 'GuideFieldHitCount': Context.GlobalGuideFieldHitCount, 'GuideFieldCanonicalPathCount': Context.GlobalGuideFieldCanonicalPathCount, 'GuideFieldFallbackCount': Context.GlobalGuideFieldFallbackCount, 'NativeConnectorBatchWorkItems': Context.NativeConnectorBatchWorkItems, 'NativeConnectorBatchActiveWorkerCount': Context.NativeConnectorBatchActiveWorkerCount, 'NativeConnectorResultHitCount': Context.NativeConnectorResultHitCount, 'NativeConnectorEmptyResultCount': Context.NativeConnectorEmptyResultCount, 'NativeConnectorAcceptedPathCount': Context.NativeConnectorAcceptedPathCount, 'NativeConnectorValidationRejectCount': Context.NativeConnectorValidationRejectCount, 'ApertureTargetContextBuildCount': Context.GlobalApertureTargetContextBuildCount, 'ApertureStaticContractBuildCount': Context.GlobalApertureStaticContractBuildCount, 'GuideCellCount': len(GuideCells)})
    NativePathResult = Context.NativeConnectorSearchResults.get(NativeSearchKey) if NativeEligible else None
    if NativePathResult is not None:
        Context.NativeConnectorResultHitCount += 1
    if NativePathResult is not None and not NativePathResult.Path:
        Context.NativeConnectorEmptyResultCount += 1
        Context.GlobalConnectorCache[CacheKey] = ()
        RecordPhysicalGlobalPathRejection(
            Context,
            Signal,
            'native-complete-no-path',
        )
        return ()
    if NativePathResult is not None and NativePathResult.Path:
        Candidate = tuple((*StemPath[:-1], *NativePathResult.Path))
        if ConnectorClaimsAreLegal(Candidate):
            Context.NativeConnectorAcceptedPathCount += 1
            if NativePathResult.UsedCanonicalField:
                Context.GlobalGuideFieldCanonicalPathCount += 1
            if NativePathResult.UsedFallback:
                Context.GlobalGuideFieldFallbackCount += 1
                Context.GlobalConnectorExpansionCount += NativePathResult.FallbackExpansionCount
            Context.GlobalConnectorCache[CacheKey] = Candidate
            RetainPortablePath(Candidate)
            return Candidate
        Context.NativeConnectorValidationRejectCount += 1
    PathResult = SelectPhysicalExteriorConnectorPath(Field, Context.ResourceGraph, Start, BlockedLocalNodes=BlockedLocalNodes, EdgeIsLegal=ForeignEdgeIsLegal, ValidateCandidate=lambda Suffix: ConnectorClaimsAreLegal(tuple((*StemPath[:-1], *Suffix))), WorkCheck=(lambda Details: Context.WorkCheck({'Signal': Signal, 'ConnectorSearchCount': Context.GlobalConnectorSearchCount, **Details})) if Context.WorkCheck is not None else None)
    if PathResult.UsedCanonicalField:
        Context.GlobalGuideFieldCanonicalPathCount += 1
    if PathResult.UsedFallback:
        Context.GlobalGuideFieldFallbackCount += 1
        Context.GlobalConnectorExpansionCount += PathResult.FallbackExpansionCount
    if not PathResult.Path:
        Context.GlobalConnectorCache[CacheKey] = ()
        RecordPhysicalGlobalPathRejection(Context, Signal, 'connector-search-exhausted')
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


def PrepareCertifiedPhysicalPortLocalSeams(
    Context,
    Signals: frozenset[str] | None = None,
    *,
    Initialize: bool = True,
) -> None:
    """Project complete certified seams through local powered feasibility.

    Exterior connector construction consumes this immutable projection so a
    seam that is already impossible inside the component never enters the
    native global connector batch.  Lane materialization reuses the same
    result and therefore cannot disagree with the prepass.
    """
    if Initialize or not hasattr(
        Context,
        "CertifiedPhysicalPortLocalSeamsByCandidate",
    ):
        Context.CertifiedPhysicalPortLocalSeamsByCandidate = {}
        Context.CertifiedPhysicalPortLocalSeamCandidateCount = 0
        Context.CertifiedPhysicalPortLocalSeamFeasibleCount = 0
        Context.CertifiedPhysicalPortLocalSeamRejectedCount = 0
        Context.PhysicalLocalSeamEligibilityCacheStatistics = {}
        Context.PhysicalLocalSeamEligibilityDiagnosticsBySignal = {}
    for Port in Context.Problem.Interface.Ports:
        Signal = str(Port.Signal)
        if Signals is not None and Signal not in Signals:
            continue
        RequiredLayer = int(Context.CoarsePlan.Layers.get(Signal, 0))
        Domains = tuple(sorted(
            (
                Domain
                for Domain in Context.Problem.OwnedTerminalDomains
                if str(Domain.Signal) == Signal
            ),
            key=lambda Value: Value.Terminal,
        ))
        if not Domains or any(not Domain.Candidates for Domain in Domains):
            continue
        CertifiedDomain = Context.CertifiedPortDomainBySignal.get(Signal)
        if CertifiedDomain is None or not CertifiedDomain.Candidates:
            continue
        CandidateByFingerprint = {
            Candidate.CandidateFingerprint: Candidate
            for Domain in Domains
            for Candidate in Domain.Candidates
        }
        ComponentIndexes = sorted({
            Context.FabricComponentByNode[Candidate.Attachment]
            for Domain in Domains
            for Candidate in Domain.Candidates
            if Candidate.Attachment in Context.FabricComponentByNode
        })
        CandidateDomainsByFabricComponentIndex = {
            ComponentIndex: tuple(
                DeduplicateCertifiedAccessCandidates(
                    Context,
                    (
                        Candidate
                        for Candidate in Domain.Candidates
                        if Context.FabricComponentByNode.get(
                            Candidate.Attachment
                        ) == ComponentIndex
                    ),
                )
                for Domain in Domains
            )
            for ComponentIndex in ComponentIndexes
        }
        for CertifiedCandidate in CertifiedDomain.Candidates:
            Key = (Signal, str(CertifiedCandidate.CandidateFingerprint))
            if Key in Context.CertifiedPhysicalPortLocalSeamsByCandidate:
                continue
            if int(CertifiedCandidate.Layer) != RequiredLayer:
                continue
            LocalPath = tuple(CertifiedCandidate.LocalPath)
            if len(LocalPath) < 2:
                continue
            if CertifiedCandidate.OwnedCandidateFingerprints:
                SelectedCandidateDomains = tuple(
                    ((CandidateByFingerprint.get(Fingerprint),))
                    for Fingerprint in
                    CertifiedCandidate.OwnedCandidateFingerprints
                )
                if (
                    len(SelectedCandidateDomains) != len(Domains)
                    or any(Values[0] is None for Values in SelectedCandidateDomains)
                ):
                    continue
            else:
                ComponentIndex = Context.FabricComponentByNode.get(
                    CertifiedCandidate.FabricAttachment
                )
                SelectedCandidateDomains = (
                    CandidateDomainsByFabricComponentIndex.get(
                        ComponentIndex,
                        (),
                    )
                )
                if (
                    len(SelectedCandidateDomains) != len(Domains)
                    or any(not Values for Values in SelectedCandidateDomains)
                ):
                    continue
            PoweredCandidateDomains = tuple(
                tuple(Values)
                for Values in FilterExternalSourcePoweredSeamCandidateDomains(
                    Context.Problem,
                    Signal,
                    Domains,
                    SelectedCandidateDomains,
                    LocalPath,
                    FabricAdjacency=Context.PoweredSeamFabricAdjacency,
                    FabricParentCache=Context.PoweredSeamFabricParentCache,
                    RouteClaimsCache=Context.PoweredSeamRouteClaimsCache,
                    TreeRepeaterSubproblemCache=(
                        Context.PoweredSeamTreeRepeaterSubproblemCache
                    ),
                    TreeRepeaterCacheStatistics=(
                        Context.PoweredSeamTreeRepeaterCacheStatistics
                    ),
                    CandidateEligibilityCache=(
                        getattr(
                            getattr(Context, "Resources", None),
                            "PhysicalLocalSeamEligibilityCache",
                            None,
                        )
                    ),
                    CandidateEligibilityCacheStatistics=(
                        Context.PhysicalLocalSeamEligibilityCacheStatistics
                    ),
                    CandidateEligibilityDiagnostics=(
                        Context.PhysicalLocalSeamEligibilityDiagnosticsBySignal
                        .setdefault(Signal, {})
                    ),
                )
            )
            Feasible = bool(
                len(PoweredCandidateDomains) == len(Domains)
                and all(PoweredCandidateDomains)
            )
            BoundCandidates = (
                tuple(Values[0] for Values in PoweredCandidateDomains)
                if Feasible
                and all(len(Values) == 1 for Values in PoweredCandidateDomains)
                else ()
            )
            Context.CertifiedPhysicalPortLocalSeamsByCandidate[Key] = (
                PreparedCertifiedPhysicalPortLocalSeam(
                    Signal=Signal,
                    CandidateFingerprint=str(
                        CertifiedCandidate.CandidateFingerprint
                    ),
                    PoweredCandidateDomains=PoweredCandidateDomains,
                    BoundCandidates=BoundCandidates,
                    Complete=True,
                    Feasible=Feasible,
                )
            )
            Context.CertifiedPhysicalPortLocalSeamCandidateCount += 1
            if Feasible:
                Context.CertifiedPhysicalPortLocalSeamFeasibleCount += 1
            else:
                Context.CertifiedPhysicalPortLocalSeamRejectedCount += 1

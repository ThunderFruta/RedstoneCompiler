"""Deterministic placement-wide access fabric construction."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass, is_dataclass, replace
from functools import lru_cache
from hashlib import sha256
from heapq import heappop, heappush
from math import ceil
from struct import pack
from time import monotonic
from types import MappingProxyType, SimpleNamespace
from typing import Any, Callable, Iterable

from ..Routing.Actions.Geometry import BuildRoutingResources
from ..Routing.ChannelPlanner import BuildNetRoutingProfiles
from ..Routing.Models import (
    BuildPlacementAccessEscapeStubChoiceId,
    FrozenPerFaceRoutingEnvelope,
    PlacementAccessAssignment,
    PlacementAccessEscapeStub,
    PlacementAccessFabric,
    PlacementAccessTerminalDomain,
    Position3,
)
from ..Routing.ResourceGraph import (
    FindSelfClaimConflicts,
    RoutingGraphRegion,
    RoutingResourceClaims,
    RoutingResourceId,
    RoutingResourceKind,
)
from ..Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)
from .Rotation import RotatedCellSize

try:
    from ..RustRouting import (
        BuildAccessRegionGraphCatalogBounded as _BuildAccessRegionGraphCatalogBounded,
        BuildDeferredRouteClaimsBatchWithTelemetry as _BuildDeferredRouteClaimsBatchWithTelemetry,
        BuildDerivedEscapeStatePathsBounded as _BuildDerivedEscapeStatePathsBounded,
        BuildLayeredAccessEscapeViewCatalogBounded as _BuildLayeredAccessEscapeViewCatalogBounded,
        BuildLayeredEscapeStatePathCatalogBounded as _BuildLayeredEscapeStatePathCatalogBounded,
        BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
        SolveLayeredAccessEscapeFactorCatalogBounded as _SolveLayeredAccessEscapeFactorCatalogBounded,
        SolveLayeredAccessGuideFactorCatalogBounded as _SolveLayeredAccessGuideFactorCatalogBounded,
    )
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import (
            BuildAccessRegionGraphCatalogBounded as _BuildAccessRegionGraphCatalogBounded,
            BuildDeferredRouteClaimsBatchWithTelemetry as _BuildDeferredRouteClaimsBatchWithTelemetry,
            BuildDerivedEscapeStatePathsBounded as _BuildDerivedEscapeStatePathsBounded,
            BuildLayeredAccessEscapeViewCatalogBounded as _BuildLayeredAccessEscapeViewCatalogBounded,
            BuildLayeredEscapeStatePathCatalogBounded as _BuildLayeredEscapeStatePathCatalogBounded,
            BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
            SolveLayeredAccessEscapeFactorCatalogBounded as _SolveLayeredAccessEscapeFactorCatalogBounded,
            SolveLayeredAccessGuideFactorCatalogBounded as _SolveLayeredAccessGuideFactorCatalogBounded,
        )
    except Exception:
        _BuildAccessRegionGraphCatalogBounded = None
        _BuildDeferredRouteClaimsBatchWithTelemetry = None
        _BuildDerivedEscapeStatePathsBounded = None
        _BuildLayeredAccessEscapeViewCatalogBounded = None
        _BuildLayeredEscapeStatePathCatalogBounded = None
        _BuildRouteClaimsBatchWithTelemetry = None
        _SolveLayeredAccessEscapeFactorCatalogBounded = None
        _SolveLayeredAccessGuideFactorCatalogBounded = None


_PerimeterFaceDirections: dict[str, Position3] = {
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "west": (-1, 0, 0),
    "east": (1, 0, 0),
}


@dataclass(frozen=True)
class PlacementAccessNativeRegionRecipe:
    """Exact immutable geometry recipe for one native access graph."""

    MemberId: str
    Bounds: tuple[int, int, int, int, int, int]
    AllowedAccess: tuple[Position3, ...]
    ActualBlocks: tuple[Position3, ...]
    ElectricalBlocks: tuple[Position3, ...]
    SolidBlocks: tuple[Position3, ...]
    TorchPoweredSupportBlocks: tuple[Position3, ...]
    NeighborOffsets: tuple[Position3, ...]
    RecipeFingerprint: str

    def __post_init__(self) -> None:
        if not self.MemberId or not self.RecipeFingerprint:
            raise ValueError("native access-region recipe requires identity")
        if len(self.Bounds) != 6 or not self.NeighborOffsets:
            raise ValueError("native access-region recipe is incomplete")


@dataclass(frozen=True)
class PlacementAccessNativeEscapeMemberPreparation:
    """One exact access member awaiting the shared native traversal."""

    MemberId: str
    TopologyKind: str
    AdjacencyValues: tuple[
        tuple[Position3, tuple[Position3, ...]], ...
    ]
    Requests: tuple[tuple[object, ...], ...]
    RequestInputs: tuple[tuple[str, object], ...]
    MaximumExpansionCount: int
    PreparationFingerprint: str

    def __post_init__(self) -> None:
        if not self.MemberId or not self.PreparationFingerprint:
            raise ValueError(
                "native escape preparation requires stable identities"
            )
        if self.MaximumExpansionCount < 1:
            raise ValueError(
                "native escape preparation requires a positive work cap"
            )


@dataclass(frozen=True)
class PlacementAccessNativeEscapeMemberResult:
    """Complete or bounded native result for one exact access member."""

    MemberId: str
    PreparationFingerprint: str
    Status: str
    Requests: tuple[tuple[object, ...], ...]
    ExpansionCount: int
    WorkCapExceeded: bool
    DeadlineExceeded: bool
    SharedBatchElapsedSeconds: float = 0.0

    @property
    def Complete(self) -> bool:
        return not self.WorkCapExceeded and not self.DeadlineExceeded


@dataclass(frozen=True)
class PlacementAccessNativeEscapeMemberView:
    """One exact layer contract over a shared prepared escape graph."""

    MemberId: str
    Objective: tuple[int, ...]
    SourceMemberId: str
    Requests: tuple[tuple[object, ...], ...]
    RequestMetadata: tuple[tuple[str, str, str], ...]
    MaximumY: int
    MaximumExpansionCount: int

    def __post_init__(self) -> None:
        if not self.MemberId or not self.SourceMemberId:
            raise ValueError("native layered access view requires identities")
        if not self.Requests or self.MaximumExpansionCount < 1:
            raise ValueError(
                "native layered access view requires requests and work"
            )
        if len(self.RequestMetadata) != len(self.Requests):
            raise ValueError(
                "native layered access view metadata is incomplete"
            )


@dataclass(frozen=True)
class PlacementAccessNativeGuideSignal:
    """One logical route variable over ordered access-terminal variables."""

    Signal: str
    TerminalVariables: tuple[str, ...]
    PortalVariantCount: int
    RegionTerminalColumns: tuple[tuple[int, int], ...] = ()
    SourceTerminalVariable: str | None = None
    SourceDetachedAnchorIndex: int | None = None

    def __post_init__(self) -> None:
        if (
            not self.Signal
            or self.PortalVariantCount < 1
            or not self.RegionTerminalColumns
            or (
                (self.SourceTerminalVariable is None)
                == (self.SourceDetachedAnchorIndex is None)
            )
            or (
                self.SourceTerminalVariable is not None
                and self.SourceTerminalVariable
                not in self.TerminalVariables
            )
            or (
                self.SourceDetachedAnchorIndex is not None
                and self.SourceDetachedAnchorIndex < 0
            )
        ):
            raise ValueError("native guide signal declaration is incomplete")


@dataclass(frozen=True)
class PlacementAccessNativeEscapeGuideMemberView(
    PlacementAccessNativeEscapeMemberView
):
    """Exact layer member plus frozen canonical guide-enumeration controls."""

    RoutingYs: tuple[int, ...] = ()
    MinimumX: int = 0
    MinimumZ: int = 0
    TrackPitch: int = 0
    LaneCount: int = 0
    MaximumShapesPerSignal: int = 0
    GuideExpansion: int = 0
    RegionExpansion: int = 0
    FabricNodeCandidates: tuple[Position3, ...] = ()
    GuideSignals: tuple[PlacementAccessNativeGuideSignal, ...] = ()
    BaseClaims: tuple[
        tuple[
            str,
            tuple[Position3, ...],
            tuple[Position3, ...],
            tuple[Position3, ...],
            tuple[Position3, ...],
        ], ...
    ] = ()
    DetachedSeedAnchors: tuple[
        tuple[str, tuple[tuple[Position3, ...], ...]], ...
    ] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            not self.RoutingYs
            or self.TrackPitch < 2
            or self.LaneCount < 1
            or self.MaximumShapesPerSignal < 1
            or self.GuideExpansion < 0
            or not self.GuideSignals
        ):
            raise ValueError("native guide member controls are incomplete")


@dataclass(frozen=True)
class PlacementAccessNativeSelectedGuide:
    """Selected native guide recipe retained for exact Python handoff."""

    Variable: str
    CandidateId: str
    AccessCandidateIds: tuple[tuple[str, str], ...]
    RoutingY: int
    Axis: str
    Lane: int
    Guide: tuple[Position3, ...]
    AccessRamps: tuple[tuple[Position3, ...], ...]
    PhysicalGuide: tuple[Position3, ...]
    DetailedHintPaths: tuple[tuple[Position3, ...], ...] = ()
    CertifiedRepeaters: tuple[tuple[Position3, str], ...] = ()


class PlacementAccessNativeEscapePreparationRequested(RuntimeError):
    """Internal control transfer after immutable request preparation."""

    def __init__(
        self,
        Preparation: PlacementAccessNativeEscapeMemberPreparation,
        Continuation: Any,
    ) -> None:
        super().__init__("placement access native escape prepared")
        self.Preparation = Preparation
        self.Continuation = Continuation


def CompletePlacementAccessNativeEscapePreparation(
    Prepared: PlacementAccessNativeEscapePreparationRequested,
    Result: PlacementAccessNativeEscapeMemberResult,
) -> PlacementAccessFabric:
    """Resume one suspended fabric exactly once with its native result."""
    try:
        Prepared.Continuation.send(Result)
    except StopIteration as Completed:
        Fabric = Completed.value
        if not isinstance(Fabric, PlacementAccessFabric):
            raise RuntimeError(
                "placement access continuation returned an invalid fabric"
            )
        return Fabric
    raise RuntimeError(
        "placement access continuation requested more than one native batch"
    )


def BuildPlacementAccessNativeRegionCatalogBounded(
    Recipes: Iterable[PlacementAccessNativeRegionRecipe],
    *,
    RemainingMilliseconds: int,
) -> tuple[
    dict[str, tuple[tuple[Position3, tuple[Position3, ...]], ...]],
    dict[str, object],
]:
    """Expand exact source graphs once in the bounded native batch."""
    Ordered = tuple(Recipes)
    if not Ordered:
        return {}, {
            "Used": False,
            "CallCount": 0,
            "GraphCount": 0,
            "Complete": True,
            "ElapsedSeconds": 0.0,
        }
    if _BuildAccessRegionGraphCatalogBounded is None:
        raise RuntimeError(
            "native access-region graph catalog binding is unavailable"
        )
    MemberIds = tuple(Value.MemberId for Value in Ordered)
    if len(MemberIds) != len(set(MemberIds)):
        raise ValueError("native access-region recipes must be unique")
    StartedAt = monotonic()
    NativeResults, Complete = _BuildAccessRegionGraphCatalogBounded(
        tuple((
            Value.MemberId,
            Value.Bounds,
            Value.AllowedAccess,
            Value.ActualBlocks,
            Value.ElectricalBlocks,
            Value.SolidBlocks,
            Value.TorchPoweredSupportBlocks,
            Value.NeighborOffsets,
        ) for Value in Ordered),
        max(1, int(RemainingMilliseconds)),
    )
    ElapsedSeconds = monotonic() - StartedAt
    ResultById = {
        str(MemberId): (
            tuple(
                (tuple(Position), tuple(map(tuple, Neighbors)))
                for Position, Neighbors in AdjacencyValues
            ),
            int(NodeCount),
            int(EdgeCount),
            bool(MemberComplete),
        )
        for (
            MemberId,
            AdjacencyValues,
            NodeCount,
            EdgeCount,
            MemberComplete,
        ) in NativeResults
    }
    UnknownIds = set(ResultById) - set(MemberIds)
    if UnknownIds:
        raise RuntimeError(
            "native access-region graph catalog returned unknown members"
        )
    MissingOrIncomplete = tuple(
        MemberId
        for MemberId in MemberIds
        if MemberId not in ResultById or not ResultById[MemberId][3]
    )
    if MissingOrIncomplete or not Complete:
        raise RuntimeError(
            "native access-region graph catalog was incomplete for "
            f"{MissingOrIncomplete!r}"
        )
    return {
        MemberId: ResultById[MemberId][0]
        for MemberId in MemberIds
    }, {
        "Used": True,
        "CallCount": 1,
        "GraphCount": len(Ordered),
        "NodeCounts": tuple(
            (MemberId, ResultById[MemberId][1])
            for MemberId in MemberIds
        ),
        "EdgeCounts": tuple(
            (MemberId, ResultById[MemberId][2])
            for MemberId in MemberIds
        ),
        "Complete": True,
        "ElapsedSeconds": ElapsedSeconds,
    }


def SolvePlacementAccessNativeEscapeCatalogBounded(
    Preparations: Iterable[
        PlacementAccessNativeEscapeMemberPreparation
    ],
    *,
    RemainingMilliseconds: int,
) -> tuple[
    dict[str, PlacementAccessNativeEscapeMemberResult],
    dict[str, object],
]:
    """Execute one exact layered access portfolio in the native kernel."""
    Ordered = tuple(Preparations)
    if not Ordered:
        return {}, {
            "Used": False,
            "CallCount": 0,
            "MemberCount": 0,
            "ExpansionCount": 0,
            "Complete": True,
            "ElapsedSeconds": 0.0,
        }
    if _BuildLayeredEscapeStatePathCatalogBounded is None:
        raise RuntimeError(
            "native layered access escape catalog binding is unavailable"
        )
    MemberIds = tuple(Value.MemberId for Value in Ordered)
    if len(MemberIds) != len(set(MemberIds)):
        raise ValueError("native layered access members must be unique")
    MaximumExpansionCount = sum(
        Value.MaximumExpansionCount for Value in Ordered
    )
    StartedAt = monotonic()
    (
        Status,
        NativeMembers,
        ExpansionCount,
        WorkCapExceeded,
        DeadlineExceeded,
    ) = _BuildLayeredEscapeStatePathCatalogBounded(
        tuple(
            (
                Value.MemberId,
                Value.AdjacencyValues,
                Value.Requests,
                Value.MaximumExpansionCount,
            )
            for Value in Ordered
        ),
        4,
        MaximumExpansionCount,
        max(1, int(RemainingMilliseconds)),
    )
    ElapsedSeconds = monotonic() - StartedAt
    NativeById = {
        str(MemberId): (
            str(MemberStatus),
            tuple(Requests),
            int(MemberExpansionCount),
            bool(MemberWorkCapExceeded),
            bool(MemberDeadlineExceeded),
        )
        for (
            MemberId,
            MemberStatus,
            Requests,
            MemberExpansionCount,
            MemberWorkCapExceeded,
            MemberDeadlineExceeded,
        ) in NativeMembers
    }
    UnknownMemberIds = set(NativeById) - set(MemberIds)
    if UnknownMemberIds:
        raise RuntimeError(
            "native layered access catalog returned unknown members: "
            f"{sorted(UnknownMemberIds)!r}"
        )
    Results = {
        Value.MemberId: PlacementAccessNativeEscapeMemberResult(
            MemberId=Value.MemberId,
            PreparationFingerprint=Value.PreparationFingerprint,
            Status=NativeById.get(
                Value.MemberId,
                ("Incomplete", (), 0, False, True),
            )[0],
            Requests=NativeById.get(
                Value.MemberId,
                ("Incomplete", (), 0, False, True),
            )[1],
            ExpansionCount=NativeById.get(
                Value.MemberId,
                ("Incomplete", (), 0, False, True),
            )[2],
            WorkCapExceeded=NativeById.get(
                Value.MemberId,
                ("Incomplete", (), 0, False, True),
            )[3],
            DeadlineExceeded=NativeById.get(
                Value.MemberId,
                ("Incomplete", (), 0, False, True),
            )[4],
            SharedBatchElapsedSeconds=ElapsedSeconds,
        )
        for Value in Ordered
    }
    return Results, {
        "Used": True,
        "CallCount": 1,
        "Status": str(Status),
        "MemberCount": len(Ordered),
        "ExpansionLimit": MaximumExpansionCount,
        "ExpansionCount": int(ExpansionCount),
        "WorkCapExceeded": bool(WorkCapExceeded),
        "DeadlineExceeded": bool(DeadlineExceeded),
        "Complete": not bool(WorkCapExceeded or DeadlineExceeded),
        "ElapsedSeconds": ElapsedSeconds,
    }


def SolvePlacementAccessNativeEscapeFactorCatalogBounded(
    Preparations: Iterable[
        PlacementAccessNativeEscapeMemberPreparation
    ],
    Views: Iterable[PlacementAccessNativeEscapeMemberView],
    *,
    MaximumAssignmentExpansionCount: int,
    RemainingMilliseconds: int,
) -> tuple[
    object,
    PlacementAccessNativeEscapeMemberResult | None,
    dict[str, object],
]:
    """Select one exact layer member and return only its path catalog."""
    if _SolveLayeredAccessEscapeFactorCatalogBounded is None:
        raise RuntimeError(
            "native layered access factor catalog binding is unavailable"
        )
    OrderedPreparations = tuple(Preparations)
    OrderedViews = tuple(sorted(
        Views,
        key=lambda Value: (Value.Objective, Value.MemberId),
    ))
    if not OrderedPreparations or not OrderedViews:
        raise ValueError(
            "native layered access factor catalog requires declarations"
        )
    PreparationById = {
        Value.MemberId: Value for Value in OrderedPreparations
    }


def SolvePlacementAccessNativeEscapeGuideFactorCatalogBounded(
    Preparations: Iterable[
        PlacementAccessNativeEscapeMemberPreparation
    ],
    Views: Iterable[PlacementAccessNativeEscapeGuideMemberView],
    *,
    MaximumAssignmentExpansionCount: int,
    RemainingMilliseconds: int,
) -> tuple[
    object,
    PlacementAccessNativeEscapeMemberResult | None,
    tuple[PlacementAccessNativeSelectedGuide, ...],
    dict[str, object],
]:
    """Select access stubs and canonical guide spines in one native call."""
    if _SolveLayeredAccessGuideFactorCatalogBounded is None:
        raise RuntimeError(
            "native layered access-guide factor catalog binding is unavailable"
        )
    OrderedPreparations = tuple(Preparations)
    OrderedViews = tuple(sorted(
        Views,
        key=lambda Value: (Value.Objective, Value.MemberId),
    ))
    if not OrderedPreparations or not OrderedViews:
        raise ValueError(
            "native layered access-guide catalog requires declarations"
        )
    PreparationById = {
        Value.MemberId: Value for Value in OrderedPreparations
    }
    if len(PreparationById) != len(OrderedPreparations):
        raise ValueError("native layered access-guide graphs must be unique")
    GraphIndexBySourceMemberId = {
        Value.MemberId: Index
        for Index, Value in enumerate(OrderedPreparations)
    }
    if any(
        Value.SourceMemberId not in GraphIndexBySourceMemberId
        for Value in OrderedViews
    ):
        raise ValueError(
            "native layered access-guide view references an unknown graph"
        )
    StartedAt = monotonic()
    (
        NativeSelection,
        NativeSelectedMember,
        NativeSelectedGuides,
        EscapeExpansionCount,
    ) = _SolveLayeredAccessGuideFactorCatalogBounded(
        tuple(
            (Value.PreparationFingerprint, Value.AdjacencyValues)
            for Value in OrderedPreparations
        ),
        tuple(
            (
                Value.MemberId,
                Value.Objective,
                GraphIndexBySourceMemberId[Value.SourceMemberId],
                Value.Requests,
                Value.RequestMetadata,
                int(Value.MaximumY),
                int(Value.MaximumExpansionCount),
                (
                    Value.RoutingYs,
                    int(Value.MinimumX),
                    int(Value.MinimumZ),
                    int(Value.TrackPitch),
                    int(Value.LaneCount),
                    int(Value.MaximumShapesPerSignal),
                    int(Value.GuideExpansion),
                    int(Value.RegionExpansion),
                    Value.FabricNodeCandidates,
                    tuple(
                        (
                            Signal.Signal,
                            Signal.TerminalVariables,
                            int(Signal.PortalVariantCount),
                            Signal.RegionTerminalColumns,
                            Signal.SourceTerminalVariable,
                            Signal.SourceDetachedAnchorIndex,
                        )
                        for Signal in Value.GuideSignals
                    ),
                    Value.BaseClaims,
                    Value.DetachedSeedAnchors,
                ),
            )
            for Value in OrderedViews
        ),
        4,
        int(MaximumAssignmentExpansionCount),
        max(1, int(RemainingMilliseconds)),
    )
    ElapsedSeconds = monotonic() - StartedAt
    ViewById = {Value.MemberId: Value for Value in OrderedViews}
    SelectedResult = None
    if NativeSelectedMember is not None:
        (
            MemberId,
            Status,
            Requests,
            MemberExpansionCount,
            WorkCapExceeded,
            DeadlineExceeded,
        ) = NativeSelectedMember
        SelectedView = ViewById.get(str(MemberId))
        if SelectedView is None:
            raise RuntimeError(
                "native access-guide selection returned an unknown member"
            )
        SourcePreparation = PreparationById[SelectedView.SourceMemberId]
        SelectedResult = PlacementAccessNativeEscapeMemberResult(
            MemberId=str(MemberId),
            PreparationFingerprint=SourcePreparation.PreparationFingerprint,
            Status=str(Status),
            Requests=tuple(Requests),
            ExpansionCount=int(MemberExpansionCount),
            WorkCapExceeded=bool(WorkCapExceeded),
            DeadlineExceeded=bool(DeadlineExceeded),
            SharedBatchElapsedSeconds=ElapsedSeconds,
        )
    SelectedGuides = tuple(
        PlacementAccessNativeSelectedGuide(
            Variable=str(Value[0]),
            CandidateId=str(Value[1]),
            AccessCandidateIds=tuple(
                (str(Variable), str(CandidateId))
                for Variable, CandidateId in Value[2]
            ),
            RoutingY=int(Value[3]),
            Axis=str(Value[4]),
            Lane=int(Value[5]),
            Guide=tuple(tuple(map(int, Position)) for Position in Value[6]),
            AccessRamps=tuple(
                tuple(tuple(map(int, Position)) for Position in Path)
                for Path in Value[7]
            ),
            PhysicalGuide=tuple(
                tuple(map(int, Position)) for Position in Value[8]
            ),
            DetailedHintPaths=tuple(
                tuple(tuple(map(int, Position)) for Position in Path)
                for Path in Value[9]
            ),
            CertifiedRepeaters=tuple(
                (tuple(map(int, Position)), str(Facing))
                for Position, Facing in Value[10]
            ),
        )
        for Value in NativeSelectedGuides
    )
    return NativeSelection, SelectedResult, SelectedGuides, {
        "Used": True,
        "CallCount": 1,
        "SourceGraphCount": len(OrderedPreparations),
        "DeclaredMemberCount": len(OrderedViews),
        "AttemptedMemberCount": len(tuple(getattr(
            NativeSelection,
            "AttemptedTemplateIds",
            (),
        ))),
        "AttemptedMemberIds": tuple(map(str, getattr(
            NativeSelection,
            "AttemptedTemplateIds",
            (),
        ))),
        "AttemptExpansionCounts": tuple(
            (str(MemberId), int(Count))
            for MemberId, Count in getattr(
                NativeSelection,
                "AttemptExpansionCounts",
                (),
            )
        ),
        "AttemptFailureNets": tuple(
            (str(MemberId), str(Signal or ""))
            for MemberId, Signal in getattr(
                NativeSelection,
                "AttemptFailureNets",
                (),
            )
        ),
        "EscapeExpansionCount": int(EscapeExpansionCount),
        "AssignmentExpansionCount": int(getattr(
            NativeSelection,
            "ExpansionCount",
            0,
        )),
        "SelectedGuideCount": len(SelectedGuides),
        "Success": bool(getattr(NativeSelection, "Success", False)),
        "Complete": bool(getattr(NativeSelection, "Complete", False)),
        "DeadlineExceeded": bool(getattr(
            NativeSelection,
            "DeadlineExceeded",
            False,
        )),
        "BudgetExhausted": bool(getattr(
            NativeSelection,
            "BudgetExhausted",
            False,
        )),
        "ElapsedSeconds": ElapsedSeconds,
    }
    if len(PreparationById) != len(OrderedPreparations):
        raise ValueError("native layered access source graphs must be unique")
    if len({Value.MemberId for Value in OrderedViews}) != len(OrderedViews):
        raise ValueError("native layered access member views must be unique")
    UnknownSources = {
        Value.SourceMemberId for Value in OrderedViews
    } - set(PreparationById)
    if UnknownSources:
        raise ValueError(
            "native layered access views reference unknown source graphs: "
            f"{sorted(UnknownSources)!r}"
        )
    GraphIndexBySourceMemberId = {
        Value.MemberId: Index
        for Index, Value in enumerate(OrderedPreparations)
    }
    StartedAt = monotonic()
    (
        NativeSelection,
        NativeSelectedMember,
        EscapeExpansionCount,
    ) = _SolveLayeredAccessEscapeFactorCatalogBounded(
        tuple(
            (Value.PreparationFingerprint, Value.AdjacencyValues)
            for Value in OrderedPreparations
        ),
        tuple(
            (
                Value.MemberId,
                Value.Objective,
                GraphIndexBySourceMemberId[Value.SourceMemberId],
                Value.Requests,
                Value.RequestMetadata,
                int(Value.MaximumY),
                int(Value.MaximumExpansionCount),
            )
            for Value in OrderedViews
        ),
        4,
        int(MaximumAssignmentExpansionCount),
        max(1, int(RemainingMilliseconds)),
    )
    ElapsedSeconds = monotonic() - StartedAt
    SelectedResult = None
    if NativeSelectedMember is not None:
        (
            MemberId,
            Status,
            Requests,
            MemberExpansionCount,
            WorkCapExceeded,
            DeadlineExceeded,
        ) = NativeSelectedMember
        ViewById = {Value.MemberId: Value for Value in OrderedViews}
        SelectedView = ViewById.get(str(MemberId))
        if SelectedView is None:
            raise RuntimeError(
                "native layered access selection returned an unknown member"
            )
        SourcePreparation = PreparationById[SelectedView.SourceMemberId]
        SelectedResult = PlacementAccessNativeEscapeMemberResult(
            MemberId=str(MemberId),
            PreparationFingerprint=(
                SourcePreparation.PreparationFingerprint
            ),
            Status=str(Status),
            Requests=tuple(Requests),
            ExpansionCount=int(MemberExpansionCount),
            WorkCapExceeded=bool(WorkCapExceeded),
            DeadlineExceeded=bool(DeadlineExceeded),
            SharedBatchElapsedSeconds=ElapsedSeconds,
        )
    return NativeSelection, SelectedResult, {
        "Used": True,
        "CallCount": 1,
        "SourceGraphCount": len(OrderedPreparations),
        "DeclaredMemberCount": len(OrderedViews),
        "AttemptedMemberCount": len(tuple(getattr(
            NativeSelection,
            "AttemptedTemplateIds",
            (),
        ))),
        "EscapeExpansionCount": int(EscapeExpansionCount),
        "AssignmentExpansionCount": int(getattr(
            NativeSelection,
            "ExpansionCount",
            0,
        )),
        "Success": bool(getattr(NativeSelection, "Success", False)),
        "Complete": bool(getattr(NativeSelection, "Complete", False)),
        "DeadlineExceeded": bool(getattr(
            NativeSelection,
            "DeadlineExceeded",
            False,
        )),
        "BudgetExhausted": bool(getattr(
            NativeSelection,
            "BudgetExhausted",
            False,
        )),
        "ElapsedSeconds": ElapsedSeconds,
    }


def BuildPlacementAccessNativeEscapeViewCatalogBounded(
    Preparations: Iterable[
        PlacementAccessNativeEscapeMemberPreparation
    ],
    Views: Iterable[PlacementAccessNativeEscapeMemberView],
    *,
    RemainingMilliseconds: int,
) -> tuple[
    dict[str, PlacementAccessNativeEscapeMemberResult],
    dict[str, object],
]:
    """Build every exact layer view without projecting paths across layers."""
    if _BuildLayeredAccessEscapeViewCatalogBounded is None:
        raise RuntimeError(
            "native layered access view catalog binding is unavailable"
        )
    OrderedPreparations = tuple(Preparations)
    OrderedViews = tuple(Views)
    if not OrderedPreparations or not OrderedViews:
        raise ValueError(
            "native layered access view catalog requires declarations"
        )
    PreparationById = {
        Value.MemberId: Value for Value in OrderedPreparations
    }
    if len(PreparationById) != len(OrderedPreparations):
        raise ValueError("native layered access source graphs must be unique")
    if len({Value.MemberId for Value in OrderedViews}) != len(OrderedViews):
        raise ValueError("native layered access member views must be unique")
    UnknownSources = {
        Value.SourceMemberId for Value in OrderedViews
    } - set(PreparationById)
    if UnknownSources:
        raise ValueError(
            "native layered access views reference unknown source graphs: "
            f"{sorted(UnknownSources)!r}"
        )
    GraphIndexBySourceMemberId = {
        Value.MemberId: Index
        for Index, Value in enumerate(OrderedPreparations)
    }
    MaximumExpansionCount = sum(
        int(Value.MaximumExpansionCount) for Value in OrderedViews
    )
    StartedAt = monotonic()
    (
        Status,
        NativeMembers,
        ExpansionCount,
        WorkCapExceeded,
        DeadlineExceeded,
    ) = _BuildLayeredAccessEscapeViewCatalogBounded(
        tuple(
            (Value.PreparationFingerprint, Value.AdjacencyValues)
            for Value in OrderedPreparations
        ),
        tuple(
            (
                Value.MemberId,
                Value.Objective,
                GraphIndexBySourceMemberId[Value.SourceMemberId],
                Value.Requests,
                Value.RequestMetadata,
                int(Value.MaximumY),
                int(Value.MaximumExpansionCount),
            )
            for Value in OrderedViews
        ),
        4,
        MaximumExpansionCount,
        max(1, int(RemainingMilliseconds)),
    )
    ElapsedSeconds = monotonic() - StartedAt
    NativeById = {
        str(MemberId): (
            str(MemberStatus),
            tuple(Requests),
            int(MemberExpansionCount),
            bool(MemberWorkCapExceeded),
            bool(MemberDeadlineExceeded),
        )
        for (
            MemberId,
            MemberStatus,
            Requests,
            MemberExpansionCount,
            MemberWorkCapExceeded,
            MemberDeadlineExceeded,
        ) in NativeMembers
    }
    ViewById = {Value.MemberId: Value for Value in OrderedViews}
    UnknownMemberIds = set(NativeById) - set(ViewById)
    if UnknownMemberIds:
        raise RuntimeError(
            "native layered access view catalog returned unknown members: "
            f"{sorted(UnknownMemberIds)!r}"
        )
    Results = {
        MemberId: PlacementAccessNativeEscapeMemberResult(
            MemberId=MemberId,
            PreparationFingerprint=(
                PreparationById[View.SourceMemberId]
                .PreparationFingerprint
            ),
            Status=Values[0],
            Requests=Values[1],
            ExpansionCount=Values[2],
            WorkCapExceeded=Values[3],
            DeadlineExceeded=Values[4],
            SharedBatchElapsedSeconds=ElapsedSeconds,
        )
        for MemberId, Values in NativeById.items()
        for View in (ViewById[MemberId],)
    }
    Complete = bool(
        not WorkCapExceeded
        and not DeadlineExceeded
        and len(Results) == len(OrderedViews)
        and all(Value.Complete for Value in Results.values())
    )
    return Results, {
        "Used": True,
        "CallCount": 1,
        "Status": str(Status),
        "SourceGraphCount": len(OrderedPreparations),
        "DeclaredMemberCount": len(OrderedViews),
        "MaterializedMemberCount": len(Results),
        "ExpansionLimit": MaximumExpansionCount,
        "ExpansionCount": int(ExpansionCount),
        "WorkCapExceeded": bool(WorkCapExceeded),
        "DeadlineExceeded": bool(DeadlineExceeded),
        "Complete": Complete,
        "ElapsedSeconds": ElapsedSeconds,
    }


def BuildPlacementAccessNativeSelectedStubFactorIds(
    Fabric: PlacementAccessFabric,
    Preparation: PlacementAccessNativeEscapeMemberPreparation,
    Result: PlacementAccessNativeEscapeMemberResult,
    SelectedCandidateIds: Iterable[tuple[str, str]],
) -> tuple[PlacementAccessFabric, tuple[tuple[str, str], ...]]:
    """Materialize selected native paths and return their stable IDs."""
    RequestInputById = dict(Preparation.RequestInputs)
    RequestResultById = {
        str(RequestId): tuple(Candidates)
        for RequestId, Candidates, _ExpansionCount, _Complete
        in Result.Requests
    }
    CandidatePathById: dict[str, tuple[Position3, ...]] = {}
    for RequestId, Candidates in RequestResultById.items():
        OriginalCandidateCount = 0
        IngressOccurrenceByPosition: dict[Position3, int] = {}
        for Ingress, _PriorDirection, Path in Candidates:
            IngressPosition = tuple(Ingress)
            IngressOccurrence = IngressOccurrenceByPosition.get(
                IngressPosition,
                0,
            )
            if IngressOccurrence == 0:
                OriginalCandidateIndex = OriginalCandidateCount
                OriginalCandidateCount += 1
                CandidateId = f"{RequestId}#{OriginalCandidateIndex}"
            else:
                OriginalCandidateIndex = OriginalCandidateCount - 1
                PowerSuffix = (
                    "power"
                    if IngressOccurrence == 1
                    else f"power:{IngressOccurrence}"
                )
                CandidateId = (
                    f"{RequestId}#{OriginalCandidateIndex}:{PowerSuffix}"
                )
            IngressOccurrenceByPosition[IngressPosition] = (
                IngressOccurrence + 1
            )
            CandidatePathById[CandidateId] = tuple(map(tuple, Path))
    SelectedByTerminal: dict[
        tuple[str, Position3], tuple[Position3, ...]
    ] = {}
    for Variable, CandidateId in SelectedCandidateIds:
        if not str(Variable).startswith("__access_terminal__:"):
            continue
        RequestId, Separator, _CandidateIndexValue = str(CandidateId).rpartition(
            "#"
        )
        if not Separator:
            raise ValueError("native access candidate id is malformed")
        RequestInput = RequestInputById.get(RequestId)
        Path = CandidatePathById.get(str(CandidateId))
        if RequestInput is None or Path is None:
            raise ValueError(
                "native access candidate is outside the selected catalog"
            )
        Signal, Terminal, Prefix = RequestInput
        StubPath = _ErasePlacementAccessPathLoops((
            *tuple(Prefix),
            *Path[1:],
        ))
        TerminalIdentity = (str(Signal), tuple(Terminal))
        Existing = SelectedByTerminal.get(TerminalIdentity)
        if Existing is not None and Existing != StubPath:
            raise ValueError(
                "native access selection assigned one terminal twice"
            )
        SelectedByTerminal[TerminalIdentity] = StubPath
    Results = []
    MaterializedDomains = []
    for DomainIndex, Domain in enumerate(Fabric.TerminalDomains):
        TerminalIdentity = (str(Domain.Signal), tuple(Domain.Terminal))
        SelectedPath = SelectedByTerminal.get(TerminalIdentity)
        if SelectedPath is None:
            raise ValueError(
                "native access selection omitted a selected fabric terminal"
            )
        MatchingStubs = tuple(
            Stub for Stub in Domain.EscapeStubs
            if tuple(Stub.Path) == SelectedPath
        )
        if not MatchingStubs:
            Claims, SelfLegal, ClaimsFingerprint = (
                _BuildDeferredPlacementAccessPathClaims(
                    SelectedPath,
                    Fabric.Technology or DefaultRedstoneRoutingTechnology,
                )
            )
            if not SelfLegal:
                raise ValueError(
                    "selected native access path is electrically self-conflicting"
                )
            SelectedStub = PlacementAccessEscapeStub(
                Terminal=tuple(Domain.Terminal),
                Ingress=tuple(SelectedPath[-1]),
                Path=SelectedPath,
                PhysicalClaims=Claims,
                CapacityResourceIds=(),
                Complete=True,
                PhysicalClaimsFingerprint=ClaimsFingerprint,
                PhysicalClaimsDeferred=True,
            )
            Domain = replace(
                Domain,
                EscapeStubs=(*Domain.EscapeStubs, SelectedStub),
            )
            MatchingStubs = (SelectedStub,)
        if len(MatchingStubs) != 1:
            raise ValueError(
                "native access path did not map to one selected fabric stub"
            )
        MaterializedDomains.append(Domain)
        LogicalKey = str(
            Domain.LogicalKey or f"{DomainIndex}:{Domain.Signal}"
        )
        Results.append((
            f"__access_terminal__:{LogicalKey}",
            "stub:"
            f"{BuildPlacementAccessEscapeStubChoiceId(MatchingStubs[0])}",
        ))
    if len(SelectedByTerminal) != len(Results):
        raise ValueError(
            "native access selection contains an unknown terminal"
        )
    return (
        replace(Fabric, TerminalDomains=tuple(MaterializedDomains)),
        tuple(Results),
    )


def BuildPlacementAccessNativeEscapeStubPathsByTerminal(
    Preparation: PlacementAccessNativeEscapeMemberPreparation,
    Result: PlacementAccessNativeEscapeMemberResult,
) -> tuple[
    tuple[tuple[str, Position3], tuple[tuple[Position3, ...], ...]], ...
]:
    """Decode every exact native path into its physical terminal stub path."""
    RequestInputById = dict(Preparation.RequestInputs)
    PathsByTerminal: dict[
        tuple[str, Position3], set[tuple[Position3, ...]]
    ] = defaultdict(set)
    for RequestId, Candidates, _ExpansionCount, Complete in Result.Requests:
        if not Complete:
            raise ValueError(
                "native escape stub path catalog contains an incomplete request"
            )
        RequestInput = RequestInputById.get(str(RequestId))
        if RequestInput is None:
            raise ValueError(
                "native escape stub path catalog references an unknown request"
            )
        Signal, Terminal, Prefix = RequestInput
        TerminalIdentity = (str(Signal), tuple(Terminal))
        for _Ingress, _PriorDirection, Path in Candidates:
            StubPath = _ErasePlacementAccessPathLoops((
                *tuple(Prefix),
                *tuple(map(tuple, Path))[1:],
            ))
            PathsByTerminal[TerminalIdentity].add(StubPath)
    return tuple(
        (TerminalIdentity, tuple(sorted(Paths)))
        for TerminalIdentity, Paths in sorted(PathsByTerminal.items())
    )


def MergePlacementAccessNativeEscapeMemberResults(
    Preparation: PlacementAccessNativeEscapeMemberPreparation,
    Results: Iterable[PlacementAccessNativeEscapeMemberResult],
) -> PlacementAccessNativeEscapeMemberResult:
    """Union complete exact layer results for one shared fabric substrate."""
    OrderedResults = tuple(Results)
    if not OrderedResults or any(not Value.Complete for Value in OrderedResults):
        raise ValueError(
            "shared layered access fabric requires complete native results"
        )
    DeclaredRequestIds = tuple(
        str(Request[0]) for Request in Preparation.Requests
    )
    CandidatesByRequestId: dict[str, set[tuple[object, ...]]] = {
        RequestId: set() for RequestId in DeclaredRequestIds
    }
    CompleteByRequestId = {RequestId: True for RequestId in DeclaredRequestIds}
    ExpansionCount = 0
    for Result in OrderedResults:
        if Result.PreparationFingerprint != Preparation.PreparationFingerprint:
            raise ValueError(
                "layered access result does not match its source graph"
            )
        ExpansionCount += int(Result.ExpansionCount)
        for RequestId, Candidates, _RequestExpansions, Complete in Result.Requests:
            RequestIdValue = str(RequestId)
            if RequestIdValue not in CandidatesByRequestId:
                raise ValueError(
                    "layered access result references an unknown source request"
                )
            CompleteByRequestId[RequestIdValue] &= bool(Complete)
            CandidatesByRequestId[RequestIdValue].update(
                (
                    tuple(Ingress),
                    tuple(Direction),
                    tuple(map(tuple, Path)),
                )
                for Ingress, Direction, Path in Candidates
            )
    if not all(CompleteByRequestId.values()):
        raise ValueError(
            "shared layered access fabric contains an incomplete request"
        )
    return PlacementAccessNativeEscapeMemberResult(
        MemberId=Preparation.MemberId,
        PreparationFingerprint=Preparation.PreparationFingerprint,
        Status="Complete",
        Requests=tuple(
            (
                RequestId,
                tuple(sorted(CandidatesByRequestId[RequestId])),
                0,
                True,
            )
            for RequestId in DeclaredRequestIds
        ),
        ExpansionCount=ExpansionCount,
        WorkCapExceeded=False,
        DeadlineExceeded=False,
        SharedBatchElapsedSeconds=max(
            float(Value.SharedBatchElapsedSeconds)
            for Value in OrderedResults
        ),
    )


def _DerivePerimeterRootAccessFace(
    AccessPath: tuple[Position3, ...],
) -> str | None:
    """Return the physical outward face encoded by one source access path.

    A compact interior producer that drives a frozen perimeter terminal must
    not be given the terminal's full lateral aperture domain.  Its existing
    source access path already records the one physical direction in which
    the macro can leave its pin bank.  Use that exact horizontal step as the
    root's fixed ring face.  A vertical-only or otherwise ambiguous path has
    no such proof and deliberately falls back to the bounded all-face domain
    at the call site.
    """
    for First, Second in zip(AccessPath, AccessPath[1:]):
        Delta = (
            int(Second[0]) - int(First[0]),
            int(Second[2]) - int(First[2]),
        )
        for Face, Direction in _PerimeterFaceDirections.items():
            if Delta == (Direction[0], Direction[2]):
                return Face
    return None


def _RestrictDerivedPerimeterSlotEscapeAdjacency(
    Adjacency: dict[Position3, tuple[Position3, ...]],
    *,
    Face: str,
    Start: Position3,
) -> dict[Position3, tuple[Position3, ...]]:
    """Keep one frozen slot's legal escape search outside its pin plane.

    The selected perimeter-slot normal is an immutable physical contract.
    Every retained ingress remains on that same exterior face, so searching
    back through the core-side half of the region cannot add a legal selected
    face entry.  Trimming only that irrelevant half-space preserves the full
    lateral ingress segment while preventing each terminal-domain proof from
    rediscovering the placed core's interior state graph.
    """
    Direction = _PerimeterFaceDirections.get(Face)
    if Direction is None:
        raise ValueError("derived perimeter slot has an unknown face")
    Axis = next(
        Index for Index, Value in enumerate(Direction) if Value
    )
    Sign = int(Direction[Axis])

    def IsOutward(Position: Position3) -> bool:
        return (int(Position[Axis]) - int(Start[Axis])) * Sign >= 0

    OutwardNodes = frozenset(
        Position for Position in Adjacency if IsOutward(Position)
    )
    return {
        Position: tuple(
            Next for Next in Neighbors if Next in OutwardNodes
        )
        for Position, Neighbors in Adjacency.items()
        if Position in OutwardNodes
    }


def _BuildDerivedPerimeterAccessPrefixDomain(
    AccessPath: tuple[Position3, ...],
    *,
    RegionNodeSet: frozenset[Position3],
) -> tuple[tuple[Position3, ...], ...]:
    """Materialize the finite legal handoff points of one pin access path.

    A macro access path describes a sequence of *available* electrical
    landing cells.  It does not require a later routing contract to occupy
    every cell in that sequence.  In particular, a ring on an elevated deck
    can require support directly below its last in-region landing.  Keeping
    that landing as dust would make the otherwise legal diagonal transition
    self-conflict through the same support cell.

    The derived perimeter factor therefore materializes every in-region
    prefix of the immutable macro access path as a finite *canonical handoff
    candidate set*.  These are not post-failure retries: their geometry is
    fixed before capacity solving, each starts at the same physical pin, and
    every legal member is offered to that one capacity solve before the
    selected stub is frozen with the rest of the pre-route contract.
    Farthest prefixes are listed first because they preserve the direct
    macro-to-ring handoff when it is legal; earlier prefixes exist precisely
    to prove a support-safe handoff when that direct landing is impossible.
    """
    Results: list[tuple[Position3, ...]] = []
    Seen = set()
    for Index in range(len(AccessPath) - 1, -1, -1):
        if AccessPath[Index] not in RegionNodeSet:
            continue
        Prefix = tuple(AccessPath[:Index + 1])
        if not Prefix or Prefix in Seen:
            continue
        Seen.add(Prefix)
        Results.append(Prefix)
    return tuple(Results)


def _DeriveLegalEscapeDirectionStateUpperBound(
    TerminalPaths: tuple[tuple[str, Position3, tuple[Position3, ...]], ...],
    *,
    RegionNodeSet: frozenset[Position3],
    RingIngressGroups: dict[tuple[int, int, str], list[Position3]],
    SlotFaceByTerminal: dict[tuple[str, Position3], str],
    PerimeterDrivenRootFaceByTerminal: dict[
        tuple[str, Position3],
        str,
    ],
    RegionAdjacency: dict[Position3, tuple[Position3, ...]],
) -> int:
    """Bound every derived escape search by its finite state graph.

    ``_BuildBoundedLegalDerivedEscapePaths`` first searches
    ``(position, prior-direction)`` states, then may certify powered
    alternatives with ``(position, prior-direction, remaining-power)``
    states.  Bound both finite state spaces for every exact prefix and ring
    ingress.  This is a termination proof, not a policy search allowance.

    This derives termination work from the declared physical factor rather
    than distributing an unrelated policy count across geometry members.  It
    intentionally over-approximates: reaching a complete finite domain must
    not be mislabeled incomplete solely because the work clamp was too small.
    """
    Total = 0
    for Signal, Terminal, AccessPath in TerminalPaths:
        PrefixDomain = _BuildDerivedPerimeterAccessPrefixDomain(
            AccessPath,
            RegionNodeSet=RegionNodeSet,
        )
        if not PrefixDomain:
            continue
        TerminalKey = (str(Signal), tuple(Terminal))
        SlotFace = SlotFaceByTerminal.get(TerminalKey)
        RootFace = PerimeterDrivenRootFaceByTerminal.get(TerminalKey)
        SelectedFace = SlotFace or RootFace
        EligibleGroups = tuple(
            Positions
            for Identity, Positions in RingIngressGroups.items()
            if SelectedFace is None or Identity[2] == SelectedFace
        )
        if not EligibleGroups:
            continue
        # Every legal macro landing is one member of the fixed handoff
        # domain.  The unrestricted region graph is a sound upper bound for
        # each traversal (the selected face only removes states), so derive
        # the shared cap arithmetically instead of rebuilding a filtered
        # adjacency map once per prefix merely to count it.
        DirectedAdjacencyEntryCount = sum(
            len(Neighbors) for Neighbors in RegionAdjacency.values()
        )
        DirectionStateUpperBound = 1 + DirectedAdjacencyEntryCount
        PoweredStateUpperBound = 1 + (
            15 * DirectedAdjacencyEntryCount
        )
        IngressCount = len(EligibleGroups)
        # One ordinary shortest-path traversal is followed by at most three
        # deterministic powered alternatives for each ingress.
        PerIngressUpperBound = (
            DirectionStateUpperBound + 3 * PoweredStateUpperBound
        )
        Total += (
            len(PrefixDomain)
            * IngressCount
            * PerIngressUpperBound
        )
    return Total


@dataclass
class _AccessFabricWorkBudget:
    """One immutable-at-entry work bound shared by a fabric construction.

    The value comes from the enclosing fixed pre-route factor budget.  It is
    deliberately a work counter rather than a timeout: exhausting it makes
    the affected local-access domain incomplete, never a reason to mutate
    geometry or ask the router for another attempt.
    """

    MaximumExpansions: int
    ExpansionCount: int = 0
    Exhausted: bool = False

    def Consume(
        self,
        WorkCheck: Callable[[dict[str, object]], None] | None = None,
        **Diagnostics: object,
    ) -> bool:
        if self.ExpansionCount >= self.MaximumExpansions:
            self.Exhausted = True
            return False
        self.ExpansionCount += 1
        if WorkCheck is not None and self.ExpansionCount % 256 == 0:
            WorkCheck({
                "Phase": "placement-access-legal-escape",
                "ExpansionCount": self.ExpansionCount,
                "ExpansionLimit": self.MaximumExpansions,
                **Diagnostics,
            })
        return True


def _GetDerivedPerimeterSlotAssignment(
    Placement: Any,
) -> Any | None:
    """Return the frozen compact terminal contract when one was selected.

    The placement and its placed-design mirror the same contract.  Keep the
    lookup deliberately duck-typed here: the access fabric is a routing
    boundary and must not import the placement solver merely to consume its
    immutable result.
    """
    Assignment = getattr(Placement, "DerivedPerimeterSlotAssignment", None)
    if Assignment is None:
        Assignment = getattr(
            getattr(Placement, "Placed", None),
            "DerivedPerimeterSlotAssignment",
            None,
        )
    return Assignment


@dataclass(frozen=True)
class DerivedPerimeterAccessEnvelopeMeasurement:
    """Exact static geometry of one frozen derived perimeter contract.

    This is deliberately smaller than :class:`PlacementAccessFabric`: it
    measures only the ring planes and active faces determined by immutable
    slot geometry, static keep-outs, and macro access paths.  It does *not*
    build terminal escape domains or choose a capacity witness.  The
    pre-route selector can therefore use its footprint/layer prefix to order
    a fixed portfolio without paying the escape-construction cost for a
    descriptor which is already dominated.
    """

    RingBounds: tuple[tuple[int, int, int, int], ...]
    OuterBounds: tuple[int, int, int, int]
    ActiveFaces: tuple[str, ...]
    SlotFaceByTerminal: tuple[tuple[str, Position3, str], ...]
    PerimeterDrivenRootFaceByTerminal: tuple[
        tuple[str, Position3, str],
        ...,
    ]
    EnvelopeFingerprint: str

    def ToDictionary(self) -> dict[str, object]:
        return {
            "RingBounds": [list(Bounds) for Bounds in self.RingBounds],
            "OuterBounds": list(self.OuterBounds),
            "ActiveFaces": list(self.ActiveFaces),
            "SlotFaceByTerminal": [
                {
                    "Signal": Signal,
                    "Terminal": list(Terminal),
                    "Face": Face,
                }
                for Signal, Terminal, Face in self.SlotFaceByTerminal
            ],
            "PerimeterDrivenRootFaceByTerminal": [
                {
                    "Signal": Signal,
                    "Terminal": list(Terminal),
                    "Face": Face,
                }
                for Signal, Terminal, Face in (
                    self.PerimeterDrivenRootFaceByTerminal
                )
            ],
            "EnvelopeFingerprint": self.EnvelopeFingerprint,
        }


def MeasureDerivedPerimeterAccessEnvelope(
    Placement: Any,
    *,
    Resources: Any | None = None,
    Technology: RedstoneRoutingTechnology = (
        DefaultRedstoneRoutingTechnology
    ),
    AccessRingTrackCount: int,
    AccessLength: int | None = None,
) -> DerivedPerimeterAccessEnvelopeMeasurement | None:
    """Measure exact derived-ring bounds before escape-domain construction.

    ``None`` means the placement has no complete frozen slot assignment.
    That case remains a typed access-factor incompleteness and must be
    materialized through :func:`BuildPlacementAccessFabric` by the caller;
    this helper never turns it into an empty geometry or a substitute
    placement choice.

    For a complete assignment, this follows the same signal-closed endpoint
    derivation as ``BuildPlacementAccessFabric``.  Keeping the two operations
    together is important: a selected external target can require its
    producer root on an opposite face, and that root's actual access landing
    changes the physical outer bounds even though no terminal escape has yet
    been searched.
    """
    if AccessRingTrackCount < 1:
        raise ValueError("derived perimeter envelope requires a track")
    DerivedSlotAssignment = _GetDerivedPerimeterSlotAssignment(Placement)
    if (
        DerivedSlotAssignment is None
        or not bool(getattr(DerivedSlotAssignment, "Success", False))
        or not bool(getattr(DerivedSlotAssignment, "Complete", False))
    ):
        return None
    Placed = Placement.Placed
    Resources = Resources or BuildRoutingResources(Placed)
    EffectiveAccessLength = (
        Technology.AccessLength
        if AccessLength is None
        else int(AccessLength)
    )
    Profiles = BuildNetRoutingProfiles(
        Placed,
        AccessLength=EffectiveAccessLength,
    )
    TerminalPathByIdentity = {
        (str(Signal), tuple(Terminal)): tuple(Path)
        for Signal, Profile in sorted(Profiles.items())
        for Terminal, Path in (
            (Profile.Root, Profile.SourceAccessPath),
            *tuple(sorted(Profile.TargetAccessPaths.items())),
        )
    }
    SelectedSlotFaceByTerminal = {
        (str(Slot.Signal), tuple(Slot.ConnectionPin)): str(Slot.Face)
        for Slot in getattr(DerivedSlotAssignment, "SelectedSlots", ())
    }
    SlotTerminalKeys = frozenset(SelectedSlotFaceByTerminal)
    PerimeterDrivenRootKeys = frozenset(
        (str(Signal), tuple(Profile.Root))
        for Signal, Profile in Profiles.items()
        if any(
            (str(Signal), tuple(Target)) in SlotTerminalKeys
            for Target in Profile.Targets
        )
    )
    PerimeterDrivenRootFaceByTerminal = {
        Key: Face
        for Key in PerimeterDrivenRootKeys
        if Key not in SlotTerminalKeys
        for Face in (
            _DerivePerimeterRootAccessFace(
                TerminalPathByIdentity.get(Key, ())
            ),
        )
        if Face is not None
    }
    (
        RingBounds,
        OuterBounds,
        ActiveFaces,
        SlotFaceByTerminal,
    ) = _BuildDerivedPerimeterRingBounds(
        DerivedSlotAssignment,
        Resources=Resources,
        Technology=Technology,
        AccessRingTrackCount=AccessRingTrackCount,
        TerminalPathByIdentity=TerminalPathByIdentity,
        PerimeterDrivenRootFaceByTerminal=(
            PerimeterDrivenRootFaceByTerminal
        ),
    )
    if SlotFaceByTerminal != SelectedSlotFaceByTerminal:
        raise RuntimeError(
            "derived perimeter envelope did not preserve the frozen slots"
        )
    SlotFaceItems = tuple(
        (Signal, Terminal, Face)
        for (Signal, Terminal), Face in sorted(SlotFaceByTerminal.items())
    )
    RootFaceItems = tuple(
        (Signal, Terminal, Face)
        for (Signal, Terminal), Face in sorted(
            PerimeterDrivenRootFaceByTerminal.items()
        )
    )
    EnvelopeFingerprint = sha256(repr((
        "derived-perimeter-access-envelope-v1",
        str(getattr(DerivedSlotAssignment, "AssignmentFingerprint", "")),
        int(AccessRingTrackCount),
        EffectiveAccessLength,
        RingBounds,
        OuterBounds,
        ActiveFaces,
        SlotFaceItems,
        RootFaceItems,
        tuple(sorted(TerminalPathByIdentity.items())),
    )).encode("utf-8")).hexdigest()[:16]
    return DerivedPerimeterAccessEnvelopeMeasurement(
        RingBounds=RingBounds,
        OuterBounds=OuterBounds,
        ActiveFaces=ActiveFaces,
        SlotFaceByTerminal=SlotFaceItems,
        PerimeterDrivenRootFaceByTerminal=RootFaceItems,
        EnvelopeFingerprint=EnvelopeFingerprint,
    )


def _MeasureDerivedPerimeterInterfaceLaunchFaces(
    Placement: Any,
    *,
    Technology: RedstoneRoutingTechnology,
) -> tuple[int, tuple[tuple[str, Position3, str], ...]] | None:
    """Return the complete signal-closed interface launches and their faces.

    A selected perimeter terminal is one launch on its selected face.  When a
    selected terminal is a signal target, its producer root belongs to the
    same frozen interface factor.  That root is another launch only when it
    is not already a selected terminal itself; this is exactly the root
    exclusion used by :func:`BuildDerivedPerimeterFabricShell`.

    The aggregate interface measurement historically counts an ambiguous
    paired root even if its access path has no horizontal outward direction.
    Preserve that count for compatibility, but omit that root from this
    face-resolved result because it has no proved physical launch face.
    """
    Assignment = _GetDerivedPerimeterSlotAssignment(Placement)
    if (
        Assignment is None
        or not bool(getattr(Assignment, "Success", False))
        or not bool(getattr(Assignment, "Complete", False))
    ):
        return None

    SlotFaceByTerminal: dict[tuple[str, Position3], str] = {}
    for Slot in getattr(Assignment, "SelectedSlots", ()):
        Key = (str(Slot.Signal), tuple(Slot.ConnectionPin))
        Face = str(Slot.Face)
        if Face not in _PerimeterFaceDirections:
            raise ValueError("derived perimeter slot has an unknown face")
        ExistingFace = SlotFaceByTerminal.get(Key)
        if ExistingFace is not None and ExistingFace != Face:
            raise ValueError("derived perimeter terminal has conflicting faces")
        SlotFaceByTerminal[Key] = Face

    Profiles = BuildNetRoutingProfiles(
        Placement.Placed,
        AccessLength=Technology.AccessLength,
    )
    TerminalKeys = set(SlotFaceByTerminal)
    LaunchFaceByTerminal = dict(SlotFaceByTerminal)
    PerimeterDrivenRootKeys = frozenset(
        (str(Signal), tuple(Profile.Root))
        for Signal, Profile in Profiles.items()
        if any(
            (str(Signal), tuple(Target)) in SlotFaceByTerminal
            for Target in Profile.Targets
        )
    )
    for Signal, Root in sorted(PerimeterDrivenRootKeys):
        RootKey = (Signal, Root)
        TerminalKeys.add(RootKey)
        # A root which is itself a selected slot takes that slot's selected
        # face.  It is already present in ``LaunchFaceByTerminal`` and must
        # not be charged to a second, path-derived face.
        if RootKey in SlotFaceByTerminal:
            continue
        Profile = Profiles.get(Signal)
        if Profile is None:
            continue
        RootFace = _DerivePerimeterRootAccessFace(
            tuple(Profile.SourceAccessPath)
        )
        if RootFace is None:
            continue
        if RootFace not in _PerimeterFaceDirections:
            raise ValueError("derived perimeter root has an unknown face")
        ExistingFace = LaunchFaceByTerminal.get(RootKey)
        if ExistingFace is not None and ExistingFace != RootFace:
            raise ValueError("derived perimeter root has conflicting faces")
        LaunchFaceByTerminal[RootKey] = RootFace

    return (
        len(TerminalKeys),
        tuple(
            (Signal, Terminal, Face)
            for (Signal, Terminal), Face in sorted(
                LaunchFaceByTerminal.items()
            )
        ),
    )


def MeasureDerivedPerimeterInterfaceLaunchDemandByFace(
    Placement: Any,
    *,
    Technology: RedstoneRoutingTechnology = (
        DefaultRedstoneRoutingTechnology
    ),
) -> Mapping[str, int]:
    """Measure immutable per-face launches of the frozen perimeter contract.

    The returned mapping contains only cardinal faces with measured demand,
    in canonical north/south/west/east order, and cannot be mutated by the
    caller.  An absent, unsuccessful, or incomplete slot assignment has no
    fixed interface factor yet, so it deliberately returns an empty mapping
    rather than guessing a perimeter face or assigning speculative capacity.
    """
    Measurement = _MeasureDerivedPerimeterInterfaceLaunchFaces(
        Placement,
        Technology=Technology,
    )
    if Measurement is None:
        return MappingProxyType({})
    _TerminalCount, LaunchFaceItems = Measurement
    DemandByFace = {
        Face: sum(
            1
            for _Signal, _Terminal, LaunchFace in LaunchFaceItems
            if LaunchFace == Face
        )
        for Face in _PerimeterFaceDirections
    }
    return MappingProxyType({
        Face: Demand
        for Face, Demand in DemandByFace.items()
        if Demand
    })


def MeasureDerivedPerimeterInterfaceDemand(
    Placement: Any,
    *,
    Technology: RedstoneRoutingTechnology = (
        DefaultRedstoneRoutingTechnology
    ),
) -> tuple[int, tuple[str, ...]]:
    """Measure the fixed signal-closed perimeter interface of a placement.

    A derived perimeter ring serves external terminal slots, not every pin in
    the packed core.  A signal whose selected slot is a target also needs its
    source endpoint in the same immutable contract; otherwise the later
    authoritative tree factor would be missing one end of that signal.  This
    helper is shared by envelope derivation so its demand and active faces
    match the physical fabric exactly.
    """
    Measurement = _MeasureDerivedPerimeterInterfaceLaunchFaces(
        Placement,
        Technology=Technology,
    )
    if Measurement is None:
        return 0, ()
    TerminalCount, LaunchFaceItems = Measurement
    ActiveFaces = {
        Face
        for _Signal, _Terminal, Face in LaunchFaceItems
    }
    return (
        TerminalCount,
        tuple(
            Face for Face in _PerimeterFaceDirections
            if Face in ActiveFaces
        ),
    )


def _BuildDerivedPerimeterRingBounds(
    Assignment: Any,
    *,
    Resources: Any,
    Technology: RedstoneRoutingTechnology,
    AccessRingTrackCount: int,
    TerminalPathByIdentity: dict[
        tuple[str, Position3],
        tuple[Position3, ...],
    ],
    PerimeterDrivenRootFaceByTerminal: dict[
        tuple[str, Position3],
        str,
    ] | None = None,
    PerimeterFaceTrackCounts: tuple[tuple[str, int], ...] | None = None,
) -> tuple[
    tuple[tuple[int, int, int, int], ...],
    tuple[int, int, int, int],
    tuple[str, ...],
    dict[tuple[str, Position3], str],
    tuple[tuple[str, int], ...],
]:
    """Derive asymmetric ring planes from frozen slots and physical claims.

    A selected terminal can only enter the side to which its physical pin
    points.  The innermost routing plane is the first exterior coordinate
    beyond both the exact static keep-out and that pin's technology-derived
    access landing.  Additional tracks are separated solely by the routing
    technology pitch; no policy-shaped shell clearance is consulted.
    """
    if not bool(getattr(Assignment, "Success", False)) or not bool(
        getattr(Assignment, "Complete", False)
    ):
        raise ValueError("derived perimeter fabric requires a complete assignment")
    if AccessRingTrackCount < 1:
        raise ValueError("derived perimeter fabric requires a track")

    FaceReservations = tuple(getattr(Assignment, "FaceReservations", ()))
    ReservationByFace = {
        str(Reservation.Face): Reservation
        for Reservation in FaceReservations
    }
    ReservedFaces = tuple(Reservation.Face for Reservation in FaceReservations)
    if len(ReservedFaces) != len(set(ReservedFaces)):
        raise ValueError("derived perimeter assignment repeats a face")

    Slots = tuple(getattr(Assignment, "SelectedSlots", ()))
    SlotFaceByTerminal: dict[tuple[str, Position3], str] = {}
    for Slot in Slots:
        Face = str(Slot.Face)
        Direction = tuple(Slot.ConnectionDirection)
        if Face not in _PerimeterFaceDirections or Direction != (
            _PerimeterFaceDirections[Face]
        ):
            raise ValueError("derived perimeter slot does not face outward")
        Key = (str(Slot.Signal), tuple(Slot.ConnectionPin))
        ExistingFace = SlotFaceByTerminal.get(Key)
        if ExistingFace is not None and ExistingFace != Face:
            raise ValueError("derived perimeter terminal has conflicting faces")
        SlotFaceByTerminal[Key] = Face

        Reservation = ReservationByFace.get(Face)
        if Reservation is None:
            raise ValueError("derived perimeter slot has no face reservation")
        NormalIndex = 2 if Face in {"north", "south"} else 0
        LateralIndex = 0 if Face in {"north", "south"} else 2
        Pin = tuple(Slot.ConnectionPin)
        if Pin[NormalIndex] != int(Reservation.NormalCoordinate):
            raise ValueError("derived perimeter reservation pin plane mismatch")
        if not (
            int(Reservation.LateralMinimum)
            <= Pin[LateralIndex]
            <= int(Reservation.LateralMaximum)
        ):
            raise ValueError("derived perimeter reservation lateral range mismatch")

    # The chosen I/O slots and paired signal roots are one signal-closed
    # interface factor.  A root can face a different side from its selected
    # terminal slot.  Its access landing must therefore grow that side's
    # ring plane before the resource region is built; adding the face after
    # computing ring bounds would strand the root one cell beyond the ring.
    TerminalFaceByIdentity = dict(SlotFaceByTerminal)
    for TerminalKey, Face in sorted(
        (PerimeterDrivenRootFaceByTerminal or {}).items()
    ):
        if Face not in _PerimeterFaceDirections:
            raise ValueError("derived perimeter root has an unknown face")
        ExistingFace = TerminalFaceByIdentity.get(TerminalKey)
        if ExistingFace is not None and ExistingFace != Face:
            raise ValueError("derived perimeter root has conflicting faces")
        TerminalFaceByIdentity[TerminalKey] = Face

    # StaticKeepOut already contains exact template electrical and support
    # exclusions.  The one extra coordinate is the next actual routing cell,
    # i.e. the same physical adjacency relation used by the resource graph.
    KeepOut = tuple(Resources.ResourceGraph.StaticKeepOut)
    Bounds = tuple(getattr(Assignment, "Bounds", ()))
    if len(Bounds) != 4:
        raise ValueError("derived perimeter assignment bounds are invalid")
    MinimumX, MinimumZ, MaximumX, MaximumZ = map(int, Bounds)
    KeepOutMinimumX = min(
        (Position[0] for Position in KeepOut),
        default=MinimumX,
    )
    KeepOutMaximumX = max(
        (Position[0] for Position in KeepOut),
        default=MaximumX,
    )
    KeepOutMinimumZ = min(
        (Position[2] for Position in KeepOut),
        default=MinimumZ,
    )
    KeepOutMaximumZ = max(
        (Position[2] for Position in KeepOut),
        default=MaximumZ,
    )
    InnermostCoordinate = {
        "north": KeepOutMinimumZ - 1,
        "south": KeepOutMaximumZ + 1,
        "west": KeepOutMinimumX - 1,
        "east": KeepOutMaximumX + 1,
    }

    def ExtendOutward(
        Face: str,
        Coordinate: int,
    ) -> None:
        if Face == "north":
            InnermostCoordinate[Face] = min(
                InnermostCoordinate[Face],
                Coordinate,
            )
        elif Face == "south":
            InnermostCoordinate[Face] = max(
                InnermostCoordinate[Face],
                Coordinate,
            )
        elif Face == "west":
            InnermostCoordinate[Face] = min(
                InnermostCoordinate[Face],
                Coordinate,
            )
        else:
            InnermostCoordinate[Face] = max(
                InnermostCoordinate[Face],
                Coordinate,
            )

    for Face, Reservation in ReservationByFace.items():
        Direction = _PerimeterFaceDirections[Face]
        NormalIndex = 2 if Face in {"north", "south"} else 0
        # ``NormalCoordinate`` is the exact terminal pin plane.  The next
        # technology access landing is derived from the actual profile below
        # when available; this fallback still remains a physical access
        # length, not a hand-tuned perimeter offset.
        ExtendOutward(
            Face,
            int(Reservation.NormalCoordinate)
            + Direction[NormalIndex] * int(Technology.AccessLength),
        )

    for TerminalKey, Face in TerminalFaceByIdentity.items():
        Path = TerminalPathByIdentity.get(TerminalKey)
        if not Path:
            continue
        Direction = _PerimeterFaceDirections[Face]
        if len(Path) >= 2:
            Landing = Technology.AccessLanding(Path)
        else:
            Last = Path[-1]
            Landing = tuple(
                Last[Index] + Direction[Index]
                for Index in range(3)
            )
        NormalIndex = 2 if Face in {"north", "south"} else 0
        ExtendOutward(Face, int(Landing[NormalIndex]))

    TrackPitch = int(Technology.TrackPitch)
    ActiveFaces = tuple(
        Face for Face in _PerimeterFaceDirections
        if Face in {
            *ReservedFaces,
            *TerminalFaceByIdentity.values(),
        }
    )
    FaceTrackByName = {
        str(Face): int(TrackCount)
        for Face, TrackCount in (
            PerimeterFaceTrackCounts
            if PerimeterFaceTrackCounts is not None
            else (
                (Face, AccessRingTrackCount)
                if Face in ActiveFaces
                else (Face, 0)
                for Face in _PerimeterFaceDirections
            )
        )
    }
    if len(FaceTrackByName) != 4:
        raise ValueError("derived perimeter face tracks are not canonical")
    if any(
        Face not in _PerimeterFaceDirections
        for Face in FaceTrackByName
    ):
        raise ValueError("derived perimeter face tracks are invalid")
    if any(
        Count < 0 or int(Count) != Count
        for Count in FaceTrackByName.values()
    ):
        raise ValueError("derived perimeter face tracks are invalid")
    if any(Count > AccessRingTrackCount for Count in FaceTrackByName.values()):
        raise ValueError("derived perimeter face track count exceeds ring tracks")
    if any(
        (Face in ActiveFaces and FaceTrackByName[Face] < 1)
        for Face in _PerimeterFaceDirections
    ):
        raise ValueError("derived perimeter face tracks are invalid")
    ResolvedFaceTrackCounts = tuple(
        (Face, FaceTrackByName[Face])
        for Face in _PerimeterFaceDirections
    )
    MaximumTrackCount = max(Track for _, Track in ResolvedFaceTrackCounts)
    if MaximumTrackCount < 1:
        raise ValueError("derived perimeter ring requires a track")
    RingBounds = tuple(
        (
            InnermostCoordinate["west"] - TrackPitch * (
                min(TrackIndex, int(FaceTrackByName["west"])) - 1
                if FaceTrackByName["west"] > 0
                else 0
            ),
            InnermostCoordinate["east"] + TrackPitch * (
                min(TrackIndex, int(FaceTrackByName["east"])) - 1
                if FaceTrackByName["east"] > 0
                else 0
            ),
            InnermostCoordinate["north"] - TrackPitch * (
                min(TrackIndex, int(FaceTrackByName["north"])) - 1
                if FaceTrackByName["north"] > 0
                else 0
            ),
            InnermostCoordinate["south"] + TrackPitch * (
                min(TrackIndex, int(FaceTrackByName["south"])) - 1
                if FaceTrackByName["south"] > 0
                else 0
            ),
        )
        for TrackIndex in range(1, MaximumTrackCount + 1)
    )
    return (
        RingBounds,
        _BuildActiveDerivedPerimeterOuterBounds(
            RingBounds,
            (MinimumX, MinimumZ, MaximumX, MaximumZ),
            ActiveFaces,
        ),
        ActiveFaces,
        SlotFaceByTerminal,
        ResolvedFaceTrackCounts,
    )


def _BuildActiveDerivedPerimeterOuterBounds(
    RingBounds: tuple[tuple[int, int, int, int], ...],
    AssignmentBounds: tuple[int, int, int, int],
    ActiveFaces: tuple[str, ...],
) -> tuple[int, int, int, int]:
    """Return the exact XZ box occupied by active frozen ring segments.

    Ring coordinates are available for all four faces because a paired
    signal root can make one additional side required.  Only active segments
    are materialized, however, so publishing the enclosing four-side box
    would turn absent material into a false footprint and an unnecessarily
    large legal-escape search region.
    """
    if not RingBounds:
        raise ValueError("derived perimeter fabric requires ring bounds")
    if len(AssignmentBounds) != 4:
        raise ValueError("derived perimeter assignment bounds are invalid")
    ActiveFaceSet = frozenset(ActiveFaces)
    if not ActiveFaceSet:
        raise ValueError("derived perimeter fabric requires an active face")
    if not ActiveFaceSet <= frozenset(_PerimeterFaceDirections):
        raise ValueError("derived perimeter fabric has an unknown active face")
    MinimumX, MinimumZ, MaximumX, MaximumZ = map(int, AssignmentBounds)
    RingMinimumX, RingMaximumX, RingMinimumZ, RingMaximumZ = RingBounds[-1]
    if ActiveFaceSet & {"north", "south"}:
        MinimumX = min(MinimumX, RingMinimumX)
        MaximumX = max(MaximumX, RingMaximumX)
    if ActiveFaceSet & {"west", "east"}:
        MinimumZ = min(MinimumZ, RingMinimumZ)
        MaximumZ = max(MaximumZ, RingMaximumZ)
    if "north" in ActiveFaceSet:
        MinimumZ = min(MinimumZ, RingMinimumZ)
    if "south" in ActiveFaceSet:
        MaximumZ = max(MaximumZ, RingMaximumZ)
    if "west" in ActiveFaceSet:
        MinimumX = min(MinimumX, RingMinimumX)
    if "east" in ActiveFaceSet:
        MaximumX = max(MaximumX, RingMaximumX)
    return (MinimumX, MinimumZ, MaximumX, MaximumZ)


@dataclass(frozen=True)
class DerivedPerimeterFabricProfile:
    """Immutable routing-profile snapshot used by one perimeter shell.

    ``NetRoutingProfile`` deliberately carries a mutable target-path mapping
    for the ordinary routing pipeline.  A pre-route perimeter factor must not
    retain that mutable mapping as part of its frozen identity, so the shell
    records the exact access facts it consumes in a tuple-only form instead.
    """

    Signal: str
    Root: Position3
    Targets: tuple[Position3, ...]
    SourceAccessPath: tuple[Position3, ...]
    TargetAccessPaths: tuple[tuple[Position3, tuple[Position3, ...]], ...]


@dataclass(frozen=True)
class DerivedPerimeterFabricShell:
    """One immutable derived-perimeter factor before fabric materialization.

    The shell owns every geometry fact that is independent of the later
    legal-escape traversal: the signal profiles, signal-closed endpoint set,
    face ownership, terminal paths, and physical ring planes.  It is built
    once for a fixed placement/access candidate and can then be consumed by
    the fabric constructor without regenerating profile or ring geometry.
    """

    InputFingerprint: str
    ShellFingerprint: str
    PerimeterSlotAssignmentFingerprint: str
    AccessRingTrackCount: int
    AccessLength: int
    TechnologyFingerprint: str
    Profiles: tuple[DerivedPerimeterFabricProfile, ...]
    TerminalPaths: tuple[tuple[str, Position3, tuple[Position3, ...]], ...]
    SlotFaceItems: tuple[tuple[str, Position3, str], ...]
    PerimeterDrivenRootFaceItems: tuple[tuple[str, Position3, str], ...]
    RingBounds: tuple[tuple[int, int, int, int], ...]
    Bounds: tuple[int, int, int, int]
    ActiveFaces: tuple[str, ...]
    PerimeterFaceTrackCounts: tuple[tuple[str, int], ...]
    FabricLayers: tuple[int, ...]
    FabricYs: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.AccessRingTrackCount < 1:
            raise ValueError("derived perimeter shell requires a track")
        if self.AccessLength < 1:
            raise ValueError("derived perimeter shell access length is invalid")
        if not self.RingBounds:
            raise ValueError("derived perimeter shell requires ring bounds")
        if (
            not self.FabricLayers
            or len(self.FabricLayers) != len(self.FabricYs)
        ):
            raise ValueError("derived perimeter shell layers are invalid")
        if not self.ActiveFaces or any(
            Face not in _PerimeterFaceDirections
            for Face in self.ActiveFaces
        ):
            raise ValueError("derived perimeter shell has invalid active faces")
        if self.ActiveFaces != tuple(
            Face for Face in _PerimeterFaceDirections if Face in self.ActiveFaces
        ):
            raise ValueError("derived perimeter shell faces are not canonical")
        PerimeterFaceTrackCountByName = {
            str(Face): int(Count)
            for Face, Count in self.PerimeterFaceTrackCounts
        }
        if (
            len(PerimeterFaceTrackCountByName) != len(_PerimeterFaceDirections)
            or tuple(sorted(PerimeterFaceTrackCountByName)) != (
                "east",
                "north",
                "south",
                "west",
            )
        ):
            raise ValueError(
                "derived perimeter shell face-track counts are invalid"
            )
        if any(
            Count < 0 or int(Count) != Count
            for Count in PerimeterFaceTrackCountByName.values()
        ):
            raise ValueError(
                "derived perimeter shell face-track counts are invalid"
            )
        if any(
            Count > self.AccessRingTrackCount
            for Count in PerimeterFaceTrackCountByName.values()
        ):
            raise ValueError(
                "derived perimeter shell face-track counts are invalid"
            )
        if any(
            (Face in self.ActiveFaces and Count < 1)
            or (Face not in self.ActiveFaces and Count != 0)
            for Face, Count in PerimeterFaceTrackCountByName.items()
        ):
            raise ValueError(
                "derived perimeter shell face-track counts are invalid"
            )
        if self.PerimeterFaceTrackCounts != tuple(
            (Face, PerimeterFaceTrackCountByName[Face])
            for Face in _PerimeterFaceDirections
        ):
            raise ValueError(
                "derived perimeter shell face-track counts are invalid"
            )
        if self.SlotFaceItems != tuple(sorted(self.SlotFaceItems)):
            raise ValueError("derived perimeter shell slot faces are not canonical")
        if self.PerimeterDrivenRootFaceItems != tuple(
            sorted(self.PerimeterDrivenRootFaceItems)
        ):
            raise ValueError("derived perimeter shell root faces are not canonical")
        if self.TerminalPaths != tuple(sorted(self.TerminalPaths)):
            raise ValueError("derived perimeter shell terminal paths are not canonical")

    @property
    def OuterBounds(self) -> tuple[int, int, int, int]:
        """Expose the exact physical bounds under the fabric field name."""
        return self.Bounds

    @property
    def SlotFaceByTerminal(self) -> dict[tuple[str, Position3], str]:
        """Return a fresh lookup map without exposing mutable shell state."""
        return {
            (Signal, Terminal): Face
            for Signal, Terminal, Face in self.SlotFaceItems
        }

    @property
    def PerimeterDrivenRootFaceByTerminal(
        self,
    ) -> dict[tuple[str, Position3], str]:
        """Return the paired-root face map as a fresh lookup map."""
        return {
            (Signal, Terminal): Face
            for Signal, Terminal, Face in self.PerimeterDrivenRootFaceItems
        }

    @property
    def ProfileBySignal(self) -> dict[str, DerivedPerimeterFabricProfile]:
        """Return immutable profile snapshots indexed by signal."""
        return {
            Profile.Signal: Profile
            for Profile in self.Profiles
        }

    @property
    def TerminalPathByIdentity(
        self,
    ) -> dict[tuple[str, Position3], tuple[Position3, ...]]:
        """Return a fresh endpoint-to-access-path map for fabric traversal."""
        return {
            (Signal, Terminal): Path
            for Signal, Terminal, Path in self.TerminalPaths
        }


def _BuildDerivedPerimeterShellAssignmentIdentity(
    Assignment: Any,
) -> tuple[object, ...]:
    """Return the immutable slot-assignment inputs consumed by a shell."""
    return (
        str(getattr(Assignment, "DomainFingerprint", "")),
        str(getattr(Assignment, "AssignmentFingerprint", "")),
        tuple(map(int, getattr(Assignment, "CoreBounds", ()))),
        tuple(map(int, getattr(Assignment, "Bounds", ()))),
        tuple(
            (
                str(getattr(Slot, "SlotId", "")),
                str(getattr(Slot, "TerminalName", "")),
                str(getattr(Slot, "Signal", "")),
                str(getattr(Slot, "Face", "")),
                tuple(getattr(Slot, "Origin", ())),
                int(getattr(Slot, "Rotation", 0)),
                bool(getattr(Slot, "MirrorX", False)),
                tuple(getattr(Slot, "MacroBounds", ())),
                tuple(getattr(Slot, "ConnectionPin", ())),
                tuple(getattr(Slot, "ConnectionDirection", ())),
                int(getattr(Slot, "InteriorSpan", 0)),
            )
            for Slot in getattr(Assignment, "SelectedSlots", ())
        ),
        tuple(
            (
                str(getattr(Reservation, "Face", "")),
                int(getattr(Reservation, "NormalCoordinate", 0)),
                int(getattr(Reservation, "LateralMinimum", 0)),
                int(getattr(Reservation, "LateralMaximum", 0)),
                tuple(map(str, getattr(Reservation, "TerminalNames", ()))),
                tuple(map(str, getattr(Reservation, "SlotIds", ()))),
            )
            for Reservation in getattr(Assignment, "FaceReservations", ())
        ),
    )


def _BuildDerivedPerimeterShellPlacementIdentity(
    Placement: Any,
) -> tuple[object, ...]:
    """Return exactly the placed macro facts from which profiles are built."""
    return (
        int(getattr(Placement, "LayerCount", 0)),
        tuple(
            sorted(
                (
                    str(Gate.Name),
                    str(Gate.Kind),
                    int(Gate.X),
                    int(Gate.Y),
                    int(Gate.Z),
                    tuple(map(str, Gate.Outputs)),
                    tuple(map(str, Gate.Inputs)),
                    tuple(map(tuple, Gate.InputPins)),
                    (
                        tuple(Gate.OutputPin)
                        if Gate.OutputPin is not None
                        else None
                    ),
                    int(Gate.Rotation),
                    bool(Gate.MirrorX),
                    tuple(map(tuple, Gate.InputDirections)),
                    (
                        tuple(Gate.OutputDirection)
                        if Gate.OutputDirection is not None
                        else None
                    ),
                )
                for Gate in Placement.Placed.PlacedGates
            )
        ),
    )


def _BuildDerivedPerimeterShellResourceIdentity(
    Resources: Any,
) -> tuple[object, ...]:
    """Return static physical inputs which can affect a ring plane."""
    ResourceGraph = Resources.ResourceGraph
    Cached = getattr(
        ResourceGraph,
        "_DerivedPerimeterShellResourceIdentity",
        None,
    )
    if Cached is not None:
        return Cached
    Identity = (
        str(getattr(ResourceGraph, "GraphVersion", "")),
        tuple(sorted(getattr(ResourceGraph, "ActualBlocks", ()))),
        tuple(sorted(getattr(ResourceGraph, "ElectricalBlocks", ()))),
        tuple(sorted(getattr(ResourceGraph, "SolidBlocks", ()))),
        tuple(sorted(ResourceGraph.StaticKeepOut)),
    )
    # Resource graphs are immutable after construction.  Store this exact
    # sorted identity on the graph itself so every layer/interface shell does
    # not repeatedly sort the same large physical sets.
    setattr(
        ResourceGraph,
        "_DerivedPerimeterShellResourceIdentity",
        Identity,
    )
    return Identity


def _BuildDerivedPerimeterShellInputFingerprint(
    Placement: Any,
    *,
    Resources: Any,
    Technology: RedstoneRoutingTechnology,
    AccessRingTrackCount: int,
    AccessLength: int,
    BoundarySignals: frozenset[str] | None,
    Assignment: Any,
    PerimeterFaceTrackCounts: tuple[tuple[str, int], ...] | None = None,
) -> str:
    """Fingerprint every fixed input consumed before fabric traversal."""
    return sha256(repr((
        "derived-perimeter-fabric-shell-input-v1",
        _BuildDerivedPerimeterShellPlacementIdentity(Placement),
        _BuildDerivedPerimeterShellAssignmentIdentity(Assignment),
        _BuildDerivedPerimeterShellResourceIdentity(Resources),
        int(AccessRingTrackCount),
        int(AccessLength),
        tuple(sorted(BoundarySignals)) if BoundarySignals is not None else None,
        tuple(PerimeterFaceTrackCounts)
        if PerimeterFaceTrackCounts is not None
        else None,
        str(getattr(Technology, "TechnologyVersion", "")),
        repr(Technology),
    )).encode("utf-8")).hexdigest()[:16]


def BuildDerivedPerimeterFabricShell(
    Placement: Any,
    *,
    Resources: Any | None = None,
    Technology: RedstoneRoutingTechnology = (
        DefaultRedstoneRoutingTechnology
    ),
    AccessRingTrackCount: int,
    AccessLength: int | None = None,
    BoundarySignals: frozenset[str] | None = None,
    PerimeterFaceTrackCounts: tuple[tuple[str, int], ...] | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> DerivedPerimeterFabricShell:
    """Build one immutable pre-fabric perimeter geometry shell.

    This is deliberately a construction step before any escape search or
    capacity assignment.  It only consumes fixed placed geometry, frozen I/O
    slots, routing technology, and static resources.
    """
    if AccessRingTrackCount < 1:
        raise ValueError("derived perimeter shell requires a positive track count")
    EffectiveAccessLength = (
        int(Technology.AccessLength)
        if AccessLength is None
        else int(AccessLength)
    )
    if EffectiveAccessLength < 1:
        raise ValueError("derived perimeter shell access length is invalid")
    Assignment = _GetDerivedPerimeterSlotAssignment(Placement)
    if Assignment is None:
        raise ValueError("derived perimeter shell requires a slot assignment")
    if (
        not bool(getattr(Assignment, "Success", False))
        or not bool(getattr(Assignment, "Complete", False))
    ):
        raise ValueError("derived perimeter shell requires a complete assignment")
    Resources = Resources or BuildRoutingResources(
        Placement.Placed,
        WorkCheck=WorkCheck,
    )
    ProfilesBySignal = BuildNetRoutingProfiles(
        Placement.Placed,
        AccessLength=EffectiveAccessLength,
    )
    if BoundarySignals is not None:
        ProfilesBySignal = {
            Signal: Profile
            for Signal, Profile in ProfilesBySignal.items()
            if Signal in BoundarySignals
        }
    Gates = tuple(Placement.Placed.PlacedGates)
    if not Gates:
        raise ValueError("derived perimeter shell requires placed gates")
    BaseY = min(int(Gate.Y) for Gate in Gates)
    MaximumFabricLayer = max(0, int(Placement.LayerCount) - 1)
    FabricLayers = tuple(range(
        MaximumFabricLayer - max(1, int(Placement.LayerCount)) + 1,
        MaximumFabricLayer + 1,
    ))
    FabricYs = tuple(
        Technology.RoutingY(BaseY, Layer)
        for Layer in FabricLayers
    )
    Profiles = tuple(
        DerivedPerimeterFabricProfile(
            Signal=str(Signal),
            Root=tuple(Profile.Root),
            Targets=tuple(map(tuple, Profile.Targets)),
            SourceAccessPath=tuple(map(tuple, Profile.SourceAccessPath)),
            TargetAccessPaths=tuple(
                (tuple(Terminal), tuple(map(tuple, Path)))
                for Terminal, Path in sorted(Profile.TargetAccessPaths.items())
            ),
        )
        for Signal, Profile in sorted(ProfilesBySignal.items())
    )
    TerminalPathByIdentity = {
        (Profile.Signal, Terminal): Path
        for Profile in Profiles
        for Terminal, Path in (
            (Profile.Root, Profile.SourceAccessPath),
            *Profile.TargetAccessPaths,
        )
    }
    AllTerminalPaths = tuple(
        (Signal, Terminal, TerminalPathByIdentity[(Signal, Terminal)])
        for Signal, Terminal in sorted(TerminalPathByIdentity)
    )
    SelectedSlotFaceByTerminal = {
        (str(Slot.Signal), tuple(Slot.ConnectionPin)): str(Slot.Face)
        for Slot in getattr(Assignment, "SelectedSlots", ())
    }
    SlotTerminalKeys = frozenset(SelectedSlotFaceByTerminal)
    PerimeterDrivenRootKeys = frozenset(
        (Profile.Signal, Profile.Root)
        for Profile in Profiles
        if any(
            (Profile.Signal, Target) in SlotTerminalKeys
            for Target in Profile.Targets
        )
    )
    PerimeterDrivenRootFaceByTerminal = {
        Key: Face
        for Key in PerimeterDrivenRootKeys
        if Key not in SlotTerminalKeys
        for Face in (
            _DerivePerimeterRootAccessFace(
                TerminalPathByIdentity.get(Key, ())
            ),
        )
        if Face is not None
    }
    (
        RingBounds,
        Bounds,
        ActiveFaces,
        SlotFaceByTerminal,
        ResolvedPerimeterFaceTrackCounts,
    ) = _BuildDerivedPerimeterRingBounds(
        Assignment,
        Resources=Resources,
        Technology=Technology,
        AccessRingTrackCount=AccessRingTrackCount,
        PerimeterFaceTrackCounts=PerimeterFaceTrackCounts,
        TerminalPathByIdentity=TerminalPathByIdentity,
        PerimeterDrivenRootFaceByTerminal=(
            PerimeterDrivenRootFaceByTerminal
        ),
    )
    if SlotFaceByTerminal != SelectedSlotFaceByTerminal:
        raise RuntimeError(
            "derived perimeter ring did not preserve the frozen slots"
        )
    FabricTerminalKeys = frozenset((
        *SlotTerminalKeys,
        *PerimeterDrivenRootKeys,
    ))
    TerminalPaths = tuple(
        Value
        for Value in AllTerminalPaths
        if (str(Value[0]), tuple(Value[1])) in FabricTerminalKeys
    )
    SlotFaceItems = tuple(sorted(
        (Signal, Terminal, Face)
        for (Signal, Terminal), Face in SlotFaceByTerminal.items()
    ))
    RootFaceItems = tuple(sorted(
        (Signal, Terminal, Face)
        for (Signal, Terminal), Face in (
            PerimeterDrivenRootFaceByTerminal.items()
        )
    ))
    InputFingerprint = _BuildDerivedPerimeterShellInputFingerprint(
        Placement,
        Resources=Resources,
        Technology=Technology,
        AccessRingTrackCount=AccessRingTrackCount,
        AccessLength=EffectiveAccessLength,
        BoundarySignals=BoundarySignals,
        Assignment=Assignment,
        PerimeterFaceTrackCounts=ResolvedPerimeterFaceTrackCounts,
    )
    ShellFingerprint = sha256(repr((
        "derived-perimeter-fabric-shell-v1",
        InputFingerprint,
        Profiles,
        TerminalPaths,
        SlotFaceItems,
        RootFaceItems,
        RingBounds,
        Bounds,
        ActiveFaces,
        ResolvedPerimeterFaceTrackCounts,
        FabricLayers,
        FabricYs,
    )).encode("utf-8")).hexdigest()[:16]
    return DerivedPerimeterFabricShell(
        InputFingerprint=InputFingerprint,
        ShellFingerprint=ShellFingerprint,
        PerimeterSlotAssignmentFingerprint=str(getattr(
            Assignment,
            "AssignmentFingerprint",
            "",
        )),
        AccessRingTrackCount=AccessRingTrackCount,
        AccessLength=EffectiveAccessLength,
        TechnologyFingerprint=sha256(repr((
            str(getattr(Technology, "TechnologyVersion", "")),
            repr(Technology),
        )).encode("utf-8")).hexdigest()[:16],
        Profiles=Profiles,
        TerminalPaths=TerminalPaths,
        SlotFaceItems=SlotFaceItems,
        PerimeterDrivenRootFaceItems=RootFaceItems,
        RingBounds=RingBounds,
        Bounds=Bounds,
        ActiveFaces=ActiveFaces,
        PerimeterFaceTrackCounts=ResolvedPerimeterFaceTrackCounts,
        FabricLayers=FabricLayers,
        FabricYs=FabricYs,
    )


def BuildPlacementAccessNativeRegionRecipe(
    Placement: Any,
    *,
    Resources: Any,
    Shell: DerivedPerimeterFabricShell | None = None,
    TopologyKind: str = "derived-perimeter-access-v1",
    AccessRingTrackCount: int = 0,
    AccessLength: int | None = None,
    FixedAccessFabricLayers: tuple[int, ...] | None = None,
    Technology: RedstoneRoutingTechnology = (
        DefaultRedstoneRoutingTechnology
    ),
    MemberId: str,
) -> PlacementAccessNativeRegionRecipe:
    """Describe one exact derived access region without expanding its graph."""
    if not MemberId:
        raise ValueError("native access-region recipe requires a member id")
    Gates = tuple(Placement.Placed.PlacedGates)
    if not Gates:
        raise ValueError("native access-region recipe requires placed gates")
    BaseY = min(int(Gate.Y) for Gate in Gates)
    if TopologyKind == "derived-perimeter-access-v1":
        if Shell is not None:
            MinimumX, MinimumZ, MaximumX, MaximumZ = Shell.OuterBounds
            FabricYs = tuple(map(int, Shell.FabricYs))
            TerminalPaths = Shell.TerminalPaths
            RegionAccessPaths = tuple(
                tuple(Path)
                for Profile in Shell.Profiles
                for Path in (
                    Profile.SourceAccessPath,
                    *(Value[1] for Value in Profile.TargetAccessPaths),
                )
            )
            ShellIdentity = Shell.ShellFingerprint
        else:
            if AccessRingTrackCount < 1:
                raise ValueError(
                    "derived native access-region recipe requires tracks"
                )
            EffectiveAccessLength = (
                int(Technology.AccessLength)
                if AccessLength is None
                else int(AccessLength)
            )
            Profiles = BuildNetRoutingProfiles(
                Placement.Placed,
                AccessLength=EffectiveAccessLength,
            )
            TerminalPaths = tuple(sorted(
                (
                    str(Signal),
                    tuple(Terminal),
                    tuple(Path),
                )
                for Signal, Profile in Profiles.items()
                for Terminal, Path in (
                    (Profile.Root, Profile.SourceAccessPath),
                    *tuple(sorted(Profile.TargetAccessPaths.items())),
                )
            ))
            RegionAccessPaths = tuple(
                tuple(Path)
                for _Signal, _Terminal, Path in TerminalPaths
            )
            FabricLayers = tuple(range(max(1, int(Placement.LayerCount))))
            FabricYs = tuple(
                int(Technology.RoutingY(BaseY, Layer))
                for Layer in FabricLayers
            )
            Margin = int(Technology.TrackPitch) * AccessRingTrackCount
            MinimumX = min(int(Gate.X) for Gate in Gates) - Margin
            MaximumX = max(
                int(Gate.X)
                + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
                - 1
                for Gate in Gates
            ) + Margin
            MinimumZ = min(int(Gate.Z) for Gate in Gates) - Margin
            MaximumZ = max(
                int(Gate.Z)
                + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
                - 1
                for Gate in Gates
            ) + Margin
            ShellIdentity = sha256(repr((
                "legacy-derived-access-native-region-v1",
                AccessRingTrackCount,
                FabricLayers,
                TerminalPaths,
            )).encode("utf-8")).hexdigest()[:16]
    elif TopologyKind == "fixed-access-band-v1":
        if Shell is not None:
            raise ValueError(
                "fixed native access-region recipe cannot use a shell"
            )
        if not FixedAccessFabricLayers:
            raise ValueError(
                "fixed native access-region recipe requires layers"
            )
        EffectiveAccessLength = (
            int(Technology.AccessLength)
            if AccessLength is None
            else int(AccessLength)
        )
        Profiles = BuildNetRoutingProfiles(
            Placement.Placed,
            AccessLength=EffectiveAccessLength,
        )
        TerminalPaths = tuple(sorted(
            (
                str(Signal),
                tuple(Terminal),
                tuple(Path),
            )
            for Signal, Profile in Profiles.items()
            for Terminal, Path in (
                (Profile.Root, Profile.SourceAccessPath),
                *tuple(sorted(Profile.TargetAccessPaths.items())),
            )
        ))
        RegionAccessPaths = tuple(
            tuple(Path)
            for _Signal, _Terminal, Path in TerminalPaths
        )
        FabricYs = tuple(
            int(Technology.RoutingY(BaseY, Layer))
            for Layer in FixedAccessFabricLayers
        )
        TrackPitch = int(Technology.TrackPitch)
        Margin = TrackPitch * 2
        MinimumX = min(int(Gate.X) for Gate in Gates) - Margin
        MaximumX = max(
            int(Gate.X)
            + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            - 1
            for Gate in Gates
        ) + Margin
        MinimumZ = min(int(Gate.Z) for Gate in Gates) - Margin
        MaximumZ = max(
            int(Gate.Z)
            + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            - 1
            for Gate in Gates
        ) + Margin
        ShellIdentity = sha256(repr((
            "fixed-access-native-region-v1",
            tuple(FixedAccessFabricLayers),
            TerminalPaths,
        )).encode("utf-8")).hexdigest()[:16]
    else:
        raise ValueError("unsupported native access-region topology")
    AllowedAccess = tuple(sorted({
        tuple(Position)
        for Path in RegionAccessPaths
        for Position in Path
    }))
    Bounds = (
        min((int(MinimumX), *(Position[0] for Position in AllowedAccess))),
        max((int(MaximumX), *(Position[0] for Position in AllowedAccess))),
        min((BaseY, *(Position[1] for Position in AllowedAccess))),
        max((max(FabricYs), *(Position[1] for Position in AllowedAccess))),
        min((int(MinimumZ), *(Position[2] for Position in AllowedAccess))),
        max((int(MaximumZ), *(Position[2] for Position in AllowedAccess))),
    )
    ResourceGraph = Resources.ResourceGraph
    NeighborOffsets = tuple(
        tuple(map(int, Position))
        for Position in Technology.NeighborPositions((0, 0, 0))
    )
    PositionGroups = (
        AllowedAccess,
        tuple(sorted(ResourceGraph.ActualBlocks)),
        tuple(sorted(ResourceGraph.ElectricalBlocks)),
        tuple(sorted(ResourceGraph.SolidBlocks)),
        tuple(sorted(ResourceGraph.TorchPoweredSupportBlocks)),
        NeighborOffsets,
    )
    Hasher = sha256()
    Hasher.update(b"placement-access-native-region-recipe-v1\0")
    Hasher.update(MemberId.encode("utf-8"))
    Hasher.update(b"\0")
    Hasher.update(ShellIdentity.encode("ascii"))
    Hasher.update(b"\0")
    Hasher.update(str(getattr(
        Technology,
        "TechnologyVersion",
        "",
    )).encode("utf-8"))
    for Value in Bounds:
        Hasher.update(pack(">i", int(Value)))
    for Group in PositionGroups:
        Hasher.update(pack(">I", len(Group)))
        for X, Y, Z in Group:
            Hasher.update(pack(">iii", int(X), int(Y), int(Z)))
    return PlacementAccessNativeRegionRecipe(
        MemberId=MemberId,
        Bounds=Bounds,
        AllowedAccess=AllowedAccess,
        ActualBlocks=PositionGroups[1],
        ElectricalBlocks=PositionGroups[2],
        SolidBlocks=PositionGroups[3],
        TorchPoweredSupportBlocks=PositionGroups[4],
        NeighborOffsets=NeighborOffsets,
        RecipeFingerprint=Hasher.hexdigest()[:16],
    )


def ProjectDerivedPerimeterFabricShellLayers(
    Source: DerivedPerimeterFabricShell,
    Placement: Any,
    *,
    Resources: Any,
    Technology: RedstoneRoutingTechnology,
    LayerCount: int,
    PerimeterFaceTrackCounts: tuple[tuple[str, int], ...],
) -> DerivedPerimeterFabricShell:
    """Project one exact shell to fewer routing decks without rebuilding it."""
    if LayerCount < 1 or int(getattr(Placement, "LayerCount", 0)) != LayerCount:
        raise ValueError("projected perimeter shell layer contract is invalid")
    if tuple(PerimeterFaceTrackCounts) != Source.PerimeterFaceTrackCounts:
        raise ValueError(
            "projected perimeter shell requires identical per-face tracks"
        )
    Assignment = _GetDerivedPerimeterSlotAssignment(Placement)
    if Assignment is None or str(getattr(
        Assignment,
        "AssignmentFingerprint",
        "",
    )) != Source.PerimeterSlotAssignmentFingerprint:
        raise ValueError(
            "projected perimeter shell requires the identical slot assignment"
        )
    BaseY = min(int(Gate.Y) for Gate in Placement.Placed.PlacedGates)
    FabricLayers = tuple(range(LayerCount))
    FabricYs = tuple(
        Technology.RoutingY(BaseY, Layer)
        for Layer in FabricLayers
    )
    InputFingerprint = _BuildDerivedPerimeterShellInputFingerprint(
        Placement,
        Resources=Resources,
        Technology=Technology,
        AccessRingTrackCount=Source.AccessRingTrackCount,
        AccessLength=Source.AccessLength,
        BoundarySignals=None,
        Assignment=Assignment,
        PerimeterFaceTrackCounts=PerimeterFaceTrackCounts,
    )
    ShellFingerprint = sha256(repr((
        "derived-perimeter-fabric-shell-v1",
        InputFingerprint,
        Source.Profiles,
        Source.TerminalPaths,
        Source.SlotFaceItems,
        Source.PerimeterDrivenRootFaceItems,
        Source.RingBounds,
        Source.Bounds,
        Source.ActiveFaces,
        Source.PerimeterFaceTrackCounts,
        FabricLayers,
        FabricYs,
    )).encode("utf-8")).hexdigest()[:16]
    return replace(
        Source,
        InputFingerprint=InputFingerprint,
        ShellFingerprint=ShellFingerprint,
        FabricLayers=FabricLayers,
        FabricYs=FabricYs,
    )


def _ValidateDerivedPerimeterFabricShell(
    Shell: DerivedPerimeterFabricShell,
    Placement: Any,
    *,
    Resources: Any,
    Technology: RedstoneRoutingTechnology,
    AccessRingTrackCount: int,
    AccessLength: int,
    BoundarySignals: frozenset[str] | None,
    Assignment: Any,
    PerimeterFaceTrackCounts: tuple[tuple[str, int], ...] | None = None,
) -> None:
    """Reject a shell whose immutable inputs differ from this request."""
    if Shell.AccessRingTrackCount != AccessRingTrackCount:
        raise ValueError("derived perimeter shell track count does not match")
    if Shell.AccessLength != AccessLength:
        raise ValueError("derived perimeter shell access length does not match")
    ExpectedTechnologyFingerprint = sha256(repr((
        str(getattr(Technology, "TechnologyVersion", "")),
        repr(Technology),
    )).encode("utf-8")).hexdigest()[:16]
    if Shell.TechnologyFingerprint != ExpectedTechnologyFingerprint:
        raise ValueError("derived perimeter shell technology does not match")
    ExpectedAssignmentFingerprint = str(getattr(
        Assignment,
        "AssignmentFingerprint",
        "",
    ))
    if (
        Shell.PerimeterSlotAssignmentFingerprint
        != ExpectedAssignmentFingerprint
    ):
        raise ValueError("derived perimeter shell assignment does not match")
    ExpectedInputFingerprint = _BuildDerivedPerimeterShellInputFingerprint(
        Placement,
        Resources=Resources,
        Technology=Technology,
        AccessRingTrackCount=AccessRingTrackCount,
        AccessLength=AccessLength,
        BoundarySignals=BoundarySignals,
        Assignment=Assignment,
        PerimeterFaceTrackCounts=(
            PerimeterFaceTrackCounts
            if PerimeterFaceTrackCounts is not None
            else Shell.PerimeterFaceTrackCounts
        ),
    )
    if Shell.InputFingerprint != ExpectedInputFingerprint:
        raise ValueError("derived perimeter shell input identity does not match")


def _BuildShortestFabricEscapePaths(
    Starts: Iterable[Position3],
    IngressNodes: frozenset[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    MaximumPaths: int,
) -> tuple[tuple[Position3, ...], ...]:
    """Return the first deterministic ingress paths in one finite graph."""
    Adjacency: dict[Position3, list[Position3]] = {}
    for First, Second in Edges:
        Adjacency.setdefault(First, []).append(Second)
        Adjacency.setdefault(Second, []).append(First)
    for Values in Adjacency.values():
        Values.sort()
    Queue = deque()
    Parent: dict[Position3, Position3 | None] = {}
    for Start in sorted(set(Starts)):
        if Start not in Adjacency or Start in Parent:
            continue
        Parent[Start] = None
        Queue.append(Start)
    Results: list[tuple[Position3, ...]] = []
    while Queue and len(Results) < MaximumPaths:
        Current = Queue.popleft()
        if Current in IngressNodes:
            ReversedPath = []
            Cursor: Position3 | None = Current
            while Cursor is not None:
                ReversedPath.append(Cursor)
                Cursor = Parent[Cursor]
            Results.append(tuple(reversed(ReversedPath)))
        for Next in Adjacency.get(Current, ()):
            if Next in Parent:
                continue
            Parent[Next] = Current
            Queue.append(Next)
    return tuple(Results)


def _BuildIndependentShortestFabricEscapePaths(
    Start: Position3,
    Ingresses: Iterable[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    AlternateIngresses: frozenset[Position3] = frozenset(),
    Adjacency: dict[Position3, tuple[Position3, ...]] | None = None,
) -> tuple[tuple[Position3, ...], ...]:
    """Build one deterministic shortest path independently per ingress."""
    if Adjacency is None:
        MutableAdjacency: dict[Position3, list[Position3]] = {}
        for First, Second in Edges:
            MutableAdjacency.setdefault(First, []).append(Second)
            MutableAdjacency.setdefault(Second, []).append(First)
        Adjacency = {
            Position: tuple(sorted(Values))
            for Position, Values in MutableAdjacency.items()
        }
    if Start not in Adjacency:
        return ()
    OrderedIngresses = tuple(dict.fromkeys(Ingresses))
    if not AlternateIngresses:
        # One deterministic breadth-first tree provides a shortest path to
        # every retained ingress.  Re-running A* once per ingress multiplies
        # identical geometry work and can delay deadline observation.
        Remaining = {
            Ingress for Ingress in OrderedIngresses
            if Ingress in Adjacency
        }
        Parent: dict[Position3, Position3 | None] = {Start: None}
        Frontier = deque((Start,))
        while Frontier and Remaining:
            Current = Frontier.popleft()
            Remaining.discard(Current)
            for Next in Adjacency.get(Current, ()):
                if Next in Parent:
                    continue
                Parent[Next] = Current
                Frontier.append(Next)
        Results = []
        for Ingress in OrderedIngresses:
            if Ingress not in Parent:
                continue
            ReversedPath = []
            Cursor: Position3 | None = Ingress
            while Cursor is not None:
                ReversedPath.append(Cursor)
                Cursor = Parent[Cursor]
            Results.append(tuple(reversed(ReversedPath)))
        return tuple(Results)
    Results = []
    for Ingress in OrderedIngresses:
        if Ingress not in Adjacency:
            continue

        def Distance(Position: Position3) -> int:
            return sum(
                abs(Position[Index] - Ingress[Index])
                for Index in range(3)
            )

        for ReverseTieBreak in (
            (False, True) if Ingress in AlternateIngresses else (False,)
        ):
            def TieKey(Position: Position3) -> tuple[int, int, int]:
                return tuple(
                    -Value if ReverseTieBreak else Value
                    for Value in Position
                )

            Frontier = [(Distance(Start), 0, TieKey(Start), Start)]
            BestCost = {Start: 0}
            Parent: dict[Position3, Position3 | None] = {Start: None}
            while Frontier:
                _Score, Cost, _Tie, Current = heappop(Frontier)
                if Cost != BestCost.get(Current):
                    continue
                if Current == Ingress:
                    break
                for Next in sorted(
                    Adjacency.get(Current, ()),
                    key=lambda Position: (
                        Distance(Position),
                        TieKey(Position),
                    ),
                ):
                    NextCost = Cost + 1
                    if NextCost >= BestCost.get(Next, 1 << 60):
                        continue
                    BestCost[Next] = NextCost
                    Parent[Next] = Current
                    heappush(
                        Frontier,
                        (
                            NextCost + Distance(Next),
                            NextCost,
                            TieKey(Next),
                            Next,
                        ),
                    )
            if Ingress not in Parent:
                continue
            ReversedPath = []
            Cursor: Position3 | None = Ingress
            while Cursor is not None:
                ReversedPath.append(Cursor)
                Cursor = Parent[Cursor]
            Path = tuple(reversed(ReversedPath))
            if Path not in Results:
                Results.append(Path)
    return tuple(Results)


def _BuildDerivedPerimeterCycleRouteNodeSets(
    Ingresses: tuple[Position3, ...],
    FabricY: int,
    FabricEdges: Iterable[tuple[Position3, Position3]],
) -> tuple[tuple[Position3, ...], ...] | None:
    """Enumerate the exact terminal-spanning arc domain of one ring cycle.

    A derived perimeter fabric is a collection of disjoint, planar cycles:
    one for each selected routing layer and ring track.  Given the ingress
    points of one signal, every minimal connected subgraph of a cycle is the
    cycle with one terminal-free gap removed.  Enumerating those gaps is
    finite, deterministic, and complete for this topology; it avoids making
    a single breadth-first tie-break into an accidental placement policy.

    ``None`` means the claimed ring component is not a cycle, so its route
    domain cannot be treated as complete.  An empty tuple means the selected
    ingresses are on different ring components, which is a complete rejection
    of that particular stub selection.
    """
    UniqueIngresses = tuple(sorted(set(Ingresses)))
    if len(UniqueIngresses) <= 1:
        return (UniqueIngresses,)
    Adjacency: dict[Position3, list[Position3]] = {}
    for First, Second in FabricEdges:
        if First[1] != FabricY or Second[1] != FabricY:
            continue
        Adjacency.setdefault(First, []).append(Second)
        Adjacency.setdefault(Second, []).append(First)
    if any(Ingress not in Adjacency for Ingress in UniqueIngresses):
        return ()
    for Neighbors in Adjacency.values():
        Neighbors.sort()

    Component: set[Position3] = set()
    Frontier = deque((UniqueIngresses[0],))
    while Frontier:
        Current = Frontier.popleft()
        if Current in Component:
            continue
        Component.add(Current)
        Frontier.extend(
            Next for Next in Adjacency[Current]
            if Next not in Component
        )
    if any(Ingress not in Component for Ingress in UniqueIngresses):
        return ()
    if any(len(Adjacency[Position]) != 2 for Position in Component):
        return None

    Cycle: list[Position3] = []
    Start = min(Component)
    Previous: Position3 | None = None
    Current = Start
    while True:
        if Current in Cycle:
            return None
        Cycle.append(Current)
        Choices = tuple(
            Next for Next in Adjacency[Current]
            if Next != Previous
        )
        if not Choices:
            return None
        Next = min(Choices)
        if Next == Start:
            break
        Previous, Current = Current, Next
    if len(Cycle) != len(Component):
        return None

    CycleLength = len(Cycle)
    CycleIndex = {Position: Index for Index, Position in enumerate(Cycle)}
    TerminalIndices = tuple(sorted(
        CycleIndex[Ingress] for Ingress in UniqueIngresses
    ))
    Results: list[tuple[Position3, ...]] = []
    Seen = set()
    for StartIndex, EndIndex in zip(
        TerminalIndices,
        (*TerminalIndices[1:], TerminalIndices[0]),
    ):
        GapLength = (EndIndex - StartIndex) % CycleLength
        if GapLength == 0:
            return None
        GapInterior = {
            Cycle[(StartIndex + Offset) % CycleLength]
            for Offset in range(1, GapLength)
        }
        Nodes = tuple(sorted(
            Position for Position in Cycle
            if Position not in GapInterior
        ))
        if Nodes in Seen:
            continue
        Seen.add(Nodes)
        Results.append(Nodes)
    return tuple(sorted(Results, key=lambda Nodes: (len(Nodes), Nodes)))


def _BuildShortestLegalFabricEscapePaths(
    Start: Position3,
    Ingresses: Iterable[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    FixedPrefix: tuple[Position3, ...],
    ResourceGraph: Any,
    Adjacency: dict[Position3, tuple[Position3, ...]] | None = None,
) -> tuple[tuple[Position3, ...], ...]:
    """Return one deterministic, geometrically distinct path per ingress."""
    if Adjacency is None:
        MutableAdjacency: dict[Position3, list[Position3]] = {}
        for First, Second in Edges:
            MutableAdjacency.setdefault(First, []).append(Second)
            MutableAdjacency.setdefault(Second, []).append(First)
        Adjacency = {
            Position: tuple(sorted(Values))
            for Position, Values in MutableAdjacency.items()
        }
    OrderedIngresses = tuple(dict.fromkeys(Ingresses))
    if (
        Start not in Adjacency
        or any(Ingress not in Adjacency for Ingress in OrderedIngresses)
    ):
        return ()
    InitialClaims = ResourceGraph.BuildRouteClaims(FixedPrefix)
    if FindSelfClaimConflicts({"PlacementAccess": InitialClaims}):
        return ()
    Results = []
    for Ingress in OrderedIngresses:
        # This search can touch a large region even though the resulting
        # fabric ring has few nodes.  The region and target are immutable for
        # this terminal, so calculating a Manhattan distance and re-sorting
        # each adjacency fanout at every heap pop only repeats deterministic
        # work.  Cache both once per ingress; path legality and tie-breaking
        # stay exactly the same.
        DistanceByPosition = {
            Position: (
                abs(Position[0] - Ingress[0])
                + abs(Position[1] - Ingress[1])
                + abs(Position[2] - Ingress[2])
            )
            for Position in Adjacency
        }
        OrderedNeighbors = {
            Position: tuple(sorted(
                Neighbors,
                key=lambda Value: (DistanceByPosition[Value], Value),
            ))
            for Position, Neighbors in Adjacency.items()
        }

        StartState = (Start, (0, 0, 0))
        Frontier = [(
            DistanceByPosition[Start],
            0,
            Start,
            (0, 0, 0),
        )]
        BestCost = {StartState: 0}
        Parent: dict[
            tuple[Position3, Position3],
            tuple[Position3, Position3] | None,
        ] = {StartState: None}
        ReachedPath: tuple[Position3, ...] | None = None
        while Frontier:
            _Score, Cost, Current, PriorDirection = heappop(Frontier)
            CurrentState = (Current, PriorDirection)
            if Cost != BestCost.get(CurrentState):
                continue
            if Current == Ingress:
                ReversedPath = []
                Cursor: tuple[Position3, Position3] | None = CurrentState
                while Cursor is not None:
                    ReversedPath.append(Cursor[0])
                    Cursor = Parent[Cursor]
                Path = tuple(reversed(ReversedPath))
                CompletePath = _ErasePlacementAccessPathLoops((
                    *FixedPrefix,
                    *Path[1:],
                ))
                if not FindSelfClaimConflicts({
                    "PlacementAccess": ResourceGraph.BuildRouteClaims(
                        CompletePath
                    )
                }):
                    ReachedPath = Path
                    break
            for Next in OrderedNeighbors.get(Current, ()):
                Direction = tuple(
                    Next[Index] - Current[Index]
                    for Index in range(3)
                )
                BendPenalty = int(
                    PriorDirection != (0, 0, 0)
                    and Direction != PriorDirection
                ) * 4
                NextCost = Cost + 1 + BendPenalty
                NextState = (Next, Direction)
                if NextCost >= BestCost.get(NextState, 1 << 60):
                    continue
                BestCost[NextState] = NextCost
                Parent[NextState] = CurrentState
                heappush(
                    Frontier,
                    (
                        NextCost + DistanceByPosition[Next],
                        NextCost,
                        Next,
                        Direction,
                    ),
                )
        if ReachedPath is not None:
            Results.append(ReachedPath)
    return tuple(Results)


def _BuildBoundedLegalDerivedEscapePaths(
    Start: Position3,
    Ingresses: Iterable[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    FixedPrefix: tuple[Position3, ...],
    ResourceGraph: Any,
    *,
    WorkBudget: _AccessFabricWorkBudget | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    Adjacency: dict[Position3, tuple[Position3, ...]] | None = None,
    RemainingMilliseconds: Callable[[], int] | None = None,
    NativeDiagnostics: dict[str, object] | None = None,
) -> tuple[tuple[tuple[Position3, ...], ...], bool]:
    """Build a finite legal escape domain with one state search per terminal.

    The old derived-perimeter path first generated geometrically short stubs,
    then re-ran a target-specific legal A* for every rejected ingress.  That
    was an accidental retry cascade inside one nominal access factor.  The
    fixed domain already has all permitted ingresses, so visit its
    ``(position, prior direction)`` state graph once, recording the first
    legal deterministic path for each ingress.  Direction remains part of
    the state because bend cost distinguishes otherwise identical positions.

    A shared work budget stops the construction cleanly.  The caller keeps
    any materialized paths for diagnostics but marks the factor incomplete,
    so a cap can never be misreported as an empty exhaustive domain.
    """
    if Adjacency is None:
        MutableAdjacency: dict[Position3, list[Position3]] = {}
        for First, Second in Edges:
            MutableAdjacency.setdefault(First, []).append(Second)
            MutableAdjacency.setdefault(Second, []).append(First)
        Adjacency = {
            Position: tuple(sorted(Values))
            for Position, Values in MutableAdjacency.items()
        }
    OrderedIngresses = tuple(dict.fromkeys(Ingresses))
    if (
        Start not in Adjacency
        or any(Ingress not in Adjacency for Ingress in OrderedIngresses)
    ):
        return (), not (WorkBudget is not None and WorkBudget.Exhausted)
    InitialClaims = ResourceGraph.BuildRouteClaims(FixedPrefix)
    if FindSelfClaimConflicts({"PlacementAccess": InitialClaims}):
        return (), not (WorkBudget is not None and WorkBudget.Exhausted)

    if _BuildDerivedEscapeStatePathsBounded is not None:
        if NativeDiagnostics is not None:
            NativeDiagnostics["Used"] = True
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "placement-access-native-legal-escape",
                "SignalTerminalStart": list(Start),
                "RemainingIngressCount": len(OrderedIngresses),
            })
        ReachedPaths: dict[Position3, tuple[Position3, ...]] = {}
        RemainingExpansionCount = (
            max(
                0,
                WorkBudget.MaximumExpansions
                - WorkBudget.ExpansionCount,
            )
            if WorkBudget is not None
            else max(
                1,
                1 + sum(len(Value) for Value in Adjacency.values()),
            )
        )
        if RemainingExpansionCount < 1:
            if WorkBudget is not None:
                WorkBudget.Exhausted = True
            return (), False
        NativeRemainingMilliseconds = max(
            1,
            int(
                RemainingMilliseconds()
                if RemainingMilliseconds is not None
                else 60_000
            ),
        )
        NativeStartedAt = monotonic()
        (
            NativeStatus,
            NativeRequests,
            NativeExpansionCount,
            NativeWorkCapExceeded,
            NativeDeadlineExceeded,
        ) = _BuildDerivedEscapeStatePathsBounded(
            tuple(sorted(Adjacency.items())),
            ((
                "escape",
                Start,
                OrderedIngresses,
                (),
                tuple(FixedPrefix),
                tuple(sorted(Adjacency)),
                True,
            ),),
            4,
            RemainingExpansionCount,
            NativeRemainingMilliseconds,
        )
        if NativeDiagnostics is not None:
            NativeDiagnostics["CallCount"] = int(
                NativeDiagnostics.get("CallCount", 0)
            ) + 1
            NativeDiagnostics["ExpansionCount"] = int(
                NativeDiagnostics.get("ExpansionCount", 0)
            ) + int(NativeExpansionCount)
            NativeDiagnostics["ElapsedSeconds"] = float(
                NativeDiagnostics.get("ElapsedSeconds", 0.0)
            ) + (monotonic() - NativeStartedAt)
            NativeDiagnostics["Complete"] = bool(
                NativeDiagnostics.get("Complete", True)
                and not NativeWorkCapExceeded
                and not NativeDeadlineExceeded
            )
        if WorkBudget is not None:
            WorkBudget.ExpansionCount += int(NativeExpansionCount)
            WorkBudget.Exhausted = bool(NativeWorkCapExceeded)
        if NativeDeadlineExceeded and WorkCheck is not None:
            WorkCheck({
                "Phase": "placement-access-native-legal-escape-complete",
                "NativeStatus": str(NativeStatus),
                "ExpansionCount": int(NativeExpansionCount),
            })
        NativeComplete = not (
            NativeWorkCapExceeded or NativeDeadlineExceeded
        )
        NativeCandidates = (
            tuple(NativeRequests[0][1]) if NativeRequests else ()
        )
        for Ingress, _PriorDirection, PathValue in NativeCandidates:
            Ingress = tuple(Ingress)
            if Ingress in ReachedPaths:
                continue
            Path = tuple(map(tuple, PathValue))
            CompletePath = _ErasePlacementAccessPathLoops((
                *FixedPrefix,
                *Path[1:],
            ))
            if not FindSelfClaimConflicts({
                "PlacementAccess": ResourceGraph.BuildRouteClaims(
                    CompletePath
                )
            }):
                ReachedPaths[Ingress] = Path
        return (
            tuple(
                ReachedPaths[Ingress]
                for Ingress in OrderedIngresses
                if Ingress in ReachedPaths
            ),
            NativeComplete,
        )

    if NativeDiagnostics is not None:
        NativeDiagnostics["FallbackUsed"] = True

    RemainingIngresses = set(OrderedIngresses)
    InitialDirection = (0, 0, 0)
    StartState = (Start, InitialDirection)
    Frontier: list[tuple[int, Position3, Position3]] = [
        (0, Start, InitialDirection),
    ]
    BestCost: dict[tuple[Position3, Position3], int] = {StartState: 0}
    Parent: dict[
        tuple[Position3, Position3],
        tuple[Position3, Position3] | None,
    ] = {StartState: None}
    ReachedPaths: dict[Position3, tuple[Position3, ...]] = {}

    while Frontier and RemainingIngresses:
        Cost, Current, PriorDirection = heappop(Frontier)
        CurrentState = (Current, PriorDirection)
        if Cost != BestCost.get(CurrentState):
            continue
        if WorkBudget is not None and not WorkBudget.Consume(
            WorkCheck,
            SignalTerminalStart=list(Start),
            RemainingIngressCount=len(RemainingIngresses),
        ):
            break
        if Current in RemainingIngresses:
            ReversedPath = []
            Cursor: tuple[Position3, Position3] | None = CurrentState
            while Cursor is not None:
                ReversedPath.append(Cursor[0])
                Cursor = Parent[Cursor]
            Path = tuple(reversed(ReversedPath))
            CompletePath = _ErasePlacementAccessPathLoops((
                *FixedPrefix,
                *Path[1:],
            ))
            if not FindSelfClaimConflicts({
                "PlacementAccess": ResourceGraph.BuildRouteClaims(
                    CompletePath
                )
            }):
                ReachedPaths[Current] = Path
                RemainingIngresses.remove(Current)
                if not RemainingIngresses:
                    break
        for Next in Adjacency.get(Current, ()):
            Direction = tuple(
                Next[Index] - Current[Index]
                for Index in range(3)
            )
            BendPenalty = int(
                PriorDirection != InitialDirection
                and Direction != PriorDirection
            ) * 4
            NextCost = Cost + 1 + BendPenalty
            NextState = (Next, Direction)
            if NextCost >= BestCost.get(NextState, 1 << 60):
                continue
            BestCost[NextState] = NextCost
            Parent[NextState] = CurrentState
            heappush(Frontier, (NextCost, Next, Direction))

    return (
        tuple(
            ReachedPaths[Ingress]
            for Ingress in OrderedIngresses
            if Ingress in ReachedPaths
        ),
        not (WorkBudget is not None and WorkBudget.Exhausted),
    )


def _BuildFabricIngressSegmentPaths(
    Anchor: Position3,
    Ingresses: Iterable[Position3],
    FabricEdges: Iterable[tuple[Position3, Position3]],
) -> tuple[tuple[Position3, ...], ...]:
    """Connect one legal face anchor to each ingress on its ring segment.

    A frozen slot's normal escape reaches one ring node; its remaining
    alternatives are lateral choices *on that already-materialized physical
    segment*.  Searching the full exterior state graph again for every
    lateral node repeats the same anchor work.  This helper traverses only
    the immutable fabric segment, preserving every reachable ingress and its
    exact edge sequence without scheduling another escape search.
    """
    OrderedIngresses = tuple(dict.fromkeys(Ingresses))
    if not OrderedIngresses:
        return ()
    Adjacency: dict[Position3, list[Position3]] = {}
    for First, Second in FabricEdges:
        Adjacency.setdefault(First, []).append(Second)
        Adjacency.setdefault(Second, []).append(First)
    if Anchor not in Adjacency:
        return ((Anchor,),) if OrderedIngresses == (Anchor,) else ()
    Parent: dict[Position3, Position3 | None] = {Anchor: None}
    Frontier = deque((Anchor,))
    Remaining = set(OrderedIngresses)
    while Frontier and Remaining:
        Current = Frontier.popleft()
        Remaining.discard(Current)
        for Next in sorted(Adjacency.get(Current, ())):
            if Next in Parent:
                continue
            Parent[Next] = Current
            Frontier.append(Next)
    Results: list[tuple[Position3, ...]] = []
    for Ingress in OrderedIngresses:
        if Ingress not in Parent:
            continue
        ReversePath = []
        Cursor: Position3 | None = Ingress
        while Cursor is not None:
            ReversePath.append(Cursor)
            Cursor = Parent[Cursor]
        Results.append(tuple(reversed(ReversePath)))
    return tuple(Results)


def _BuildSharedLegalFabricEscapePaths(
    Start: Position3,
    Ingresses: Iterable[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    FixedPrefix: tuple[Position3, ...],
    ResourceGraph: Any,
) -> tuple[tuple[Position3, ...], ...]:
    """Build the compact shared escape tree used by one-plane fabrics."""
    Adjacency: dict[Position3, list[Position3]] = {}
    for First, Second in Edges:
        Adjacency.setdefault(First, []).append(Second)
        Adjacency.setdefault(Second, []).append(First)
    for Values in Adjacency.values():
        Values.sort()
    OrderedIngresses = tuple(dict.fromkeys(Ingresses))
    RemainingIngresses = set(OrderedIngresses)
    if Start not in Adjacency:
        return ()
    Queue = deque((Start,))
    Parent: dict[Position3, Position3 | None] = {Start: None}
    CompletePathByNode = {Start: tuple(FixedPrefix)}
    ClaimsByNode = {
        Start: ResourceGraph.BuildRouteClaims(FixedPrefix)
    }
    ReachedPaths: dict[Position3, tuple[Position3, ...]] = {}
    while Queue and RemainingIngresses:
        Current = Queue.popleft()
        if Current in RemainingIngresses:
            ReversedPath = []
            Cursor: Position3 | None = Current
            while Cursor is not None:
                ReversedPath.append(Cursor)
                Cursor = Parent[Cursor]
            ReachedPaths[Current] = tuple(reversed(ReversedPath))
            RemainingIngresses.remove(Current)
        for Next in Adjacency.get(Current, ()):
            if Next in Parent:
                continue
            CandidatePath = _ErasePlacementAccessPathLoops((
                *CompletePathByNode[Current],
                Next,
            ))
            CurrentClaims = ClaimsByNode[Current]
            # Extending one simple BFS path changes claims only at the new
            # wire node and at graph primitives joining it to already-owned
            # wire cells.  Build that exact additive delta instead of
            # rebuilding the increasingly long path at every visited node.
            # If loop erasure changed the prefix, fall back to the canonical
            # builder because claims must also be removed in that rare case.
            if len(CandidatePath) != len(CompletePathByNode[Current]) + 1:
                CandidateClaims = ResourceGraph.BuildRouteClaims(
                    CandidatePath
                )
            else:
                WireCells = CurrentClaims.WireCells | frozenset((Next,))
                RequiredAirCells = set(
                    CurrentClaims.RequiredAirCells
                )
                for Neighbor in ResourceGraph.Technology.NeighborPositions(
                    Next
                ):
                    if Neighbor not in CurrentClaims.WireCells:
                        continue
                    Primitive = ResourceGraph.BuildPrimitive(Next, Neighbor)
                    if Primitive is not None:
                        RequiredAirCells.update(
                            Primitive.Claims.RequiredAirCells
                        )
                CandidateClaims = RoutingResourceClaims(
                    WireCells=WireCells,
                    SupportCells=(
                        CurrentClaims.SupportCells
                        | frozenset(((Next[0], Next[1] - 1, Next[2]),))
                    ),
                    RequiredAirCells=frozenset(RequiredAirCells),
                    ElectricalCells=(
                        CurrentClaims.ElectricalCells
                        | frozenset((Next,))
                        | frozenset(
                            ResourceGraph.Technology.NeighborPositions(Next)
                        )
                    ),
                )
            if FindSelfClaimConflicts({"PlacementAccess": CandidateClaims}):
                continue
            Parent[Next] = Current
            CompletePathByNode[Next] = CandidatePath
            ClaimsByNode[Next] = CandidateClaims
            Queue.append(Next)
    return tuple(
        ReachedPaths[Ingress]
        for Ingress in OrderedIngresses
        if Ingress in ReachedPaths
    )


def _ErasePlacementAccessPathLoops(
    Path: Iterable[Position3],
) -> tuple[Position3, ...]:
    """Erase complete walk loops without inventing non-graph transitions."""
    Result: list[Position3] = []
    PositionIndex: dict[Position3, int] = {}
    for Position in Path:
        PriorIndex = PositionIndex.get(Position)
        if PriorIndex is not None:
            for Removed in Result[PriorIndex + 1:]:
                PositionIndex.pop(Removed, None)
            del Result[PriorIndex + 1:]
            continue
        PositionIndex[Position] = len(Result)
        Result.append(Position)
    return tuple(Result)


def ResolveFixedAccessFabricLayerCount(
    Profiles: Mapping[str, Any],
    DeclaredRoutingLayerCount: int,
) -> int:
    """Return the one physical access band owned by a fixed layer world.

    The band is placed on the declared world's highest routing deck below.
    Adjacent full bands cannot coexist at the technology's two-block pitch:
    the higher support plane occupies lower-deck headroom.  Layer identity is
    therefore expressed by band elevation, not by stacking speculative bands.
    """
    if DeclaredRoutingLayerCount < 1:
        raise ValueError("declared routing layer count must be positive")
    _ = Profiles
    return 1


def _BuildDeferredPlacementAccessPathClaims(
    Path: tuple[Position3, ...],
    Technology: RedstoneRoutingTechnology,
) -> tuple[RoutingResourceClaims, bool, str]:
    """Freeze an exact path without expanding derived claim categories.

    Support and electrical cells are deterministic monotone functions of the
    wire path and technology.  The compact native catalog expands them lazily
    for an attempted member; Python retains the exact required-air set needed
    for vertical self-legality and validates the complete selected claims at
    handoff.
    """
    WireCells = frozenset(Path)
    VerticalConnectionOffsets = _DeferredVerticalConnectionOffsets(
        Technology
    )
    RequiredAirCells: set[Position3] = set()
    for First in WireCells:
        for DeltaX, DeltaY, DeltaZ in VerticalConnectionOffsets:
            Second = (
                First[0] + DeltaX,
                First[1] + DeltaY,
                First[2] + DeltaZ,
            )
            if (
                Second in WireCells
                and Second > First
            ):
                Lower = First if First[1] < Second[1] else Second
                RequiredAirCells.add((
                    Lower[0],
                    Lower[1] + 1,
                    Lower[2],
                ))
    RequiredAir = frozenset(RequiredAirCells)
    SelfLegal = not (
        RequiredAir & WireCells
        or any(
            (X, Y - 1, Z) in WireCells
            or (X, Y - 1, Z) in RequiredAir
            for X, Y, Z in WireCells
        )
    )
    Claims = RoutingResourceClaims(
        WireCells=WireCells,
        RequiredAirCells=RequiredAir,
    )
    Fingerprint = sha256(repr((
        "deferred-placement-access-path-claims-v1",
        getattr(Technology, "TechnologyVersion", ""),
        repr(Technology),
        tuple(sorted(WireCells)),
        tuple(sorted(RequiredAir)),
    )).encode("utf-8")).hexdigest()[:16]
    return Claims, SelfLegal, Fingerprint


def _BuildDeferredPlacementAccessClaimsFingerprint(
    WireCells: frozenset[Position3],
    RequiredAirCells: frozenset[Position3],
    Technology: RedstoneRoutingTechnology,
) -> str:
    """Hash an exact deferred claim without repr-building its full sets."""
    Hasher = sha256()
    Hasher.update(b"deferred-placement-access-path-claims-v2\0")
    Hasher.update(str(getattr(
        Technology,
        "TechnologyVersion",
        "",
    )).encode("utf-8"))
    Hasher.update(b"\0")
    Hasher.update(repr(Technology).encode("utf-8"))
    for Category, Cells in (
        (b"wire", WireCells),
        (b"air", RequiredAirCells),
    ):
        Hasher.update(Category)
        Hasher.update(pack(">I", len(Cells)))
        for X, Y, Z in sorted(Cells):
            Hasher.update(pack(">iii", int(X), int(Y), int(Z)))
    return Hasher.hexdigest()[:16]


@lru_cache(maxsize=None)
def _DeferredVerticalConnectionOffsets(
    Technology: RedstoneRoutingTechnology,
) -> tuple[Position3, ...]:
    """Cache exact vertical dust-neighbor deltas for deferred claims."""
    Origin = (0, 0, 0)
    return tuple(
        (X, Y, Z)
        for X, Y, Z in Technology.NeighborPositions(Origin)
        if Y != 0
    )


def _BuildPlacementAccessFabricGenerator(
    Placement: Any,
    *,
    Resources: Any | None = None,
    Technology: RedstoneRoutingTechnology = (
        DefaultRedstoneRoutingTechnology
    ),
    AccessLength: int | None = None,
    LaneCount: int | None = None,
    MaximumEscapeStubsPerTerminal: int | None = None,
    TopologyKind: str = "fixed-access-band-v1",
    AccessRingTrackCount: int = 0,
    Shell: DerivedPerimeterFabricShell | None = None,
    BoundarySignals: frozenset[str] | None = None,
    BoundaryTerminalKeys: frozenset[
        tuple[str, Position3]
    ] | None = None,
    CompleteRouteSignals: frozenset[str] = frozenset(),
    MaximumLegalEscapeExpansions: int | None = None,
    DeriveLegalEscapeWorkLimit: bool = False,
    RestrictDerivedIngressToRepresentatives: bool = False,
    DeferEscapeStubCapacityResourceIds: bool = False,
    FixedAccessFabricLayers: tuple[int, ...] | None = None,
    NativeEscapeRemainingMilliseconds: Callable[[], int] | None = None,
    NativeEscapeBatchMemberId: str = "",
    NativeEscapeBatchPhysicalIdentity: str = "",
    PreparedNativeRegionAdjacencyValues: tuple[
        tuple[Position3, tuple[Position3, ...]], ...
    ] | None = None,
    PrepareNativeEscapeBatchOnly: bool = False,
    NativeEscapeBatchResult: (
        PlacementAccessNativeEscapeMemberResult | None
    ) = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PlacementAccessFabric:
    """Construct one fixed access fabric from placement and technology."""
    if LaneCount is not None and LaneCount < 1:
        raise ValueError("placement access fabric requires a positive lane count")
    if TopologyKind not in {
        "fixed-access-band-v1",
        "derived-perimeter-access-v1",
    }:
        raise ValueError("unsupported placement access topology")
    IsPerimeterTopology = TopologyKind == "derived-perimeter-access-v1"
    if IsPerimeterTopology:
        if AccessRingTrackCount < 1:
            raise ValueError("access ring requires a positive track count")
    elif AccessRingTrackCount != 0:
        raise ValueError("non-ring access fabric cannot declare ring tracks")
    if (
        Shell is not None
        and TopologyKind != "derived-perimeter-access-v1"
    ):
        raise ValueError("derived perimeter shell requires derived topology")
    if (
        MaximumEscapeStubsPerTerminal is not None
        and MaximumEscapeStubsPerTerminal < 1
    ):
        raise ValueError("placement access fabric requires escape candidates")
    if (
        MaximumLegalEscapeExpansions is not None
        and MaximumLegalEscapeExpansions < 1
    ):
        raise ValueError("placement access fabric requires legal escape work")
    if not isinstance(DeriveLegalEscapeWorkLimit, bool):
        raise TypeError("DeriveLegalEscapeWorkLimit must be bool")
    if not isinstance(RestrictDerivedIngressToRepresentatives, bool):
        raise TypeError(
            "RestrictDerivedIngressToRepresentatives must be bool"
        )
    if not isinstance(DeferEscapeStubCapacityResourceIds, bool):
        raise TypeError("DeferEscapeStubCapacityResourceIds must be bool")
    if FixedAccessFabricLayers is not None:
        if TopologyKind != "fixed-access-band-v1":
            raise ValueError(
                "fixed access layer catalog requires fixed topology"
            )
        if (
            not FixedAccessFabricLayers
            or FixedAccessFabricLayers
            != tuple(sorted(set(FixedAccessFabricLayers)))
            or any(Layer < 0 for Layer in FixedAccessFabricLayers)
        ):
            raise ValueError(
                "fixed access layer catalog requires canonical layers"
            )
    if not isinstance(PrepareNativeEscapeBatchOnly, bool):
        raise TypeError("PrepareNativeEscapeBatchOnly must be bool")
    if PrepareNativeEscapeBatchOnly and not NativeEscapeBatchMemberId:
        raise ValueError(
            "native escape batch preparation requires a member id"
        )
    if (
        PreparedNativeRegionAdjacencyValues is not None
        and not PrepareNativeEscapeBatchOnly
    ):
        raise ValueError(
            "prepared native access adjacency is preparation-only"
        )
    if (
        (PrepareNativeEscapeBatchOnly or NativeEscapeBatchResult is not None)
        and not NativeEscapeBatchPhysicalIdentity
    ):
        raise ValueError(
            "native escape batch requires an exact physical identity"
        )
    if (
        NativeEscapeBatchResult is not None
        and not NativeEscapeBatchMemberId
    ):
        raise ValueError("native escape batch result requires a member id")
    if PrepareNativeEscapeBatchOnly and NativeEscapeBatchResult is not None:
        raise ValueError(
            "native escape batch preparation cannot consume a result"
        )
    Placed = Placement.Placed
    Resources = Resources or BuildRoutingResources(Placed, WorkCheck=WorkCheck)
    NativeEscapeDiagnostics: dict[str, object] = {
        "Used": False,
        "CallCount": 0,
        "ExpansionCount": 0,
        "Complete": True,
        "ElapsedSeconds": 0.0,
        "FallbackUsed": False,
        "ClaimBatchWorkItems": 0,
        "ClaimBatchWorkerCount": 0,
        "ClaimBatchElapsedSeconds": 0.0,
        "DominatedEscapeStubCount": 0,
        "SharedBatchUsed": NativeEscapeBatchResult is not None,
        "SharedBatchElapsedSeconds": (
            float(NativeEscapeBatchResult.SharedBatchElapsedSeconds)
            if NativeEscapeBatchResult is not None
            else 0.0
        ),
    }

    def ResolveNativeEscapeRequests(
        Requests: tuple[tuple[object, ...], ...],
        RequestInputs: Mapping[str, object],
        Adjacency: Mapping[Position3, tuple[Position3, ...]],
        MaximumExpansionCount: int,
    ) -> (
        tuple[str, tuple[tuple[object, ...], ...], int, bool, bool, float]
        | PlacementAccessNativeEscapeMemberPreparation
    ):
        AdjacencyValues = tuple(sorted(Adjacency.items()))
        PreparationFingerprint = sha256(repr((
            "placement-access-native-escape-member-v3",
            NativeEscapeBatchMemberId,
            NativeEscapeBatchPhysicalIdentity,
            TopologyKind,
            getattr(Technology, "TechnologyVersion", ""),
            len(AdjacencyValues),
            sum(len(Neighbors) for _Position, Neighbors in AdjacencyValues),
            tuple(str(Request[0]) for Request in Requests),
            int(MaximumExpansionCount),
        )).encode("utf-8")).hexdigest()[:16]
        if (
            PrepareNativeEscapeBatchOnly
            and NativeEscapeBatchResult is None
        ):
            return PlacementAccessNativeEscapeMemberPreparation(
                MemberId=NativeEscapeBatchMemberId,
                TopologyKind=TopologyKind,
                AdjacencyValues=AdjacencyValues,
                Requests=Requests,
                RequestInputs=tuple(sorted(RequestInputs.items())),
                MaximumExpansionCount=int(MaximumExpansionCount),
                PreparationFingerprint=PreparationFingerprint,
            )
        if NativeEscapeBatchResult is not None:
            if (
                NativeEscapeBatchResult.MemberId
                != NativeEscapeBatchMemberId
                or NativeEscapeBatchResult.PreparationFingerprint
                != PreparationFingerprint
            ):
                raise RuntimeError(
                    "native layered access result does not match its exact "
                    "member preparation"
                )
            return (
                NativeEscapeBatchResult.Status,
                NativeEscapeBatchResult.Requests,
                NativeEscapeBatchResult.ExpansionCount,
                NativeEscapeBatchResult.WorkCapExceeded,
                NativeEscapeBatchResult.DeadlineExceeded,
                0.0,
            )
        if _BuildDerivedEscapeStatePathsBounded is None:
            raise RuntimeError(
                "native placement access escape binding is unavailable"
            )
        NativeRemainingMilliseconds = max(
            1,
            int(
                NativeEscapeRemainingMilliseconds()
                if NativeEscapeRemainingMilliseconds is not None
                else 60_000
            ),
        )
        NativeStartedAt = monotonic()
        Result = _BuildDerivedEscapeStatePathsBounded(
            AdjacencyValues,
            Requests,
            4,
            int(MaximumExpansionCount),
            NativeRemainingMilliseconds,
        )
        return (*Result, monotonic() - NativeStartedAt)
    EffectiveAccessLength = (
        int(Technology.AccessLength)
        if AccessLength is None
        else int(AccessLength)
    )
    if EffectiveAccessLength < 1:
        raise ValueError("placement access fabric requires positive access length")
    DerivedSlotAssignment = (
        _GetDerivedPerimeterSlotAssignment(Placement)
        if TopologyKind == "derived-perimeter-access-v1"
        else None
    )
    Gates = tuple(Placed.PlacedGates)
    if not Gates:
        if Shell is not None:
            raise ValueError("derived perimeter shell requires placed gates")
        return PlacementAccessFabric(
            FabricFingerprint=sha256(b"empty-placement-access-fabric-v1").hexdigest()[:16],
            Nodes=(),
            Edges=(),
            IngressNodes=(),
            PhysicalClaims=Resources.ResourceGraph.BuildRouteClaims(()),
            CapacityResourceIds=(),
            TerminalDomains=(),
            TopologyKind=TopologyKind,
            Complete=True,
            AccessRingTrackCount=AccessRingTrackCount,
            AccessRingFingerprint=(
                sha256(repr((
                    TopologyKind,
                    AccessRingTrackCount,
                )).encode("utf-8")).hexdigest()[:16]
                if AccessRingTrackCount
                else ""
            ),
            Technology=Technology,
        )
    if (
        DerivedSlotAssignment is not None
        and (
            not bool(getattr(DerivedSlotAssignment, "Success", False))
            or not bool(getattr(DerivedSlotAssignment, "Complete", False))
        )
    ):
        AssignmentBounds = tuple(getattr(
            DerivedSlotAssignment,
            "Bounds",
            (),
        ))
        OuterBounds = (
            tuple(map(int, AssignmentBounds))
            if len(AssignmentBounds) == 4
            else None
        )
        AssignmentFingerprint = str(getattr(
            DerivedSlotAssignment,
            "AssignmentFingerprint",
            "",
        ))
        IncompleteReason = str(getattr(
            DerivedSlotAssignment,
            "IncompleteReason",
            "",
        )) or "incomplete-derived-perimeter-slot-domain"
        return PlacementAccessFabric(
            FabricFingerprint=sha256(repr((
                "incomplete-derived-perimeter-slot-fabric-v1",
                str(getattr(
                    DerivedSlotAssignment,
                    "DomainFingerprint",
                    "",
                )),
                AssignmentFingerprint,
                IncompleteReason,
            )).encode("utf-8")).hexdigest()[:16],
            Nodes=(),
            Edges=(),
            IngressNodes=(),
            PhysicalClaims=Resources.ResourceGraph.BuildRouteClaims(()),
            CapacityResourceIds=(),
            TerminalDomains=(),
            TopologyKind=TopologyKind,
            Complete=False,
            AccessRingTrackCount=AccessRingTrackCount,
            OuterBounds=OuterBounds,
            ActiveFaces=tuple(
                str(Reservation.Face)
                for Reservation in getattr(
                    DerivedSlotAssignment,
                    "FaceReservations",
                    (),
                )
            ),
            PerimeterSlotAssignmentFingerprint=AssignmentFingerprint,
            IncompleteReason=IncompleteReason,
            Technology=Technology,
        )
    if DerivedSlotAssignment is not None:
        if Shell is None:
            Shell = BuildDerivedPerimeterFabricShell(
                Placement,
                Resources=Resources,
                Technology=Technology,
                AccessRingTrackCount=AccessRingTrackCount,
                AccessLength=EffectiveAccessLength,
                BoundarySignals=BoundarySignals,
                WorkCheck=WorkCheck,
            )
        else:
            _ValidateDerivedPerimeterFabricShell(
                Shell,
                Placement,
                Resources=Resources,
                Technology=Technology,
                AccessRingTrackCount=AccessRingTrackCount,
                AccessLength=EffectiveAccessLength,
                BoundarySignals=BoundarySignals,
                Assignment=DerivedSlotAssignment,
                PerimeterFaceTrackCounts=Shell.PerimeterFaceTrackCounts,
            )
    elif Shell is not None:
        raise ValueError("derived perimeter shell requires a slot assignment")

    if Shell is not None:
        Profiles = Shell.ProfileBySignal
    else:
        Profiles = BuildNetRoutingProfiles(
            Placed,
            AccessLength=EffectiveAccessLength,
        )
        if BoundarySignals is not None:
            Profiles = {
                Signal: Profile
                for Signal, Profile in Profiles.items()
                if Signal in BoundarySignals
            }

    TrackPitch = Technology.TrackPitch
    MinimumX = min(Gate.X for Gate in Gates)
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in Gates
    )
    MinimumZ = min(Gate.Z for Gate in Gates)
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in Gates
    )
    BaseY = min(Gate.Y for Gate in Gates)
    # Legacy/no-slot perimeter construction has no frozen face ownership.
    # Keep that fact explicit before deriving its canonical four-face track
    # tuple; a later complete shell replaces it with the selected faces.
    ActiveFaces: tuple[str, ...] = ()
    if Shell is not None:
        FabricLayers = Shell.FabricLayers
        FabricYs = Shell.FabricYs
        FabricLayerCount = len(FabricLayers)
        TerminalPathByIdentity = Shell.TerminalPathByIdentity
        TerminalPaths = Shell.TerminalPaths
        PerimeterFaceTrackCountsForRouting: tuple[tuple[str, int], ...] = (
            Shell.PerimeterFaceTrackCounts
        )
    else:
        MaximumFabricLayer = max(0, int(Placement.LayerCount) - 1)
        # The certified incumbent retains its historic access-band witness.
        # New derived perimeter contracts use the selected envelope exactly.
        FabricLayerCount = (
            ResolveFixedAccessFabricLayerCount(
                Profiles,
                int(Placement.LayerCount),
            )
            if TopologyKind == "fixed-access-band-v1"
            else max(1, int(Placement.LayerCount))
        )
        FabricLayers = (
            FixedAccessFabricLayers
            if FixedAccessFabricLayers is not None
            else tuple(range(
                MaximumFabricLayer - FabricLayerCount + 1,
                MaximumFabricLayer + 1,
            ))
        )
        FabricLayerCount = len(FabricLayers)
        FabricYs = tuple(
            Technology.RoutingY(BaseY, Layer)
            for Layer in FabricLayers
        )
        TerminalPathByIdentity = {
            (str(Signal), tuple(Terminal)): tuple(Path)
            for Signal, Profile in sorted(Profiles.items())
            for Terminal, Path in (
                (Profile.Root, Profile.SourceAccessPath),
                *tuple(sorted(Profile.TargetAccessPaths.items())),
            )
        }
        TerminalPaths = tuple(
            (Signal, Terminal, TerminalPathByIdentity[(Signal, Terminal)])
            for Signal, Terminal in sorted(TerminalPathByIdentity)
        )
        if BoundaryTerminalKeys is not None:
            NormalizedBoundaryTerminalKeys = frozenset(
                (str(Signal), tuple(Terminal))
                for Signal, Terminal in BoundaryTerminalKeys
            )
            TerminalPaths = tuple(
                Value
                for Value in TerminalPaths
                if (str(Value[0]), tuple(Value[1]))
                in NormalizedBoundaryTerminalKeys
            )
            TerminalPathByIdentity = {
                (str(Signal), tuple(Terminal)): tuple(Path)
                for Signal, Terminal, Path in TerminalPaths
            }
        PerimeterFaceTrackCountsForRouting = (
            ()
            if TopologyKind == "fixed-access-band-v1"
            else tuple(
                (
                    Face,
                    AccessRingTrackCount if Face in ActiveFaces else 0,
                )
                for Face in _PerimeterFaceDirections
            )
        )
        if TopologyKind == "fixed-access-band-v1":
            PerimeterFaceTrackCountsForRouting = tuple(
                (Face, 0) for Face in _PerimeterFaceDirections
            )
    TerminalLogicalKeyByIdentity = {
        (str(Signal), tuple(Profile.Root)): f"{Signal}:root"
        for Signal, Profile in Profiles.items()
    }
    TerminalLogicalKeyByIdentity.update({
        (str(Signal), tuple(Terminal)): f"{Signal}:target-{TargetIndex}"
        for Signal, Profile in Profiles.items()
        for TargetIndex, Terminal in enumerate(Profile.Targets)
    })
    Margin = TrackPitch * (
        AccessRingTrackCount
        if IsPerimeterTopology
        else 2
    )
    RingBounds: tuple[tuple[int, int, int, int], ...] = ()
    OuterBounds: tuple[int, int, int, int] | None = None
    SlotFaceByTerminal: dict[tuple[str, Position3], str] = {}
    PerimeterDrivenRootFaceByTerminal: dict[
        tuple[str, Position3],
        str,
    ] = {}
    if DerivedSlotAssignment is not None:
        if Shell is None:
            raise RuntimeError("derived perimeter fabric did not build a shell")
        # The shell was fixed before legal-escape traversal.  Consume its
        # signal-closed face maps and exact ring geometry verbatim; building
        # the fabric below may only materialize graph nodes and escape stubs.
        RingBounds = Shell.RingBounds
        OuterBounds = Shell.OuterBounds
        ActiveFaces = Shell.ActiveFaces
        SlotFaceByTerminal = Shell.SlotFaceByTerminal
        PerimeterDrivenRootFaceByTerminal = (
            Shell.PerimeterDrivenRootFaceByTerminal
        )
        TerminalPathByIdentity = Shell.TerminalPathByIdentity
        TerminalPaths = Shell.TerminalPaths
    EffectiveLaneCount = (
        min(16, max(4, len(TerminalPaths)))
        if LaneCount is None
        else LaneCount
    )
    EffectiveMaximumEscapeStubs = (
        (
            # One ingress per perimeter face and selected ring track is the
            # finite side-choice domain.  Additional layer counts are already
            # explicit envelope alternatives in the enclosing pre-route
            # problem; multiplying identical side choices by every layer
            # repeats geometry rather than adding a new perimeter choice.
            (
                sum(
                    TrackCount
                    for Face, TrackCount in PerimeterFaceTrackCountsForRouting
                    if Face in ActiveFaces
                )
                if TopologyKind == "derived-perimeter-access-v1"
                else 4 * AccessRingTrackCount
            )
            if IsPerimeterTopology
            else min(
                max(3, ceil(4 / FabricLayerCount)),
                EffectiveLaneCount,
            ) * FabricLayerCount
        )
        if MaximumEscapeStubsPerTerminal is None
        else MaximumEscapeStubsPerTerminal
    )
    AllowedAccess = frozenset(
        Position
        for _Signal, _Terminal, Path in TerminalPaths
        for Position in Path
    )
    RegionBounds = (
        (
            OuterBounds[0],
            OuterBounds[2],
            BaseY,
            max(FabricYs),
            OuterBounds[1],
            OuterBounds[3],
        )
        if OuterBounds is not None
        else (
            MinimumX - Margin,
            MaximumX + Margin,
            BaseY,
            max(FabricYs),
            MinimumZ - Margin,
            MaximumZ + Margin,
        )
    )
    if PreparedNativeRegionAdjacencyValues is None:
        Region = Resources.ResourceGraph.BuildRegion(
            RegionBounds,
            AllowedAccess=AllowedAccess,
            WorkCheck=WorkCheck,
        )
    else:
        PreparedRegionNodes = frozenset(
            Position
            for Position, _Neighbors in (
                PreparedNativeRegionAdjacencyValues
            )
        )
        Region = RoutingGraphRegion(
            Bounds=RegionBounds,
            Nodes=PreparedRegionNodes,
            Edges=frozenset(),
        )
    if IsPerimeterTopology:
        if not RingBounds:
            RingBounds = tuple(
                (
                    MinimumX - TrackPitch * TrackIndex,
                    MaximumX + TrackPitch * TrackIndex,
                    MinimumZ - TrackPitch * TrackIndex,
                    MaximumZ + TrackPitch * TrackIndex,
                )
                for TrackIndex in range(1, AccessRingTrackCount + 1)
            )
        def RingFacesForPosition(Position: Position3) -> frozenset[str]:
            """Return every perimeter face touching one ring node.

            Corners intentionally belong to both adjacent active faces so a
            north/east or north/west contract retains exactly the one corner
            edge needed to join those two segments.
            """
            X, _Y, Z = Position
            Faces = set()
            for (
                RingMinimumX,
                RingMaximumX,
                RingMinimumZ,
                RingMaximumZ,
            ) in RingBounds:
                if RingMinimumX <= X <= RingMaximumX:
                    if Z == RingMinimumZ:
                        Faces.add("north")
                    if Z == RingMaximumZ:
                        Faces.add("south")
                if RingMinimumZ <= Z <= RingMaximumZ:
                    if X == RingMinimumX:
                        Faces.add("west")
                    if X == RingMaximumX:
                        Faces.add("east")
            return frozenset(Faces)

        # ``ActiveFaces`` is the signal-closed physical contract: selected
        # terminal-slot faces plus the exact source faces paired with those
        # slots.  ``SlotFaceByTerminal`` still preserves the individual
        # ingress side.  Interior-only signals retain ordinary authoritative
        # portals rather than allocating absent perimeter material.
        HasFrozenPerimeterAssignment = DerivedSlotAssignment is not None
        ActiveFaceSet = frozenset(ActiveFaces)
        FabricNodes = tuple(sorted(
            Position
            for Position in Region.Nodes
            if Position[1] in FabricYs
            and (
                any(
                    (
                        Position[0] in {RingMinimumX, RingMaximumX}
                        and RingMinimumZ <= Position[2] <= RingMaximumZ
                    )
                    or (
                        Position[2] in {RingMinimumZ, RingMaximumZ}
                        and RingMinimumX <= Position[0] <= RingMaximumX
                    )
                    for (
                        RingMinimumX,
                        RingMaximumX,
                        RingMinimumZ,
                        RingMaximumZ,
                    ) in RingBounds
                )
            )
            and (
                TopologyKind != "derived-perimeter-access-v1"
                or not HasFrozenPerimeterAssignment
                or bool(RingFacesForPosition(Position) & ActiveFaceSet)
            )
        ))
    else:
        LaneCoordinates = tuple(
            MinimumZ - Margin + TrackPitch * Index
            for Index in range(EffectiveLaneCount)
        )
        SpineCoordinates = tuple(range(
            MinimumX - Margin,
            MaximumX + Margin + 1,
            TrackPitch,
        ))
        MinimumLaneZ = min(LaneCoordinates)
        MaximumLaneZ = max(LaneCoordinates)
        FabricNodes = tuple(sorted(
            Position
            for Position in Region.Nodes
            if (
                Position[1] in FabricYs
                and (
                    Position[2] in LaneCoordinates
                    or (
                        Position[0] in SpineCoordinates
                        and MinimumLaneZ <= Position[2] <= MaximumLaneZ
                    )
                )
            )
        ))
    FabricNodeSet = frozenset(FabricNodes)
    FabricEdges = tuple(sorted(
        (First, Second)
        for First, Neighbors in (
            PreparedNativeRegionAdjacencyValues or ()
        )
        for Second in Neighbors
        if First < Second
        and First in FabricNodeSet
        and Second in FabricNodeSet
    )) if PreparedNativeRegionAdjacencyValues is not None else tuple(sorted(
        (First, Second)
        for First, Second in Region.Edges
        if First in FabricNodeSet and Second in FabricNodeSet
    ))
    if IsPerimeterTopology:
        RingIngressGroups: dict[
            tuple[int, int, str],
            list[Position3],
        ] = {}
        for Position in FabricNodes:
            X, Y, Z = Position
            for TrackIndex, (
                RingMinimumX,
                RingMaximumX,
                RingMinimumZ,
                RingMaximumZ,
            ) in enumerate(RingBounds, start=1):
                if (
                    TopologyKind == "derived-perimeter-access-v1"
                    and HasFrozenPerimeterAssignment
                ):
                    # A frozen face owns its complete segment, including a
                    # corner when its adjacent side is inactive.  The old
                    # ``if/elif`` classification assigned that corner only
                    # to west/east and could strand a north-facing terminal
                    # whose exact pin aligned with the core edge.
                    Faces = []
                    if (
                        Z == RingMinimumZ
                        and RingMinimumX <= X <= RingMaximumX
                    ):
                        Faces.append("north")
                    if (
                        X == RingMaximumX
                        and RingMinimumZ <= Z <= RingMaximumZ
                    ):
                        Faces.append("east")
                    if (
                        Z == RingMaximumZ
                        and RingMinimumX <= X <= RingMaximumX
                    ):
                        Faces.append("south")
                    if (
                        X == RingMinimumX
                        and RingMinimumZ <= Z <= RingMaximumZ
                    ):
                        Faces.append("west")
                    for Face in Faces:
                        if Face not in ActiveFaceSet:
                            continue
                        RingIngressGroups.setdefault(
                            (Y, TrackIndex, Face),
                            [],
                        ).append(Position)
                else:
                    # Preserve the historical legacy ring ordering exactly
                    # when no frozen perimeter contract exists.
                    Face = None
                    if Z == RingMinimumZ and RingMinimumX < X < RingMaximumX:
                        Face = "north"
                    elif X == RingMaximumX and RingMinimumZ <= Z <= RingMaximumZ:
                        Face = "east"
                    elif Z == RingMaximumZ and RingMinimumX < X < RingMaximumX:
                        Face = "south"
                    elif X == RingMinimumX and RingMinimumZ <= Z <= RingMaximumZ:
                        Face = "west"
                    if Face is not None:
                        RingIngressGroups.setdefault(
                            (Y, TrackIndex, Face),
                            [],
                        ).append(Position)
        RingIngressGroups = {
            Identity: sorted(Positions)
            for Identity, Positions in sorted(RingIngressGroups.items())
        }
        RingIngressCoordinateCatalog = {
            Identity: (
                (
                    0
                    if Identity[2] in {"north", "south"}
                    else 2
                ),
                tuple(Positions),
                tuple(
                    Position[
                        0
                        if Identity[2] in {"north", "south"}
                        else 2
                    ]
                    for Position in Positions
                ),
            )
            for Identity, Positions in RingIngressGroups.items()
        }

        def BuildNearestRingIngressRepresentatives(
            Groups: Mapping[
                tuple[int, int, str],
                Iterable[Position3],
            ],
            Starts: tuple[Position3, ...],
        ) -> dict[tuple[int, int, str], Position3]:
            """Select exact Manhattan-nearest nodes on straight segments."""
            Results = {}
            for Identity in Groups:
                Axis, Positions, Coordinates = (
                    RingIngressCoordinateCatalog[Identity]
                )
                if not Starts:
                    Results[Identity] = min(Positions)
                    continue
                CandidateIndices = set()
                for Start in Starts:
                    Index = bisect_left(Coordinates, Start[Axis])
                    if Index < len(Positions):
                        CandidateIndices.add(Index)
                    if Index > 0:
                        CandidateIndices.add(Index - 1)
                Results[Identity] = min(
                    (Positions[Index] for Index in CandidateIndices),
                    key=lambda Position: (
                        min(
                            abs(Position[0] - Start[0])
                            + abs(Position[1] - Start[1])
                            + abs(Position[2] - Start[2])
                            for Start in Starts
                        ),
                        Position,
                    ),
                )
            return Results

        IngressNodes = tuple(sorted({
            Position
            for Positions in RingIngressGroups.values()
            for Position in Positions
        }))
    else:
        IngressNodes = tuple(sorted(
            Position
            for Position in FabricNodes
            if (
                Position[2] in LaneCoordinates
                and (Position[0] - (MinimumX - Margin)) % TrackPitch == 0
            )
        ))
    RegionAdjacency: dict[Position3, tuple[Position3, ...]] | None = None
    LegalEscapeWorkBudget: _AccessFabricWorkBudget | None = None
    LegalEscapeWorkLimitKind = ""
    LegalEscapeDirectionStateUpperBound: int | None = None
    RegionNodeSet = frozenset(Region.Nodes)
    if Region.Nodes:
        if PreparedNativeRegionAdjacencyValues is not None:
            RegionAdjacency = dict(PreparedNativeRegionAdjacencyValues)
        else:
            MutableRegionAdjacency: dict[Position3, list[Position3]] = {}
            for First, Second in Region.Edges:
                MutableRegionAdjacency.setdefault(First, []).append(Second)
                MutableRegionAdjacency.setdefault(Second, []).append(First)
            RegionAdjacency = {
                Position: tuple(sorted(Values))
                for Position, Values in MutableRegionAdjacency.items()
            }
        if (
            TopologyKind == "derived-perimeter-access-v1"
            and DeriveLegalEscapeWorkLimit
        ):
            LegalEscapeDirectionStateUpperBound = (
                _DeriveLegalEscapeDirectionStateUpperBound(
                    TerminalPaths,
                    RegionNodeSet=RegionNodeSet,
                    RingIngressGroups=RingIngressGroups,
                    SlotFaceByTerminal=SlotFaceByTerminal,
                    PerimeterDrivenRootFaceByTerminal=(
                        PerimeterDrivenRootFaceByTerminal
                    ),
                    RegionAdjacency=RegionAdjacency,
                )
            )
        # An explicit test/diagnostic cap intentionally wins over the
        # derived traversal bound.  This keeps incomplete-domain fixtures
        # meaningful while production callers can bind termination directly
        # to the immutable physical state graph.
        if (
            TopologyKind == "derived-perimeter-access-v1"
            and MaximumLegalEscapeExpansions is not None
        ):
            LegalEscapeWorkLimitKind = "explicit"
            LegalEscapeWorkBudget = _AccessFabricWorkBudget(
                MaximumExpansions=int(MaximumLegalEscapeExpansions),
            )
        elif (
            DeriveLegalEscapeWorkLimit
            and LegalEscapeDirectionStateUpperBound > 0
        ):
            LegalEscapeWorkLimitKind = "derived-direction-state-v1"
            LegalEscapeWorkBudget = _AccessFabricWorkBudget(
                MaximumExpansions=LegalEscapeDirectionStateUpperBound,
            )
    BatchedDerivedEscapePaths: dict[
        tuple[str, Position3, tuple[Position3, ...]],
        tuple[tuple[tuple[Position3, ...], ...], bool],
    ] | None = None
    RestrictedAdjacencyByFacePlane: dict[
        tuple[str, int],
        dict[Position3, tuple[Position3, ...]],
    ] = {}
    RestrictedNodeMasksByFacePlane: dict[
        tuple[str, int],
        tuple[Position3, ...],
    ] = {}
    DerivedGroupRepresentativesByTerminal: dict[
        tuple[str, Position3],
        dict[tuple[int, int, str], Position3],
    ] = {}

    def RestrictedAdjacencyForFacePlane(
        Face: str,
        Start: Position3,
    ) -> tuple[
        dict[Position3, tuple[Position3, ...]],
        tuple[Position3, ...],
    ]:
        Direction = _PerimeterFaceDirections.get(Face)
        if Direction is None:
            raise ValueError("derived perimeter slot has an unknown face")
        Axis = next(
            Index for Index, Value in enumerate(Direction) if Value
        )
        Key = (Face, int(Start[Axis]))
        CachedAdjacency = RestrictedAdjacencyByFacePlane.get(Key)
        if CachedAdjacency is None:
            if RegionAdjacency is None:
                raise RuntimeError(
                    "derived perimeter restriction requires adjacency"
                )
            CachedAdjacency = (
                _RestrictDerivedPerimeterSlotEscapeAdjacency(
                    RegionAdjacency,
                    Face=Face,
                    Start=Start,
                )
            )
            RestrictedAdjacencyByFacePlane[Key] = CachedAdjacency
            RestrictedNodeMasksByFacePlane[Key] = tuple(sorted(
                CachedAdjacency
            ))
        return (
            CachedAdjacency,
            RestrictedNodeMasksByFacePlane[Key],
        )

    if (
        TopologyKind == "derived-perimeter-access-v1"
        and _BuildDerivedEscapeStatePathsBounded is not None
        and RegionAdjacency is not None
    ):
        NativeRequests: list[tuple[object, ...]] = []
        RequestInputs: dict[
            str,
            tuple[str, Position3, tuple[Position3, ...]],
        ] = {}
        for TerminalIndex, (Signal, Terminal, AccessPath) in enumerate(
            TerminalPaths
        ):
            PrefixDomain = _BuildDerivedPerimeterAccessPrefixDomain(
                AccessPath,
                RegionNodeSet=RegionNodeSet,
            )
            if not PrefixDomain:
                continue
            Starts = tuple(Prefix[-1] for Prefix in PrefixDomain)

            def BatchedIngressDistance(Position: Position3) -> int:
                return min(
                    abs(Position[0] - Start[0])
                    + abs(Position[1] - Start[1])
                    + abs(Position[2] - Start[2])
                    for Start in Starts
                )

            TerminalKey = (str(Signal), tuple(Terminal))
            SelectedFace = (
                SlotFaceByTerminal.get(TerminalKey)
                or PerimeterDrivenRootFaceByTerminal.get(TerminalKey)
            )
            EligibleGroups = {
                Identity: Positions
                for Identity, Positions in RingIngressGroups.items()
                if SelectedFace is None or Identity[2] == SelectedFace
            }
            GroupRepresentatives = (
                BuildNearestRingIngressRepresentatives(
                    EligibleGroups,
                    Starts,
                )
            )
            DerivedGroupRepresentativesByTerminal[TerminalKey] = (
                GroupRepresentatives
            )
            AnchorIngressNodes = tuple(sorted(
                GroupRepresentatives.values(),
                key=lambda Position: (
                    BatchedIngressDistance(Position),
                    Position,
                ),
            ))
            for PrefixIndex, Prefix in enumerate(PrefixDomain):
                Start = Prefix[-1]
                # The native member already owns this exact adjacency.
                # An empty mask is its typed full-graph sentinel; serializing
                # the same tens of thousands of nodes for every prefix only
                # repeats representation work.
                AllowedNodeMask: tuple[Position3, ...] = ()
                if SelectedFace is not None:
                    _AllowedAdjacency, AllowedNodeMask = (
                        RestrictedAdjacencyForFacePlane(
                            SelectedFace,
                            Start,
                        )
                    )
                RequestId = f"{TerminalIndex}:{PrefixIndex}"
                RequestKey = (str(Signal), tuple(Terminal), Prefix)
                RequestInputs[RequestId] = RequestKey
                NativeRequests.append((
                    RequestId,
                    Start,
                    AnchorIngressNodes,
                    (),
                    Prefix,
                    AllowedNodeMask,
                    True,
                ))
        if NativeRequests:
            RemainingExpansionCount = (
                max(
                    0,
                    LegalEscapeWorkBudget.MaximumExpansions
                    - LegalEscapeWorkBudget.ExpansionCount,
                )
                if LegalEscapeWorkBudget is not None
                else max(
                    1,
                    len(NativeRequests)
                    * (
                        1
                        + sum(
                            len(Value)
                            for Value in RegionAdjacency.values()
                        )
                    ),
                )
            )
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "placement-access-native-legal-escape-batch",
                    "RequestCount": len(NativeRequests),
                })
            NativeResolution = ResolveNativeEscapeRequests(
                tuple(NativeRequests),
                RequestInputs,
                RegionAdjacency,
                RemainingExpansionCount,
            )
            if isinstance(
                NativeResolution,
                PlacementAccessNativeEscapeMemberPreparation,
            ):
                NativeEscapeBatchResult = yield NativeResolution
                NativeEscapeDiagnostics.update({
                    "SharedBatchUsed": True,
                    "SharedBatchElapsedSeconds": float(
                        NativeEscapeBatchResult.SharedBatchElapsedSeconds
                    ),
                })
                NativeResolution = ResolveNativeEscapeRequests(
                    tuple(NativeRequests),
                    RequestInputs,
                    RegionAdjacency,
                    RemainingExpansionCount,
                )
            (
                NativeStatus,
                NativeResults,
                NativeExpansionCount,
                NativeWorkCapExceeded,
                NativeDeadlineExceeded,
                NativeElapsedSeconds,
            ) = NativeResolution
            NativeEscapeDiagnostics.update({
                "Used": True,
                "CallCount": (
                    0 if NativeEscapeBatchResult is not None else 1
                ),
                "ExpansionCount": int(NativeExpansionCount),
                "Complete": not (
                    NativeWorkCapExceeded or NativeDeadlineExceeded
                ),
                "ElapsedSeconds": NativeElapsedSeconds,
            })
            if LegalEscapeWorkBudget is not None:
                LegalEscapeWorkBudget.ExpansionCount += int(
                    NativeExpansionCount
                )
                LegalEscapeWorkBudget.Exhausted = bool(
                    NativeWorkCapExceeded
                )
            if NativeDeadlineExceeded and WorkCheck is not None:
                WorkCheck({
                    "Phase": (
                        "placement-access-native-legal-escape-batch-complete"
                    ),
                    "NativeStatus": str(NativeStatus),
                    "ExpansionCount": int(NativeExpansionCount),
                })
            BatchedDerivedEscapePaths = {}
            NativeResultById = {
                str(RequestId): (
                    tuple(Candidates),
                    bool(RequestComplete),
                )
                for (
                    RequestId,
                    Candidates,
                    _RequestExpansionCount,
                    RequestComplete,
                ) in NativeResults
            }
            for RequestId, RequestKey in RequestInputs.items():
                Candidates, RequestComplete = NativeResultById.get(
                    RequestId,
                    ((), False),
                )
                _Signal, _Terminal, _Prefix = RequestKey
                ReachedPaths: dict[
                    Position3, tuple[Position3, ...]
                ] = {}
                for Ingress, _PriorDirection, PathValue in Candidates:
                    Ingress = tuple(Ingress)
                    if Ingress in ReachedPaths:
                        continue
                    # Decode the immutable native path only. BuildStubs below
                    # erases loops, builds the authoritative graph claims,
                    # and applies FindSelfClaimConflicts exactly once for the
                    # terminal that consumes it. Eagerly repeating that work
                    # for the entire unselected layer portfolio dominated
                    # access-fabric materialization.
                    ReachedPaths[Ingress] = tuple(map(tuple, PathValue))
                BatchedDerivedEscapePaths[RequestKey] = (
                    tuple(
                        ReachedPaths[Ingress]
                        for Ingress in tuple(dict.fromkeys(
                            Candidate[0] for Candidate in Candidates
                        ))
                        if Ingress in ReachedPaths
                    ),
                    bool(
                        RequestComplete
                        and not NativeWorkCapExceeded
                        and not NativeDeadlineExceeded
                    ),
                )
    BatchedFixedEscapePaths: dict[
        tuple[str, Position3, tuple[Position3, ...]],
        tuple[tuple[tuple[Position3, ...], ...], bool],
    ] | None = None
    if (
        TopologyKind == "fixed-access-band-v1"
        and _BuildDerivedEscapeStatePathsBounded is not None
        and RegionAdjacency is not None
    ):
        FixedNativeRequests: list[tuple[object, ...]] = []
        FixedRequestInputs: dict[
            str,
            tuple[str, Position3, tuple[Position3, ...]],
        ] = {}
        MaximumFixedFabricY = max(FabricYs)
        FixedAllowedNodeMaskByY = {
            int(FabricY): (
                ()
                if int(FabricY) == int(MaximumFixedFabricY)
                else tuple(sorted(
                    Position
                    for Position in RegionAdjacency
                    if int(Position[1]) <= int(FabricY)
                ))
            )
            for FabricY in FabricYs
        }
        for TerminalIndex, (Signal, Terminal, AccessPath) in enumerate(
            TerminalPaths
        ):
            EscapePrefix = list(AccessPath)
            if len(AccessPath) >= 2:
                Delta = tuple(
                    AccessPath[-1][Index] - AccessPath[-2][Index]
                    for Index in range(3)
                )
                for Offset in range(1, TrackPitch + 1):
                    Extension = tuple(
                        AccessPath[-1][Index] + Delta[Index] * Offset
                        for Index in range(3)
                    )
                    if Extension not in RegionNodeSet:
                        break
                    EscapePrefix.append(Extension)
            Starts = tuple(
                Position for Position in reversed(EscapePrefix)
                if Position in RegionNodeSet
            )[:1]
            if not Starts:
                continue
            LastAccessibleIndex = max(
                Index
                for Index, Position in enumerate(EscapePrefix)
                if Position == Starts[0]
            )
            Prefix = tuple(EscapePrefix[:LastAccessibleIndex + 1])

            def FixedIngressDistance(Position: Position3) -> int:
                return (
                    abs(Position[0] - Starts[0][0])
                    + abs(Position[1] - Starts[0][1])
                    + abs(Position[2] - Starts[0][2])
                )

            RequestKey = (str(Signal), tuple(Terminal), Prefix)
            FixedLayerEscapeLimit = min(4, EffectiveLaneCount)
            for FabricY in FabricYs:
                RankedIngressNodes = tuple(sorted(
                    (
                        Position for Position in IngressNodes
                        if int(Position[1]) == int(FabricY)
                    ),
                    key=lambda Position: (
                        FixedIngressDistance(Position),
                        Position,
                    ),
                ))
                DiverseIngressNodes: list[Position3] = []
                SeenLaneCoordinates: set[tuple[int, int]] = set()
                for Ingress in RankedIngressNodes:
                    LaneIdentity = (Ingress[1], Ingress[2])
                    if LaneIdentity in SeenLaneCoordinates:
                        continue
                    SeenLaneCoordinates.add(LaneIdentity)
                    DiverseIngressNodes.append(Ingress)
                    if len(DiverseIngressNodes) >= FixedLayerEscapeLimit:
                        break
                if not DiverseIngressNodes:
                    continue
                RequestId = f"{TerminalIndex}:{FabricY}"
                FixedRequestInputs[RequestId] = RequestKey
                # The maximal graph is shared, but each layer request owns
                # the exact node mask of the independently declared fixed
                # world. This prevents an escape to a lower band from
                # borrowing nodes above that world's physical ceiling.
                AllowedNodeMask = FixedAllowedNodeMaskByY[int(FabricY)]
                FixedNativeRequests.append((
                    RequestId,
                    Starts[0],
                    tuple(DiverseIngressNodes),
                    (),
                    Prefix,
                    AllowedNodeMask,
                    # The immutable fixed-layer contract retains one exact
                    # least-cost path for each declared ingress.  The Python
                    # decoder has always consumed only that first settled
                    # path; asking Rust to flood every later direction-state
                    # only serialized candidates that were discarded before
                    # stub construction.
                    True,
                ))
        if FixedNativeRequests:
            FixedExpansionLimit = max(
                1,
                len(FixedNativeRequests)
                * (
                    1
                    + sum(
                        len(Neighbors)
                        for Neighbors in RegionAdjacency.values()
                    )
                ),
            )
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "placement-access-native-fixed-escape-batch",
                    "RequestCount": len(FixedNativeRequests),
                })
            NativeResolution = ResolveNativeEscapeRequests(
                tuple(FixedNativeRequests),
                FixedRequestInputs,
                RegionAdjacency,
                FixedExpansionLimit,
            )
            if isinstance(
                NativeResolution,
                PlacementAccessNativeEscapeMemberPreparation,
            ):
                NativeEscapeBatchResult = yield NativeResolution
                NativeEscapeDiagnostics.update({
                    "SharedBatchUsed": True,
                    "SharedBatchElapsedSeconds": float(
                        NativeEscapeBatchResult.SharedBatchElapsedSeconds
                    ),
                })
                NativeResolution = ResolveNativeEscapeRequests(
                    tuple(FixedNativeRequests),
                    FixedRequestInputs,
                    RegionAdjacency,
                    FixedExpansionLimit,
                )
            (
                _NativeStatus,
                NativeResults,
                NativeExpansionCount,
                NativeWorkCapExceeded,
                NativeDeadlineExceeded,
                NativeElapsedSeconds,
            ) = NativeResolution
            NativeEscapeDiagnostics.update({
                "Used": True,
                "CallCount": (
                    0 if NativeEscapeBatchResult is not None else 1
                ),
                "ExpansionCount": int(NativeExpansionCount),
                "Complete": not (
                    NativeWorkCapExceeded or NativeDeadlineExceeded
                ),
                "ElapsedSeconds": NativeElapsedSeconds,
            })
            NativeResultById = {
                str(RequestId): (
                    tuple(Candidates),
                    bool(RequestComplete),
                )
                for (
                    RequestId,
                    Candidates,
                    _RequestExpansionCount,
                    RequestComplete,
                ) in NativeResults
            }
            BatchedFixedEscapePaths = {}
            for RequestId, RequestKey in FixedRequestInputs.items():
                Candidates, RequestComplete = NativeResultById.get(
                    RequestId,
                    ((), False),
                )
                _Signal, _Terminal, _Prefix = RequestKey
                ReachedPaths: dict[
                    Position3, tuple[Position3, ...]
                ] = {}
                for Ingress, _PriorDirection, PathValue in Candidates:
                    Ingress = tuple(Ingress)
                    if Ingress in ReachedPaths:
                        continue
                    ReachedPaths[Ingress] = tuple(map(tuple, PathValue))
                ExistingPaths, ExistingComplete = (
                    BatchedFixedEscapePaths.get(
                        RequestKey,
                        ((), True),
                    )
                )
                BatchedFixedEscapePaths[RequestKey] = (
                    (
                        *ExistingPaths,
                        *tuple(
                        ReachedPaths[Ingress]
                        for Ingress in tuple(dict.fromkeys(
                            Candidate[0] for Candidate in Candidates
                        ))
                        if Ingress in ReachedPaths
                        ),
                    ),
                    bool(
                        ExistingComplete
                        and
                        RequestComplete
                        and not NativeWorkCapExceeded
                        and not NativeDeadlineExceeded
                    ),
                )
    NativeAccessClaimsByWireCells: dict[
        frozenset[Position3], RoutingResourceClaims
    ] = {}
    NativeDeferredSelfLegalByWireCells: dict[
        frozenset[Position3], bool
    ] = {}
    RetainedNativeAccessWireCells: frozenset[
        frozenset[Position3]
    ] = frozenset()
    if (
        Technology == DefaultRedstoneRoutingTechnology
        and (
            (
                DeferEscapeStubCapacityResourceIds
                and _BuildDeferredRouteClaimsBatchWithTelemetry is not None
            )
            or (
                not DeferEscapeStubCapacityResourceIds
                and _BuildRouteClaimsBatchWithTelemetry is not None
            )
        )
    ):
        CandidateWireCellsByTerminalIngress: dict[
            tuple[str, Position3, Position3],
            set[frozenset[Position3]],
        ] = defaultdict(set)
        for BatchedPaths in (
            BatchedDerivedEscapePaths,
            BatchedFixedEscapePaths,
        ):
            if BatchedPaths is None:
                continue
            for (Signal, Terminal, Prefix), (Paths, _Complete) in (
                BatchedPaths.items()
            ):
                for Path in Paths:
                    StubPath = _ErasePlacementAccessPathLoops((
                        *Prefix,
                        *Path[1:],
                    ))
                    if not StubPath:
                        continue
                    CandidateWireCellsByTerminalIngress[
                        (str(Signal), tuple(Terminal), tuple(Path[-1]))
                    ].add(frozenset(StubPath))
        CandidateWireCells: set[frozenset[Position3]] = set()
        for WireCellValues in (
            CandidateWireCellsByTerminalIngress.values()
        ):
            NonDominatedWireCells: list[frozenset[Position3]] = []
            for WireCells in sorted(
                WireCellValues,
                key=lambda Value: (len(Value), repr(Value)),
            ):
                if any(
                    Existing <= WireCells
                    for Existing in NonDominatedWireCells
                ):
                    NativeEscapeDiagnostics["DominatedEscapeStubCount"] = (
                        int(NativeEscapeDiagnostics[
                            "DominatedEscapeStubCount"
                        ]) + 1
                    )
                    continue
                NonDominatedWireCells.append(WireCells)
                CandidateWireCells.add(WireCells)
        OrderedCandidateWireCells = tuple(sorted(
            CandidateWireCells,
            key=repr,
        ))
        RetainedNativeAccessWireCells = frozenset(
            OrderedCandidateWireCells
        )
        if len(OrderedCandidateWireCells) > 1:
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "placement-access-native-claim-batch",
                    "ClaimBatchWorkItems": len(
                        OrderedCandidateWireCells
                    ),
                })
            ClaimBatchStartedAt = monotonic()
            if DeferEscapeStubCapacityResourceIds:
                NativeClaimValues, ActiveWorkerCount = (
                    _BuildDeferredRouteClaimsBatchWithTelemetry(
                        [tuple(sorted(Values)) for Values in (
                            OrderedCandidateWireCells
                        )],
                    )
                )
            else:
                NativeClaimValues, ActiveWorkerCount = (
                    _BuildRouteClaimsBatchWithTelemetry(
                    [tuple(sorted(Values)) for Values in (
                        OrderedCandidateWireCells
                    )],
                    tuple(sorted(Resources.ResourceGraph.ActualBlocks)),
                    tuple(sorted(Resources.ResourceGraph.SolidBlocks)),
                    )
                )
            NativeEscapeDiagnostics.update({
                "ClaimBatchWorkItems": len(OrderedCandidateWireCells),
                "ClaimBatchWorkerCount": int(ActiveWorkerCount),
                "ClaimBatchElapsedSeconds": (
                    monotonic() - ClaimBatchStartedAt
                ),
            })
            if DeferEscapeStubCapacityResourceIds:
                for WireCells, (Air, SelfLegal) in zip(
                    OrderedCandidateWireCells,
                    NativeClaimValues,
                    strict=True,
                ):
                    NativeAccessClaimsByWireCells[WireCells] = (
                        RoutingResourceClaims(
                            WireCells=WireCells,
                            RequiredAirCells=frozenset(Air),
                        )
                    )
                    NativeDeferredSelfLegalByWireCells[WireCells] = bool(
                        SelfLegal
                    )
            else:
                for WireCells, (
                    Wire,
                    Support,
                    Air,
                    Electrical,
                ) in zip(
                    OrderedCandidateWireCells,
                    NativeClaimValues,
                    strict=True,
                ):
                    NativeAccessClaimsByWireCells[WireCells] = (
                        RoutingResourceClaims(
                            WireCells=frozenset(Wire),
                            SupportCells=frozenset(Support),
                            RequiredAirCells=frozenset(Air),
                            ElectricalCells=frozenset(Electrical),
                        )
                    )
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": (
                        "placement-access-native-claim-batch-complete"
                    ),
                    "ClaimBatchWorkItems": len(
                        OrderedCandidateWireCells
                    ),
                    "ClaimBatchWorkerCount": int(ActiveWorkerCount),
                    "ClaimBatchElapsedSeconds": round(
                        monotonic() - ClaimBatchStartedAt,
                        6,
                    ),
                })
    TerminalDomains = []
    for TerminalIndex, (Signal, Terminal, AccessPath) in enumerate(
        TerminalPaths
    ):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "placement-access-terminal-domain",
                "CompletedTerminalCount": TerminalIndex,
                "TerminalCount": len(TerminalPaths),
                "Signal": Signal,
                "NativeEscapeKernelUsed": bool(
                    NativeEscapeDiagnostics["Used"]
                ),
                "NativeEscapeKernelExpansionCount": int(
                    NativeEscapeDiagnostics["ExpansionCount"]
                ),
                "NativeEscapeKernelElapsedSeconds": round(
                    float(NativeEscapeDiagnostics["ElapsedSeconds"]),
                    6,
                ),
                "NativeClaimBatchWorkItems": int(
                    NativeEscapeDiagnostics["ClaimBatchWorkItems"]
                ),
                "NativeClaimBatchWorkerCount": int(
                    NativeEscapeDiagnostics["ClaimBatchWorkerCount"]
                ),
                "NativeClaimBatchElapsedSeconds": round(
                    float(
                        NativeEscapeDiagnostics[
                            "ClaimBatchElapsedSeconds"
                        ]
                    ),
                    6,
                ),
            })
        if (
            LegalEscapeWorkBudget is not None
            and LegalEscapeWorkBudget.Exhausted
        ):
            TerminalDomains.append(PlacementAccessTerminalDomain(
                Signal=Signal,
                Terminal=Terminal,
                EscapeStubs=(),
                Complete=False,
                IncompleteReason="legal-escape-work-cap",
                LogicalKey=TerminalLogicalKeyByIdentity.get(
                    (str(Signal), tuple(Terminal)),
                    f"{Signal}:terminal-{TerminalIndex}",
                ),
            ))
            continue
        EscapePrefix = list(AccessPath)
        if (
            len(AccessPath) >= 2
            and not IsPerimeterTopology
        ):
            Delta = tuple(
                AccessPath[-1][Index] - AccessPath[-2][Index]
                for Index in range(3)
            )
            for Offset in range(1, TrackPitch + 1):
                Extension = tuple(
                    AccessPath[-1][Index] + Delta[Index] * Offset
                    for Index in range(3)
                )
                if Extension not in RegionNodeSet:
                    break
                EscapePrefix.append(Extension)
        DerivedPrefixDomain: tuple[tuple[Position3, ...], ...] = ()
        if TopologyKind == "derived-perimeter-access-v1":
            # A derived terminal can hand off at any of its fixed macro
            # access landings which lie in the declared resource region.
            # Keep the complete finite prefix domain here, before the one
            # capacity solve, rather than treating a failed farthest landing
            # as a reason to generate another placement or routing attempt.
            DerivedPrefixDomain = _BuildDerivedPerimeterAccessPrefixDomain(
                tuple(EscapePrefix),
                RegionNodeSet=RegionNodeSet,
            )
            Starts = tuple(
                Prefix[-1] for Prefix in DerivedPrefixDomain
            )
            if DerivedPrefixDomain:
                # Retain the farthest prefix only for common ranking and
                # diagnostics below.  Each member remains present in
                # ``EscapePrefixDomain`` and is searched explicitly once.
                EscapePrefix = list(DerivedPrefixDomain[0])
        else:
            Starts = tuple(
                Position for Position in reversed(EscapePrefix)
                if Position in RegionNodeSet
            )[:1]
            if Starts:
                # A technology access path can legitimately extend beyond
                # the selected perimeter plane.  The immutable access fabric
                # owns only nodes in its declared resource region; retaining
                # the off-fabric tail in ``FixedPrefix`` would force a path
                # to leave the ring and then turn back into it, which is a
                # false self-claim conflict rather than a physical escape
                # constraint.
                #
                # Keep the maximal prefix ending at the farthest accessible
                # resource node.  This is a geometry-derived boundary
                # handoff, not a shortened electrical access rule: the
                # omitted suffix is outside the selected fixed interface
                # contract and cannot be a portal, stub, or later routing
                # alternative.
                LastAccessibleIndex = max(
                    Index
                    for Index, Position in enumerate(EscapePrefix)
                    if Position == Starts[0]
                )
                EscapePrefix = EscapePrefix[:LastAccessibleIndex + 1]
        EscapePrefixDomain = (
            DerivedPrefixDomain
            if TopologyKind == "derived-perimeter-access-v1"
            else (tuple(EscapePrefix),) if Starts else ()
        )
        def IngressDistance(Position: Position3) -> int:
            return (
                min(
                    abs(Position[0] - Start[0])
                    + abs(Position[1] - Start[1])
                    + abs(Position[2] - Start[2])
                    for Start in Starts
                )
                if Starts
                else 1 << 30
            )

        if IsPerimeterTopology:
            TerminalKey = (str(Signal), tuple(Terminal))
            SlotFace = SlotFaceByTerminal.get(TerminalKey)
            RootFace = PerimeterDrivenRootFaceByTerminal.get(TerminalKey)
            SelectedFace = SlotFace or RootFace
            IsFrozenSlotTerminal = SlotFace is not None
            EligibleRingIngressGroups = (
                {
                    Identity: Positions
                    for Identity, Positions in RingIngressGroups.items()
                    if Identity[2] == SelectedFace
                }
                if SelectedFace is not None
                else RingIngressGroups
            )
            GroupRepresentatives = (
                DerivedGroupRepresentativesByTerminal.get(TerminalKey)
                or BuildNearestRingIngressRepresentatives(
                    EligibleRingIngressGroups,
                    tuple(Starts),
                )
            )
            if IsFrozenSlotTerminal:
                # A selected I/O slot owns one face, but distinct lateral
                # ingress claims on that face can be capacity-incompatible.
                # Keep its finite physical face segment in the pre-route
                # domain; this is geometry construction, not a later repair.
                RankedIngressNodes = tuple(sorted(
                    (
                        GroupRepresentatives.values()
                        if RestrictDerivedIngressToRepresentatives
                        else (
                            Position
                            for Positions in (
                                EligibleRingIngressGroups.values()
                            )
                            for Position in Positions
                        )
                    ),
                    key=lambda Position: (
                        IngressDistance(Position),
                        Position,
                    ),
                ))
            else:
                # A perimeter-driven root owns its exact source-access face,
                # while an otherwise unconstrained interior endpoint retains
                # every physical face.  Either way, one nearest ingress per
                # concrete (layer, ring-track, face) creates a bounded domain
                # without turning placement into a second detailed router.
                RankedIngressNodes = tuple(sorted(
                    GroupRepresentatives.values(),
                    key=lambda Position: (
                        IngressDistance(Position),
                        Position,
                    ),
                ))
        else:
            RankedIngressNodes = tuple(sorted(
                IngressNodes,
                key=lambda Position: (IngressDistance(Position), Position),
            ))
        DiverseIngressNodes = []
        SeenLaneCoordinates = set()
        for Ingress in RankedIngressNodes:
            LaneIdentity = (
                Ingress
                if IsPerimeterTopology
                else (Ingress[1], Ingress[2])
            )
            if LaneIdentity in SeenLaneCoordinates:
                continue
            SeenLaneCoordinates.add(LaneIdentity)
            DiverseIngressNodes.append(Ingress)
            if (
                TopologyKind != "derived-perimeter-access-v1"
                and len(DiverseIngressNodes) >= EffectiveMaximumEscapeStubs
            ):
                break
        DerivedLegalSearchComplete = True
        PathMembers: tuple[
            tuple[tuple[Position3, ...], tuple[Position3, ...]],
            ...,
        ]
        if Starts and TopologyKind == "derived-perimeter-access-v1":
            # A slot's lateral alternatives lie on one already-materialized
            # face segment.  First prove one normal escape per concrete
            # layer/track/face group, then extend it across that fixed
            # segment below.  This preserves every lateral ingress while
            # avoiding a full direction-state traversal merely to rediscover
            # the same normal anchor for each position on the segment.  A
            # macro may expose more than one in-region landing along its
            # fixed access path; every physically legal member of that fixed
            # canonical set is an option in the one capacity problem, not a
            # failed-route fallback.
            AnchorIngressNodes = tuple(sorted(
                GroupRepresentatives.values(),
                key=lambda Position: (
                    IngressDistance(Position),
                    Position,
                ),
            ))
            MutablePathMembers: list[
                tuple[tuple[Position3, ...], tuple[Position3, ...]],
            ] = []
            for Prefix in EscapePrefixDomain:
                Start = Prefix[-1]
                BatchedResult = (
                    BatchedDerivedEscapePaths.get((
                        str(Signal),
                        tuple(Terminal),
                        Prefix,
                    ))
                    if BatchedDerivedEscapePaths is not None
                    else None
                )
                if BatchedResult is not None:
                    AnchorPaths, PrefixSearchComplete = BatchedResult
                else:
                    EscapeAdjacency = RegionAdjacency
                    if (
                        SelectedFace is not None
                        and EscapeAdjacency is not None
                    ):
                        # Both a frozen I/O slot and the paired source
                        # endpoint carry an exact outward normal. Reuse the
                        # immutable face/plane restriction prepared for the
                        # native batch; the fallback consumes the same graph.
                        EscapeAdjacency, _AllowedNodeMask = (
                            RestrictedAdjacencyForFacePlane(
                                SelectedFace,
                                Start,
                            )
                        )
                    AnchorPaths, PrefixSearchComplete = (
                        _BuildBoundedLegalDerivedEscapePaths(
                            Start,
                            AnchorIngressNodes,
                            Region.Edges,
                            Prefix,
                            Resources.ResourceGraph,
                            WorkBudget=LegalEscapeWorkBudget,
                            WorkCheck=WorkCheck,
                            Adjacency=EscapeAdjacency,
                            RemainingMilliseconds=(
                                NativeEscapeRemainingMilliseconds
                            ),
                            NativeDiagnostics=NativeEscapeDiagnostics,
                        )
                    )
                DerivedLegalSearchComplete = (
                    DerivedLegalSearchComplete and PrefixSearchComplete
                )
                if (
                    IsFrozenSlotTerminal
                    and not RestrictDerivedIngressToRepresentatives
                ):
                    PathByAnchor = {
                        Path[-1]: Path
                        for Path in AnchorPaths
                    }
                    MutablePathMembers.extend(
                        (Prefix, (*AnchorPath, *SegmentPath[1:]))
                        for Identity, Ingresses in sorted(
                            EligibleRingIngressGroups.items()
                        )
                        for AnchorPath in (
                            PathByAnchor.get(
                                GroupRepresentatives[Identity]
                            ),
                        )
                        if AnchorPath is not None
                        for SegmentPath in _BuildFabricIngressSegmentPaths(
                            GroupRepresentatives[Identity],
                            Ingresses,
                            FabricEdges,
                        )
                    )
                else:
                    MutablePathMembers.extend(
                        (Prefix, Path) for Path in AnchorPaths
                    )
                if not PrefixSearchComplete:
                    # The shared immutable work budget is exhausted.  The
                    # remaining fixed members stay unmaterialized and the
                    # entire terminal domain is explicitly incomplete.
                    break
            PathMembers = tuple(MutablePathMembers)
        elif Starts and IsPerimeterTopology:
            PathMembers = tuple(
                (tuple(EscapePrefix), Path)
                for Path in _BuildIndependentShortestFabricEscapePaths(
                    Starts[0],
                    DiverseIngressNodes,
                    Region.Edges,
                    AlternateIngresses=frozenset(DiverseIngressNodes),
                )
            )
        elif Starts:
            BatchedResult = (
                BatchedFixedEscapePaths.get((
                    str(Signal),
                    tuple(Terminal),
                    tuple(EscapePrefix),
                ))
                if BatchedFixedEscapePaths is not None
                else None
            )
            if BatchedResult is not None:
                EscapePaths, DerivedLegalSearchComplete = BatchedResult
            elif FabricLayerCount == 1:
                EscapePaths = _BuildSharedLegalFabricEscapePaths(
                    Starts[0],
                    DiverseIngressNodes,
                    Region.Edges,
                    tuple(EscapePrefix),
                    Resources.ResourceGraph,
                )
            else:
                EscapePaths, DerivedLegalSearchComplete = (
                    _BuildBoundedLegalDerivedEscapePaths(
                        Starts[0],
                        DiverseIngressNodes,
                        Region.Edges,
                        tuple(EscapePrefix),
                        Resources.ResourceGraph,
                        WorkBudget=LegalEscapeWorkBudget,
                        WorkCheck=WorkCheck,
                        Adjacency=RegionAdjacency,
                        RemainingMilliseconds=(
                            NativeEscapeRemainingMilliseconds
                        ),
                        NativeDiagnostics=NativeEscapeDiagnostics,
                    )
                )
            PathMembers = tuple(
                (tuple(EscapePrefix), Path)
                for Path in EscapePaths
            )
        else:
            PathMembers = ()
        def BuildStubs(
            CandidatePaths: Iterable[
                tuple[tuple[Position3, ...], tuple[Position3, ...]],
            ],
        ) -> list[PlacementAccessEscapeStub]:
            Results = []
            SeenStubPaths = set()
            PathRecords = []
            for Prefix, Path in CandidatePaths:
                StubPath = _ErasePlacementAccessPathLoops((
                    *Prefix,
                    *Path[1:],
                ))
                if not StubPath or StubPath in SeenStubPaths:
                    continue
                WireCells = frozenset(StubPath)
                if (
                    RetainedNativeAccessWireCells
                    and WireCells not in RetainedNativeAccessWireCells
                ):
                    continue
                SeenStubPaths.add(StubPath)
                PathRecords.append((
                    tuple(Path[-1]),
                    WireCells,
                    StubPath,
                ))
            OrderedPathRecords = sorted(
                PathRecords,
                key=lambda Value: (
                    Value[0],
                    len(Value[1]),
                    Value[2],
                ),
            )
            if RetainedNativeAccessWireCells:
                NonDominatedPathRecords = OrderedPathRecords
            else:
                NonDominatedPathRecords = []
                NonDominatedPathRecordsByIngress: dict[
                    Position3,
                    list[tuple[
                        Position3,
                        frozenset[Position3],
                        tuple[Position3, ...],
                    ]],
                ] = {}
                for Ingress, WireCells, StubPath in OrderedPathRecords:
                    if any(
                        ExistingWireCells <= WireCells
                        for (
                            _ExistingIngress,
                            ExistingWireCells,
                            _ExistingPath,
                        ) in NonDominatedPathRecordsByIngress.get(
                            Ingress,
                            (),
                        )
                    ):
                        continue
                    Record = (Ingress, WireCells, StubPath)
                    NonDominatedPathRecords.append(Record)
                    NonDominatedPathRecordsByIngress.setdefault(
                        Ingress,
                        [],
                    ).append(Record)
            for Ingress, WireCells, StubPath in NonDominatedPathRecords:
                ClaimsFingerprint = ""
                ClaimsDeferred = False
                Claims = NativeAccessClaimsByWireCells.get(WireCells)
                if DeferEscapeStubCapacityResourceIds:
                    if Claims is None:
                        (
                            Claims,
                            SelfLegal,
                            ClaimsFingerprint,
                        ) = _BuildDeferredPlacementAccessPathClaims(
                            StubPath,
                            Technology,
                        )
                    else:
                        SelfLegal = (
                            NativeDeferredSelfLegalByWireCells.get(
                                WireCells,
                                False,
                            )
                        )
                        ClaimsFingerprint = (
                            _BuildDeferredPlacementAccessClaimsFingerprint(
                                WireCells,
                                Claims.RequiredAirCells,
                                Technology,
                            )
                        )
                    ClaimsDeferred = True
                else:
                    if Claims is None:
                        Claims = Resources.ResourceGraph.BuildRouteClaims(
                            StubPath
                        )
                    SelfLegal = not FindSelfClaimConflicts({Signal: Claims})
                if not SelfLegal:
                    continue
                Results.append(PlacementAccessEscapeStub(
                    Terminal=Terminal,
                    Ingress=Ingress,
                    Path=StubPath,
                    PhysicalClaims=Claims,
                    CapacityResourceIds=() if (
                        DeferEscapeStubCapacityResourceIds
                    ) else tuple(
                        RoutingResourceId(Kind, Position)
                        for Kind, Positions in (
                            (
                                RoutingResourceKind.Air,
                                Claims.RequiredAirCells,
                            ),
                            (
                                RoutingResourceKind.Electrical,
                                Claims.ElectricalCells,
                            ),
                            (
                                RoutingResourceKind.Support,
                                Claims.SupportCells,
                            ),
                            (
                                RoutingResourceKind.Wire,
                                Claims.WireCells,
                            ),
                        )
                        for Position in sorted(Positions)
                    ),
                    Complete=True,
                    PhysicalClaimsFingerprint=ClaimsFingerprint,
                    PhysicalClaimsDeferred=ClaimsDeferred,
                ))
            if TopologyKind == "derived-perimeter-access-v1":
                if (
                    DeferEscapeStubCapacityResourceIds
                    and not RetainedNativeAccessWireCells
                ):
                    # These values own the same terminal variable and the
                    # same physical ingress. A value whose complete claim
                    # vocabulary is a superset of an already-retained value
                    # cannot satisfy any capacity or named-contract witness
                    # that the retained value cannot also satisfy. This is
                    # exact physical-domain dominance, not objective-, layer-
                    # or circuit-based pruning.
                    Results.sort(key=lambda Stub: (
                        sum((
                            len(Stub.PhysicalClaims.WireCells),
                            len(Stub.PhysicalClaims.SupportCells),
                            len(Stub.PhysicalClaims.RequiredAirCells),
                            len(Stub.PhysicalClaims.ElectricalCells),
                        )),
                        len(Stub.Path),
                        Stub.Ingress,
                        Stub.Path,
                    ))
                    NonDominatedResults: list[
                        PlacementAccessEscapeStub
                    ] = []
                    NonDominatedResultsByIngress: dict[
                        Position3,
                        list[PlacementAccessEscapeStub],
                    ] = {}
                    for Stub in Results:
                        Claims = Stub.PhysicalClaims
                        Dominated = any(
                            Existing.PhysicalClaims.WireCells
                            <= Claims.WireCells
                            and Existing.PhysicalClaims.SupportCells
                            <= Claims.SupportCells
                            and Existing.PhysicalClaims.RequiredAirCells
                            <= Claims.RequiredAirCells
                            and Existing.PhysicalClaims.ElectricalCells
                            <= Claims.ElectricalCells
                            for Existing in (
                                NonDominatedResultsByIngress.get(
                                    Stub.Ingress,
                                    (),
                                )
                            )
                        )
                        if Dominated:
                            NativeEscapeDiagnostics[
                                "DominatedEscapeStubCount"
                            ] = int(
                                NativeEscapeDiagnostics[
                                    "DominatedEscapeStubCount"
                                ]
                            ) + 1
                            continue
                        NonDominatedResults.append(Stub)
                        NonDominatedResultsByIngress.setdefault(
                            Stub.Ingress,
                            [],
                        ).append(Stub)
                    Results = NonDominatedResults
                # The terminal capacity solver preserves option order.  Put
                # smaller realized material first so its one fixed solve has
                # the same deterministic compactness objective as the
                # enclosing pre-route selector.
                Results.sort(key=lambda Stub: (
                    (
                        sum((
                            len(Stub.PhysicalClaims.WireCells),
                            len(Stub.PhysicalClaims.SupportCells),
                            len(Stub.PhysicalClaims.RequiredAirCells),
                            len(Stub.PhysicalClaims.ElectricalCells),
                        ))
                        if DeferEscapeStubCapacityResourceIds
                        else len(Stub.CapacityResourceIds)
                    ),
                    len(Stub.Path),
                    Stub.Path,
                    Stub.Ingress,
                ))
            return Results

        Stubs = BuildStubs(PathMembers)
        if (
            not Stubs
            and Starts
            and IsPerimeterTopology
            and TopologyKind != "derived-perimeter-access-v1"
        ):
            # The fast path search intentionally ignores electrical
            # self-exclusion.
            # Complete an empty terminal domain with the bounded legal search
            # before the one capacity solve; this does not change geometry or
            # schedule an alternative domain after failure.
            Stubs = BuildStubs(
                (tuple(EscapePrefix), Path)
                for Path in _BuildShortestLegalFabricEscapePaths(
                    Starts[0],
                    DiverseIngressNodes,
                    Region.Edges,
                    tuple(EscapePrefix),
                    Resources.ResourceGraph,
                    Adjacency=RegionAdjacency,
                )
            )
        Stubs = tuple(Stubs)
        TerminalDomains.append(PlacementAccessTerminalDomain(
            Signal=Signal,
            Terminal=Terminal,
            EscapeStubs=Stubs,
            Complete=bool(Stubs) and DerivedLegalSearchComplete,
            IncompleteReason=(
                ""
                if Stubs and DerivedLegalSearchComplete
                else "legal-escape-work-cap"
                if not DerivedLegalSearchComplete
                else "no-legal-fabric-escape"
            ),
            LogicalKey=TerminalLogicalKeyByIdentity.get(
                (str(Signal), tuple(Terminal)),
                f"{Signal}:terminal-{TerminalIndex}",
            ),
        ))
    PhysicalClaims = Resources.ResourceGraph.BuildRouteClaims(FabricNodes)
    Complete = all(Domain.Complete for Domain in TerminalDomains)
    IncompleteReason = (
        "legal-escape-work-cap"
        if LegalEscapeWorkBudget is not None
        and LegalEscapeWorkBudget.Exhausted
        else next(
            (
                Domain.IncompleteReason
                for Domain in TerminalDomains
                if not Domain.Complete and Domain.IncompleteReason
            ),
            "",
        )
    )
    PerimeterFaceTrackCountsForFingerprint = (
        PerimeterFaceTrackCountsForRouting
        if TopologyKind == "derived-perimeter-access-v1"
        else ()
    )
    FabricGraphFingerprint = sha256(repr((
        "placement-access-fabric-graph-v1",
        getattr(Technology, "TechnologyVersion", ""),
        FabricLayers,
        FabricNodes,
        FabricEdges,
    )).encode("utf-8")).hexdigest()[:16]
    CanonicalIdentity = (
        TopologyKind,
        AccessRingTrackCount,
        OuterBounds,
        ActiveFaces,
        str(getattr(
            DerivedSlotAssignment,
            "AssignmentFingerprint",
            "",
        )),
        getattr(Technology, "TechnologyVersion", ""),
        repr(Technology),
        FabricLayers,
        FabricGraphFingerprint,
        PerimeterFaceTrackCountsForFingerprint,
        tuple(
            (
                Domain.Signal,
                Domain.Terminal,
                tuple(
                    # Claims are a deterministic function of this exact path,
                    # the resource graph and the technology identities that
                    # already precede the terminal domains in this canonical
                    # value. Repeating four large claim sets per stub made
                    # fingerprinting quadratic in representation size.
                    (
                        Stub.Ingress,
                        Stub.ChoiceId,
                        Stub.PhysicalClaimsFingerprint,
                    )
                    for Stub in Domain.EscapeStubs
                ),
                Domain.Complete,
            )
            for Domain in TerminalDomains
        ),
        Complete,
    )
    AccessRingFingerprint = (
        sha256(repr((
            TopologyKind,
            AccessRingTrackCount,
            OuterBounds,
            ActiveFaces,
            PerimeterFaceTrackCountsForFingerprint,
            str(getattr(
                DerivedSlotAssignment,
                "AssignmentFingerprint",
                "",
            )),
            FabricGraphFingerprint,
        )).encode("utf-8")).hexdigest()[:16]
        if IsPerimeterTopology
        else ""
    )
    def FrozenProfileAccessPaths(Profile: Any) -> tuple[
        tuple[Position3, ...], ...
    ]:
        TargetPaths = Profile.TargetAccessPaths
        TargetPathValues = (
            tuple(TargetPaths.values())
            if hasattr(TargetPaths, "values")
            else tuple(Path for _Terminal, Path in TargetPaths)
        )
        return (tuple(Profile.SourceAccessPath), *TargetPathValues)

    FrozenAccessPositions = tuple(
        {
            *FabricNodes,
            *(
                Position
                for Profile in Profiles.values()
                for Path in FrozenProfileAccessPaths(Profile)
                for Position in Path
            ),
            *(
                Position
                for Domain in TerminalDomains
                for Stub in Domain.EscapeStubs
                for Position in Stub.Path
            ),
        }
    )
    FrozenMinimumY = min(
        BaseY,
        *(Position[1] for Position in FrozenAccessPositions),
    )
    FrozenMaximumY = max(
        max(FabricYs) + 1,
        max(
            (Position[1] + 1 for Position in FrozenAccessPositions),
            default=BaseY,
        ),
    ) + 1
    FrozenRoutingEnvelope = (
        FrozenPerFaceRoutingEnvelope(
            RoutingRegionBounds=OuterBounds,
            # This first migration freezes the current conservative canvas
            # before routing rather than shrinking it after a capacity or
            # route result.  The next template-selection increment can make
            # the per-face counts physically asymmetric without reopening
            # detailed-routing geometry.
            CanvasBounds=(
                OuterBounds[0] - Margin,
                OuterBounds[1] - Margin,
                OuterBounds[2] + Margin,
                OuterBounds[3] + Margin,
            ),
            YBounds=(FrozenMinimumY, FrozenMaximumY),
            PermittedLayers=FabricLayers,
            PerimeterFaceTrackCounts=(
                PerimeterFaceTrackCountsForRouting
            ),
            EnvelopeFingerprint=sha256(repr((
                "frozen-per-face-routing-envelope-v1",
                OuterBounds,
                Margin,
                FrozenMinimumY,
                FrozenMaximumY,
                FabricLayers,
                ActiveFaces,
                PerimeterFaceTrackCountsForRouting,
            )).encode("utf-8")).hexdigest()[:16],
        )
        if IsPerimeterTopology and OuterBounds is not None and FabricLayers
        else None
    )
    return PlacementAccessFabric(
        FabricFingerprint=sha256(repr(CanonicalIdentity).encode("utf-8")).hexdigest()[:16],
        Nodes=FabricNodes,
        Edges=FabricEdges,
        IngressNodes=IngressNodes,
        PhysicalClaims=PhysicalClaims,
        # The compact catalog consumes exact claim categories by reference.
        # Expanding the same fabric-wide categories into thousands of typed
        # resource-id objects for every unselected member is representation
        # work; selected handoff reconstructs the authoritative resource IDs
        # from the frozen claims. Ordinary callers retain the eager contract.
        CapacityResourceIds=(
            ()
            if DeferEscapeStubCapacityResourceIds
            else tuple(sorted(PhysicalClaims.ResourceIds, key=str))
        ),
        TerminalDomains=tuple(TerminalDomains),
        TopologyKind=TopologyKind,
        Complete=Complete,
        AccessRingTrackCount=AccessRingTrackCount,
        AccessRingFingerprint=AccessRingFingerprint,
        OuterBounds=OuterBounds,
        ActiveFaces=ActiveFaces,
        PerimeterSlotAssignmentFingerprint=str(getattr(
            DerivedSlotAssignment,
            "AssignmentFingerprint",
            "",
        )),
        FrozenRoutingEnvelope=FrozenRoutingEnvelope,
        LegalEscapeExpansionCount=(
            LegalEscapeWorkBudget.ExpansionCount
            if LegalEscapeWorkBudget is not None
            else 0
        ),
        LegalEscapeExpansionLimit=(
            LegalEscapeWorkBudget.MaximumExpansions
            if LegalEscapeWorkBudget is not None
            else None
        ),
        LegalEscapeWorkLimitKind=LegalEscapeWorkLimitKind,
        LegalEscapeDirectionStateUpperBound=(
            LegalEscapeDirectionStateUpperBound
        ),
        NativeEscapeKernelUsed=bool(
            NativeEscapeDiagnostics["Used"]
        ),
        NativeEscapeKernelCallCount=int(
            NativeEscapeDiagnostics["CallCount"]
        ),
        NativeEscapeKernelExpansionCount=int(
            NativeEscapeDiagnostics["ExpansionCount"]
        ),
        NativeEscapeKernelComplete=bool(
            NativeEscapeDiagnostics["Complete"]
        ),
        NativeEscapeKernelElapsedSeconds=round(
            float(NativeEscapeDiagnostics["ElapsedSeconds"]),
            6,
        ),
        NativeEscapeSharedBatchUsed=bool(
            NativeEscapeDiagnostics["SharedBatchUsed"]
        ),
        NativeEscapeSharedBatchElapsedSeconds=round(
            float(NativeEscapeDiagnostics["SharedBatchElapsedSeconds"]),
            6,
        ),
        NativeEscapeFallbackUsed=bool(
            NativeEscapeDiagnostics["FallbackUsed"]
        ),
        NativeClaimBatchWorkItems=int(
            NativeEscapeDiagnostics["ClaimBatchWorkItems"]
        ),
        NativeClaimBatchWorkerCount=int(
            NativeEscapeDiagnostics["ClaimBatchWorkerCount"]
        ),
        NativeClaimBatchElapsedSeconds=round(
            float(NativeEscapeDiagnostics["ClaimBatchElapsedSeconds"]),
            6,
        ),
        DominatedEscapeStubCount=int(
            NativeEscapeDiagnostics["DominatedEscapeStubCount"]
        ),
        IncompleteReason=("" if Complete else IncompleteReason),
        Technology=Technology,
    )


def BuildPlacementAccessFabric(
    Placement: Any,
    **Arguments: Any,
) -> PlacementAccessFabric:
    """Build directly, or suspend once for the shared native catalog."""
    Continuation = _BuildPlacementAccessFabricGenerator(
        Placement,
        **Arguments,
    )
    try:
        Preparation = next(Continuation)
    except StopIteration as Completed:
        Fabric = Completed.value
        if not isinstance(Fabric, PlacementAccessFabric):
            raise RuntimeError(
                "placement access builder returned an invalid fabric"
            )
        return Fabric
    if not isinstance(
        Preparation,
        PlacementAccessNativeEscapeMemberPreparation,
    ):
        raise RuntimeError(
            "placement access builder yielded an invalid native request"
        )
    raise PlacementAccessNativeEscapePreparationRequested(
        Preparation,
        Continuation,
    )


def AttachPlacementAccessFabric(
    Placement: Any,
    Fabric: PlacementAccessFabric,
) -> Any:
    """Attach one immutable fabric to both placement stage boundaries."""
    AttachedPlaced = (
        replace(Placement.Placed, PlacementAccessFabric=Fabric)
        if is_dataclass(Placement.Placed)
        else SimpleNamespace(**{
            **vars(Placement.Placed),
            "PlacementAccessFabric": Fabric,
        })
    )
    return (
        replace(
            Placement,
            Placed=AttachedPlaced,
            PlacementAccessFabric=Fabric,
        )
        if is_dataclass(Placement)
        else SimpleNamespace(**{
            **vars(Placement),
            "Placed": AttachedPlaced,
            "PlacementAccessFabric": Fabric,
        })
    )


def _MergePlacementAccessClaims(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> RoutingResourceClaims:
    return RoutingResourceClaims(
        WireCells=First.WireCells | Second.WireCells,
        SupportCells=First.SupportCells | Second.SupportCells,
        RequiredAirCells=(
            First.RequiredAirCells | Second.RequiredAirCells
        ),
        ElectricalCells=First.ElectricalCells | Second.ElectricalCells,
    )


def _PlacementAccessClaimsConflict(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> bool:
    return bool(
        (First.WireCells & Second.ElectricalCells)
        or (Second.WireCells & First.ElectricalCells)
        or (
            First.SupportCells
            & (Second.WireCells | Second.RequiredAirCells)
        )
        or (
            Second.SupportCells
            & (First.WireCells | First.RequiredAirCells)
        )
        or (First.RequiredAirCells & Second.WireCells)
        or (Second.RequiredAirCells & First.WireCells)
    )


@dataclass(frozen=True)
class _ImmutableStubClaimMask:
    """One immutable stub claim encoded as position bit sets.

    Derived perimeter access factors select only frozen terminal stubs.  Their
    claim-conflict relation is entirely pairwise: every illegal union is a
    wire/air/support/electrical intersection between either one stub or two
    stubs.  Representing the four position sets as integers makes that
    relation cheap to compile once without dropping any legal stub option.
    """

    WireMask: int
    SupportMask: int
    RequiredAirMask: int
    ElectricalMask: int


@dataclass(frozen=True)
class _ImmutableStubCapacityFactor:
    """Precompiled exact compatibility relation for one frozen fabric."""

    ValidOptionMasks: tuple[int, ...]
    ConflictMasksByDomainOption: tuple[
        tuple[tuple[int, ...], ...], ...
    ]


def _ImmutableStubClaimMaskHasSelfConflict(
    Claims: _ImmutableStubClaimMask,
) -> bool:
    """Match ``FindSelfClaimConflicts`` for one bit-encoded claim union."""
    return bool(
        (Claims.RequiredAirMask & Claims.WireMask)
        or (
            Claims.SupportMask
            & (Claims.WireMask | Claims.RequiredAirMask)
        )
    )


def _MergeImmutableStubClaimMasks(
    First: _ImmutableStubClaimMask,
    Second: _ImmutableStubClaimMask,
) -> _ImmutableStubClaimMask:
    """Return the exact union of two immutable stub claim masks."""
    return _ImmutableStubClaimMask(
        WireMask=First.WireMask | Second.WireMask,
        SupportMask=First.SupportMask | Second.SupportMask,
        RequiredAirMask=(
            First.RequiredAirMask | Second.RequiredAirMask
        ),
        ElectricalMask=First.ElectricalMask | Second.ElectricalMask,
    )


def _ImmutableStubClaimMasksConflict(
    First: _ImmutableStubClaimMask,
    Second: _ImmutableStubClaimMask,
) -> bool:
    """Match ``_PlacementAccessClaimsConflict`` for encoded claims."""
    return bool(
        (First.WireMask & Second.ElectricalMask)
        or (Second.WireMask & First.ElectricalMask)
        or (
            First.SupportMask
            & (Second.WireMask | Second.RequiredAirMask)
        )
        or (
            Second.SupportMask
            & (First.WireMask | First.RequiredAirMask)
        )
        or (First.RequiredAirMask & Second.WireMask)
        or (Second.RequiredAirMask & First.WireMask)
    )


def _BuildImmutableStubCapacityFactor(
    Fabric: PlacementAccessFabric,
    *,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> _ImmutableStubCapacityFactor:
    """Compile every frozen-stub compatibility relation once.

    The normal generic solver merges frozensets every time its MRV search
    revisits a partial state.  This factor instead records the exact option
    conflicts as bit masks.  Same-signal pairs are checked against the
    self-conflict predicate for their union; different-signal pairs use the
    ordinary placement-claim conflict predicate.  Since both predicates are
    pairwise set intersections, pairwise validity is equivalent to validity
    of the complete selected union.
    """
    Domains = Fabric.TerminalDomains
    Positions = tuple(sorted({
        Position
        for Domain in Domains
        for Stub in Domain.EscapeStubs
        for Position in (
            *Stub.PhysicalClaims.WireCells,
            *Stub.PhysicalClaims.SupportCells,
            *Stub.PhysicalClaims.RequiredAirCells,
            *Stub.PhysicalClaims.ElectricalCells,
        )
    }))
    PositionIndex = {
        Position: Index
        for Index, Position in enumerate(Positions)
    }

    def BuildMask(ClaimPositions: Iterable[Position3]) -> int:
        Mask = 0
        for Position in ClaimPositions:
            Mask |= 1 << PositionIndex[Position]
        return Mask

    ClaimMasksByDomain = tuple(
        tuple(
            _ImmutableStubClaimMask(
                WireMask=BuildMask(Stub.PhysicalClaims.WireCells),
                SupportMask=BuildMask(Stub.PhysicalClaims.SupportCells),
                RequiredAirMask=BuildMask(
                    Stub.PhysicalClaims.RequiredAirCells
                ),
                ElectricalMask=BuildMask(
                    Stub.PhysicalClaims.ElectricalCells
                ),
            )
            for Stub in Domain.EscapeStubs
        )
        for Domain in Domains
    )
    ValidOptionMasks = tuple(
        sum(
            1 << OptionIndex
            for OptionIndex, Claims in enumerate(DomainMasks)
            if not _ImmutableStubClaimMaskHasSelfConflict(Claims)
        )
        for DomainMasks in ClaimMasksByDomain
    )
    DomainCount = len(Domains)
    MutableConflictMasks: list[list[list[int]]] = [
        [
            [0 for _ in range(DomainCount)]
            for _Option in DomainMasks
        ]
        for DomainMasks in ClaimMasksByDomain
    ]
    TotalPairCount = sum(
        len(ClaimMasksByDomain[FirstDomainIndex])
        * len(ClaimMasksByDomain[SecondDomainIndex])
        for FirstDomainIndex in range(DomainCount)
        for SecondDomainIndex in range(
            FirstDomainIndex + 1,
            DomainCount,
        )
    )
    CompletedPairCount = 0
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "placement-access-immutable-stub-factor",
            "CompletedPairCount": CompletedPairCount,
            "PairCount": TotalPairCount,
            "TerminalCount": DomainCount,
        })
    for FirstDomainIndex in range(DomainCount):
        FirstSignal = Domains[FirstDomainIndex].Signal
        for SecondDomainIndex in range(
            FirstDomainIndex + 1,
            DomainCount,
        ):
            SameSignal = FirstSignal == Domains[SecondDomainIndex].Signal
            for FirstOptionIndex, FirstClaims in enumerate(
                ClaimMasksByDomain[FirstDomainIndex]
            ):
                for SecondOptionIndex, SecondClaims in enumerate(
                    ClaimMasksByDomain[SecondDomainIndex]
                ):
                    CompletedPairCount += 1
                    if (
                        WorkCheck is not None
                        and CompletedPairCount % 256 == 0
                    ):
                        WorkCheck({
                            "Phase": (
                                "placement-access-immutable-stub-factor"
                            ),
                            "CompletedPairCount": CompletedPairCount,
                            "PairCount": TotalPairCount,
                            "TerminalCount": DomainCount,
                        })
                    Conflict = (
                        _ImmutableStubClaimMaskHasSelfConflict(
                            _MergeImmutableStubClaimMasks(
                                FirstClaims,
                                SecondClaims,
                            )
                        )
                        if SameSignal
                        else _ImmutableStubClaimMasksConflict(
                            FirstClaims,
                            SecondClaims,
                        )
                    )
                    if not Conflict:
                        continue
                    MutableConflictMasks[FirstDomainIndex][
                        FirstOptionIndex
                    ][SecondDomainIndex] |= 1 << SecondOptionIndex
                    MutableConflictMasks[SecondDomainIndex][
                        SecondOptionIndex
                    ][FirstDomainIndex] |= 1 << FirstOptionIndex
    return _ImmutableStubCapacityFactor(
        ValidOptionMasks=ValidOptionMasks,
        ConflictMasksByDomainOption=tuple(
            tuple(
                tuple(ConflictMasks)
                for ConflictMasks in DomainConflictMasks
            )
            for DomainConflictMasks in MutableConflictMasks
        ),
    )


def _CanUseImmutableStubCapacityFactor(
    Fabric: PlacementAccessFabric,
    *,
    AssignmentValidator: Callable[[PlacementAccessAssignment], bool] | None,
    RequiredCompleteSignalRoutes: frozenset[str],
    OptionalLocalRouteClaims: tuple[Any, ...],
    RequireCompleteSignalRoutes: bool | None,
) -> bool:
    """Limit the fast factor to terminal-only derived perimeter contracts."""
    return bool(
        Fabric.TopologyKind == "derived-perimeter-access-v1"
        and AssignmentValidator is None
        and not RequiredCompleteSignalRoutes
        and not OptionalLocalRouteClaims
        and RequireCompleteSignalRoutes is False
    )


def _SolveImmutableStubCapacityFactor(
    Fabric: PlacementAccessFabric,
    *,
    MaximumExpansions: int,
    WorkCheck: Callable[[dict[str, object]], None] | None,
) -> PlacementAccessAssignment:
    """Solve one terminal-only immutable-stub factor with bit propagation.

    This has the same MRV order and one-expansion-per-selected-stub contract
    as ``SolvePlacementAccessFabricCapacity``.  It changes only how the
    already-fixed option compatibility is evaluated.
    """
    Factor = _BuildImmutableStubCapacityFactor(
        Fabric,
        WorkCheck=WorkCheck,
    )
    Domains = Fabric.TerminalDomains
    Selected: dict[int, int] = {}
    ExpansionCount = 0
    Exhausted = False
    ConflictSignals: set[str] = set()
    FirstUnroutableSignal = ""

    def CompatibleOptionMask(
        DomainIndex: int,
        *,
        RecordConflicts: bool,
    ) -> int:
        Domain = Domains[DomainIndex]
        ValidMask = Factor.ValidOptionMasks[DomainIndex]
        Mask = ValidMask
        if RecordConflicts and (
            ValidMask != (1 << len(Domain.EscapeStubs)) - 1
        ):
            # The generic path records a signal as soon as one of its own
            # options has an electrical self-conflict, even when a sibling
            # option remains usable.  Preserve that diagnostic behavior from
            # the precompiled validity mask without re-merging claim sets.
            ConflictSignals.add(Domain.Signal)
        for SelectedDomainIndex, SelectedOptionIndex in Selected.items():
            BlockingMask = Factor.ConflictMasksByDomainOption[
                SelectedDomainIndex
            ][SelectedOptionIndex][DomainIndex]
            if RecordConflicts and ValidMask & BlockingMask:
                SelectedSignal = Domains[SelectedDomainIndex].Signal
                if SelectedSignal == Domain.Signal:
                    # Same-signal conflicts are self-conflicts of the merged
                    # claim union, so the generic solver reports only this
                    # signal rather than an inter-signal conflict pair.
                    ConflictSignals.add(Domain.Signal)
                else:
                    ConflictSignals.update((Domain.Signal, SelectedSignal))
            Mask &= ~BlockingMask
        return Mask & ValidMask

    def RecordEmptyDomainConflict(DomainIndex: int) -> None:
        """Retain a small exact signal core when propagation empties a domain."""
        Domain = Domains[DomainIndex]
        ConflictSignals.add(Domain.Signal)
        InitialMask = Factor.ValidOptionMasks[DomainIndex]
        for SelectedDomainIndex, SelectedOptionIndex in Selected.items():
            if Domains[SelectedDomainIndex].Signal == Domain.Signal:
                continue
            BlockingMask = (
                Factor.ConflictMasksByDomainOption[SelectedDomainIndex]
                [SelectedOptionIndex][DomainIndex]
            )
            if InitialMask & BlockingMask:
                ConflictSignals.add(Domains[SelectedDomainIndex].Signal)

    def IterateOptionIndices(Mask: int) -> Iterable[int]:
        while Mask:
            LeastSignificantBit = Mask & -Mask
            yield LeastSignificantBit.bit_length() - 1
            Mask ^= LeastSignificantBit

    def Search() -> bool:
        nonlocal ExpansionCount, Exhausted, FirstUnroutableSignal
        if WorkCheck is not None and ExpansionCount % 256 == 0:
            WorkCheck({
                "Phase": "placement-access-capacity-search",
                "ExpansionCount": ExpansionCount,
                "MaximumExpansions": MaximumExpansions,
                "SelectedTerminalCount": len(Selected),
                "TerminalCount": len(Domains),
                "SelectedLocalRouteCount": 0,
                "OptionalLocalRouteCount": 0,
            })
        if len(Selected) == len(Domains):
            return True
        SelectedSignals = {
            Domains[DomainIndex].Signal
            for DomainIndex in Selected
        }
        RankedDomains: list[tuple[
            int,
            int,
            str,
            Position3,
            int,
            int,
        ]] = []
        for DomainIndex, Domain in enumerate(Domains):
            if DomainIndex in Selected:
                continue
            CompatibleMask = CompatibleOptionMask(
                DomainIndex,
                RecordConflicts=True,
            )
            if not CompatibleMask:
                if not FirstUnroutableSignal:
                    FirstUnroutableSignal = Domain.Signal
                RecordEmptyDomainConflict(DomainIndex)
                return False
            RankedDomains.append((
                0 if Domain.Signal in SelectedSignals else 1,
                CompatibleMask.bit_count(),
                Domain.Signal,
                Domain.Terminal,
                DomainIndex,
                CompatibleMask,
            ))
        (
            _PartiallySelectedRank,
            _CompatibleCount,
            _Signal,
            _Terminal,
            DomainIndex,
            CompatibleMask,
        ) = min(RankedDomains)
        for OptionIndex in IterateOptionIndices(CompatibleMask):
            if ExpansionCount >= MaximumExpansions:
                Exhausted = True
                return False
            ExpansionCount += 1
            Selected[DomainIndex] = OptionIndex
            if Search():
                return True
            Selected.pop(DomainIndex, None)
        return False

    Success = Search()
    SelectedValues = tuple(
        (
            Domains[DomainIndex].Signal,
            Domains[DomainIndex].Terminal,
            Selected[DomainIndex],
        )
        for DomainIndex in sorted(Selected)
    ) if Success else ()
    ClaimsBySignal: dict[str, RoutingResourceClaims] = {}
    if Success:
        for DomainIndex in sorted(Selected):
            Domain = Domains[DomainIndex]
            Stub = Domain.EscapeStubs[Selected[DomainIndex]]
            ClaimsBySignal[Domain.Signal] = _MergePlacementAccessClaims(
                ClaimsBySignal.get(
                    Domain.Signal,
                    RoutingResourceClaims(),
                ),
                Stub.PhysicalClaims,
            )
    CapacityResourceIds = tuple(sorted({
        Resource
        for Claims in ClaimsBySignal.values()
        for Resource in Claims.ResourceIds
    }, key=str)) if Success else ()
    AssignmentFingerprint = (
        sha256(repr((
            Fabric.FabricFingerprint,
            SelectedValues,
            (),
            (),
            CapacityResourceIds,
        )).encode("utf-8")).hexdigest()[:16]
        if Success
        else ""
    )
    return PlacementAccessAssignment(
        FabricFingerprint=Fabric.FabricFingerprint,
        AssignmentFingerprint=AssignmentFingerprint,
        SelectedStubIndices=SelectedValues,
        CapacityResourceIds=CapacityResourceIds,
        ExpansionCount=ExpansionCount,
        Success=Success,
        Complete=not Exhausted,
        ConflictSignals=(
            () if Success else tuple(sorted(ConflictSignals))
        ),
        FirstUnroutableSignal=(
            "" if Success else FirstUnroutableSignal
        ),
        IncompleteReason=(
            "work-cap-exhausted" if Exhausted else ""
        ),
    )


def SolvePlacementAccessFabricCapacity(
    Fabric: PlacementAccessFabric,
    *,
    MaximumExpansions: int = 50_000,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    AssignmentValidator: (
        Callable[[PlacementAccessAssignment], bool] | None
    ) = None,
    RequiredCompleteSignalRoutes: frozenset[str] = frozenset(),
    OptionalLocalRouteClaims: tuple[Any, ...] = (),
    RequireCompleteSignalRoutes: bool | None = None,
) -> PlacementAccessAssignment:
    """Select one immutable local-access contract in one bounded solve.

    A complete placement-local claim is an alternative to fabric escapes for
    its signal, not an obstacle selected before the problem is known.  The
    bounded search therefore chooses to retain or release every supplied
    claim alongside terminal escapes.  An optional validator folds a
    downstream exact-capacity factor into this same search.  A rejected leaf
    is backtracked inside this invocation; it is not a second capacity solve
    or a post-route retry.
    """
    if MaximumExpansions < 1:
        raise ValueError("placement access capacity requires a work cap")
    if not Fabric.Complete:
        return PlacementAccessAssignment(
            FabricFingerprint=Fabric.FabricFingerprint,
            AssignmentFingerprint="",
            SelectedStubIndices=(),
            CapacityResourceIds=(),
            ExpansionCount=0,
            Success=False,
            Complete=False,
            IncompleteReason=Fabric.IncompleteReason,
        )
    if _CanUseImmutableStubCapacityFactor(
        Fabric,
        AssignmentValidator=AssignmentValidator,
        RequiredCompleteSignalRoutes=RequiredCompleteSignalRoutes,
        OptionalLocalRouteClaims=OptionalLocalRouteClaims,
        RequireCompleteSignalRoutes=RequireCompleteSignalRoutes,
    ):
        return _SolveImmutableStubCapacityFactor(
            Fabric,
            MaximumExpansions=MaximumExpansions,
            WorkCheck=WorkCheck,
        )
    Selected: dict[int, int] = {}
    ClaimsBySignal: dict[str, RoutingResourceClaims] = {}
    SelectedSignalRoutes: dict[str, tuple[Position3, ...]] = {}
    ExpansionCount = 0
    Exhausted = False
    ConflictSignals: set[str] = set()
    MaximumRoutedSignalCount = 0
    FrontierSignals: tuple[str, ...] = ()
    FirstUnroutableSignal = ""
    RejectedCompleteAssignmentCount = 0
    IncompleteRouteDomain = False
    FirstIncompleteRouteSignal = ""
    FabricNodeSet = frozenset(Fabric.Nodes)
    FabricEdgeSet = frozenset(
        tuple(sorted((First, Second))) for First, Second in Fabric.Edges
    )
    EffectiveTechnology = (
        Fabric.Technology or DefaultRedstoneRoutingTechnology
    )
    FabricYValues = tuple(sorted({Position[1] for Position in Fabric.Nodes}))
    FabricZValuesByY = {
        FabricY: tuple(sorted({
            Position[2]
            for Position in Fabric.Nodes
            if Position[1] == FabricY
        }))
        for FabricY in FabricYValues
    }
    TrunkCoordinatesByY = {
        FabricY: tuple(sorted(
            X
            for X in {
                Position[0]
                for Position in Fabric.Nodes
                if Position[1] == FabricY
            }
            if all(
                (X, FabricY, Z) in FabricNodeSet
                for Z in FabricZValuesByY[FabricY]
            )
        ))
        for FabricY in FabricYValues
    }
    LaneCoordinatesByY = {
        FabricY: tuple(sorted(
            Z
            for Z in {
                Position[2]
                for Position in Fabric.Nodes
                if Position[1] == FabricY
            }
            if all(
                (X, FabricY, Z) in FabricNodeSet
                for X in {
                    Position[0]
                    for Position in Fabric.Nodes
                    if Position[1] == FabricY
                }
            )
        ))
        for FabricY in FabricYValues
    }
    TerminalDomainCountBySignal: dict[str, int] = {}
    for Domain in Fabric.TerminalDomains:
        TerminalDomainCountBySignal[Domain.Signal] = (
            TerminalDomainCountBySignal.get(Domain.Signal, 0) + 1
        )
    LocalClaimBySignal: dict[str, Any] = {}
    for Claim in OptionalLocalRouteClaims:
        Signal = str(getattr(Claim, "Signal", ""))
        Claims = getattr(Claim, "Claims", None)
        if not Signal or Claims is None:
            raise ValueError(
                "optional local-route claims require signal and claims"
            )
        if Signal not in TerminalDomainCountBySignal:
            # A claim unrelated to this fabric cannot establish a terminal
            # contract, so keeping it would make the factor depend on hidden
            # geometry outside its published domain.
            continue
        if Signal in LocalClaimBySignal:
            raise ValueError(
                "optional local-route claims must be unique per signal"
            )
        LocalClaimBySignal[Signal] = Claim
    LocalClaimChoice: dict[str, bool] = {}
    SelectedLocalRouteSignals: set[str] = set()
    # The default preserves the complete ring-tree contract used by focused
    # access-fabric callers.  The compact placement flow may instead freeze
    # terminal access here and carry one authoritative track-preparation
    # witness as its complete tree proof.  That proof is still built before
    # the sole route attempt; it simply avoids treating a perimeter ring as a
    # second, oversized detailed router.
    RequireAllCompleteSignalRoutes = (
        True
        if RequireCompleteSignalRoutes is None
        else bool(RequireCompleteSignalRoutes)
    )

    def SignalRequiresCompleteRoute(Signal: str) -> bool:
        return (
            RequireAllCompleteSignalRoutes
            or Signal in RequiredCompleteSignalRoutes
        )

    def BuildCurrentAssignment() -> PlacementAccessAssignment:
        SelectedValues = tuple(
            (
                Fabric.TerminalDomains[Index].Signal,
                Fabric.TerminalDomains[Index].Terminal,
                Selected[Index],
            )
            for Index in sorted(Selected)
        )
        CapacityResourceIds = tuple(sorted({
            Resource
            for Claims in ClaimsBySignal.values()
            for Resource in Claims.ResourceIds
        }, key=str))
        AssignmentFingerprint = sha256(repr((
            Fabric.FabricFingerprint,
            SelectedValues,
            tuple(sorted(SelectedLocalRouteSignals)),
            tuple(sorted(SelectedSignalRoutes.items())),
            CapacityResourceIds,
        )).encode("utf-8")).hexdigest()[:16]
        return PlacementAccessAssignment(
            FabricFingerprint=Fabric.FabricFingerprint,
            AssignmentFingerprint=AssignmentFingerprint,
            SelectedStubIndices=SelectedValues,
            CapacityResourceIds=CapacityResourceIds,
            ExpansionCount=ExpansionCount,
            Success=True,
            Complete=True,
            SignalRoutes=tuple(sorted(SelectedSignalRoutes.items())),
            SelectedLocalRouteSignals=tuple(
                sorted(SelectedLocalRouteSignals)
            ),
        )

    def BuildSignalRouteCandidates(
        Signal: str,
        Ingresses: tuple[Position3, ...],
    ) -> tuple[tuple[tuple[Position3, ...], RoutingResourceClaims], ...]:
        nonlocal IncompleteRouteDomain, FirstIncompleteRouteSignal
        if len(Ingresses) <= 1:
            Nodes = tuple(Ingresses)
            return ((Nodes, RoutingResourceClaims()),)
        IngressLayers = {Position[1] for Position in Ingresses}
        if len(IngressLayers) != 1:
            return ()
        FabricY = next(iter(IngressLayers))
        MinimumZ = min(Position[2] for Position in Ingresses)
        MaximumZ = max(Position[2] for Position in Ingresses)
        MinimumX = min(Position[0] for Position in Ingresses)
        MaximumX = max(Position[0] for Position in Ingresses)
        Results = []
        SeenNodeSets: set[frozenset[Position3]] = set()

        def RetainRouteNodes(Nodes: set[Position3]) -> None:
            NodeSet = frozenset(Nodes)
            if NodeSet in SeenNodeSets or not NodeSet <= FabricNodeSet:
                return
            if any(
                tuple(sorted((First, Second))) not in FabricEdgeSet
                for First in NodeSet
                for Second in (
                    (First[0] + 1, First[1], First[2]),
                    (First[0], First[1], First[2] + 1),
                )
                if Second in NodeSet
            ):
                return
            OrderedNodes = tuple(sorted(NodeSet))
            Claims = RoutingResourceClaims(
                WireCells=NodeSet,
                SupportCells=frozenset(
                    (X, Y - 1, Z) for X, Y, Z in NodeSet
                ),
                RequiredAirCells=frozenset(),
                ElectricalCells=frozenset(
                    Position
                    for Node in NodeSet
                    for Position in (
                        Node,
                        *EffectiveTechnology.NeighborPositions(Node),
                    )
                ),
            )
            if FindSelfClaimConflicts({Signal: Claims}):
                return
            SeenNodeSets.add(NodeSet)
            Results.append((OrderedNodes, Claims))

        if Fabric.TopologyKind == "derived-perimeter-access-v1":
            CycleNodeSets = _BuildDerivedPerimeterCycleRouteNodeSets(
                Ingresses,
                FabricY,
                Fabric.Edges,
            )
            if CycleNodeSets is None:
                IncompleteRouteDomain = True
                if not FirstIncompleteRouteSignal:
                    FirstIncompleteRouteSignal = Signal
                return ()
            for Nodes in CycleNodeSets:
                RetainRouteNodes(set(Nodes))
            return tuple(Results)

        for TrunkX in TrunkCoordinatesByY.get(FabricY, ()):
            Nodes = {
                (TrunkX, FabricY, Z)
                for Z in range(MinimumZ, MaximumZ + 1)
            }
            for IngressX, _IngressY, IngressZ in Ingresses:
                Nodes.update(
                    (X, FabricY, IngressZ)
                    for X in range(
                        min(IngressX, TrunkX),
                        max(IngressX, TrunkX) + 1,
                    )
                )
            RetainRouteNodes(Nodes)
        for TrunkZ in LaneCoordinatesByY.get(FabricY, ()):
            Nodes = {
                (X, FabricY, TrunkZ)
                for X in range(MinimumX, MaximumX + 1)
            }
            for IngressX, _IngressY, IngressZ in Ingresses:
                Nodes.update(
                    (IngressX, FabricY, Z)
                    for Z in range(
                        min(IngressZ, TrunkZ),
                        max(IngressZ, TrunkZ) + 1,
                    )
                )
            RetainRouteNodes(Nodes)
        return tuple(Results)

    def SelectCompleteSignalRoutes() -> bool:
        nonlocal ExpansionCount, Exhausted, SelectedSignalRoutes
        IngressesBySignal: dict[str, list[Position3]] = {}
        for DomainIndex, StubIndex in Selected.items():
            Domain = Fabric.TerminalDomains[DomainIndex]
            IngressesBySignal.setdefault(Domain.Signal, []).append(
                Domain.EscapeStubs[StubIndex].Ingress
            )
        RouteDomains = {
            Signal: BuildSignalRouteCandidates(
                Signal,
                tuple(sorted(set(Ingresses))),
            )
            for Signal, Ingresses in IngressesBySignal.items()
        }
        if any(not Values for Values in RouteDomains.values()):
            ConflictSignals.update(
                Signal for Signal, Values in RouteDomains.items() if not Values
            )
            return False
        RouteClaimsBySignal: dict[str, RoutingResourceClaims] = {}
        RouteNodesBySignal: dict[str, tuple[Position3, ...]] = {}

        def SelectRoute() -> bool:
            nonlocal ExpansionCount, Exhausted, SelectedSignalRoutes
            if len(RouteNodesBySignal) == len(RouteDomains):
                SelectedSignalRoutes = dict(RouteNodesBySignal)
                return True
            Ranked = []
            for Signal, Candidates in RouteDomains.items():
                if Signal in RouteNodesBySignal:
                    continue
                Compatible = []
                for Nodes, RouteClaims in Candidates:
                    CombinedClaims = _MergePlacementAccessClaims(
                        ClaimsBySignal[Signal],
                        RouteClaims,
                    )
                    if FindSelfClaimConflicts({Signal: CombinedClaims}):
                        continue
                    if any(
                        OtherSignal != Signal
                        and _PlacementAccessClaimsConflict(
                            CombinedClaims,
                            _MergePlacementAccessClaims(
                                ClaimsBySignal[OtherSignal],
                                RouteClaimsBySignal.get(
                                    OtherSignal,
                                    RoutingResourceClaims(),
                                ),
                            ),
                        )
                        for OtherSignal in ClaimsBySignal
                    ):
                        continue
                    Compatible.append((Nodes, RouteClaims))
                if not Compatible:
                    ConflictSignals.add(Signal)
                    return False
                Ranked.append((len(Compatible), Signal, Compatible))
            _Count, Signal, Compatible = min(Ranked)
            for Nodes, RouteClaims in Compatible:
                if ExpansionCount >= MaximumExpansions:
                    Exhausted = True
                    return False
                ExpansionCount += 1
                RouteNodesBySignal[Signal] = Nodes
                RouteClaimsBySignal[Signal] = RouteClaims
                if SelectRoute():
                    return True
                RouteNodesBySignal.pop(Signal, None)
                RouteClaimsBySignal.pop(Signal, None)
            return False

        return SelectRoute()

    def CompatibleStubs(
        DomainIndex: int,
    ) -> tuple[tuple[int, RoutingResourceClaims], ...]:
        Domain = Fabric.TerminalDomains[DomainIndex]
        ExistingSignalClaims = ClaimsBySignal.get(
            Domain.Signal,
            RoutingResourceClaims(),
        )
        Compatible = []
        for StubIndex, Stub in enumerate(Domain.EscapeStubs):
            MergedClaims = _MergePlacementAccessClaims(
                ExistingSignalClaims,
                Stub.PhysicalClaims,
            )
            # A terminal domain can contribute more than one escape to the
            # same fanout signal.  Checking each stub in isolation is not
            # sufficient: their union must be electrically self-consistent
            # before it becomes a frozen portal tuple for the authoritative
            # planner.
            if FindSelfClaimConflicts({Domain.Signal: MergedClaims}):
                ConflictSignals.add(Domain.Signal)
                continue
            BlockingSignals = tuple(
                Signal
                for Signal, Claims in ClaimsBySignal.items()
                if (
                    Signal != Domain.Signal
                    and _PlacementAccessClaimsConflict(
                        MergedClaims,
                        Claims,
                    )
                )
            )
            if BlockingSignals:
                ConflictSignals.update((Domain.Signal, *BlockingSignals))
                continue
            Compatible.append((StubIndex, MergedClaims))
        return tuple(Compatible)

    def Search() -> bool:
        nonlocal ExpansionCount, Exhausted
        nonlocal MaximumRoutedSignalCount, FrontierSignals
        nonlocal FirstUnroutableSignal
        nonlocal RejectedCompleteAssignmentCount
        if len(SelectedSignalRoutes) > MaximumRoutedSignalCount:
            MaximumRoutedSignalCount = len(SelectedSignalRoutes)
            FrontierSignals = tuple(sorted(SelectedSignalRoutes))
        if WorkCheck is not None and ExpansionCount % 256 == 0:
            WorkCheck({
                "Phase": "placement-access-capacity-search",
                "ExpansionCount": ExpansionCount,
                "MaximumExpansions": MaximumExpansions,
                "SelectedTerminalCount": len(Selected),
                "TerminalCount": len(Fabric.TerminalDomains),
                "SelectedLocalRouteCount": len(SelectedLocalRouteSignals),
                "OptionalLocalRouteCount": len(LocalClaimBySignal),
            })
        PendingLocalClaimSignals = tuple(sorted(
            (
                Signal
                for Signal in LocalClaimBySignal
                if Signal not in LocalClaimChoice
            ),
            key=lambda Signal: (
                -len(LocalClaimBySignal[Signal].Claims.ResourceIds),
                Signal,
            ),
        ))
        if PendingLocalClaimSignals:
            Signal = PendingLocalClaimSignals[0]
            Claim = LocalClaimBySignal[Signal]
            for KeepClaim in (True, False):
                if ExpansionCount >= MaximumExpansions:
                    Exhausted = True
                    return False
                ExpansionCount += 1
                if KeepClaim:
                    ClaimValues = Claim.Claims
                    if FindSelfClaimConflicts({Signal: ClaimValues}):
                        ConflictSignals.add(Signal)
                        continue
                    BlockingSignals = tuple(
                        OtherSignal
                        for OtherSignal, OtherClaims in ClaimsBySignal.items()
                        if (
                            OtherSignal != Signal
                            and _PlacementAccessClaimsConflict(
                                ClaimValues,
                                OtherClaims,
                            )
                        )
                    )
                    if BlockingSignals:
                        ConflictSignals.update((Signal, *BlockingSignals))
                        continue
                    ClaimsBySignal[Signal] = ClaimValues
                    LocalClaimChoice[Signal] = True
                    SelectedLocalRouteSignals.add(Signal)
                    if Search():
                        return True
                    SelectedLocalRouteSignals.remove(Signal)
                    LocalClaimChoice.pop(Signal, None)
                    ClaimsBySignal.pop(Signal, None)
                    continue
                LocalClaimChoice[Signal] = False
                if Search():
                    return True
                LocalClaimChoice.pop(Signal, None)
            return False
        AllTerminalDomainsResolved = all(
            DomainIndex in Selected
            or LocalClaimChoice.get(Domain.Signal) is True
            for DomainIndex, Domain in enumerate(Fabric.TerminalDomains)
        )
        if AllTerminalDomainsResolved:
            LocallyComplete = (
                all(
                    not SignalRequiresCompleteRoute(Signal)
                    or LocalClaimChoice.get(Signal) is True
                    or Signal in SelectedSignalRoutes
                    for Signal in TerminalDomainCountBySignal
                )
            )
            if not LocallyComplete:
                return False
            if AssignmentValidator is None:
                return True
            if AssignmentValidator(BuildCurrentAssignment()):
                return True
            RejectedCompleteAssignmentCount += 1
            return False
        RankedDomains = []
        for DomainIndex, Domain in enumerate(Fabric.TerminalDomains):
            if (
                DomainIndex in Selected
                or LocalClaimChoice.get(Domain.Signal) is True
            ):
                continue
            Compatible = CompatibleStubs(DomainIndex)
            if not Compatible:
                if not FirstUnroutableSignal:
                    FirstUnroutableSignal = Domain.Signal
                ConflictSignals.add(Domain.Signal)
                return False
            RankedDomains.append((
                0 if Domain.Signal in ClaimsBySignal else 1,
                len(Compatible),
                Domain.Signal,
                Domain.Terminal,
                DomainIndex,
                Compatible,
            ))
        (
            _PartiallySelectedRank,
            _CompatibleCount,
            _Signal,
            _Terminal,
            DomainIndex,
            Compatible,
        ) = min(RankedDomains)
        Domain = Fabric.TerminalDomains[DomainIndex]
        ExistingSignalClaims = ClaimsBySignal.get(
            Domain.Signal,
            RoutingResourceClaims(),
        )
        for StubIndex, MergedClaims in Compatible:
            if ExpansionCount >= MaximumExpansions:
                Exhausted = True
                return False
            ExpansionCount += 1
            Selected[DomainIndex] = StubIndex
            ClaimsBySignal[Domain.Signal] = MergedClaims
            SelectedSignalTerminalCount = sum(
                Fabric.TerminalDomains[Index].Signal == Domain.Signal
                for Index in Selected
            )
            if (
                SignalRequiresCompleteRoute(Domain.Signal)
                and
                SelectedSignalTerminalCount
                == TerminalDomainCountBySignal[Domain.Signal]
            ):
                Ingresses = tuple(sorted({
                    Fabric.TerminalDomains[Index]
                    .EscapeStubs[Selected[Index]].Ingress
                    for Index in Selected
                    if Fabric.TerminalDomains[Index].Signal == Domain.Signal
                }))
                RouteCandidates = BuildSignalRouteCandidates(
                    Domain.Signal,
                    Ingresses,
                )
                RoutedCurrentSignal = False
                for RouteNodes, RouteClaims in RouteCandidates:
                    if ExpansionCount >= MaximumExpansions:
                        Exhausted = True
                        break
                    CompleteClaims = _MergePlacementAccessClaims(
                        MergedClaims,
                        RouteClaims,
                    )
                    if FindSelfClaimConflicts({
                        Domain.Signal: CompleteClaims
                    }):
                        continue
                    BlockingSignals = tuple(
                        OtherSignal
                        for OtherSignal, OtherClaims
                        in ClaimsBySignal.items()
                        if (
                            OtherSignal != Domain.Signal
                            and _PlacementAccessClaimsConflict(
                                CompleteClaims,
                                OtherClaims,
                            )
                        )
                    )
                    if BlockingSignals:
                        ConflictSignals.update((
                            Domain.Signal,
                            *BlockingSignals,
                        ))
                        continue
                    ExpansionCount += 1
                    RoutedCurrentSignal = True
                    ClaimsBySignal[Domain.Signal] = CompleteClaims
                    SelectedSignalRoutes[Domain.Signal] = RouteNodes
                    if Search():
                        return True
                    SelectedSignalRoutes.pop(Domain.Signal, None)
                    ClaimsBySignal[Domain.Signal] = MergedClaims
                if not RoutedCurrentSignal and not FirstUnroutableSignal:
                    FirstUnroutableSignal = Domain.Signal
            elif Search():
                return True
            Selected.pop(DomainIndex, None)
            if ExistingSignalClaims.ResourceIds:
                ClaimsBySignal[Domain.Signal] = ExistingSignalClaims
            else:
                ClaimsBySignal.pop(Domain.Signal, None)
        return False

    Success = Search()
    SelectedValues = tuple(
        (
            Fabric.TerminalDomains[Index].Signal,
            Fabric.TerminalDomains[Index].Terminal,
            Selected[Index],
        )
        for Index in sorted(Selected)
    ) if Success else ()
    CapacityResourceIds = tuple(sorted({
        Resource
        for Signal, SignalClaims in ClaimsBySignal.items()
        for Resource in _MergePlacementAccessClaims(
            SignalClaims,
            (
                RoutingResourceClaims(
                    WireCells=frozenset(SelectedSignalRoutes.get(Signal, ())),
                    SupportCells=frozenset(
                        (X, Y - 1, Z)
                        for X, Y, Z in SelectedSignalRoutes.get(Signal, ())
                    ),
                    RequiredAirCells=frozenset(),
                    ElectricalCells=frozenset(
                        Position
                        for Node in SelectedSignalRoutes.get(Signal, ())
                        for Position in (
                            Node,
                            *EffectiveTechnology.NeighborPositions(Node),
                        )
                    ),
                )
            ),
        ).ResourceIds
    }, key=str)) if Success else ()
    AssignmentFingerprint = (
        sha256(repr((
            Fabric.FabricFingerprint,
            SelectedValues,
            tuple(sorted(SelectedLocalRouteSignals)),
            tuple(sorted(SelectedSignalRoutes.items())),
            CapacityResourceIds,
        )).encode("utf-8")).hexdigest()[:16]
        if Success
        else ""
    )
    return PlacementAccessAssignment(
        FabricFingerprint=Fabric.FabricFingerprint,
        AssignmentFingerprint=AssignmentFingerprint,
        SelectedStubIndices=SelectedValues,
        CapacityResourceIds=CapacityResourceIds,
        ExpansionCount=ExpansionCount,
        Success=Success,
        Complete=not Exhausted and not IncompleteRouteDomain,
        ConflictSignals=(() if Success else tuple(sorted(ConflictSignals))),
        FrontierSignals=(() if Success else FrontierSignals),
        MaximumRoutedSignalCount=MaximumRoutedSignalCount,
        FirstUnroutableSignal=(
            ""
            if Success
            else FirstUnroutableSignal or FirstIncompleteRouteSignal
        ),
        IncompleteReason=(
            "work-cap-exhausted"
            if Exhausted
            else "incomplete-derived-perimeter-route-domain"
            if IncompleteRouteDomain
            else ""
        ),
        SignalRoutes=tuple(sorted(SelectedSignalRoutes.items())) if Success else (),
        SelectedLocalRouteSignals=(
            tuple(sorted(SelectedLocalRouteSignals)) if Success else ()
        ),
    )


def BuildPlacementAccessAssignmentFromStubFactor(
    Fabric: PlacementAccessFabric,
    SelectedContractClaimChoiceIds: Iterable[tuple[str, str]],
    *,
    ExpansionCount: int,
) -> PlacementAccessAssignment:
    """Reconstruct one frozen fabric assignment from raw stub-factor values."""
    DomainIndexByLogicalKey = {
        str(Domain.LogicalKey or f"{Index}:{Domain.Signal}"): Index
        for Index, Domain in enumerate(Fabric.TerminalDomains)
    }
    if len(DomainIndexByLogicalKey) != len(Fabric.TerminalDomains):
        raise ValueError("access fabric terminal factor roles are not unique")
    SelectedByDomain: dict[int, int] = {}
    for SyntheticSignal, CandidateId in SelectedContractClaimChoiceIds:
        LogicalKey = str(SyntheticSignal).removeprefix(
            "__access_terminal__:"
        )
        CandidateParts = str(CandidateId).split(":")
        if (
            not str(SyntheticSignal).startswith("__access_terminal__:")
            or len(CandidateParts) != 2
            or CandidateParts[0] != "stub"
        ):
            continue
        DomainIndex = DomainIndexByLogicalKey.get(LogicalKey)
        if DomainIndex is None:
            raise ValueError("stub factor selection has an unknown terminal role")
        ChoiceId = CandidateParts[1]
        StubIndexesByChoiceId = {
            BuildPlacementAccessEscapeStubChoiceId(Stub): StubIndex
            for StubIndex, Stub in enumerate(
                Fabric.TerminalDomains[DomainIndex].EscapeStubs
            )
        }
        StubIndex = StubIndexesByChoiceId.get(ChoiceId)
        if StubIndex is None or DomainIndex in SelectedByDomain:
            raise ValueError("stub factor selection has an invalid domain value")
        SelectedByDomain[DomainIndex] = StubIndex
    if len(SelectedByDomain) != len(Fabric.TerminalDomains):
        raise ValueError("stub factor selection omitted a terminal domain")
    Selected = tuple(
        (
            Domain.Signal,
            Domain.Terminal,
            SelectedByDomain[Index],
        )
        for Index, Domain in enumerate(Fabric.TerminalDomains)
    )
    if any(
        StubIndex < 0 or StubIndex >= len(Fabric.TerminalDomains[Index].EscapeStubs)
        for Index, StubIndex in SelectedByDomain.items()
    ):
        raise ValueError("stub factor selection references an unknown stub")
    CapacityResourceIds = tuple(sorted({
        Resource
        for Index, StubIndex in SelectedByDomain.items()
        for Resource in Fabric.TerminalDomains[Index].EscapeStubs[StubIndex]
        .PhysicalClaims.ResourceIds
    }, key=str))
    Fingerprint = sha256(repr((Fabric.FabricFingerprint, Selected)).encode("utf-8")).hexdigest()[:16]
    return PlacementAccessAssignment(
        FabricFingerprint=Fabric.FabricFingerprint,
        AssignmentFingerprint=Fingerprint,
        SelectedStubIndices=Selected,
        CapacityResourceIds=CapacityResourceIds,
        ExpansionCount=max(0, int(ExpansionCount)),
        Success=True,
        Complete=True,
    )


def MaterializeSelectedPlacementAccessStubClaims(
    Fabric: PlacementAccessFabric,
    SelectedContractClaimChoiceIds: Iterable[tuple[str, str]],
    ResourceGraph: Any,
) -> PlacementAccessFabric:
    """Expand full physical claims for only the selected access values."""
    ChoiceIdByLogicalKey = {
        str(SyntheticSignal).removeprefix("__access_terminal__:"):
        str(CandidateId).removeprefix("stub:")
        for SyntheticSignal, CandidateId in SelectedContractClaimChoiceIds
        if (
            str(SyntheticSignal).startswith("__access_terminal__:")
            and str(CandidateId).startswith("stub:")
        )
    }
    MaterializedDomains = []
    for DomainIndex, Domain in enumerate(Fabric.TerminalDomains):
        LogicalKey = str(
            Domain.LogicalKey or f"{DomainIndex}:{Domain.Signal}"
        )
        SelectedChoiceId = ChoiceIdByLogicalKey.get(LogicalKey)
        if SelectedChoiceId is None:
            raise ValueError(
                "selected access claim materialization omitted a terminal"
            )
        MaterializedStubs = []
        SelectedCount = 0
        for Stub in Domain.EscapeStubs:
            if BuildPlacementAccessEscapeStubChoiceId(Stub) != SelectedChoiceId:
                MaterializedStubs.append(Stub)
                continue
            SelectedCount += 1
            if Stub.PhysicalClaimsDeferred:
                Claims = ResourceGraph.BuildRouteClaims(Stub.Path)
                Conflicts = FindSelfClaimConflicts({Domain.Signal: Claims})
                if Conflicts:
                    raise ValueError(
                        "selected deferred access path is electrically "
                        f"self-conflicting: {tuple(map(str, Conflicts))!r}"
                    )
                Stub = replace(
                    Stub,
                    PhysicalClaims=Claims,
                    CapacityResourceIds=tuple(sorted(
                        Claims.ResourceIds,
                        key=str,
                    )),
                    PhysicalClaimsDeferred=False,
                )
            MaterializedStubs.append(Stub)
        if SelectedCount != 1:
            raise ValueError(
                "selected access claim materialization has an invalid value"
            )
        MaterializedDomains.append(replace(
            Domain,
            EscapeStubs=tuple(MaterializedStubs),
        ))
    return replace(
        Fabric,
        TerminalDomains=tuple(MaterializedDomains),
    )


def AttachPlacementAccessAssignment(
    Placement: Any,
    Assignment: PlacementAccessAssignment,
) -> Any:
    """Freeze the selected access witness at both placement boundaries."""
    AttachedPlaced = (
        replace(
            Placement.Placed,
            PlacementAccessAssignment=Assignment,
        )
        if is_dataclass(Placement.Placed)
        else SimpleNamespace(**{
            **vars(Placement.Placed),
            "PlacementAccessAssignment": Assignment,
        })
    )
    return (
        replace(
            Placement,
            Placed=AttachedPlaced,
            PlacementAccessAssignment=Assignment,
        )
        if is_dataclass(Placement)
        else SimpleNamespace(**{
            **vars(Placement),
            "Placed": AttachedPlaced,
            "PlacementAccessAssignment": Assignment,
        })
    )

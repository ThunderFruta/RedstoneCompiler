"""Raw portal and route-tree cache identity management."""

from __future__ import annotations

from ...Placement.Geometry import GetGateInputAccess

from ..Contracts.Component import PhysicalComponentAssemblyPlan

from ..Contracts.Core import Position2

from ..Contracts.Core import Position3

from ..Contracts.PhysicalInterface import PreparedPhysicalComponentPortFactorDomain

from ..Contracts.Placement import ClusterInterfaceConflictComponent

from ..Contracts.Placement import ClusterInterfaceProblem

from ..Contracts.Placement import ClusterInterfaceTerminalDomain

from ..Contracts.Results import RoutingResources

from ..Failures import RoutingFailure

from ..Failures import RoutingFailureReason

from ..Failures import RoutingStageError

from ..Interfaces.BoundaryRelations import RawPortalGeometryCache

from ..Reliability import BuildStableFingerprint

from ..Reliability import RoutingDeadline

from ..ResourceGraph import FindSelfClaimConflicts

from ..ResourceGraph import IndexedRoutingResourceGraph

from ..ResourceGraph import LocalRouteClaim

from ..ResourceGraph import NormalizeRoutingEdge

from ..ResourceGraph import PinAccessPortal

from ..ResourceGraph import PortalReservation

from ..ResourceGraph import RoutingResourceClaims

from collections import defaultdict

from dataclasses import dataclass

from dataclasses import replace

from typing import Any

from typing import Iterable

from typing import Mapping

from .PhysicalGuides import (
    BuildPhysicalExteriorResourceGraphFingerprint,
)

from .RunModels import (
    RepeatedWorkTransition,
)

def BuildClusterInterfaceAccessDomainFingerprint(
    Domains: dict[
        tuple[str, Position3],
        tuple[
            tuple[
                int,
                int,
                PinAccessPortal,
                RoutingResourceClaims,
            ],
            ...,
        ],
    ],
    SelectedSignals: frozenset[str] | None = None,
) -> str:
    """Identify complete portal-stem ownership domains without identifiers.

    Placement feedback must distinguish geometrically different candidates
    that expose the same authoritative access choices. Portal identifiers and
    signal names are diagnostic only, so neither participates. Coordinates are
    normalized to the selected interface, making rigid translation irrelevant
    while retaining terminal layout, routing layers, full paths, and exact
    claim categories.
    """
    Selected = (
        frozenset(map(str, SelectedSignals))
        if SelectedSignals is not None
        else frozenset(Signal for Signal, _Terminal in Domains)
    )
    SelectedDomains = {
        (str(Signal), Terminal): tuple(Values)
        for (Signal, Terminal), Values in Domains.items()
        if str(Signal) in Selected
    }
    Positions = tuple(
        Position
        for (_Signal, Terminal), Values in SelectedDomains.items()
        for Position in (
            Terminal,
            *(
                Point
                for _Cost, _Layer, Portal, Claims in Values
                for Point in (
                    *Portal.Path,
                    *Claims.WireCells,
                    *Claims.SupportCells,
                    *Claims.RequiredAirCells,
                    *Claims.ElectricalCells,
                )
            ),
        )
    )
    MinimumX = min((Position[0] for Position in Positions), default=0)
    MinimumY = min((Position[1] for Position in Positions), default=0)
    MinimumZ = min((Position[2] for Position in Positions), default=0)

    def Relative(Position: Position3) -> Position3:
        return (
            Position[0] - MinimumX,
            Position[1] - MinimumY,
            Position[2] - MinimumZ,
        )

    def ClaimSignature(
        Claims: RoutingResourceClaims,
    ) -> tuple[tuple[Position3, ...], ...]:
        return tuple(
            tuple(sorted(map(Relative, Values)))
            for Values in (
                Claims.WireCells,
                Claims.SupportCells,
                Claims.RequiredAirCells,
                Claims.ElectricalCells,
            )
        )

    SignalSignatures = []
    for Signal in sorted({
        Signal for Signal, _Terminal in SelectedDomains
    }):
        TerminalSignatures = []
        for (_Signal, Terminal), Values in SelectedDomains.items():
            if _Signal != Signal:
                continue
            TerminalSignatures.append((
                Relative(Terminal),
                tuple(sorted(
                    (
                        int(Layer),
                        tuple(map(Relative, Portal.Path)),
                        ClaimSignature(Claims),
                    )
                    for _Cost, Layer, Portal, Claims in Values
                )),
            ))
        SignalSignatures.append(tuple(sorted(TerminalSignatures)))
    return BuildStableFingerprint(tuple(sorted(SignalSignatures)))

def BuildClusterInterfaceProblem(
    Domains: dict[
        tuple[str, Position3],
        tuple[
            tuple[int, int, PinAccessPortal, RoutingResourceClaims], ...
        ],
    ],
    *,
    PlacementVariantFingerprint: str,
    OwnershipFingerprint: str,
    DomainComplete: bool = False,
) -> ClusterInterfaceProblem:
    """Publish the exact bounded interface CSP before route-tree planning.

    The lease matcher owns the concrete candidate domains.  This small shared
    contract deliberately records their identifier-independent shape so
    placement can compare variants and failures can prove that a repeated
    state is not new work.
    """
    DomainFingerprint = BuildClusterInterfaceAccessDomainFingerprint(Domains)
    RawDomainRecords = []
    for (Signal, Terminal), Values in Domains.items():
        CandidateRecords = []
        for CandidateIndex, (_Cost, Layer, Portal, Claims) in enumerate(
            Values
        ):
            ClaimFingerprint = BuildStableFingerprint((
                int(Layer),
                tuple(Portal.Path),
                tuple(sorted(Claims.WireCells)),
                tuple(sorted(Claims.SupportCells)),
                tuple(sorted(Claims.RequiredAirCells)),
                tuple(sorted(Claims.ElectricalCells)),
            ))
            CandidateRecords.append((
                ClaimFingerprint,
                CandidateIndex,
                Claims,
            ))
        CandidateRecords.sort(key=lambda Value: Value[0])
        TerminalFingerprint = BuildStableFingerprint((
            Terminal,
            tuple(Value[0] for Value in CandidateRecords),
        ))
        RawDomainRecords.append((
            TerminalFingerprint,
            str(Signal),
            tuple(CandidateRecords),
        ))
    RawDomainRecords.sort(key=lambda Value: (
        Value[0],
        tuple(Record[0] for Record in Value[2]),
    ))
    TerminalDomains = tuple(
        ClusterInterfaceTerminalDomain(
            TerminalFingerprint=TerminalFingerprint,
            CandidateClaimFingerprints=tuple(
                Record[0] for Record in CandidateRecords
            ),
        )
        for TerminalFingerprint, _Signal, CandidateRecords
        in RawDomainRecords
    )
    DomainSizes = tuple(sorted(
        Domain.CandidateCount for Domain in TerminalDomains
    ))
    MandatoryClaimCount = sum(
        len(Value[3].ResourceIds)
        for Values in Domains.values()
        for Value in Values
    )
    Parents = list(range(len(TerminalDomains)))

    def Find(Value: int) -> int:
        while Parents[Value] != Value:
            Parents[Value] = Parents[Parents[Value]]
            Value = Parents[Value]
        return Value

    def Union(First: int, Second: int) -> None:
        FirstRoot = Find(First)
        SecondRoot = Find(Second)
        if FirstRoot != SecondRoot:
            Parents[max(FirstRoot, SecondRoot)] = min(
                FirstRoot,
                SecondRoot,
            )

    ResourceOwners: dict[
        tuple[str, Position3],
        list[tuple[int, int, str]],
    ] = {}
    for DomainIndex, (
        _TerminalFingerprint,
        Signal,
        CandidateRecords,
    ) in enumerate(RawDomainRecords):
        for CanonicalCandidateIndex, (
            _ClaimFingerprint,
            _OriginalCandidateIndex,
            Claims,
        ) in enumerate(CandidateRecords):
            for Kind, Positions in (
                ("Wire", Claims.WireCells),
                ("Support", Claims.SupportCells),
                ("Air", Claims.RequiredAirCells),
                ("Electrical", Claims.ElectricalCells),
            ):
                for Position in Positions:
                    ResourceOwners.setdefault(
                        (Kind, Position),
                        [],
                    ).append((
                        DomainIndex,
                        CanonicalCandidateIndex,
                        Signal,
                    ))
    ComponentResources: dict[int, set[str]] = {}
    IncompatibleEdges: set[tuple[int, int, int, int, str]] = set()
    IncompatibleEdgeCountsByDomainPair: dict[
        tuple[int, int], int
    ] = {}
    for Resource, Owners in ResourceOwners.items():
        DistinctOwners = tuple(sorted(set(Owners)))
        ResourceFingerprint = BuildStableFingerprint(Resource)
        for FirstIndex, FirstOwner in enumerate(DistinctOwners):
            FirstDomain, FirstCandidate, FirstSignal = FirstOwner
            for SecondOwner in DistinctOwners[FirstIndex + 1:]:
                SecondDomain, SecondCandidate, SecondSignal = SecondOwner
                if (
                    FirstDomain == SecondDomain
                    or FirstSignal == SecondSignal
                ):
                    continue
                Union(FirstDomain, SecondDomain)
                DomainPair = tuple(sorted((
                    FirstDomain,
                    SecondDomain,
                )))
                IncompatibleEdgeCountsByDomainPair[DomainPair] = (
                    IncompatibleEdgeCountsByDomainPair.get(
                        DomainPair,
                        0,
                    )
                    + 1
                )
                if len(IncompatibleEdges) < 512:
                    IncompatibleEdges.add((
                        FirstDomain,
                        FirstCandidate,
                        SecondDomain,
                        SecondCandidate,
                        ResourceFingerprint,
                    ))
    ComponentDomains: dict[int, list[int]] = {}
    for DomainIndex in range(len(TerminalDomains)):
        ComponentDomains.setdefault(
            Find(DomainIndex),
            [],
        ).append(DomainIndex)
    for (
        FirstDomain,
        _FirstCandidate,
        SecondDomain,
        _SecondCandidate,
        ResourceFingerprint,
    ) in IncompatibleEdges:
        Root = Find(FirstDomain)
        if Root == Find(SecondDomain):
            ComponentResources.setdefault(Root, set()).add(
                ResourceFingerprint
            )
    ConflictComponents = []
    for Root, DomainIndices in ComponentDomains.items():
        SortedDomainIndices = tuple(sorted(DomainIndices))
        ComponentEdges = tuple(sorted(
            Edge
            for Edge in IncompatibleEdges
            if Find(Edge[0]) == Root and Find(Edge[2]) == Root
        ))
        ResourceFingerprints = tuple(sorted(
            ComponentResources.get(Root, set())
        ))
        IncompatibleDomainEdgeCount = sum(
            Count
            for (FirstDomain, SecondDomain), Count
            in IncompatibleEdgeCountsByDomainPair.items()
            if (
                Find(FirstDomain) == Root
                and Find(SecondDomain) == Root
            )
        )
        ConflictComponents.append(ClusterInterfaceConflictComponent(
            ComponentFingerprint=BuildStableFingerprint((
                tuple(
                    TerminalDomains[Index].TerminalFingerprint
                    for Index in SortedDomainIndices
                ),
                ResourceFingerprints,
                tuple(
                    (
                        TerminalDomains[Edge[0]].TerminalFingerprint,
                        Edge[1],
                        TerminalDomains[Edge[2]].TerminalFingerprint,
                        Edge[3],
                        Edge[4],
                    )
                    for Edge in ComponentEdges
                ),
            )),
            TerminalDomainIndices=SortedDomainIndices,
            ConflictingResourceFingerprints=ResourceFingerprints,
            IncompatibleDomainEdges=ComponentEdges,
            IncompatibleDomainEdgeCount=(
                IncompatibleDomainEdgeCount
            ),
            WitnessesComplete=(
                len(ComponentEdges)
                == IncompatibleDomainEdgeCount
            ),
        ))
    ConflictComponents.sort(key=lambda Component: (
        -len(Component.TerminalDomainIndices),
        Component.ComponentFingerprint,
    ))
    return ClusterInterfaceProblem(
        ComponentFingerprint=DomainFingerprint,
        PlacementVariantFingerprint=PlacementVariantFingerprint,
        OwnershipFingerprint=OwnershipFingerprint,
        TerminalDomainSizes=DomainSizes,
        MandatoryClaimCount=MandatoryClaimCount,
        DomainComplete=DomainComplete,
        DomainCandidateCount=sum(DomainSizes),
        TerminalDomains=TerminalDomains,
        ConflictComponents=tuple(ConflictComponents),
    )

def BuildClusterInterfaceReservationAssignmentFingerprint(
    Reservations: Iterable[PortalReservation],
) -> str:
    """Identify frozen terminal ownership independent of record ordering."""
    Values = tuple(
        Reservation
        for Reservation in Reservations
        if Reservation.Purpose == "cluster-boundary-lease"
    )
    return BuildStableFingerprint(tuple(sorted(
        tuple(sorted(
            (
                Reservation.Terminal,
                Reservation.Layer,
                tuple(Reservation.FirstSegment),
                tuple(sorted(Reservation.Claims.WireCells)),
                tuple(sorted(Reservation.Claims.SupportCells)),
                tuple(sorted(Reservation.Claims.RequiredAirCells)),
                tuple(sorted(Reservation.Claims.ElectricalCells)),
            )
            for Reservation in Values
            if Reservation.Signal == Signal
        ))
        for Signal in {
            Reservation.Signal for Reservation in Values
        }
        if any(
            Reservation.Signal == Signal for Reservation in Values
        )
    )))

def ValidateFrozenPhysicalComponentPostClosurePortalHandoff(
    Resources: RoutingResources,
    Preparation: PreparedPhysicalComponentPortFactorDomain | None,
    Plan: PhysicalComponentAssemblyPlan | None,
) -> RawPortalGeometryCache:
    """Return the exact prepared exterior fabric or reject identity drift."""

    Handoff = Resources.FrozenPhysicalComponentPostClosurePortalHandoff
    Mismatches: list[str] = []
    if Handoff is None:
        Mismatches.append("MissingHandoff")
        RawCache = None
    else:
        RawCache = Handoff.RawPortalGeometryCache
    if Preparation is None:
        Mismatches.append("MissingPreparation")
    elif Handoff is not None:
        if not Preparation.Complete:
            Mismatches.append("IncompletePreparation")
        if (
            Handoff.PreparationDomainFingerprint
            != Preparation.DomainFingerprint
        ):
            Mismatches.append("PreparationDomainFingerprint")
        if Handoff.PlacementFingerprint != Preparation.PlacementFingerprint:
            Mismatches.append("PreparationPlacementFingerprint")
        if (
            Handoff.ComponentGraphFingerprint
            != Preparation.ComponentGraphFingerprint
        ):
            Mismatches.append("PreparationComponentGraphFingerprint")
        if (
            Handoff.ResourceGraphFingerprint
            != Preparation.ResourceGraphFingerprint
        ):
            Mismatches.append("PreparationResourceGraphFingerprint")
        if (
            Handoff.ExteriorRegionFingerprint
            != Preparation.ExteriorRegionFingerprint
        ):
            Mismatches.append("PreparationExteriorRegionFingerprint")
    if Plan is None:
        Mismatches.append("MissingAssemblyPlan")
    elif Handoff is not None:
        if Plan.PlacementFingerprint != Handoff.PlacementFingerprint:
            Mismatches.append("PlanPlacementFingerprint")
        if Plan.ComponentGraphFingerprint != Handoff.ComponentGraphFingerprint:
            Mismatches.append("PlanComponentGraphFingerprint")
        if Plan.ResourceGraphFingerprint != Handoff.ResourceGraphFingerprint:
            Mismatches.append("PlanResourceGraphFingerprint")
        if Plan.ExteriorRegionFingerprint != Handoff.ExteriorRegionFingerprint:
            Mismatches.append("PlanExteriorRegionFingerprint")
    if Handoff is not None and not isinstance(RawCache, RawPortalGeometryCache):
        Mismatches.append("RawPortalGeometryCacheType")
    elif Handoff is not None and RawCache is not None:
        if (
            RawCache.ExteriorRegionFingerprint
            != Handoff.ExteriorRegionFingerprint
        ):
            Mismatches.append("CacheExteriorRegionFingerprint")
        if (
            RawCache.AuthoritativeResourceGraphFingerprint
            != Handoff.ResourceGraphFingerprint
        ):
            Mismatches.append("CacheResourceGraphFingerprint")
        CurrentResourceFingerprint = (
            BuildPhysicalExteriorResourceGraphFingerprint(
                Resources.ResourceGraph,
                Handoff.ExteriorRegionFingerprint,
                RawCache.Region,
            )
        )
        if CurrentResourceFingerprint != Handoff.ResourceGraphFingerprint:
            Mismatches.append("CurrentResourceGraphFingerprint")
    if Mismatches:
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ComponentAssemblyIdentityMismatch,
            Stage="PhysicalComponentGlobalPlanning",
            Detail=(
                "the frozen post-closure portal handoff does not match the "
                "prepared domain, assembly plan, and resource graph"
            ),
            Diagnostics={
                "IdentityMismatches": sorted(set(Mismatches)),
                "PreparationDomainFingerprint": str(getattr(
                    Preparation,
                    "DomainFingerprint",
                    "",
                )),
                "PhysicalAssemblyPlanFingerprint": str(getattr(
                    Plan,
                    "PlanFingerprint",
                    "",
                )),
                "HandoffPreparationDomainFingerprint": str(getattr(
                    Handoff,
                    "PreparationDomainFingerprint",
                    "",
                )),
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    assert RawCache is not None
    return RawCache

def BuildFrozenPostClosurePortalHandoffTelemetry(
    Resources: RoutingResources,
    Preparation: PreparedPhysicalComponentPortFactorDomain | None,
    Plan: PhysicalComponentAssemblyPlan | None,
) -> dict[str, object]:
    """Validate and describe the exact exterior fabric consumed globally."""
    RawPortalCache = (
        ValidateFrozenPhysicalComponentPostClosurePortalHandoff(
            Resources,
            Preparation,
            Plan,
        )
    )
    return {
        "Applied": True,
        "PreparationDomainFingerprint": str(
            getattr(Preparation, "DomainFingerprint", "")
        ),
        "PhysicalAssemblyPlanFingerprint": str(
            getattr(Plan, "PlanFingerprint", "")
        ),
        "ExteriorRegionFingerprint": (
            RawPortalCache.ExteriorRegionFingerprint
        ),
        "AssignedColumnCount": len(RawPortalCache.AssignedColumns),
        "ReservedAccessCount": len(RawPortalCache.ReservedAccess),
        "PortalEntryCount": len(RawPortalCache.PortalEntries),
        "PortableProofUsed": False,
    }

def BuildConfiguredPortalRequestDomainFingerprint(
    Signal: str,
    PortalVariantCount: int,
    MaximumExpansions: int,
    GuideInputFingerprint: str,
    Bounds: tuple[int, int, int, int],
    RequestRecords: Iterable[tuple[object, ...]],
) -> str:
    """Bind every finite input to one authoritative portal request domain."""
    return BuildStableFingerprint((
        "configured-portal-request-domain-v1",
        str(Signal),
        int(PortalVariantCount),
        int(MaximumExpansions),
        str(GuideInputFingerprint),
        tuple(Bounds),
        tuple(sorted(RequestRecords)),
    ))

MaximumRawPortalGeometryCacheEntries = 8

@dataclass(frozen=True)
class RawPortalGeometryReusePlan:
    """One exact or signal-scoped reuse of immutable portal geometry."""

    Cache: RawPortalGeometryCache
    ReusedSignals: frozenset[str]
    GeneratedSignals: frozenset[str]
    ExactMatch: bool
    PortableAcrossPlacement: bool = False
    SignalTranslations: tuple[tuple[str, Position3], ...] = ()
    SignalPlanarTransforms: tuple[
        tuple[str, str, Position3], ...
    ] = ()

def TransformPlanarRoutingPosition(
    Position: Position3,
    Transform: str,
    Translation: Position3 = (0, 0, 0),
) -> Position3:
    """Apply one deterministic X/Z dihedral transform and translation."""
    X, Y, Z = Position
    TransformedXZ = {
        "Identity": (X, Z),
        "Rotate90": (-Z, X),
        "Rotate180": (-X, -Z),
        "Rotate270": (Z, -X),
        "MirrorX": (-X, Z),
        "MirrorZ": (X, -Z),
        "SwapXZ": (Z, X),
        "AntiSwapXZ": (-Z, -X),
    }.get(Transform)
    if TransformedXZ is None:
        raise ValueError(f"unknown planar routing transform: {Transform}")
    return (
        TransformedXZ[0] + Translation[0],
        Y + Translation[1],
        TransformedXZ[1] + Translation[2],
    )

def BuildTranslatedPortablePortalId(
    Signal: str,
    Terminal: Position3,
    Layer: int,
    Path: tuple[Position3, ...],
    *,
    Length: int,
    BendCount: int,
    ViaCount: int,
    Cost: int,
) -> str:
    """Build a bounded identity from final geometry, not cache lineage."""
    GeometryFingerprint = BuildStableFingerprint((
        "translated-portable-portal-v1",
        Signal,
        Terminal,
        Layer,
        Path,
        Length,
        BendCount,
        ViaCount,
        Cost,
    ))
    return (
        f"{Signal}:{Terminal}:{Layer}:translated:"
        f"{GeometryFingerprint[:16]}"
    )

def MaterializeValidatedPortablePortalPositiveWitness(
    Portal: PinAccessPortal,
    *,
    Signal: str,
    Terminal: Position3,
    Layer: int,
    Transform: str,
    Translation: Position3,
    ResourceGraph: Any,
    RegionNodes: frozenset[Position3],
    RegionEdges: frozenset[RoutingEdge],
) -> PinAccessPortal | None:
    """Rebind one positive portal witness to the current exact geometry.

    This deliberately returns no domain-completeness information.  The
    caller has already matched routing policy and access topology; this final
    materialization check binds the witness to the current terminal, resource
    graph, exterior region, and rebuilt physical claims.
    """
    CurrentSignal = str(Signal)
    CurrentTerminal = tuple(map(int, Terminal))
    CurrentLayer = int(Layer)
    if (
        str(Portal.Signal) != CurrentSignal
        or int(Portal.Layer) != CurrentLayer
        or TransformPlanarRoutingPosition(
            tuple(Portal.Terminal),
            Transform,
            Translation,
        ) != CurrentTerminal
    ):
        return None
    TransformedPath = tuple(
        TransformPlanarRoutingPosition(
            tuple(Position),
            Transform,
            Translation,
        )
        for Position in Portal.Path
    )
    TransformedEdges = frozenset(
        NormalizeRoutingEdge(First, Second)
        for First, Second in zip(
            TransformedPath,
            TransformedPath[1:],
        )
    )
    if (
        not TransformedPath
        or not frozenset(TransformedPath) <= RegionNodes
        or not TransformedEdges <= RegionEdges
    ):
        return None
    Claims = ResourceGraph.BuildRouteClaims(TransformedPath)
    if FindSelfClaimConflicts({CurrentSignal: Claims}):
        return None
    return replace(
        Portal,
        PortalId=BuildTranslatedPortablePortalId(
            CurrentSignal,
            CurrentTerminal,
            CurrentLayer,
            TransformedPath,
            Length=Portal.Length,
            BendCount=Portal.BendCount,
            ViaCount=Portal.ViaCount,
            Cost=Portal.Cost,
        ),
        Signal=CurrentSignal,
        Terminal=CurrentTerminal,
        Layer=CurrentLayer,
        Path=TransformedPath,
        Edges=TransformedEdges,
        Claims=Claims,
    )

def SelectRawPortalGeometryReusePlan(
    Caches: tuple[Any, ...],
    Placed: Any,
    Resources: RoutingResources,
    LayerCount: int,
    PortalLimit: int,
    PortalVariantCounts: dict[str, int],
    GuideExpansion: int,
    StrictMaximumExpansions: int,
    AccessGeometryFingerprint: tuple[object, ...],
    CoordinatedSignals: frozenset[str],
    AllowPortableSignalReuse: bool = False,
    PhysicalGlobalKeepoutFingerprint: str = "",
) -> RawPortalGeometryReusePlan | None:
    """Select exact work first, then a coordinated signal-only delta."""
    RequestedSignals = frozenset(PortalVariantCounts)
    PartialPlan: RawPortalGeometryReusePlan | None = None
    PortablePlan: RawPortalGeometryReusePlan | None = None

    def SignalGeometryByName(
        Fingerprint: tuple[object, ...],
    ) -> dict[str, object]:
        return {
            str(Entry[0]): Entry
            for Entry in Fingerprint
            if (
                isinstance(Entry, tuple)
                and len(Entry) >= 4
                and isinstance(Entry[0], str)
            )
        }

    def GeometryTransform(
        Cached: object,
        Requested: object,
    ) -> tuple[str, Position3] | None:
        if not (
            isinstance(Cached, tuple)
            and isinstance(Requested, tuple)
            and len(Cached) == 4
            and len(Requested) == 4
            and Cached[0] == Requested[0]
            and isinstance(Cached[1], tuple)
            and isinstance(Requested[1], tuple)
            and len(Cached[1]) == 3
            and len(Requested[1]) == 3
        ):
            return None
        for Transform in (
            "Identity",
            "Rotate90",
            "Rotate180",
            "Rotate270",
            "MirrorX",
            "MirrorZ",
            "SwapXZ",
            "AntiSwapXZ",
        ):
            TransformedRoot = TransformPlanarRoutingPosition(
                Cached[1],
                Transform,
            )
            Delta = tuple(
                int(Requested[1][Index])
                - int(TransformedRoot[Index])
                for Index in range(3)
            )

            def TransformPosition(Position: Position3) -> Position3:
                return TransformPlanarRoutingPosition(
                    Position,
                    Transform,
                    Delta,
                )

            Transformed = (
                Cached[0],
                TransformPosition(Cached[1]),
                tuple(
                    TransformPosition(Position)
                    for Position in Cached[2]
                ),
                tuple(
                    (
                        TransformPosition(Target),
                        tuple(
                            TransformPosition(Position)
                            for Position in Path
                        ),
                    )
                    for Target, Path in Cached[3]
                ),
            )
            if Transformed == Requested:
                return Transform, Delta
        return None

    RequestedGeometryBySignal = SignalGeometryByName(
        AccessGeometryFingerprint
    )
    for Value in reversed(Caches):
        if not isinstance(Value, RawPortalGeometryCache):
            continue
        Cache = Value
        if (
            PhysicalGlobalKeepoutFingerprint
            and Cache.PhysicalGlobalKeepoutFingerprint
            != PhysicalGlobalKeepoutFingerprint
        ):
            continue
        CachedVariantCounts = dict(Cache.PortalVariantCounts)
        SamePlacementResources = Cache.MatchesPlacementResources(
            Placed,
            Resources,
        )
        CommonPolicyMismatch = (
            not Cache.GuidePlanPrepared
            or Cache.LayerCount != LayerCount
            or Cache.PortalLimit != PortalLimit
            or Cache.GuideExpansion != GuideExpansion
            or Cache.StrictMaximumExpansions
            != StrictMaximumExpansions
        )
        if CommonPolicyMismatch:
            continue
        if (
            SamePlacementResources
            and Cache.AccessGeometryFingerprint
            != AccessGeometryFingerprint
        ):
            CachedGeometryBySignal = SignalGeometryByName(
                Cache.AccessGeometryFingerprint
            )
            SignalTransforms = {
                Signal: GeometryTransform(
                    CachedGeometryBySignal.get(Signal),
                    RequestedGeometryBySignal.get(Signal),
                )
                for Signal in RequestedSignals
            }
            ReusedSignals = frozenset(
                Signal
                for Signal in RequestedSignals
                if (
                    CachedVariantCounts.get(Signal)
                    == PortalVariantCounts[Signal]
                    and SignalTransforms.get(Signal) is not None
                    and Signal in dict(Cache.SignalRequestCounts)
                    and Signal in dict(Cache.SignalTargetCounts)
                    and Signal in dict(Cache.SignalStarvationCounts)
                )
            )
            if ReusedSignals:
                CandidateGeometryDeltaPlan = (
                    RawPortalGeometryReusePlan(
                        Cache=Cache,
                        ReusedSignals=ReusedSignals,
                        GeneratedSignals=(
                            RequestedSignals - ReusedSignals
                        ),
                        ExactMatch=False,
                        # Reuse the translation/revalidation path even
                        # though the placement itself is structurally the
                        # same. Its per-signal access geometry is the delta.
                        PortableAcrossPlacement=True,
                        SignalTranslations=tuple(sorted(
                            (
                                Signal,
                                SignalTransforms[Signal][1],
                            )
                            for Signal in ReusedSignals
                        )),
                        SignalPlanarTransforms=tuple(sorted(
                            (
                                Signal,
                                SignalTransforms[Signal][0],
                                SignalTransforms[Signal][1],
                            )
                            for Signal in ReusedSignals
                        )),
                    )
                )
                if (
                    PortablePlan is None
                    or len(CandidateGeometryDeltaPlan.ReusedSignals)
                    > len(PortablePlan.ReusedSignals)
                ):
                    PortablePlan = CandidateGeometryDeltaPlan
            continue
        if not SamePlacementResources:
            if not AllowPortableSignalReuse:
                continue
            CachedGeometryBySignal = SignalGeometryByName(
                Cache.AccessGeometryFingerprint
            )
            SignalTransforms = {
                Signal: GeometryTransform(
                    CachedGeometryBySignal.get(Signal),
                    RequestedGeometryBySignal.get(Signal),
                )
                for Signal in RequestedSignals
            }
            ReusedSignals = frozenset(
                Signal
                for Signal in RequestedSignals
                if (
                    CachedVariantCounts.get(Signal)
                    == PortalVariantCounts[Signal]
                    and SignalTransforms.get(Signal) is not None
                    and Signal in dict(Cache.SignalRequestCounts)
                    and Signal in dict(Cache.SignalTargetCounts)
                    and Signal in dict(Cache.SignalStarvationCounts)
                )
            )
            if not ReusedSignals:
                continue
            CandidatePortablePlan = RawPortalGeometryReusePlan(
                Cache=Cache,
                ReusedSignals=ReusedSignals,
                GeneratedSignals=RequestedSignals - ReusedSignals,
                ExactMatch=False,
                PortableAcrossPlacement=True,
                SignalTranslations=tuple(sorted(
                    (Signal, SignalTransforms[Signal][1])
                    for Signal in ReusedSignals
                )),
                SignalPlanarTransforms=tuple(sorted(
                    (
                        Signal,
                        SignalTransforms[Signal][0],
                        SignalTransforms[Signal][1],
                    )
                    for Signal in ReusedSignals
                )),
            )
            if (
                PortablePlan is None
                or len(CandidatePortablePlan.ReusedSignals)
                > len(PortablePlan.ReusedSignals)
            ):
                PortablePlan = CandidatePortablePlan
            continue
        CachedSignals = frozenset(CachedVariantCounts)
        if not CachedSignals <= RequestedSignals:
            continue
        if Cache.AccessGeometryFingerprint != AccessGeometryFingerprint:
            continue
        ChangedSignals = frozenset(
            Signal
            for Signal in CachedSignals
            if CachedVariantCounts[Signal]
            != PortalVariantCounts[Signal]
        )
        MissingSignals = RequestedSignals - CachedSignals
        if not ChangedSignals and not MissingSignals:
            return RawPortalGeometryReusePlan(
                Cache=Cache,
                ReusedSignals=RequestedSignals,
                GeneratedSignals=frozenset(),
                ExactMatch=True,
            )
        if ChangedSignals and not ChangedSignals <= CoordinatedSignals:
            continue
        ReportedSignals = (
            frozenset(dict(Cache.SignalRequestCounts))
            & frozenset(dict(Cache.SignalTargetCounts))
            & frozenset(dict(Cache.SignalStarvationCounts))
        )
        ReusedSignals = CachedSignals - ChangedSignals
        if not ReusedSignals <= ReportedSignals:
            continue
        CandidatePartialPlan = RawPortalGeometryReusePlan(
                Cache=Cache,
                ReusedSignals=ReusedSignals,
                GeneratedSignals=RequestedSignals - ReusedSignals,
                ExactMatch=False,
            )
        if (
            PartialPlan is None
            or len(CandidatePartialPlan.ReusedSignals)
            > len(PartialPlan.ReusedSignals)
        ):
            PartialPlan = CandidatePartialPlan
    return PartialPlan or PortablePlan

def BuildPinnedOrdinaryPortalReuseColumns(
    ReusePlan: RawPortalGeometryReusePlan | None,
    ExcludedTerminals: frozenset[
        tuple[str, Position3]
    ] = frozenset(),
) -> frozenset[Position2]:
    """Keep validated ordinary portal paths inside the next route region."""
    if ReusePlan is None or not ReusePlan.PortableAcrossPlacement:
        return frozenset()
    Transforms = {
        Signal: (Transform, Translation)
        for Signal, Transform, Translation in (
            ReusePlan.SignalPlanarTransforms
        )
    }
    Columns = set()
    for (Signal, Terminal, _Layer), Portals in (
        ReusePlan.Cache.PortalEntries
    ):
        if Signal not in ReusePlan.ReusedSignals:
            continue
        Transform, Translation = Transforms.get(
            Signal,
            ("Identity", (0, 0, 0)),
        )
        TransformedTerminal = TransformPlanarRoutingPosition(
            Terminal,
            Transform,
            Translation,
        )
        if (Signal, TransformedTerminal) in ExcludedTerminals:
            continue
        Columns.update(
            (
                Transformed[0],
                Transformed[2],
            )
            for Portal in Portals
            for Position in Portal.Path
            for Transformed in (
                TransformPlanarRoutingPosition(
                    Position,
                    Transform,
                    Translation,
                ),
            )
        )
    return frozenset(Columns)

def TransformPortableCompletePortalDomainKeys(
    CompletePortalDomainKeys: Iterable[tuple[str, Position3, int]],
    SignalPlanarTransforms: Mapping[
        str, tuple[str, Position3]
    ],
    ValidatedPortalDomainKeys: Iterable[tuple[str, Position3, int]],
    ExactPhysicalPortalTerminals: frozenset[
        tuple[str, Position3]
    ] = frozenset(),
    RegeneratedRequestDomainSignals: frozenset[str] = frozenset(),
) -> frozenset[tuple[str, Position3, int]]:
    """Transfer completeness only from validated transformed source keys."""
    ValidatedKeys = frozenset(ValidatedPortalDomainKeys)
    Result = set()
    for Signal, Terminal, Layer in CompletePortalDomainKeys:
        if str(Signal) in RegeneratedRequestDomainSignals:
            continue
        TransformValue = SignalPlanarTransforms.get(str(Signal))
        if TransformValue is None:
            continue
        Transform, Translation = TransformValue
        TransformedTerminal = TransformPlanarRoutingPosition(
            Terminal,
            Transform,
            Translation,
        )
        TransformedKey = (
            str(Signal),
            TransformedTerminal,
            int(Layer),
        )
        if (
            (str(Signal), TransformedTerminal)
            in ExactPhysicalPortalTerminals
            or TransformedKey not in ValidatedKeys
        ):
            continue
        Result.add(TransformedKey)
    return frozenset(Result)

def MergePostClosurePortalCompletionKeys(
    PolicyCompleteEmptyKeys: Iterable[tuple[str, Position3, int]],
    RegeneratedRequestKeys: Iterable[tuple[str, Position3, int]],
) -> tuple[tuple[str, Position3, int], ...]:
    """Close the regenerated request domain without dropping policy proofs."""
    return tuple(sorted({
        *PolicyCompleteEmptyKeys,
        *RegeneratedRequestKeys,
    }))

def SelectPortablePortalProofReusableSignals(
    ValidatedPositiveSignals: Iterable[str],
    ExactPhysicalAssemblySignals: Iterable[str],
) -> frozenset[str]:
    """Keep exact-plan signals out of portable request-domain proofs."""
    return frozenset(map(str, ValidatedPositiveSignals)) - frozenset(
        map(str, ExactPhysicalAssemblySignals)
    )

def SelectPortablePortalPositiveReusableSignals(
    ValidatedPositiveSignals: Iterable[str],
) -> frozenset[str]:
    """Retain validated portal witnesses without transferring completeness.

    Exact physical-plan signals may reuse a positively materialized generic
    portal after its transformed path has been checked against the current
    resource graph and exterior region.  That witness says nothing about the
    rest of the configured request domain, so proof reuse remains a separate
    and deliberately narrower decision.
    """
    return frozenset(map(str, ValidatedPositiveSignals))

def PartitionExpectedGenericPortalDomainKeys(
    ExpectedKeys: Iterable[tuple[str, Position3, int]],
    CompleteKeys: Iterable[tuple[str, Position3, int]],
) -> tuple[
    frozenset[tuple[str, Position3, int]],
    frozenset[str],
    frozenset[str],
]:
    """Identify precisely which exact-cache signals require regeneration."""
    Expected = frozenset(ExpectedKeys)
    Complete = frozenset(CompleteKeys)
    Missing = Expected - Complete
    Signals = frozenset(Key[0] for Key in Expected)
    GeneratedSignals = frozenset(Key[0] for Key in Missing)
    return Missing, Signals - GeneratedSignals, GeneratedSignals

def MergeSignalScopedRawPortalEntries(
    CachedEntries: tuple[
        tuple[tuple[str, Position3, int], tuple[PinAccessPortal, ...]], ...
    ],
    GeneratedEntries: tuple[
        tuple[tuple[str, Position3, int], tuple[PinAccessPortal, ...]], ...
    ],
    GeneratedSignals: frozenset[str],
) -> tuple[
    tuple[tuple[str, Position3, int], tuple[PinAccessPortal, ...]], ...
]:
    """Replace only regenerated signals in one immutable portal dictionary."""
    if any(Key[0] not in GeneratedSignals for Key, _Values in GeneratedEntries):
        raise ValueError(
            "GeneratedEntries contains an unchanged portal signal"
        )
    return tuple(sorted((
        *(
            Entry
            for Entry in CachedEntries
            if Entry[0][0] not in GeneratedSignals
        ),
        *GeneratedEntries,
    )))

def PartitionPhysicalOwnedTerminalPortalRequests(
    Requests: Iterable[tuple[Any, ...]],
    Metadata: Iterable[tuple[str, Position3, int]],
    OwnedTerminalPairs: frozenset[tuple[str, Position3]],
) -> tuple[
    list[tuple[Any, ...]],
    list[tuple[str, Position3, int]],
    list[tuple[Any, ...]],
    list[tuple[str, Position3, int]],
]:
    """Split one exact portal domain into owned-first and deferred batches."""
    RequestValues = list(Requests)
    MetadataValues = list(Metadata)
    if len(RequestValues) != len(MetadataValues):
        raise ValueError("portal request metadata length mismatch")
    OwnedRequests = []
    OwnedMetadata = []
    DeferredRequests = []
    DeferredMetadata = []
    for Request, Entry in zip(RequestValues, MetadataValues):
        Destination = (
            (OwnedRequests, OwnedMetadata)
            if (Entry[0], Entry[1]) in OwnedTerminalPairs
            else (DeferredRequests, DeferredMetadata)
        )
        Destination[0].append(Request)
        Destination[1].append(Entry)
    return (
        OwnedRequests,
        OwnedMetadata,
        DeferredRequests,
        DeferredMetadata,
    )

def RetainRawPortalGeometryCache(
    Resources: RoutingResources,
    Cache: RawPortalGeometryCache,
    MaximumEntries: int = MaximumRawPortalGeometryCacheEntries,
) -> None:
    """Retain one complete portal cache in a bounded newest-last tuple."""
    if MaximumEntries < 1:
        raise ValueError("MaximumEntries must be positive")
    RetentionIdentity = (
        Cache.PlacementGeometryFingerprint,
        Cache.ResourceGeometryFingerprint,
        id(Cache.Region),
        Cache.LayerCount,
        Cache.PortalLimit,
        Cache.PortalVariantCounts,
        Cache.GuideExpansion,
        Cache.StrictMaximumExpansions,
        Cache.AccessGeometryFingerprint,
        Cache.AssignedColumns,
        Cache.ReservedAccess,
        id(Cache.GuidePlan),
    )
    Retained = tuple(
        Value
        for Value in Resources.RawPortalGeometryCaches
        if not (
            isinstance(Value, RawPortalGeometryCache)
            and (
                Value.PlacementGeometryFingerprint,
                Value.ResourceGeometryFingerprint,
                id(Value.Region),
                Value.LayerCount,
                Value.PortalLimit,
                Value.PortalVariantCounts,
                Value.GuideExpansion,
                Value.StrictMaximumExpansions,
                Value.AccessGeometryFingerprint,
                Value.AssignedColumns,
                Value.ReservedAccess,
                id(Value.GuidePlan),
            )
            == RetentionIdentity
        )
    )
    Resources.RawPortalGeometryCaches = (
        *Retained,
        Cache,
    )[-MaximumEntries:]

def FindUnindexedClaimPositions(
    Indexed: IndexedRoutingResourceGraph,
    Claims: RoutingResourceClaims,
) -> frozenset[Position3]:
    """Return claim cells unavailable in the active assignment domain."""
    return frozenset(
        Position
        for Values in (
            Claims.WireCells,
            Claims.SupportCells,
            Claims.RequiredAirCells,
            Claims.ElectricalCells,
        )
        for Position in Values
        if Position not in Indexed.PositionIndices
    )

def ExtendIndexedRoutingResourceGraph(
    Indexed: IndexedRoutingResourceGraph,
    Claims: Iterable[RoutingResourceClaims],
) -> IndexedRoutingResourceGraph:
    """Extend an assignment index to cover every declared physical claim."""
    ResourcePositions = set(Indexed.ResourcePositions)
    for Claim in Claims:
        ResourcePositions.update(Claim.WireCells)
        ResourcePositions.update(Claim.SupportCells)
        ResourcePositions.update(Claim.RequiredAirCells)
        ResourcePositions.update(Claim.ElectricalCells)
    if len(ResourcePositions) == len(Indexed.ResourcePositions):
        return Indexed
    OrderedPositions = tuple(sorted(ResourcePositions))
    return IndexedRoutingResourceGraph(
        ResourcePositions=OrderedPositions,
        PositionIndices={
            Position: Index
            for Index, Position in enumerate(OrderedPositions)
        },
    )

@dataclass(frozen=True)
class PreparedPortalDomainCache:
    """Reserved or unreserved portal domain for one unchanged raw geometry."""

    RawPortalCache: RawPortalGeometryCache
    UnreservedPortalMode: bool
    ReservationVariant: int
    PortalEntries: tuple[
        tuple[tuple[str, Position3, int], tuple[PinAccessPortal, ...]], ...
    ]
    Reservations: tuple[PortalReservation, ...]
    SeedReservationPrepared: bool = False
    ReservedPortalSeedEntries: tuple[
        tuple[
            str,
            tuple[
                int,
                PinAccessPortal,
                tuple[PinAccessPortal, ...],
            ],
        ],
        ...,
    ] = ()

    def Matches(
        self,
        RawPortalCache: RawPortalGeometryCache,
        UnreservedPortalMode: bool,
        ReservationVariant: int,
    ) -> bool:
        return (
            self.RawPortalCache is RawPortalCache
            and self.UnreservedPortalMode == UnreservedPortalMode
            and self.ReservationVariant == ReservationVariant
        )

    def BuildPortalDictionary(
        self,
    ) -> dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]]:
        return dict(self.PortalEntries)

MaximumPreparedPortalDomainCacheEntries = 8

MaximumPhysicalGlobalRouteTreeResultCacheEntries = 512

def BuildPhysicalGlobalRouteTreeResultCacheKey(
    Request: tuple[Any, ...],
    ResourceGraphFingerprint: str,
    TechnologyFingerprint: str,
) -> str:
    """Identify one exact native route-tree request independent of its plan."""
    return BuildStableFingerprint((
        "physical-global-native-route-tree-v2",
        ResourceGraphFingerprint,
        TechnologyFingerprint,
        Request,
    ))

def BuildExactPhysicalPortalCertificateIdentityConditions(
    Plan: Any,
    Problem: Any,
    Preparation: Any,
    CurrentResourceGraphFingerprint: str,
    CurrentRegionFingerprint: str,
    CurrentTechnologyFingerprint: str,
) -> dict[str, bool]:
    """Compare portal proof identities under their original contracts."""
    PlanFabrics = tuple(getattr(Plan, "ExteriorFabrics", ()))
    PreparationFabrics = tuple(getattr(
        Preparation,
        "ExteriorFabrics",
        (),
    ))
    PlanFingerprint = str(getattr(Plan, "PlanFingerprint", ""))
    PlanResourceFingerprint = str(getattr(
        Plan,
        "ResourceGraphFingerprint",
        "",
    ))
    PlanRegionFingerprint = str(getattr(
        Plan,
        "ExteriorRegionFingerprint",
        "",
    ))
    PlanTechnologyFingerprint = str(getattr(
        Plan,
        "TechnologyFingerprint",
        "",
    ))
    PlanPlacementFingerprint = str(getattr(
        Plan,
        "PlacementFingerprint",
        "",
    ))
    PlanComponentGraphFingerprint = str(getattr(
        Plan,
        "ComponentGraphFingerprint",
        "",
    ))
    PlanInterfaceFingerprint = str(getattr(
        Plan,
        "InterfaceFingerprint",
        "",
    ))
    ProblemInterface = getattr(Problem, "Interface", None)
    return {
        "PlanFingerprintPresent": bool(PlanFingerprint),
        "ProblemPlanIdentityMatch": bool(
            Problem is not None
            and getattr(Problem, "PhysicalAssemblyPlan", None) == Plan
        ),
        "PlacementIdentityMatch": bool(
            Problem is not None
            and PlanPlacementFingerprint
            and str(getattr(Problem, "PlacementFingerprint", ""))
            == PlanPlacementFingerprint
            and Preparation is not None
            and str(getattr(
                Preparation,
                "PlacementFingerprint",
                "",
            )) == PlanPlacementFingerprint
        ),
        "ComponentGraphIdentityMatch": bool(
            PlanComponentGraphFingerprint
            and Preparation is not None
            and str(getattr(
                Preparation,
                "ComponentGraphFingerprint",
                "",
            )) == PlanComponentGraphFingerprint
        ),
        "InterfaceIdentityMatch": bool(
            ProblemInterface is not None
            and PlanInterfaceFingerprint
            and str(getattr(
                ProblemInterface,
                "InterfaceFingerprint",
                "",
            )) == PlanInterfaceFingerprint
            and str(getattr(
                ProblemInterface,
                "PhysicalAssemblyPlanFingerprint",
                "",
            )) == PlanFingerprint
        ),
        "ResourceGraphCurrentIdentityMatch": bool(
            PlanResourceFingerprint
            and CurrentResourceGraphFingerprint
            and PlanResourceFingerprint
            == str(CurrentResourceGraphFingerprint)
        ),
        "ResourceGraphPreparationIdentityMatch": bool(
            Preparation is not None
            and PlanResourceFingerprint
            and str(getattr(
                Preparation,
                "ResourceGraphFingerprint",
                "",
            )) == PlanResourceFingerprint
        ),
        "ExteriorRegionCurrentIdentityMatch": bool(
            PlanRegionFingerprint
            and CurrentRegionFingerprint
            and PlanRegionFingerprint == str(CurrentRegionFingerprint)
        ),
        "ExteriorRegionPreparationIdentityMatch": bool(
            Preparation is not None
            and PlanRegionFingerprint
            and str(getattr(
                Preparation,
                "ExteriorRegionFingerprint",
                "",
            )) == PlanRegionFingerprint
        ),
        "ExteriorFabricIdentityMatch": bool(
            PlanFabrics
            and all(
                bool(getattr(Fabric, "Complete", False))
                and str(getattr(
                    Fabric,
                    "ResourceGraphFingerprint",
                    "",
                )) == PlanResourceFingerprint
                and str(getattr(
                    Fabric,
                    "RegionFingerprint",
                    "",
                )) == PlanRegionFingerprint
                for Fabric in PlanFabrics
            )
        ),
        "ExteriorFabricPreparationIdentityMatch": bool(
            Preparation is not None
            and PlanFabrics
            and PlanFabrics == PreparationFabrics
            and str(getattr(
                Plan,
                "ExteriorFabricSetFingerprint",
                "",
            ))
            == str(getattr(
                Preparation,
                "ExteriorFabricSetFingerprint",
                "",
            ))
            and bool(getattr(
                Plan,
                "ExteriorFabricSetFingerprint",
                "",
            ))
        ),
        "TechnologyIdentityMatch": bool(
            PlanTechnologyFingerprint
            and CurrentTechnologyFingerprint
            and PlanTechnologyFingerprint
            == str(CurrentTechnologyFingerprint)
        ),
    }

def FilterPhysicalCandidatesToCurrentPortalDomain(
    CandidatesBySignal: Mapping[str, Iterable[Any]],
    PortalsByTerminal: Mapping[Any, Iterable[Any]],
) -> tuple[
    dict[str, tuple[Any, ...]],
    dict[str, tuple[str, ...]],
]:
    """Discard retained candidates whose portal contract is not current."""
    VisiblePortalIds = frozenset(
        str(Portal.PortalId)
        for Values in PortalsByTerminal.values()
        for Portal in Values
    )
    Filtered: dict[str, tuple[Any, ...]] = {}
    RemovedBySignal: dict[str, tuple[str, ...]] = {}
    for Signal, Candidates in sorted(CandidatesBySignal.items()):
        Retained = []
        Removed = []
        for Candidate in Candidates:
            CandidatePortalIds = (
                str(Candidate.SourcePortalId),
                *(str(Value) for Value in Candidate.TargetPortalIds.values()),
            )
            if all(Value in VisiblePortalIds for Value in CandidatePortalIds):
                Retained.append(Candidate)
            else:
                Removed.append(str(Candidate.CandidateId))
        Filtered[str(Signal)] = tuple(Retained)
        if Removed:
            RemovedBySignal[str(Signal)] = tuple(sorted(Removed))
    return Filtered, RemovedBySignal

def ClassifyEmptyPhysicalCandidateDomains(
    CandidatesBySignal: Mapping[str, Iterable[Any]],
    RemovedCandidateIdsBySignal: Mapping[str, Iterable[str]],
    CertifiedCurrentEmptyDomainSignals: Iterable[str] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Separate proven empty route domains from portal identity failures.

    At the authoritative completion boundary, an empty signal with no
    candidate removed by portal rebinding is a complete negative route-domain
    result. A restored complete continuation is also authoritative after its
    portal IDs were revalidated and the current sibling-aperture filter was
    reapplied. Only any remaining empty signal caused by removing stale
    portal-bound candidates is an assembly identity mismatch.
    """
    EmptySignals = frozenset(
        str(Signal)
        for Signal, Values in CandidatesBySignal.items()
        if not tuple(Values)
    )
    CertifiedCurrentEmptySignals = frozenset(map(
        str,
        CertifiedCurrentEmptyDomainSignals,
    )) & EmptySignals
    IdentityMismatchSignals = frozenset(
        Signal
        for Signal in EmptySignals
        if tuple(RemovedCandidateIdsBySignal.get(Signal, ()))
    ) - CertifiedCurrentEmptySignals
    return (
        tuple(sorted(
            (EmptySignals - IdentityMismatchSignals)
            | CertifiedCurrentEmptySignals
        )),
        tuple(sorted(IdentityMismatchSignals)),
    )

def RetainPhysicalGlobalRouteTreeResults(
    Cache: dict[str, Any],
    Entries: Iterable[tuple[str, Any]],
    MaximumEntries: int = (
        MaximumPhysicalGlobalRouteTreeResultCacheEntries
    ),
) -> int:
    """Retain native route trees in deterministic bounded LRU order."""
    if MaximumEntries < 1:
        raise ValueError("MaximumEntries must be positive")
    for Key, Value in Entries:
        if Key in Cache:
            Cache.pop(Key)
        Cache[Key] = Value
    EvictedCount = 0
    while len(Cache) > MaximumEntries:
        Cache.pop(next(iter(Cache)))
        EvictedCount += 1
    return EvictedCount

def TouchPhysicalGlobalRouteTreeResult(
    Cache: dict[str, Any],
    Key: str,
) -> Any:
    """Read one retained result and make it the newest deterministic entry."""
    Value = Cache.pop(Key)
    Cache[Key] = Value
    return Value

def ReadRouteTreeBatchCompletionMask(
    BatchResult: Any,
    RequestCount: int,
) -> tuple[bool, ...]:
    """Read exact ordered native completion without inventing a prefix.

    Older native modules and lightweight tests may not expose the mask.  A
    fully completed batch remains unambiguous in that case; an interrupted
    batch certifies no individual request and therefore cannot advance a
    replay cursor.
    """
    if RequestCount < 0:
        raise ValueError("RequestCount cannot be negative")
    RawMask = getattr(BatchResult, "CompletionMask", None)
    CompletedWork = int(getattr(BatchResult, "CompletedWork", 0))
    DeadlineExceeded = bool(getattr(BatchResult, "DeadlineExceeded", False))
    if RawMask is None:
        if not DeadlineExceeded and CompletedWork == RequestCount:
            return (True,) * RequestCount
        return (False,) * RequestCount
    CompletionMask = tuple(bool(Value) for Value in RawMask)
    if len(CompletionMask) != RequestCount:
        raise ValueError(
            "native route-tree completion mask length does not match requests"
        )
    if sum(CompletionMask) != CompletedWork:
        raise ValueError(
            "native route-tree completion mask disagrees with completed work"
        )
    return CompletionMask

def ReadPortalBatchCandidatesAndCompletionMask(
    BatchResult: Any,
    RequestCount: int,
) -> tuple[list[Any], tuple[bool, ...]]:
    """Require one aligned candidate slot for every portal request."""
    Candidates = list(getattr(BatchResult, "Candidates", ()))
    if len(Candidates) != RequestCount:
        raise ValueError(
            "native portal result count does not match request count"
        )
    CompletionMask = ReadRouteTreeBatchCompletionMask(
        BatchResult,
        RequestCount,
    )
    if len(CompletionMask) != len(Candidates):
        raise ValueError(
            "native portal completion mask does not align with candidates"
        )
    TotalWork = int(getattr(BatchResult, "TotalWork", RequestCount))
    if TotalWork != RequestCount:
        raise ValueError(
            "native portal total work does not match request count"
        )
    if (
        not bool(getattr(BatchResult, "DeadlineExceeded", False))
        and not all(CompletionMask)
    ):
        raise ValueError(
            "native portal batch is incomplete without a deadline"
        )
    return Candidates, CompletionMask

def SelectCompletedPortalBatchEntries(
    Metadata: Iterable[tuple[str, Position3, int]],
    Results: Iterable[Any],
    CompletionMask: Iterable[bool],
) -> tuple[tuple[tuple[str, Position3, int], Any], ...]:
    """Select exact completed portal work by index, never by prefix."""
    MetadataValues = tuple(Metadata)
    ResultValues = tuple(Results)
    MaskValues = tuple(bool(Value) for Value in CompletionMask)
    if not (
        len(MetadataValues) == len(ResultValues) == len(MaskValues)
    ):
        raise ValueError(
            "portal metadata, results, and completion mask must align"
        )
    return tuple(
        (MetadataValues[Index], ResultValues[Index])
        for Index, Completed in enumerate(MaskValues)
        if Completed
    )

def MergePartialRawPortalBatchWork(
    CachedEntries: Iterable[
        tuple[
            tuple[str, Position3, int],
            tuple[PinAccessPortal, ...],
        ]
    ],
    GeneratedEntries: Iterable[
        tuple[
            tuple[str, Position3, int],
            tuple[PinAccessPortal, ...],
        ]
    ],
    CachedCompleteKeys: Iterable[tuple[str, Position3, int]],
    GeneratedCompleteKeys: Iterable[tuple[str, Position3, int]],
    GeneratedSignals: Iterable[str],
    DeadlineExceeded: bool,
) -> tuple[
    tuple[
        tuple[
            tuple[str, Position3, int],
            tuple[PinAccessPortal, ...],
        ],
        ...,
    ],
    tuple[tuple[str, Position3, int], ...],
]:
    """Publish completed portal work while preserving interrupted siblings."""
    GeneratedEntryValues = tuple(GeneratedEntries)
    GeneratedKeyValues = frozenset(GeneratedCompleteKeys)
    if DeadlineExceeded:
        EntryDictionary = dict(CachedEntries)
        EntryDictionary.update(GeneratedEntryValues)
        CompleteKeys = set(CachedCompleteKeys)
    else:
        GeneratedSignalSet = frozenset(GeneratedSignals)
        EntryDictionary = dict(MergeSignalScopedRawPortalEntries(
            CachedEntries,
            GeneratedEntryValues,
            GeneratedSignalSet,
        ))
        CompleteKeys = {
            Key
            for Key in CachedCompleteKeys
            if Key[0] not in GeneratedSignalSet
        }
    CompleteKeys.update(GeneratedKeyValues)
    return tuple(sorted(EntryDictionary.items())), tuple(sorted(CompleteKeys))

def SelectMatchingPartialPortalReplaySignals(
    GeneratedSignals: Iterable[str],
    CurrentRequestDomainFingerprints: Mapping[str, str],
    CachedRequestDomainFingerprints: Mapping[str, str],
    PortableAcrossPlacement: bool,
) -> frozenset[str]:
    """Select same-geometry interrupted domains safe to accumulate."""
    if PortableAcrossPlacement:
        return frozenset()
    return frozenset(
        Signal
        for Signal in GeneratedSignals
        if (
            Signal in CachedRequestDomainFingerprints
            and CurrentRequestDomainFingerprints.get(Signal)
            == CachedRequestDomainFingerprints[Signal]
        )
    )

def SelectPreparedPortalDomainCache(
    Caches: tuple[Any, ...],
    RawPortalCache: RawPortalGeometryCache,
    UnreservedPortalMode: bool,
    ReservationVariant: int,
) -> PreparedPortalDomainCache | None:
    """Select immutable ownership work for one unchanged portal geometry."""
    for Value in reversed(Caches):
        if (
            isinstance(Value, PreparedPortalDomainCache)
            and Value.Matches(
                RawPortalCache,
                UnreservedPortalMode,
                ReservationVariant,
            )
        ):
            return Value
    return None

def RetainPreparedPortalDomainCache(
    Resources: RoutingResources,
    Cache: PreparedPortalDomainCache,
    MaximumEntries: int = MaximumPreparedPortalDomainCacheEntries,
) -> None:
    """Retain bounded state-specific ownership work below raw geometry."""
    if MaximumEntries < 1:
        raise ValueError("MaximumEntries must be positive")
    Retained = tuple(
        Value
        for Value in Resources.PreparedPortalDomainCaches
        if not (
            isinstance(Value, PreparedPortalDomainCache)
            and Value.Matches(
                Cache.RawPortalCache,
                Cache.UnreservedPortalMode,
                Cache.ReservationVariant,
            )
        )
    )
    Resources.PreparedPortalDomainCaches = (
        *Retained,
        Cache,
    )[-MaximumEntries:]

def ChooseRepeatedWorkTransition(
    UnreservedPortalMode: bool,
    Deadline: RoutingDeadline,
) -> RepeatedWorkTransition:
    """Try unreserved portals once before terminating duplicate work."""
    if UnreservedPortalMode:
        return RepeatedWorkTransition(
            Action="Terminate",
            SkipStrictPortalReservation=True,
            Deadline=Deadline,
        )
    return RepeatedWorkTransition(
        Action="TryUnreservedPortals",
        SkipStrictPortalReservation=True,
        Deadline=Deadline,
    )

def SelectAuthoritativeBaseClaims(
    AllLocalClaims: tuple[LocalRouteClaim, ...],
    DisableLocalBaseClaims: bool,
) -> tuple[LocalRouteClaim, ...]:
    """Preserve every enabled placement-owned claim for exact assignment."""
    return () if DisableLocalBaseClaims else AllLocalClaims

def _CollectSignalTargets(Placed: Any) -> dict[str, tuple[Position3, ...]]:
    """Collect required route targets for every driven signal.

    This is the same terminal map used for routing profiles, including module
    outputs via OUTPUT gate inputs.
    """
    Targets: dict[str, set[Position3]] = defaultdict(set)
    for Gate in Placed.PlacedGates:
        for InputIndex, Signal in enumerate(Gate.Inputs):
            try:
                Pin, _Direction = GetGateInputAccess(Gate, InputIndex)
            except Exception:
                continue
            Targets[Signal].add(Pin)
    return {Signal: tuple(sorted(Positions)) for Signal, Positions in Targets.items()}

def GrowAssignmentExpansionLimit(
    CurrentLimit: int,
    MaximumLimit: int,
    GrowthFactor: int,
) -> int:
    """Grow exact-assignment work smoothly without exceeding its budget."""
    if CurrentLimit < 1 or MaximumLimit < 1 or GrowthFactor < 2:
        raise ValueError("assignment growth controls must be positive and growing")
    return min(MaximumLimit, CurrentLimit * GrowthFactor)

def ShouldRunShapeOptimization(QualityTarget: str) -> bool:
    """Keep first-legal routing focused on correctness and bounded completion."""
    return QualityTarget != "first-legal"

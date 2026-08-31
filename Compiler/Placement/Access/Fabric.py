"""Placement-wide immutable access-fabric construction."""

from __future__ import annotations

from dataclasses import (
    is_dataclass,
    replace,
)
from hashlib import (
    sha256,
)
from math import (
    ceil,
)
from types import (
    SimpleNamespace,
)
from typing import (
    Any,
    Callable,
    Iterable,
)
from Compiler.Routing.Actions.Geometry import (
    BuildRoutingResources,
)
from Compiler.Routing.ChannelPlanner import (
    BuildNetRoutingProfiles,
)
from Compiler.Routing.Contracts.Placement import (
    PlacementAccessAssignment,
    PlacementAccessEscapeStub,
    PlacementAccessFabric,
    PlacementAccessTerminalDomain,
)
from Compiler.Routing.Contracts.Core import (
    Position3,
)
from Compiler.Routing.ResourceGraph import (
    FindSelfClaimConflicts,
)
from Compiler.Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)
from Compiler.Placement.Rotation import (
    RotatedCellSize,
)
from Compiler.Placement.Geometry import (
    BuildPlacementPinAccessWitness,
)
from .Capacity import (
    FixedPlacementPinAccessDomain,
    SolveFixedPlacementPinAccessDomains,
)
from .EscapePaths import (
    _BuildBoundedLegalDerivedEscapePaths,
    _BuildFabricIngressSegmentPaths,
    _BuildIndependentShortestFabricEscapePaths,
    _BuildSharedLegalFabricEscapePaths,
    _BuildShortestLegalFabricEscapePaths,
    _ErasePlacementAccessPathLoops,
    _ValidateDerivedPerimeterFabricShell,
)
from .Geometry import (
    BuildDerivedPerimeterFabricShell,
    _AccessFabricWorkBudget,
    _BuildDerivedPerimeterAccessPrefixDomain,
    _DeriveLegalEscapeDirectionStateUpperBound,
    _GetDerivedPerimeterSlotAssignment,
    _RestrictDerivedPerimeterSlotEscapeAdjacency,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .Geometry import (
        DerivedPerimeterFabricShell,
    )


def _RingFacesForPosition(
    Position: Position3,
    RingBounds: tuple[tuple[int, int, int, int], ...],
) -> frozenset[str]:
    """Return every perimeter face touching one ring node."""
    X, _Y, Z = Position
    Faces = set()
    for RingMinimumX, RingMaximumX, RingMinimumZ, RingMaximumZ in RingBounds:
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


def BuildFixedPlacementPinAccessSolve(
    PinAccessWitness: Any,
    ResourceGraph: Any,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> Any:
    """Decide the complete catalog-selected rays for one fixed placement."""
    return SolveFixedPlacementPinAccessDomains(
        (
            FixedPlacementPinAccessDomain(
                DomainId=(
                    f"{Selection.GateName}:"
                    f"{Selection.Role}:{Selection.PinId}"
                ),
                Signal=Selection.Signal,
                Terminal=Selection.Terminal,
                Options=(Selection,),
                Complete=PinAccessWitness.Complete,
                IncompleteReason=PinAccessWitness.IncompleteReason,
            )
            for Selection in PinAccessWitness.Selections
        ),
        ResourceGraph=ResourceGraph,
        MaximumExpansions=max(1, len(PinAccessWitness.Selections) * 2),
        WorkCheck=WorkCheck,
    )


def _BuildPlacementAccessEscapeStubs(
    CandidatePaths: Iterable[
        tuple[tuple[Position3, ...], tuple[Position3, ...]]
    ],
    *,
    Signal: str,
    Terminal: Position3,
    Resources: Any,
    TopologyKind: str,
) -> list[PlacementAccessEscapeStub]:
    """Materialize the deterministic conflict-free stubs of one terminal."""
    Results = []
    SeenStubPaths = set()
    for Prefix, Path in CandidatePaths:
        StubPath = _ErasePlacementAccessPathLoops((*Prefix, *Path[1:]))
        if not StubPath or StubPath in SeenStubPaths:
            continue
        Claims = Resources.ResourceGraph.BuildRouteClaims(StubPath)
        if FindSelfClaimConflicts({Signal: Claims}):
            continue
        SeenStubPaths.add(StubPath)
        Results.append(PlacementAccessEscapeStub(
            Terminal=Terminal,
            Ingress=Path[-1],
            Path=StubPath,
            PhysicalClaims=Claims,
            CapacityResourceIds=tuple(sorted(Claims.ResourceIds, key=str)),
            Complete=True,
        ))
    if TopologyKind == "derived-perimeter-access-v1":
        Results.sort(key=lambda Stub: (
            len(Stub.PhysicalClaims.ResourceIds),
            len(Stub.Path),
            Stub.Path,
            Stub.Ingress,
        ))
    return Results


def _ValidatePlacementAccessFabricOptions(
    *,
    LaneCount: int | None,
    TopologyKind: str,
    AccessRingTrackCount: int,
    Shell: DerivedPerimeterFabricShell | None,
    MaximumEscapeStubsPerTerminal: int | None,
    MaximumLegalEscapeExpansions: int | None,
    DeriveLegalEscapeWorkLimit: bool,
) -> bool:
    """Validate topology options and report whether they use a perimeter."""
    if LaneCount is not None and LaneCount < 1:
        raise ValueError("placement access fabric requires a positive lane count")
    if TopologyKind not in {
        "fixed-access-band-v1",
        "perimeter-access-ring-v1",
        "derived-perimeter-access-v1",
    }:
        raise ValueError("unsupported placement access topology")
    IsPerimeterTopology = TopologyKind in {
        "perimeter-access-ring-v1",
        "derived-perimeter-access-v1",
    }
    if IsPerimeterTopology:
        if AccessRingTrackCount < 1:
            raise ValueError("access ring requires a positive track count")
    elif AccessRingTrackCount != 0:
        raise ValueError("non-ring access fabric cannot declare ring tracks")
    if Shell is not None and TopologyKind != "derived-perimeter-access-v1":
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
    return IsPerimeterTopology


def BuildPlacementAccessFabric(
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
    CompleteRouteSignals: frozenset[str] = frozenset(),
    MaximumLegalEscapeExpansions: int | None = None,
    DeriveLegalEscapeWorkLimit: bool = False,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PlacementAccessFabric:
    """Construct one fixed access fabric from placement and technology."""
    IsPerimeterTopology = _ValidatePlacementAccessFabricOptions(
        LaneCount=LaneCount,
        TopologyKind=TopologyKind,
        AccessRingTrackCount=AccessRingTrackCount,
        Shell=Shell,
        MaximumEscapeStubsPerTerminal=MaximumEscapeStubsPerTerminal,
        MaximumLegalEscapeExpansions=MaximumLegalEscapeExpansions,
        DeriveLegalEscapeWorkLimit=DeriveLegalEscapeWorkLimit,
    )
    Placed = Placement.Placed
    Resources = Resources or BuildRoutingResources(Placed, WorkCheck=WorkCheck)
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
    PinAccessWitness = BuildPlacementPinAccessWitness(
        Gates,
        AccessLength=EffectiveAccessLength,
        RequireCatalogMatch=True,
    )
    FixedPinAccessSolve = BuildFixedPlacementPinAccessSolve(
        PinAccessWitness,
        Resources.ResourceGraph,
        WorkCheck,
    )
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
            PinAccessWitness=PinAccessWitness,
            FixedPinAccessSolve=FixedPinAccessSolve,
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
            PinAccessWitness=PinAccessWitness,
            FixedPinAccessSolve=FixedPinAccessSolve,
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
            )
    elif Shell is not None:
        raise ValueError("derived perimeter shell requires a slot assignment")

    if Shell is not None:
        Profiles = Shell.ProfileBySignal
    else:
        Profiles = BuildNetRoutingProfiles(
            Placed,
            AccessLength=EffectiveAccessLength,
            AccessWitness=PinAccessWitness,
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
    if Shell is not None:
        FabricLayers = Shell.FabricLayers
        FabricYs = Shell.FabricYs
        FabricLayerCount = len(FabricLayers)
        TerminalPathByIdentity = Shell.TerminalPathByIdentity
        TerminalPaths = Shell.TerminalPaths
    else:
        MaximumFabricLayer = max(0, int(Placement.LayerCount) - 1)
        # The certified incumbent retains its historic access-band witness.
        # New derived perimeter contracts use the selected envelope exactly.
        FabricLayerCount = (
            min(
                max(1, int(Placement.LayerCount)),
                max(1, ceil(len(Profiles) / 6)),
            )
            if TopologyKind == "fixed-access-band-v1"
            else max(1, int(Placement.LayerCount))
        )
        FabricLayers = tuple(range(
            MaximumFabricLayer - FabricLayerCount + 1,
            MaximumFabricLayer + 1,
        ))
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
    Margin = TrackPitch * (
        AccessRingTrackCount
        if IsPerimeterTopology
        else 2
    )
    RingBounds: tuple[tuple[int, int, int, int], ...] = ()
    OuterBounds: tuple[int, int, int, int] | None = None
    ActiveFaces: tuple[str, ...] = ()
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
            4 * AccessRingTrackCount
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
    Region = Resources.ResourceGraph.BuildRegion(
        RegionBounds,
        AllowedAccess=AllowedAccess,
        WorkCheck=WorkCheck,
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
        CenterX = (MinimumX + MaximumX) // 2
        CenterZ = (MinimumZ + MaximumZ) // 2
        Outermost = RingBounds[-1]

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
                or (
                    TopologyKind == "perimeter-access-ring-v1"
                    and
                    Position[0] == CenterX
                    and Outermost[2] <= Position[2] <= Outermost[3]
                )
                or (
                    TopologyKind == "perimeter-access-ring-v1"
                    and
                    Position[2] == CenterZ
                    and Outermost[0] <= Position[0] <= Outermost[1]
                )
            )
            and (
                TopologyKind != "derived-perimeter-access-v1"
                or not HasFrozenPerimeterAssignment
                or bool(
                    _RingFacesForPosition(Position, RingBounds)
                    & ActiveFaceSet
                )
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
        if TopologyKind == "perimeter-access-ring-v1":
            for Position in FabricNodes:
                X, Y, Z = Position
                if X == CenterX and Outermost[2] < Z < Outermost[3]:
                    Face = "north-aperture" if Z <= CenterZ else "south-aperture"
                elif Z == CenterZ and Outermost[0] < X < Outermost[1]:
                    Face = "west-aperture" if X <= CenterX else "east-aperture"
                else:
                    continue
                RingIngressGroups.setdefault((Y, 0, Face), []).append(Position)
        RingIngressGroups = {
            Identity: sorted(Positions)
            for Identity, Positions in sorted(RingIngressGroups.items())
        }
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
    if TopologyKind == "derived-perimeter-access-v1":
        MutableRegionAdjacency: dict[Position3, list[Position3]] = {}
        for First, Second in Region.Edges:
            MutableRegionAdjacency.setdefault(First, []).append(Second)
            MutableRegionAdjacency.setdefault(Second, []).append(First)
        RegionAdjacency = {
            Position: tuple(sorted(Values))
            for Position, Values in MutableRegionAdjacency.items()
        }
        if DeriveLegalEscapeWorkLimit:
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
        if MaximumLegalEscapeExpansions is not None:
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
            GroupRepresentatives = {
                Identity: min(
                    Positions,
                    key=lambda Value: (
                        IngressDistance(Value),
                        Value,
                    ),
                )
                for Identity, Positions in EligibleRingIngressGroups.items()
            }
            if IsFrozenSlotTerminal:
                # A selected I/O slot owns one face, but distinct lateral
                # ingress claims on that face can be capacity-incompatible.
                # Keep its finite physical face segment in the pre-route
                # domain; this is geometry construction, not a later repair.
                RankedIngressNodes = tuple(sorted(
                    (
                        Position
                        for Positions in EligibleRingIngressGroups.values()
                        for Position in Positions
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
                EscapeAdjacency = RegionAdjacency
                if SelectedFace is not None and EscapeAdjacency is not None:
                    # Both a frozen I/O slot and the paired source endpoint
                    # of its signal carry an exact outward normal.  Keep the
                    # full lateral segment on that declared face, but do not
                    # search the core-side half of the resource region for
                    # an escape that the immutable endpoint contract cannot
                    # use.  This is a sound domain reduction: the omitted
                    # half-space is not a selectable access face, not a
                    # deferred alternative.
                    EscapeAdjacency = (
                        _RestrictDerivedPerimeterSlotEscapeAdjacency(
                            EscapeAdjacency,
                            Face=SelectedFace,
                            Start=Start,
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
                    )
                )
                DerivedLegalSearchComplete = (
                    DerivedLegalSearchComplete and PrefixSearchComplete
                )
                if IsFrozenSlotTerminal:
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
            PathMembers = tuple(
                (tuple(EscapePrefix), Path)
                for Path in (
                    _BuildSharedLegalFabricEscapePaths
                    if FabricLayerCount == 1
                    else _BuildShortestLegalFabricEscapePaths
                )(
                    Starts[0],
                    DiverseIngressNodes,
                    Region.Edges,
                    tuple(EscapePrefix),
                    Resources.ResourceGraph,
                )
            )
        else:
            PathMembers = ()
        Stubs = _BuildPlacementAccessEscapeStubs(
            PathMembers,
            Signal=Signal,
            Terminal=Terminal,
            Resources=Resources,
            TopologyKind=TopologyKind,
        )
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
            Stubs = _BuildPlacementAccessEscapeStubs(
                (
                    (tuple(EscapePrefix), Path)
                    for Path in _BuildShortestLegalFabricEscapePaths(
                        Starts[0],
                        DiverseIngressNodes,
                        Region.Edges,
                        tuple(EscapePrefix),
                        Resources.ResourceGraph,
                        Adjacency=RegionAdjacency,
                    )
                ),
                Signal=Signal,
                Terminal=Terminal,
                Resources=Resources,
                TopologyKind=TopologyKind,
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
        FabricNodes,
        FabricEdges,
        tuple(
            (
                Domain.Signal,
                Domain.Terminal,
                tuple(
                    (Stub.Ingress, Stub.Path, Stub.CapacityResourceIds)
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
            str(getattr(
                DerivedSlotAssignment,
                "AssignmentFingerprint",
                "",
            )),
            FabricLayers,
            FabricNodes,
            FabricEdges,
        )).encode("utf-8")).hexdigest()[:16]
        if IsPerimeterTopology
        else ""
    )
    return PlacementAccessFabric(
        FabricFingerprint=sha256(repr(CanonicalIdentity).encode("utf-8")).hexdigest()[:16],
        Nodes=FabricNodes,
        Edges=FabricEdges,
        IngressNodes=IngressNodes,
        PhysicalClaims=PhysicalClaims,
        CapacityResourceIds=tuple(sorted(PhysicalClaims.ResourceIds, key=str)),
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
        IncompleteReason=("" if Complete else IncompleteReason),
        PinAccessWitness=PinAccessWitness,
        FixedPinAccessSolve=FixedPinAccessSolve,
        Technology=Technology,
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

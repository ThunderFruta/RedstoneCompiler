"""Derived-perimeter access geometry and immutable shell construction."""

from __future__ import annotations

from collections.abc import (
    Mapping,
)
from dataclasses import (
    dataclass,
)
from hashlib import (
    sha256,
)
from types import (
    MappingProxyType,
)
from typing import (
    Any,
    Callable,
)
from PhysicalDesign.Redstone.Rules.Geometry import BuildRoutingResources
from PhysicalDesign.Routing.Planning.ChannelPlanner import BuildNetRoutingProfiles
from PhysicalDesign.Contracts.Core import Position3
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology, RedstoneRoutingTechnology


_PerimeterFaceDirections: dict[str, Position3] = {
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "west": (-1, 0, 0),
    "east": (1, 0, 0),
}

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

    ``_BuildBoundedLegalDerivedEscapePaths`` consumes work only after it
    dequeues a current ``(position, prior-direction)`` state.  For one fixed
    adjacency, its number of such states cannot exceed the initial state plus
    the directed adjacency entries.  Sum that upper bound for every terminal
    which has a legal start and at least one eligible frozen ring ingress.

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
        DirectionStateUpperBound = 1 + sum(
            len(Neighbors) for Neighbors in RegionAdjacency.values()
        )
        Total += len(PrefixDomain) * DirectionStateUpperBound
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
    Resources = Resources or BuildRoutingResources(
        Placed,
        Technology=Technology,
    )
    EffectiveAccessLength = (
        Technology.AccessLength
        if AccessLength is None
        else int(AccessLength)
    )
    SelectedPinAccessWitness = (
        getattr(Placement, "SelectedPinAccessWitness", None)
        or getattr(Placed, "SelectedPinAccessWitness", None)
    )
    Profiles = BuildNetRoutingProfiles(
        Placed,
        AccessLength=(
            Technology.AccessLength
            if SelectedPinAccessWitness is not None
            else EffectiveAccessLength
        ),
        AccessWitness=SelectedPinAccessWitness,
        RequireExplicitAccessWitness=(
            SelectedPinAccessWitness is not None
        ),
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
) -> tuple[
    tuple[tuple[int, int, int, int], ...],
    tuple[int, int, int, int],
    tuple[str, ...],
    dict[tuple[str, Position3], str],
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
    RingBounds = tuple(
        (
            InnermostCoordinate["west"] - TrackPitch * (TrackIndex - 1),
            InnermostCoordinate["east"] + TrackPitch * (TrackIndex - 1),
            InnermostCoordinate["north"] - TrackPitch * (TrackIndex - 1),
            InnermostCoordinate["south"] + TrackPitch * (TrackIndex - 1),
        )
        for TrackIndex in range(1, AccessRingTrackCount + 1)
    )
    ActiveFaces = tuple(
        Face for Face in _PerimeterFaceDirections
        if Face in {
            *ReservedFaces,
            *TerminalFaceByIdentity.values(),
        }
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
    return (
        str(getattr(ResourceGraph, "GraphVersion", "")),
        tuple(sorted(getattr(ResourceGraph, "ActualBlocks", ()))),
        tuple(sorted(getattr(ResourceGraph, "ElectricalBlocks", ()))),
        tuple(sorted(getattr(ResourceGraph, "SolidBlocks", ()))),
        tuple(sorted(ResourceGraph.StaticKeepOut)),
    )

def _BuildDerivedPerimeterShellInputFingerprint(
    Placement: Any,
    *,
    Resources: Any,
    Technology: RedstoneRoutingTechnology,
    AccessRingTrackCount: int,
    AccessLength: int,
    BoundarySignals: frozenset[str] | None,
    Assignment: Any,
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
        Technology=Technology,
    )
    SelectedPinAccessWitness = (
        getattr(Placement, "SelectedPinAccessWitness", None)
        or getattr(Placement.Placed, "SelectedPinAccessWitness", None)
    )
    ProfilesBySignal = BuildNetRoutingProfiles(
        Placement.Placed,
        AccessLength=(
            Technology.AccessLength
            if SelectedPinAccessWitness is not None
            else EffectiveAccessLength
        ),
        AccessWitness=SelectedPinAccessWitness,
        RequireExplicitAccessWitness=(
            SelectedPinAccessWitness is not None
        ),
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
    ) = _BuildDerivedPerimeterRingBounds(
        Assignment,
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
        FabricLayers=FabricLayers,
        FabricYs=FabricYs,
    )

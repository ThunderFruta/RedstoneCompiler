"""Immutable pre-route interface selection shared by placement backends.

The selector deliberately knows nothing about circuits or signal names.  A
backend publishes a finite set of complete local witnesses per component; the
selector chooses one witness per component subject to capacity-one resources.
This is the boundary between bounded placement/interface exploration and the
single later global-routing attempt.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import ceil
from typing import Any, Callable, Iterable

from ..Routing.Reliability import BuildStableFingerprint


@dataclass(frozen=True)
class PlacementAccessDemand:
    """Geometry and technology facts used to derive an access envelope."""

    ComponentCount: int
    TerminalCount: int
    PeakBoundaryDemand: int
    CoreBounds: tuple[int, int, int, int]
    TrackPitch: int
    AccessLength: int
    MinimumRoutingLayerCount: int
    MaximumRoutingLayerCount: int
    TechnologyFingerprint: str
    # A derived single-component slot assignment may reserve only the faces
    # which have an outward terminal aperture.  The legacy/default contract
    # remains a four-sided perimeter.
    ActivePerimeterFaces: tuple[str, ...] = (
        "north",
        "south",
        "west",
        "east",
    )
    # A complete, immutable measured launch-demand map for the active faces.
    # The empty default deliberately preserves the older aggregate
    # ``PeakBoundaryDemand`` derivation for callers which do not yet publish
    # physical face measurements.  When present, one ring track on one layer
    # carries one launch on a particular face, so the maximum face demand is
    # the capacity constraint rather than an assumed even split over faces.
    PerimeterFaceLaunchDemand: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        MinimumX, MinimumZ, MaximumX, MaximumZ = self.CoreBounds
        if self.ComponentCount < 1:
            raise ValueError("placement access demand requires a component")
        if self.TerminalCount < 0 or self.PeakBoundaryDemand < 0:
            raise ValueError("placement access demand cannot be negative")
        if MinimumX > MaximumX or MinimumZ > MaximumZ:
            raise ValueError("placement access bounds are inverted")
        if self.TrackPitch < 1 or self.AccessLength < 1:
            raise ValueError("placement access technology must be positive")
        if (
            self.MinimumRoutingLayerCount < 1
            or self.MaximumRoutingLayerCount
            < self.MinimumRoutingLayerCount
        ):
            raise ValueError("placement access layer bounds are invalid")
        ValidFaces = {"north", "south", "west", "east"}
        if (
            not self.ActivePerimeterFaces
            or len(self.ActivePerimeterFaces)
            != len(set(self.ActivePerimeterFaces))
            or any(Face not in ValidFaces for Face in self.ActivePerimeterFaces)
        ):
            raise ValueError("placement access perimeter faces are invalid")
        RawFaceLaunchDemand = self.PerimeterFaceLaunchDemand
        if isinstance(RawFaceLaunchDemand, Mapping):
            FaceLaunchDemandItems = tuple(RawFaceLaunchDemand.items())
        else:
            try:
                FaceLaunchDemandItems = tuple(RawFaceLaunchDemand)
            except TypeError as Error:
                raise ValueError(
                    "placement access face launch demand must be a mapping"
                ) from Error
        if not FaceLaunchDemandItems:
            # Normalize even an explicitly supplied empty mutable mapping so
            # the frozen demand contract never retains caller-owned state.
            object.__setattr__(self, "PerimeterFaceLaunchDemand", ())
            return
        NormalizedFaceLaunchDemand: dict[str, int] = {}
        for Item in FaceLaunchDemandItems:
            if not isinstance(Item, tuple) or len(Item) != 2:
                raise ValueError(
                    "placement access face launch demand entries are invalid"
                )
            Face, LaunchDemand = Item
            if (
                not isinstance(Face, str)
                or Face not in ValidFaces
                or Face not in self.ActivePerimeterFaces
                or not isinstance(LaunchDemand, int)
                or isinstance(LaunchDemand, bool)
                or LaunchDemand < 0
                or Face in NormalizedFaceLaunchDemand
            ):
                raise ValueError(
                    "placement access face launch demand entries are invalid"
                )
            NormalizedFaceLaunchDemand[Face] = LaunchDemand
        if set(NormalizedFaceLaunchDemand) != set(self.ActivePerimeterFaces):
            raise ValueError(
                "placement access face launch demand must cover active faces"
            )
        FaceOrder = ("north", "south", "west", "east")
        object.__setattr__(
            self,
            "PerimeterFaceLaunchDemand",
            tuple(
                (Face, NormalizedFaceLaunchDemand[Face])
                for Face in FaceOrder
                if Face in NormalizedFaceLaunchDemand
            ),
        )

    @property
    def CoreWidth(self) -> int:
        return self.CoreBounds[2] - self.CoreBounds[0] + 1

    @property
    def CoreDepth(self) -> int:
        return self.CoreBounds[3] - self.CoreBounds[1] + 1

    @property
    def DemandFingerprint(self) -> str:
        # Translation does not change the access problem.  Fingerprint the
        # normalized geometry and technology facts, while retaining absolute
        # bounds in diagnostics and in the emitted physical envelope.
        return BuildStableFingerprint({
            "ComponentCount": self.ComponentCount,
            "TerminalCount": self.TerminalCount,
            "PeakBoundaryDemand": self.PeakBoundaryDemand,
            "CoreWidth": self.CoreWidth,
            "CoreDepth": self.CoreDepth,
            "TrackPitch": self.TrackPitch,
            "AccessLength": self.AccessLength,
            "MinimumRoutingLayerCount": self.MinimumRoutingLayerCount,
            "MaximumRoutingLayerCount": self.MaximumRoutingLayerCount,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ActivePerimeterFaces": self.ActivePerimeterFaces,
            "PerimeterFaceLaunchDemand": self.PerimeterFaceLaunchDemand,
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ComponentCount": self.ComponentCount,
            "TerminalCount": self.TerminalCount,
            "PeakBoundaryDemand": self.PeakBoundaryDemand,
            "CoreBounds": list(self.CoreBounds),
            "CoreWidth": self.CoreWidth,
            "CoreDepth": self.CoreDepth,
            "TrackPitch": self.TrackPitch,
            "AccessLength": self.AccessLength,
            "MinimumRoutingLayerCount": self.MinimumRoutingLayerCount,
            "MaximumRoutingLayerCount": self.MaximumRoutingLayerCount,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ActivePerimeterFaces": list(self.ActivePerimeterFaces),
            "PerimeterFaceLaunchDemand": {
                Face: LaunchDemand
                for Face, LaunchDemand in self.PerimeterFaceLaunchDemand
            },
        }


@dataclass(frozen=True)
class DerivedRoutingEnvelope:
    """One routing-space contract derived from immutable physical demand."""

    Demand: PlacementAccessDemand
    RoutingLayerCount: int
    AccessRingTrackCount: int
    PermittedLayers: tuple[int, ...]

    @property
    def EnvelopeFingerprint(self) -> str:
        return BuildStableFingerprint((
            self.Demand.DemandFingerprint,
            self.RoutingLayerCount,
            self.AccessRingTrackCount,
            self.PermittedLayers,
        ))

    def __post_init__(self) -> None:
        if not (
            self.Demand.MinimumRoutingLayerCount
            <= self.RoutingLayerCount
            <= self.Demand.MaximumRoutingLayerCount
        ):
            raise ValueError("derived routing layer count is outside technology")
        if self.AccessRingTrackCount < 1:
            raise ValueError("derived access ring requires a track")
        if self.PermittedLayers != tuple(range(self.RoutingLayerCount)):
            raise ValueError("derived routing layers must be contiguous")

    @property
    def Name(self) -> str:
        return "derived-capacity"

    @property
    def ComponentSpacing(self) -> int:
        return self.Demand.TrackPitch * self.AccessRingTrackCount

    @property
    def BoundaryCorridorWidth(self) -> int:
        return self.AccessRingTrackCount

    @property
    def BoundaryCorridorPitch(self) -> int:
        return self.Demand.TrackPitch

    @property
    def AccessLength(self) -> int:
        return self.Demand.AccessLength

    @property
    def EnvelopeBounds(self) -> tuple[int, int, int, int]:
        MinimumX, MinimumZ, MaximumX, MaximumZ = self.Demand.CoreBounds
        return (
            MinimumX - (
                self.ComponentSpacing
                if "west" in self.Demand.ActivePerimeterFaces
                else 0
            ),
            MinimumZ - (
                self.ComponentSpacing
                if "north" in self.Demand.ActivePerimeterFaces
                else 0
            ),
            MaximumX + (
                self.ComponentSpacing
                if "east" in self.Demand.ActivePerimeterFaces
                else 0
            ),
            MaximumZ + (
                self.ComponentSpacing
                if "south" in self.Demand.ActivePerimeterFaces
                else 0
            ),
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Name": self.Name,
            "Demand": self.Demand.ToDictionary(),
            "ComponentSpacing": self.ComponentSpacing,
            "RoutingLayerCount": self.RoutingLayerCount,
            "PermittedLayers": list(self.PermittedLayers),
            "BoundaryCorridorWidth": self.BoundaryCorridorWidth,
            "BoundaryCorridorPitch": self.BoundaryCorridorPitch,
            "AccessLength": self.AccessLength,
            "AccessRingTrackCount": self.AccessRingTrackCount,
            "EnvelopeBounds": list(self.EnvelopeBounds),
            "EnvelopeFingerprint": self.EnvelopeFingerprint,
        }


@dataclass(frozen=True)
class DerivedPerimeterTerminalSlot:
    """One outward-facing terminal placement on a packed-core perimeter.

    The slot is placement geometry, not a routing candidate.  In particular,
    ``ConnectionDirection`` must point away from ``Face`` and remains frozen
    until the one later routing attempt consumes the assignment.
    """

    SlotId: str
    TerminalName: str
    Signal: str
    Face: str
    Origin: tuple[int, int, int]
    Rotation: int
    MirrorX: bool
    MacroBounds: tuple[int, int, int, int]
    ConnectionPin: tuple[int, int, int]
    ConnectionDirection: tuple[int, int, int]
    InteriorSpan: int

    def __post_init__(self) -> None:
        if self.Face not in {"north", "south", "west", "east"}:
            raise ValueError("perimeter slot requires a cardinal face")
        FaceDirections = {
            "north": (0, 0, -1),
            "south": (0, 0, 1),
            "west": (-1, 0, 0),
            "east": (1, 0, 0),
        }
        if self.ConnectionDirection != FaceDirections[self.Face]:
            raise ValueError(
                "perimeter slot connection direction must point outward"
            )
        MinimumX, MinimumZ, MaximumX, MaximumZ = self.MacroBounds
        if MinimumX > MaximumX or MinimumZ > MaximumZ:
            raise ValueError("perimeter slot bounds are inverted")
        if self.InteriorSpan < 0:
            raise ValueError("perimeter slot interior span cannot be negative")

    @property
    def Width(self) -> int:
        return self.MacroBounds[2] - self.MacroBounds[0] + 1

    @property
    def Depth(self) -> int:
        return self.MacroBounds[3] - self.MacroBounds[1] + 1

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SlotId": self.SlotId,
            "TerminalName": self.TerminalName,
            "Signal": self.Signal,
            "Face": self.Face,
            "Origin": list(self.Origin),
            "Rotation": self.Rotation,
            "MirrorX": self.MirrorX,
            "MacroBounds": list(self.MacroBounds),
            "ConnectionPin": list(self.ConnectionPin),
            "ConnectionDirection": list(self.ConnectionDirection),
            "InteriorSpan": self.InteriorSpan,
        }


@dataclass(frozen=True)
class DerivedPerimeterFaceReservation:
    """The selected terminal aperture range on one exterior face.

    ``NormalCoordinate`` is the terminal pin plane, rather than an inferred
    routing-track coordinate.  This lets the access fabric derive its own
    technology-pitched outward tracks while retaining a precise face contract.
    Bounds are inclusive throughout this contract.
    """

    Face: str
    NormalCoordinate: int
    LateralMinimum: int
    LateralMaximum: int
    TerminalNames: tuple[str, ...]
    SlotIds: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.Face not in {"north", "south", "west", "east"}:
            raise ValueError("perimeter face reservation requires a cardinal face")
        if self.LateralMinimum > self.LateralMaximum:
            raise ValueError("perimeter face reservation range is inverted")
        if len(self.TerminalNames) != len(self.SlotIds):
            raise ValueError("perimeter face reservation terminal/slot mismatch")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Face": self.Face,
            "NormalCoordinate": self.NormalCoordinate,
            "LateralMinimum": self.LateralMinimum,
            "LateralMaximum": self.LateralMaximum,
            "TerminalNames": list(self.TerminalNames),
            "SlotIds": list(self.SlotIds),
        }


@dataclass(frozen=True)
class DerivedPerimeterSlotDomain:
    """A finite, pre-routing portfolio of legal outward terminal slots."""

    CoreBounds: tuple[int, int, int, int]
    TerminalSlots: tuple[
        tuple[str, tuple[DerivedPerimeterTerminalSlot, ...]], ...
    ]
    IncompatibleSlotPairs: tuple[tuple[str, str], ...] = ()
    Complete: bool = True
    WorkCount: int = 0
    IncompleteReason: str = ""

    def __post_init__(self) -> None:
        MinimumX, MinimumZ, MaximumX, MaximumZ = self.CoreBounds
        if MinimumX > MaximumX or MinimumZ > MaximumZ:
            raise ValueError("perimeter slot domain core bounds are inverted")
        TerminalNames = tuple(Name for Name, _Slots in self.TerminalSlots)
        if len(TerminalNames) != len(set(TerminalNames)):
            raise ValueError("perimeter slot domain repeats a terminal")
        SlotIds = tuple(
            Slot.SlotId
            for _TerminalName, Slots in self.TerminalSlots
            for Slot in Slots
        )
        if len(SlotIds) != len(set(SlotIds)):
            raise ValueError("perimeter slot domain repeats a slot identity")
        KnownSlotIds = frozenset(SlotIds)
        for First, Second in self.IncompatibleSlotPairs:
            if First >= Second:
                raise ValueError("perimeter slot incompatibilities must be ordered")
            if First not in KnownSlotIds or Second not in KnownSlotIds:
                raise ValueError("perimeter slot incompatibility references no slot")
        if self.WorkCount < 0:
            raise ValueError("perimeter slot work count cannot be negative")
        if self.Complete and self.IncompleteReason:
            raise ValueError("complete perimeter slot domain has an incomplete reason")

    @property
    def DomainFingerprint(self) -> str:
        return BuildStableFingerprint({
            "CoreDimensions": (
                self.CoreBounds[2] - self.CoreBounds[0] + 1,
                self.CoreBounds[3] - self.CoreBounds[1] + 1,
            ),
            "TerminalSlots": tuple(
                (
                    TerminalName,
                    tuple(
                        (
                            Slot.SlotId,
                            Slot.Face,
                            Slot.Rotation,
                            Slot.MirrorX,
                            (
                                Slot.MacroBounds[2] - Slot.MacroBounds[0] + 1,
                                Slot.MacroBounds[3] - Slot.MacroBounds[1] + 1,
                            ),
                            Slot.ConnectionDirection,
                            Slot.InteriorSpan,
                        )
                        for Slot in Slots
                    ),
                )
                for TerminalName, Slots in self.TerminalSlots
            ),
            "IncompatibleSlotPairs": self.IncompatibleSlotPairs,
            "Complete": self.Complete,
        })

    @property
    def SlotCount(self) -> int:
        return sum(len(Slots) for _Name, Slots in self.TerminalSlots)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CoreBounds": list(self.CoreBounds),
            "TerminalSlots": {
                Name: [Slot.ToDictionary() for Slot in Slots]
                for Name, Slots in self.TerminalSlots
            },
            "IncompatibleSlotPairs": [list(Pair) for Pair in self.IncompatibleSlotPairs],
            "Complete": self.Complete,
            "WorkCount": self.WorkCount,
            "IncompleteReason": self.IncompleteReason,
            "SlotCount": self.SlotCount,
            "DomainFingerprint": self.DomainFingerprint,
        }


@dataclass(frozen=True)
class DerivedPerimeterSlotAssignment:
    """One frozen selection from a derived perimeter slot domain."""

    DomainFingerprint: str
    AssignmentFingerprint: str
    CoreBounds: tuple[int, int, int, int]
    SelectedSlots: tuple[DerivedPerimeterTerminalSlot, ...]
    FaceReservations: tuple[DerivedPerimeterFaceReservation, ...]
    Bounds: tuple[int, int, int, int]
    Objective: tuple[Any, ...]
    ExpansionCount: int
    Success: bool
    Complete: bool
    IncompleteReason: str = ""

    def __post_init__(self) -> None:
        MinimumX, MinimumZ, MaximumX, MaximumZ = self.Bounds
        if MinimumX > MaximumX or MinimumZ > MaximumZ:
            raise ValueError("perimeter slot assignment bounds are inverted")
        if self.ExpansionCount < 0:
            raise ValueError("perimeter slot assignment expansion count cannot be negative")
        if self.Success and not self.SelectedSlots:
            raise ValueError("successful perimeter slot assignment needs slots")
        if self.Success and not self.Complete:
            raise ValueError("successful perimeter slot assignment must be complete")
        if self.Success and self.IncompleteReason:
            raise ValueError("successful perimeter slot assignment has incomplete reason")

    @property
    def Width(self) -> int:
        return self.Bounds[2] - self.Bounds[0] + 1

    @property
    def Depth(self) -> int:
        return self.Bounds[3] - self.Bounds[1] + 1

    @property
    def XzFootprint(self) -> int:
        return self.Width * self.Depth

    def ToDictionary(self) -> dict[str, object]:
        return {
            "DomainFingerprint": self.DomainFingerprint,
            "AssignmentFingerprint": self.AssignmentFingerprint,
            "CoreBounds": list(self.CoreBounds),
            "SelectedSlots": [Slot.ToDictionary() for Slot in self.SelectedSlots],
            "FaceReservations": [
                Reservation.ToDictionary()
                for Reservation in self.FaceReservations
            ],
            "Bounds": list(self.Bounds),
            "Width": self.Width,
            "Depth": self.Depth,
            "XzFootprint": self.XzFootprint,
            "Objective": list(self.Objective),
            "ExpansionCount": self.ExpansionCount,
            "Success": self.Success,
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
        }


@dataclass(frozen=True)
class DerivedPerimeterInterfaceTemplate:
    """One fixed compact I/O geometry member before access materialization."""

    TemplateId: str
    SlotAssignment: DerivedPerimeterSlotAssignment
    FaceSignature: tuple[int, int, int, int]
    Objective: tuple[Any, ...]

    def __post_init__(self) -> None:
        if not self.TemplateId:
            raise ValueError("derived interface template requires an id")
        if not self.SlotAssignment.Success or not self.SlotAssignment.Complete:
            raise ValueError("derived interface template requires a complete slot assignment")
        if len(self.FaceSignature) != 4 or any(Value < 0 for Value in self.FaceSignature):
            raise ValueError("derived interface template face signature is invalid")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "SlotAssignmentFingerprint": self.SlotAssignment.AssignmentFingerprint,
            "FaceSignature": list(self.FaceSignature),
            "Objective": list(self.Objective),
            "Bounds": list(self.SlotAssignment.Bounds),
        }


@dataclass(frozen=True)
class DerivedPerimeterInterfaceTemplateDomain:
    """A bounded fixed portfolio of compact terminal geometries.

    Members are generated before routing from terminal face-demand profiles.
    Each profile retains the lexicographically smallest exact legal slot
    layout.  This removes lateral slot permutations that do not change the
    bounded profile's ring demand while retaining physically distinct face
    capacity choices.  The domain is not exhaustive over every lateral slot
    permutation, so rejecting all of it remains typed ``incomplete`` upstream
    rather than UNSAT.
    """

    SlotDomainFingerprint: str
    Templates: tuple[DerivedPerimeterInterfaceTemplate, ...]
    ExpansionCount: int
    Complete: bool
    IncompleteReason: str = ""

    def __post_init__(self) -> None:
        TemplateIds = tuple(Value.TemplateId for Value in self.Templates)
        if len(TemplateIds) != len(set(TemplateIds)):
            raise ValueError("derived interface template domain repeats an id")
        if self.ExpansionCount < 0:
            raise ValueError("derived interface template expansion count is invalid")
        if self.Complete and self.IncompleteReason:
            raise ValueError("complete interface template domain has an incomplete reason")

    @property
    def DomainFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "derived-perimeter-interface-template-domain-v1",
            "SlotDomainFingerprint": self.SlotDomainFingerprint,
            "Templates": [Value.ToDictionary() for Value in self.Templates],
            "Complete": self.Complete,
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "DomainFingerprint": self.DomainFingerprint,
            "SlotDomainFingerprint": self.SlotDomainFingerprint,
            "TemplateCount": len(self.Templates),
            "Templates": [Value.ToDictionary() for Value in self.Templates],
            "ExpansionCount": self.ExpansionCount,
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "NonExhaustive": True,
        }


def BuildDerivedPerimeterFaceReservations(
    Slots: Iterable[DerivedPerimeterTerminalSlot],
) -> tuple[DerivedPerimeterFaceReservation, ...]:
    """Summarize frozen slots into exact face apertures for access routing."""
    ByFace: dict[str, list[DerivedPerimeterTerminalSlot]] = {}
    for Slot in Slots:
        ByFace.setdefault(Slot.Face, []).append(Slot)
    Reservations = []
    for Face in ("north", "south", "west", "east"):
        FaceSlots = tuple(sorted(
            ByFace.get(Face, ()),
            key=lambda Slot: (Slot.SlotId, Slot.TerminalName),
        ))
        if not FaceSlots:
            continue
        if Face in {"north", "south"}:
            NormalCoordinates = {Slot.ConnectionPin[2] for Slot in FaceSlots}
            LateralMinimum = min(
                Slot.MacroBounds[0] for Slot in FaceSlots
            )
            LateralMaximum = max(
                Slot.MacroBounds[2] for Slot in FaceSlots
            )
        else:
            NormalCoordinates = {Slot.ConnectionPin[0] for Slot in FaceSlots}
            LateralMinimum = min(
                Slot.MacroBounds[1] for Slot in FaceSlots
            )
            LateralMaximum = max(
                Slot.MacroBounds[3] for Slot in FaceSlots
            )
        # A face can contain macros of different shapes, but all outward
        # pins must lie on one aperture plane for the access contract.
        if len(NormalCoordinates) != 1:
            raise ValueError("perimeter face slots must share a pin plane")
        Reservations.append(DerivedPerimeterFaceReservation(
            Face=Face,
            NormalCoordinate=next(iter(NormalCoordinates)),
            LateralMinimum=LateralMinimum,
            LateralMaximum=LateralMaximum,
            TerminalNames=tuple(Slot.TerminalName for Slot in FaceSlots),
            SlotIds=tuple(Slot.SlotId for Slot in FaceSlots),
        ))
    return tuple(Reservations)


def BuildDerivedPerimeterInterfaceTemplateDomain(
    Domain: DerivedPerimeterSlotDomain,
    MaximumExpansions: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> DerivedPerimeterInterfaceTemplateDomain:
    """Build fixed terminal-face representatives before access/routing work."""
    if MaximumExpansions < 1:
        raise ValueError("derived interface template domain requires a work cap")
    Incompatible = frozenset(Domain.IncompatibleSlotPairs)
    OrderedDomains = tuple(
        (
            Name,
            tuple(
                min(
                    (Slot for Slot in Slots if Slot.Face == Face),
                    key=lambda Slot: (
                        Slot.InteriorSpan, Slot.MacroBounds,
                        Slot.Rotation, Slot.MirrorX, Slot.SlotId,
                    ),
                )
                for Face in ("north", "south", "west", "east")
                if any(Slot.Face == Face for Slot in Slots)
            ),
        )
        for Name, Slots in sorted(Domain.TerminalSlots)
    )
    BestByFaceSignature: dict[
        tuple[int, int, int, int],
        tuple[tuple[Any, ...], tuple[DerivedPerimeterTerminalSlot, ...]],
    ] = {}
    ExpansionCount = 0
    ExhaustedWork = False

    def BoundsFor(Slots: tuple[DerivedPerimeterTerminalSlot, ...]) -> tuple[int, int, int, int]:
        MinimumX, MinimumZ, MaximumX, MaximumZ = Domain.CoreBounds
        for Slot in Slots:
            MinimumX = min(MinimumX, Slot.MacroBounds[0])
            MinimumZ = min(MinimumZ, Slot.MacroBounds[1])
            MaximumX = max(MaximumX, Slot.MacroBounds[2])
            MaximumZ = max(MaximumZ, Slot.MacroBounds[3])
        return MinimumX, MinimumZ, MaximumX, MaximumZ

    def ObjectiveFor(Slots: tuple[DerivedPerimeterTerminalSlot, ...]) -> tuple[Any, ...]:
        MinimumX, MinimumZ, MaximumX, MaximumZ = BoundsFor(Slots)
        Width = MaximumX - MinimumX + 1
        Depth = MaximumZ - MinimumZ + 1
        FaceSignature = tuple(
            sum(Slot.Face == Face for Slot in Slots)
            for Face in ("north", "south", "west", "east")
        )
        return (
            Width * Depth,
            max(Width, Depth),
            sum(Count * Count for Count in FaceSignature),
            sum(Slot.InteriorSpan for Slot in Slots),
            tuple(Slot.SlotId for Slot in Slots),
        )

    def Search(Index: int, Selected: tuple[DerivedPerimeterTerminalSlot, ...]) -> None:
        nonlocal ExpansionCount, ExhaustedWork
        if ExhaustedWork:
            return
        if Index == len(OrderedDomains):
            OrderedSlots = tuple(sorted(Selected, key=lambda Slot: Slot.TerminalName))
            FaceSignature = tuple(
                sum(Slot.Face == Face for Slot in OrderedSlots)
                for Face in ("north", "south", "west", "east")
            )
            Objective = ObjectiveFor(OrderedSlots)
            Existing = BestByFaceSignature.get(FaceSignature)
            if Existing is None or Objective < Existing[0]:
                BestByFaceSignature[FaceSignature] = (
                    Objective,
                    OrderedSlots,
                )
            return
        _TerminalName, Slots = OrderedDomains[Index]
        for Slot in Slots:
            if ExpansionCount >= MaximumExpansions:
                ExhaustedWork = True
                return
            ExpansionCount += 1
            if WorkCheck is not None:
                WorkCheck({"Phase": "derived-perimeter-interface-template-domain", "ExpansionCount": ExpansionCount, "TerminalName": Slot.TerminalName})
            if any(tuple(sorted((Slot.SlotId, Existing.SlotId))) in Incompatible for Existing in Selected):
                continue
            Search(Index + 1, (*Selected, Slot))
            if ExhaustedWork:
                return

    Search(0, ())
    Templates = []
    for Index, (FaceSignature, (Objective, Slots)) in enumerate(sorted(
        BestByFaceSignature.items(),
        key=lambda Value: (Value[1][0], Value[0]),
    )):
        Bounds = BoundsFor(Slots)
        Reservations = BuildDerivedPerimeterFaceReservations(Slots)
        Assignment = DerivedPerimeterSlotAssignment(
            DomainFingerprint=Domain.DomainFingerprint,
            AssignmentFingerprint=BuildStableFingerprint({
                "DomainFingerprint": Domain.DomainFingerprint,
                "SelectedSlots": tuple(Slot.SlotId for Slot in Slots),
                "FaceReservations": tuple((Value.Face, Value.NormalCoordinate, Value.LateralMinimum, Value.LateralMaximum, Value.SlotIds) for Value in Reservations),
            }),
            CoreBounds=Domain.CoreBounds,
            SelectedSlots=Slots,
            FaceReservations=Reservations,
            Bounds=Bounds,
            Objective=Objective,
            ExpansionCount=ExpansionCount,
            Success=True,
            Complete=True,
        )
        Templates.append(DerivedPerimeterInterfaceTemplate(
            TemplateId=f"interface-face-{''.join(map(str, FaceSignature))}-{Index}",
            SlotAssignment=Assignment,
            FaceSignature=FaceSignature,
            Objective=Objective,
        ))
    Complete = Domain.Complete and not ExhaustedWork
    return DerivedPerimeterInterfaceTemplateDomain(
        SlotDomainFingerprint=Domain.DomainFingerprint,
        Templates=tuple(Templates),
        ExpansionCount=ExpansionCount,
        Complete=Complete,
        IncompleteReason="work-cap" if ExhaustedWork else Domain.IncompleteReason,
    )


def SolveDerivedPerimeterSlotDomain(
    Domain: DerivedPerimeterSlotDomain,
    MaximumExpansions: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> DerivedPerimeterSlotAssignment:
    """Choose one compact, pairwise-legal terminal slot assignment once.

    This is intentionally only a placement-domain solve.  It does not grow a
    domain, release a local claim, or route a signal.  A bounded exploration
    without a selected complete assignment remains typed ``incomplete``.
    """
    if MaximumExpansions < 1:
        raise ValueError("perimeter slot solve requires a positive work cap")

    Incompatible = frozenset(Domain.IncompatibleSlotPairs)
    OrderedDomains = tuple(
        (Name, tuple(sorted(Slots, key=lambda Slot: (
            Slot.InteriorSpan,
            Slot.Face,
            Slot.MacroBounds,
            Slot.Rotation,
            Slot.MirrorX,
            Slot.SlotId,
        ))))
        for Name, Slots in sorted(Domain.TerminalSlots)
    )
    ExpansionCount = 0
    ExhaustedWork = False
    BestSlots: tuple[DerivedPerimeterTerminalSlot, ...] | None = None
    BestObjective: tuple[Any, ...] | None = None

    def BoundsFor(
        Slots: tuple[DerivedPerimeterTerminalSlot, ...],
    ) -> tuple[int, int, int, int]:
        Bounds = (Domain.CoreBounds[0], Domain.CoreBounds[1],
                  Domain.CoreBounds[2], Domain.CoreBounds[3])
        for Slot in Slots:
            Bounds = (
                min(Bounds[0], Slot.MacroBounds[0]),
                min(Bounds[1], Slot.MacroBounds[1]),
                max(Bounds[2], Slot.MacroBounds[2]),
                max(Bounds[3], Slot.MacroBounds[3]),
            )
        return Bounds

    def ObjectiveFor(
        Slots: tuple[DerivedPerimeterTerminalSlot, ...],
    ) -> tuple[Any, ...]:
        MinimumX, MinimumZ, MaximumX, MaximumZ = BoundsFor(Slots)
        Width = MaximumX - MinimumX + 1
        Depth = MaximumZ - MinimumZ + 1
        FaceCounts = tuple(
            sum(Slot.Face == Face for Slot in Slots)
            for Face in ("north", "south", "west", "east")
        )
        return (
            Width * Depth,
            max(Width, Depth),
            max(FaceCounts, default=0),
            sum(Count * Count for Count in FaceCounts),
            sum(Slot.InteriorSpan for Slot in Slots),
            tuple(Slot.SlotId for Slot in Slots),
        )

    SuffixRequiredMaximumX = [-(1 << 60)] * (len(OrderedDomains) + 1)
    SuffixRequiredMaximumZ = [-(1 << 60)] * (len(OrderedDomains) + 1)
    SuffixRequiredMinimumX = [1 << 60] * (len(OrderedDomains) + 1)
    SuffixRequiredMinimumZ = [1 << 60] * (len(OrderedDomains) + 1)
    for DomainIndex in range(len(OrderedDomains) - 1, -1, -1):
        _TerminalName, Slots = OrderedDomains[DomainIndex]
        if not Slots:
            continue
        SuffixRequiredMaximumX[DomainIndex] = max(
            min(Slot.MacroBounds[2] for Slot in Slots),
            SuffixRequiredMaximumX[DomainIndex + 1],
        )
        SuffixRequiredMaximumZ[DomainIndex] = max(
            min(Slot.MacroBounds[3] for Slot in Slots),
            SuffixRequiredMaximumZ[DomainIndex + 1],
        )
        SuffixRequiredMinimumX[DomainIndex] = min(
            max(Slot.MacroBounds[0] for Slot in Slots),
            SuffixRequiredMinimumX[DomainIndex + 1],
        )
        SuffixRequiredMinimumZ[DomainIndex] = min(
            max(Slot.MacroBounds[1] for Slot in Slots),
            SuffixRequiredMinimumZ[DomainIndex + 1],
        )

    def BoundsLowerBound(
        Index: int,
        Bounds: tuple[int, int, int, int],
    ) -> tuple[int, int]:
        """Return an admissible final hull prefix for this partial branch.

        Every unassigned terminal must choose a slot.  Its lowest possible
        maximum coordinate and highest possible minimum coordinate bound the
        final hull even when the choices are considered independently.  The
        result is deliberately weak rather than heuristic: it is safe to use
        as a proof-pruning bound for the lexicographic footprint objective.
        """
        if any(not Slots for _TerminalName, Slots in OrderedDomains[Index:]):
            return (1 << 60, 1 << 60)
        MinimumX, MinimumZ, MaximumX, MaximumZ = Bounds
        MaximumX = max(MaximumX, SuffixRequiredMaximumX[Index])
        MaximumZ = max(MaximumZ, SuffixRequiredMaximumZ[Index])
        MinimumX = min(MinimumX, SuffixRequiredMinimumX[Index])
        MinimumZ = min(MinimumZ, SuffixRequiredMinimumZ[Index])
        Width = MaximumX - MinimumX + 1
        Depth = MaximumZ - MinimumZ + 1
        return (Width * Depth, max(Width, Depth))

    def PairIsCompatible(
        Slot: DerivedPerimeterTerminalSlot,
        Selected: tuple[DerivedPerimeterTerminalSlot, ...],
    ) -> bool:
        return all(
            tuple(sorted((Slot.SlotId, Existing.SlotId))) not in Incompatible
            for Existing in Selected
        )

    def Search(
        Index: int,
        Selected: tuple[DerivedPerimeterTerminalSlot, ...],
        Bounds: tuple[int, int, int, int],
        FaceCounts: tuple[int, int, int, int],
        InteriorSpan: int,
    ) -> None:
        nonlocal BestSlots, BestObjective, ExpansionCount, ExhaustedWork
        if ExhaustedWork:
            return
        if Index == len(OrderedDomains):
            MinimumX, MinimumZ, MaximumX, MaximumZ = Bounds
            Width = MaximumX - MinimumX + 1
            Depth = MaximumZ - MinimumZ + 1
            Objective = (
                Width * Depth,
                max(Width, Depth),
                max(FaceCounts, default=0),
                sum(Count * Count for Count in FaceCounts),
                InteriorSpan,
                tuple(Slot.SlotId for Slot in Selected),
            )
            if BestObjective is None or Objective < BestObjective:
                BestObjective = Objective
                BestSlots = Selected
            return
        if (
            BestObjective is not None
            and BoundsLowerBound(Index, Bounds) > BestObjective[:2]
        ):
            return
        _TerminalName, Slots = OrderedDomains[Index]
        def SlotSearchKey(
            Slot: DerivedPerimeterTerminalSlot,
        ) -> tuple[Any, ...]:
            CandidateBounds = (
                min(Bounds[0], Slot.MacroBounds[0]),
                min(Bounds[1], Slot.MacroBounds[1]),
                max(Bounds[2], Slot.MacroBounds[2]),
                max(Bounds[3], Slot.MacroBounds[3]),
            )
            CandidateWidth = CandidateBounds[2] - CandidateBounds[0] + 1
            CandidateDepth = CandidateBounds[3] - CandidateBounds[1] + 1
            FaceIndex = ("north", "south", "west", "east").index(
                Slot.Face
            )
            CandidateFaceCounts = tuple(
                Count + int(ValueIndex == FaceIndex)
                for ValueIndex, Count in enumerate(FaceCounts)
            )
            CandidateObjective = (
                CandidateWidth * CandidateDepth,
                max(CandidateWidth, CandidateDepth),
                max(CandidateFaceCounts, default=0),
                sum(Count * Count for Count in CandidateFaceCounts),
                InteriorSpan + Slot.InteriorSpan,
            )
            CandidateLowerBound = BoundsLowerBound(
                Index + 1,
                CandidateBounds,
            )
            return (
                CandidateLowerBound,
                CandidateObjective[:5],
                Slot.SlotId,
            )
        for Slot in sorted(Slots, key=SlotSearchKey):
            if ExpansionCount >= MaximumExpansions:
                ExhaustedWork = True
                return
            ExpansionCount += 1
            if WorkCheck is not None and ExpansionCount % 64 == 1:
                WorkCheck({
                    "Phase": "derived-perimeter-slot-assignment",
                    "ExpansionCount": ExpansionCount,
                    "TerminalName": Slot.TerminalName,
                })
            if not PairIsCompatible(Slot, Selected):
                continue
            CandidateBounds = (
                min(Bounds[0], Slot.MacroBounds[0]),
                min(Bounds[1], Slot.MacroBounds[1]),
                max(Bounds[2], Slot.MacroBounds[2]),
                max(Bounds[3], Slot.MacroBounds[3]),
            )
            FaceIndex = ("north", "south", "west", "east").index(
                Slot.Face
            )
            CandidateFaceCounts = tuple(
                Count + int(ValueIndex == FaceIndex)
                for ValueIndex, Count in enumerate(FaceCounts)
            )
            Search(
                Index + 1,
                (*Selected, Slot),
                CandidateBounds,
                CandidateFaceCounts,
                InteriorSpan + Slot.InteriorSpan,
            )
            if ExhaustedWork:
                return

    Search(0, (), Domain.CoreBounds, (0, 0, 0, 0), 0)
    if BestSlots is not None and not ExhaustedWork:
        OrderedSlots = tuple(sorted(BestSlots, key=lambda Slot: Slot.TerminalName))
        Bounds = BoundsFor(OrderedSlots)
        Objective = ObjectiveFor(OrderedSlots)
        AssignmentFingerprint = BuildStableFingerprint({
            "DomainFingerprint": Domain.DomainFingerprint,
            "SelectedSlots": tuple(Slot.SlotId for Slot in OrderedSlots),
            "FaceReservations": tuple(
                (
                    Reservation.Face,
                    Reservation.NormalCoordinate,
                    Reservation.LateralMinimum,
                    Reservation.LateralMaximum,
                    Reservation.SlotIds,
                )
                for Reservation in BuildDerivedPerimeterFaceReservations(
                    OrderedSlots
                )
            ),
        })
        return DerivedPerimeterSlotAssignment(
            DomainFingerprint=Domain.DomainFingerprint,
            AssignmentFingerprint=AssignmentFingerprint,
            CoreBounds=Domain.CoreBounds,
            SelectedSlots=OrderedSlots,
            FaceReservations=BuildDerivedPerimeterFaceReservations(OrderedSlots),
            Bounds=Bounds,
            Objective=Objective,
            ExpansionCount=ExpansionCount,
            Success=True,
            Complete=True,
        )
    Complete = Domain.Complete and not ExhaustedWork
    return DerivedPerimeterSlotAssignment(
        DomainFingerprint=Domain.DomainFingerprint,
        AssignmentFingerprint="",
        CoreBounds=Domain.CoreBounds,
        SelectedSlots=(),
        FaceReservations=(),
        Bounds=Domain.CoreBounds,
        Objective=(),
        ExpansionCount=ExpansionCount,
        Success=False,
        Complete=Complete,
        IncompleteReason=(
            "work-cap"
            if ExhaustedWork
            else Domain.IncompleteReason
            if not Domain.Complete
            else "complete-empty-domain"
        ),
    )


def DeriveRoutingEnvelopes(
    Demand: PlacementAccessDemand,
) -> tuple[DerivedRoutingEnvelope, ...]:
    """Materialize the technology-supported envelope domain once."""
    PerimeterSlotsPerTrack = sum(
        max(
            1,
            (
                Demand.CoreWidth
                if Face in {"north", "south"}
                else Demand.CoreDepth
            ) // Demand.TrackPitch,
        )
        for Face in Demand.ActivePerimeterFaces
    )
    Result = []
    for LayerCount in range(
        Demand.MinimumRoutingLayerCount,
        Demand.MaximumRoutingLayerCount + 1,
    ):
        if Demand.PerimeterFaceLaunchDemand:
            # Face measurements describe the actual fixed launch contract.
            # A congested face cannot borrow capacity from an unused one, so
            # choose the greatest measured face requirement instead of
            # averaging aggregate demand over all active faces.
            LaunchDemandTracks = max(
                ceil(LaunchDemand / LayerCount)
                for _, LaunchDemand in Demand.PerimeterFaceLaunchDemand
            )
        else:
            # Compatibility path for unmeasured legacy callers.
            LaunchDemandTracks = ceil(
                Demand.PeakBoundaryDemand
                / max(1, len(Demand.ActivePerimeterFaces) * LayerCount)
            )
        DemandTracks = max(
            LaunchDemandTracks,
            ceil(
                Demand.TerminalCount
                / max(1, PerimeterSlotsPerTrack * LayerCount)
            ),
        )
        Result.append(DerivedRoutingEnvelope(
            Demand=Demand,
            RoutingLayerCount=LayerCount,
            AccessRingTrackCount=max(1, DemandTracks),
            PermittedLayers=tuple(range(LayerCount)),
        ))
    return tuple(Result)


@dataclass(frozen=True)
class DerivedPlacementCandidate:
    """One finite placed geometry entering the pre-route capacity solve."""

    CandidateId: str
    GeometryFingerprint: str
    Bounds: tuple[int, int, int, int]
    RoutingEnvelope: DerivedRoutingEnvelope
    Complete: bool
    WorkCount: int = 0
    IncompleteReason: str = ""

    @property
    def Width(self) -> int:
        return self.Bounds[2] - self.Bounds[0] + 1

    @property
    def Depth(self) -> int:
        return self.Bounds[3] - self.Bounds[1] + 1

    @property
    def XzFootprint(self) -> int:
        return self.Width * self.Depth

    @property
    def ObjectivePrefix(self) -> tuple[int, ...]:
        return (
            self.XzFootprint,
            max(self.Width, self.Depth),
            self.RoutingEnvelope.RoutingLayerCount,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CandidateId": self.CandidateId,
            "GeometryFingerprint": self.GeometryFingerprint,
            "Bounds": list(self.Bounds),
            "Width": self.Width,
            "Depth": self.Depth,
            "XzFootprint": self.XzFootprint,
            "Complete": self.Complete,
            "WorkCount": self.WorkCount,
            "IncompleteReason": self.IncompleteReason,
            "RoutingEnvelope": self.RoutingEnvelope.ToDictionary(),
        }


@dataclass(frozen=True)
class PreRouteInterfaceWitness:
    """One frozen local access/seam witness for a template."""

    WitnessId: str
    CapacityResourceIds: tuple[str, ...]
    Objective: tuple[int, ...]
    FrozenContract: Any = None


@dataclass(frozen=True)
class PreRouteInterfaceTemplate:
    """One immutable geometry and its finite local interface witnesses."""

    ComponentId: str
    TemplateId: str
    GeometryFingerprint: str
    LocalClaimsFingerprint: str
    TerminalDomainFingerprint: str
    SeamDomainFingerprint: str
    DerivedPlacement: DerivedPlacementCandidate
    RoutingEnvelope: DerivedRoutingEnvelope
    Witnesses: tuple[PreRouteInterfaceWitness, ...]
    Complete: bool
    AccessRingTrackCount: int = 0
    AccessRingFingerprint: str = ""
    IncompleteReason: str = ""


@dataclass(frozen=True)
class PreRouteInterfaceProblem:
    """Bounded component/template capacity problem before global routing."""

    Templates: tuple[PreRouteInterfaceTemplate, ...]
    NonExhaustiveDomain: bool = True
    MaximumExpansions: int = 50_000

    @property
    def ProblemFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Templates": [
                {
                    "ComponentId": Value.ComponentId,
                    "TemplateId": Value.TemplateId,
                    "GeometryFingerprint": Value.GeometryFingerprint,
                    "LocalClaimsFingerprint": Value.LocalClaimsFingerprint,
                    "TerminalDomainFingerprint": Value.TerminalDomainFingerprint,
                    "SeamDomainFingerprint": Value.SeamDomainFingerprint,
                    "DerivedPlacement": Value.DerivedPlacement.ToDictionary(),
                    "RoutingEnvelope": Value.RoutingEnvelope.ToDictionary(),
                    "AccessRingTrackCount": Value.AccessRingTrackCount,
                    "AccessRingFingerprint": Value.AccessRingFingerprint,
                    "Witnesses": [
                        {
                            "WitnessId": Witness.WitnessId,
                            "CapacityResourceIds": list(
                                Witness.CapacityResourceIds
                            ),
                            "Objective": list(Witness.Objective),
                        }
                        for Witness in Value.Witnesses
                    ],
                    "Complete": Value.Complete,
                    "IncompleteReason": Value.IncompleteReason,
                }
                for Value in self.Templates
            ],
            "NonExhaustiveDomain": self.NonExhaustiveDomain,
            "MaximumExpansions": self.MaximumExpansions,
        })


@dataclass(frozen=True)
class PreRouteInterfaceSelection:
    """Terminal result of one bounded pre-route interface solve."""

    ProblemFingerprint: str
    SelectionFingerprint: str
    SelectedTemplateIds: tuple[tuple[str, str], ...]
    SelectedWitnessIds: tuple[tuple[str, str], ...]
    Objective: tuple[int, ...]
    ExpansionCount: int
    Success: bool
    Complete: bool
    Unsatisfiable: bool
    IncompleteReason: str = ""
    FirstConflictResourceIds: tuple[str, ...] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ProblemFingerprint": self.ProblemFingerprint,
            "SelectionFingerprint": self.SelectionFingerprint,
            "SelectedTemplateIds": [list(Value) for Value in self.SelectedTemplateIds],
            "SelectedWitnessIds": [list(Value) for Value in self.SelectedWitnessIds],
            "Objective": list(self.Objective),
            "ExpansionCount": self.ExpansionCount,
            "Success": self.Success,
            "Complete": self.Complete,
            "Unsatisfiable": self.Unsatisfiable,
            "IncompleteReason": self.IncompleteReason,
            "FirstConflictResourceIds": list(self.FirstConflictResourceIds),
        }


def SolvePreRouteInterfaceProblem(
    Problem: PreRouteInterfaceProblem,
    *,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PreRouteInterfaceSelection:
    """Select compatible local contracts without enumerating a cross-product.

    The depth-first frontier carries only owned capacity resources and the
    selected contract identity.  It is therefore also suitable for CLA
    component factors: callers publish a finite witness set per component
    rather than materialising every whole-design placement combination.
    """
    if Problem.MaximumExpansions < 1:
        raise ValueError("pre-route interface selection requires a work cap")
    ByComponent: dict[str, list[PreRouteInterfaceTemplate]] = {}
    for Template in Problem.Templates:
        ByComponent.setdefault(Template.ComponentId, []).append(Template)
    Components = tuple(sorted(ByComponent))
    OrderedDomains = tuple(
        tuple(sorted(
            ByComponent[Component],
            key=lambda Value: (
                Value.TemplateId,
                Value.GeometryFingerprint,
            ),
        ))
        for Component in Components
    )
    DomainComplete = all(
        Template.Complete
        for Domain in OrderedDomains
        for Template in Domain
    )
    Expansions = 0
    FirstConflict: tuple[str, ...] = ()
    Best: tuple[
        tuple[int, ...],
        tuple[tuple[str, str], ...],
        tuple[tuple[str, str], ...],
    ] | None = None

    def Visit(
        Index: int,
        UsedResources: frozenset[str],
        Objective: tuple[int, ...],
        TemplateIds: tuple[tuple[str, str], ...],
        WitnessIds: tuple[tuple[str, str], ...],
    ) -> bool:
        nonlocal Expansions, FirstConflict, Best
        if Expansions >= Problem.MaximumExpansions:
            return False
        if WorkCheck is not None:
            WorkCheck({
                "ExpansionCount": Expansions,
                "FrontierComponentIndex": Index,
                "ComponentCount": len(Components),
            })
        if Index == len(Components):
            Candidate = (Objective, TemplateIds, WitnessIds)
            if Best is None or Candidate < Best:
                Best = Candidate
            return True
        Complete = True
        Component = Components[Index]
        for Template in OrderedDomains[Index]:
            if not Template.Complete:
                # Domain completeness is classified separately from search
                # completion.  Visiting an explicitly incomplete member is
                # not a work-cap exhaustion.
                continue
            for Witness in sorted(
                Template.Witnesses,
                key=lambda Value: (Value.Objective, Value.WitnessId),
            ):
                Expansions += 1
                ResourceSet = frozenset(Witness.CapacityResourceIds)
                Conflict = tuple(sorted(ResourceSet & UsedResources))
                if Conflict:
                    if not FirstConflict:
                        FirstConflict = Conflict
                    continue
                if not Visit(
                    Index + 1,
                    UsedResources | ResourceSet,
                    tuple(
                        (Objective[Value] if Value < len(Objective) else 0)
                        + (
                            Witness.Objective[Value]
                            if Value < len(Witness.Objective)
                            else 0
                        )
                        for Value in range(max(len(Objective), len(Witness.Objective)))
                    ),
                    (*TemplateIds, (Component, Template.TemplateId)),
                    (*WitnessIds, (Component, Witness.WitnessId)),
                ):
                    Complete = False
                if Expansions >= Problem.MaximumExpansions:
                    Complete = False
                    break
        return Complete

    SearchComplete = Visit(0, frozenset(), (), (), ())
    if Best is not None and SearchComplete:
        Objective, TemplateIds, WitnessIds = Best
        SelectionFingerprint = BuildStableFingerprint({
            "ProblemFingerprint": Problem.ProblemFingerprint,
            "TemplateIds": TemplateIds,
            "WitnessIds": WitnessIds,
        })
        return PreRouteInterfaceSelection(
            ProblemFingerprint=Problem.ProblemFingerprint,
            SelectionFingerprint=SelectionFingerprint,
            SelectedTemplateIds=TemplateIds,
            SelectedWitnessIds=WitnessIds,
            Objective=Objective,
            ExpansionCount=Expansions,
            Success=True,
            # A selected complete witness is sufficient to freeze one route.
            # An unselected bounded alternative may be incomplete without
            # invalidating that concrete selected contract.
            Complete=True,
            Unsatisfiable=False,
            FirstConflictResourceIds=FirstConflict,
        )
    Complete = SearchComplete and DomainComplete
    Unsatisfiable = Complete and not Problem.NonExhaustiveDomain
    IncompleteReason = (
        "complete-capacity-core"
        if Unsatisfiable
        else "work-cap"
        if not SearchComplete
        else "incomplete-template-domain"
        if not DomainComplete
        else "non-exhaustive-template-domain"
    )
    return PreRouteInterfaceSelection(
        ProblemFingerprint=Problem.ProblemFingerprint,
        SelectionFingerprint="",
        SelectedTemplateIds=(),
        SelectedWitnessIds=(),
        Objective=(),
        ExpansionCount=Expansions,
        Success=False,
        Complete=Complete,
        Unsatisfiable=Unsatisfiable,
        IncompleteReason=IncompleteReason,
        FirstConflictResourceIds=FirstConflict,
    )

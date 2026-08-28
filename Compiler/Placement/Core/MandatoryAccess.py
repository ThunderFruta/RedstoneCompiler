"""Exact mandatory-access screening and conflict measurement."""

from __future__ import annotations

from copy import (
    deepcopy,
)
from dataclasses import (
    dataclass,
)
from hashlib import (
    sha256,
)
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
)
from Compiler.Placement.Rotation import (
    RotatedCellSize,
)
from Compiler.Placement.Geometry import (
    BuildPlacedGate,
    GetGateInputAccess,
    PlacedGate,
)
from Compiler.Placement.PreRouteInterface import (
    DerivedPerimeterSlotDomain,
    DerivedPerimeterTerminalSlot,
)
from Compiler.Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
)
from Compiler.Routing.Reliability import (
    BuildStableFingerprint,
)
from Compiler.Routing.ResourceGraph import (
    FindClaimConflicts,
    FindClaimConflictsByResourceIndex,
    FindSelfClaimConflicts,
    RoutingResourceClaims,
    RoutingResourceGraph,
    RoutingResourceId,
)
from .Clustering import (
    PcbGatesConflict,
)


def BuildDerivedPerimeterTerminalSlotDomain(
    TerminalGates: Iterable[Any],
    CoreGates: Iterable[PlacedGate],
    DesiredPinsByTerminal: Mapping[
        str,
        Iterable[tuple[int, int, int]],
    ],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> DerivedPerimeterSlotDomain:
    """Materialize the finite outward-facing I/O slot domain once.

    This deliberately derives every coordinate from the committed NAND hull,
    terminal macro pin geometry, and the pins served by that terminal.  It
    contains no terminal-bank spacing, lateral-radius, or setback policy.  A
    later access-capacity solver may consume the complete domain, but this
    placement step only proves macro/electrical legality and freezes one
    deterministic compact member.
    """
    Core = tuple(CoreGates)
    Terminals = tuple(TerminalGates)
    if not Core:
        raise ValueError("derived perimeter terminals require a placed core")
    MinimumX = min(Gate.X for Gate in Core)
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in Core
    )
    MinimumZ = min(Gate.Z for Gate in Core)
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in Core
    )
    CoreBounds = (MinimumX, MinimumZ, MaximumX, MaximumZ)
    TerminalDeckY = min(Gate.Y for Gate in Core)
    FaceDirections = {
        "north": (0, 0, -1),
        "south": (0, 0, 1),
        "west": (-1, 0, 0),
        "east": (1, 0, 0),
    }

    def TerminalKind(Gate: Any) -> str:
        Kind = getattr(Gate, "Kind", "")
        return str(getattr(Kind, "value", Kind))

    def TerminalSignal(Gate: Any) -> str:
        return str(
            Gate.Outputs[0]
            if TerminalKind(Gate) == "INPUT"
            else Gate.Inputs[0]
        )

    # First inspect every supported terminal transform.  Pin-plane distances
    # are derived from the actual macro shape, then shared per face so an
    # access fabric receives one coherent aperture plane rather than a
    # terminal-kind-specific shell offset.
    PrototypesByTerminal: dict[str, list[tuple[
        Any,
        str,
        int,
        bool,
        tuple[int, int, int],
        tuple[int, int, int],
        int,
        int,
    ]]] = {}
    FaceReach = {Face: 1 for Face in FaceDirections}
    for Gate in sorted(Terminals, key=lambda Value: Value.Name):
        TerminalPrototypes = []
        Seen = set()
        for Rotation in (0, 90, 180, 270):
            for MirrorX in (False, True):
                Prototype = BuildPlacedGate(
                    Gate,
                    0,
                    TerminalDeckY,
                    0,
                    Rotation,
                    MirrorX,
                )
                if TerminalKind(Gate) == "INPUT":
                    Pin = Prototype.OutputPin
                    Direction = Prototype.OutputDirection
                else:
                    Pin = Prototype.InputPins[0]
                    Direction = Prototype.InputDirections[0]
                if Pin is None or Direction not in FaceDirections.values():
                    continue
                Face = next(
                    Name
                    for Name, FaceDirection in FaceDirections.items()
                    if Direction == FaceDirection
                )
                Width, Depth = RotatedCellSize(
                    Prototype.Kind,
                    Prototype.Rotation,
                )
                Identity = (
                    Face,
                    Prototype.Rotation,
                    Prototype.MirrorX,
                    Pin,
                    Direction,
                    Width,
                    Depth,
                )
                if Identity in Seen:
                    continue
                Seen.add(Identity)
                TerminalPrototypes.append((
                    Gate,
                    Face,
                    Prototype.Rotation,
                    Prototype.MirrorX,
                    Pin,
                    Direction,
                    Width,
                    Depth,
                ))
                Reach = (
                    Depth - Pin[2]
                    if Face == "north"
                    else Pin[2] + 1
                    if Face == "south"
                    else Width - Pin[0]
                    if Face == "west"
                    else Pin[0] + 1
                )
                FaceReach[Face] = max(FaceReach[Face], Reach)
        PrototypesByTerminal[Gate.Name] = TerminalPrototypes

    FaceNormalCoordinate = {
        "north": MinimumZ - FaceReach["north"],
        "south": MaximumZ + FaceReach["south"],
        "west": MinimumX - FaceReach["west"],
        "east": MaximumX + FaceReach["east"],
    }

    def TangentialOrigins(
        Face: str,
        Pin: tuple[int, int, int],
        Width: int,
        Depth: int,
        Targets: tuple[tuple[int, int, int], ...],
    ) -> tuple[int, ...]:
        if Face in {"north", "south"}:
            Lower = MinimumX
            Upper = MaximumX - Width + 1
            Raw = {Target[0] - Pin[0] for Target in Targets}
        else:
            Lower = MinimumZ
            Upper = MaximumZ - Depth + 1
            Raw = {Target[2] - Pin[2] for Target in Targets}
        # If a terminal is wider than its core projection, its two exact
        # hull-boundary alignments remain the finite derived possibilities.
        if Lower > Upper:
            return tuple(sorted({Lower, Upper}))
        return tuple(sorted({
            Lower,
            Upper,
            *(min(Upper, max(Lower, Value)) for Value in Raw),
        }))

    TerminalSlots = []
    WorkCount = 0
    Complete = True
    IncompleteReason = ""
    for TerminalName, Prototypes in sorted(PrototypesByTerminal.items()):
        Targets = tuple(sorted(
            DesiredPinsByTerminal.get(TerminalName, ())
        ))
        Slots = []
        if not Targets:
            Complete = False
            IncompleteReason = (
                IncompleteReason
                or f"missing-served-pin:{TerminalName}"
            )
        for (
            Gate,
            Face,
            Rotation,
            MirrorX,
            LocalPin,
            Direction,
            Width,
            Depth,
        ) in Prototypes:
            for TangentialOrigin in TangentialOrigins(
                Face,
                LocalPin,
                Width,
                Depth,
                Targets,
            ) if Targets else ():
                WorkCount += 1
                if WorkCheck is not None:
                    WorkCheck({
                        "Phase": "derived-perimeter-slot-domain",
                        "TerminalName": TerminalName,
                        "Face": Face,
                        "WorkCount": WorkCount,
                    })
                OriginX = (
                    TangentialOrigin
                    if Face in {"north", "south"}
                    else FaceNormalCoordinate[Face] - LocalPin[0]
                )
                OriginZ = (
                    FaceNormalCoordinate[Face] - LocalPin[2]
                    if Face in {"north", "south"}
                    else TangentialOrigin
                )
                Candidate = BuildPlacedGate(
                    Gate,
                    OriginX,
                    TerminalDeckY,
                    OriginZ,
                    Rotation,
                    MirrorX,
                )
                ConnectionPin = (
                    Candidate.OutputPin
                    if TerminalKind(Gate) == "INPUT"
                    else Candidate.InputPins[0]
                )
                ConnectionDirection = (
                    Candidate.OutputDirection
                    if TerminalKind(Gate) == "INPUT"
                    else Candidate.InputDirections[0]
                )
                if ConnectionPin is None or ConnectionDirection != Direction:
                    raise ValueError("derived perimeter terminal transform drifted")
                if any(
                    PcbGatesConflict(Candidate, Existing)
                    for Existing in Core
                ):
                    continue
                MaximumSlotX = Candidate.X + Width - 1
                MaximumSlotZ = Candidate.Z + Depth - 1
                InteriorSpan = sum(
                    abs(ConnectionPin[0] - Target[0])
                    + abs(ConnectionPin[2] - Target[2])
                    for Target in Targets
                )
                SlotId = BuildStableFingerprint({
                    "TerminalName": TerminalName,
                    "Signal": TerminalSignal(Gate),
                    "Face": Face,
                    "Origin": (Candidate.X, Candidate.Y, Candidate.Z),
                    "Rotation": Candidate.Rotation,
                    "MirrorX": Candidate.MirrorX,
                    "ConnectionPin": ConnectionPin,
                    "ConnectionDirection": ConnectionDirection,
                })
                Slots.append(DerivedPerimeterTerminalSlot(
                    SlotId=SlotId,
                    TerminalName=TerminalName,
                    Signal=TerminalSignal(Gate),
                    Face=Face,
                    Origin=(Candidate.X, Candidate.Y, Candidate.Z),
                    Rotation=Candidate.Rotation,
                    MirrorX=Candidate.MirrorX,
                    MacroBounds=(
                        Candidate.X,
                        Candidate.Z,
                        MaximumSlotX,
                        MaximumSlotZ,
                    ),
                    ConnectionPin=ConnectionPin,
                    ConnectionDirection=ConnectionDirection,
                    InteriorSpan=InteriorSpan,
                ))
        Slots = sorted(
            Slots,
            key=lambda Slot: (
                Slot.Face,
                Slot.MacroBounds,
                Slot.Rotation,
                Slot.MirrorX,
                Slot.SlotId,
            ),
        )
        if not Slots:
            Complete = False
            IncompleteReason = (
                IncompleteReason
                or f"no-legal-outward-slot:{TerminalName}"
            )
        TerminalSlots.append((TerminalName, tuple(Slots)))

    TerminalGateByName = {Gate.Name: Gate for Gate in Terminals}
    AllSlots = tuple(
        Slot
        for _TerminalName, Slots in TerminalSlots
        for Slot in Slots
    )
    IncompatibleSlotPairs = tuple(sorted({
        tuple(sorted((First.SlotId, Second.SlotId)))
        for FirstIndex, First in enumerate(AllSlots)
        for Second in AllSlots[FirstIndex + 1:]
        if First.TerminalName != Second.TerminalName
        and (
            (
                First.Face == Second.Face
                and (
                    First.ConnectionPin[2]
                    if First.Face in {"north", "south"}
                    else First.ConnectionPin[0]
                ) != (
                    Second.ConnectionPin[2]
                    if Second.Face in {"north", "south"}
                    else Second.ConnectionPin[0]
                )
            )
            or PcbGatesConflict(
                BuildPlacedGate(
                    TerminalGateByName[First.TerminalName],
                    *First.Origin,
                    First.Rotation,
                    First.MirrorX,
                ),
                BuildPlacedGate(
                    TerminalGateByName[Second.TerminalName],
                    *Second.Origin,
                    Second.Rotation,
                    Second.MirrorX,
                ),
            )
        )
    }))
    return DerivedPerimeterSlotDomain(
        CoreBounds=CoreBounds,
        TerminalSlots=tuple(TerminalSlots),
        IncompatibleSlotPairs=IncompatibleSlotPairs,
        Complete=Complete,
        WorkCount=WorkCount,
        IncompleteReason=IncompleteReason,
    )

@dataclass(frozen=True)
class MandatoryAccessConflictProfile:
    """Immutable, routing-grade ownership and conflict summary for pin access."""

    OwnershipFingerprint: str
    ConflictFingerprint: str
    OwnershipRecords: tuple[
        tuple[str, tuple[RoutingResourceId, ...]], ...
    ]
    CrossConflicts: tuple[
        tuple[RoutingResourceId, tuple[str, ...]], ...
    ]
    SelfConflicts: tuple[
        tuple[RoutingResourceId, tuple[str, ...]], ...
    ]

    @property
    def SignalCount(self) -> int:
        return len(self.OwnershipRecords)

    @property
    def ClaimCount(self) -> int:
        return sum(
            len(Resources) for _Signal, Resources in self.OwnershipRecords
        )

    @property
    def CrossConflictCount(self) -> int:
        return len(self.CrossConflicts)

    @property
    def SelfConflictCount(self) -> int:
        return len(self.SelfConflicts)

    @property
    def ExactConflictCount(self) -> int:
        """Preserve the existing self-plus-cross mandatory conflict metric."""
        return self.CrossConflictCount + self.SelfConflictCount

    @property
    def ConflictResourceCount(self) -> int:
        return len({
            Resource
            for Resource, _Owners in (
                *self.CrossConflicts,
                *self.SelfConflicts,
            )
        })

    @property
    def ConflictSignals(self) -> tuple[str, ...]:
        return tuple(sorted({
            Signal
            for _Resource, Owners in (
                *self.CrossConflicts,
                *self.SelfConflicts,
            )
            for Signal in Owners
        }))

    @property
    def HasConflicts(self) -> bool:
        return self.ExactConflictCount > 0

    def ToDictionary(self) -> dict[str, object]:
        def ConflictRecords(
            Values: tuple[
                tuple[RoutingResourceId, tuple[str, ...]], ...
            ],
        ) -> list[dict[str, object]]:
            return [
                {
                    "Resource": str(Resource),
                    "Kind": Resource.Kind.value,
                    "Position": list(Resource.Position),
                    "Owners": list(Owners),
                }
                for Resource, Owners in Values
            ]

        return {
            "OwnershipFingerprint": self.OwnershipFingerprint,
            "ConflictFingerprint": self.ConflictFingerprint,
            "SignalCount": self.SignalCount,
            "ClaimCount": self.ClaimCount,
            "ClaimCountsBySignal": {
                Signal: len(Resources)
                for Signal, Resources in self.OwnershipRecords
            },
            "CrossConflictCount": self.CrossConflictCount,
            "SelfConflictCount": self.SelfConflictCount,
            "ExactConflictCount": self.ExactConflictCount,
            "ConflictResourceCount": self.ConflictResourceCount,
            "ConflictSignals": list(self.ConflictSignals),
            "HasConflicts": self.HasConflicts,
            "CrossConflicts": ConflictRecords(self.CrossConflicts),
            "SelfConflicts": ConflictRecords(self.SelfConflicts),
        }

def OrderExactStatesForMandatoryAccessCommit(
    States: Iterable[dict[str, object]],
    ProfilesBySearchCandidate: Mapping[
        int,
        MandatoryAccessConflictProfile,
    ],
) -> tuple[dict[str, object], ...]:
    """Promote the first exact zero-conflict state without losing identity.

    The joint beam's candidate index is a search-order identity.  A topology
    cut epoch additionally needs a commit order whose first state is known to
    have realizable mandatory pin access.  Preserve both identities so cached
    geometry and failure evidence remain attributable to the state that
    produced them.
    """
    CopiedStates = [deepcopy(State) for State in States]
    LegalStates = [
        State for State in CopiedStates
        if bool(State.get("ExactLegal"))
    ]
    RejectedStates = [
        State for State in CopiedStates
        if not bool(State.get("ExactLegal"))
    ]
    FirstZeroConflictState = next(
        (
            State
            for State in LegalStates
            if (
                int(State["CandidateIndex"])
                in ProfilesBySearchCandidate
                and not ProfilesBySearchCandidate[
                    int(State["CandidateIndex"])
                ].HasConflicts
            )
        ),
        None,
    )
    if FirstZeroConflictState is not None:
        LegalStates = [
            FirstZeroConflictState,
            *(
                State for State in LegalStates
                if State is not FirstZeroConflictState
            ),
        ]
    OrderedStates = [*LegalStates, *RejectedStates]
    for CommitCandidateIndex, State in enumerate(OrderedStates):
        SearchCandidateIndex = int(State["CandidateIndex"])
        State["SearchCandidateIndex"] = SearchCandidateIndex
        State["CandidateIndex"] = CommitCandidateIndex
        Profile = ProfilesBySearchCandidate.get(SearchCandidateIndex)
        State["ExactMandatoryAccessScreened"] = Profile is not None
        if Profile is not None:
            State["ExactMandatoryAccessConflictResources"] = (
                Profile.ConflictResourceCount
            )
            State["ExactMandatoryAccessConflictSignals"] = list(
                Profile.ConflictSignals
            )
            State["ExactMandatoryAccessOwnershipFingerprint"] = (
                Profile.OwnershipFingerprint
            )
            State["ExactMandatoryAccessConflictFingerprint"] = (
                Profile.ConflictFingerprint
            )
    return tuple(OrderedStates)

def SelectExactInterfaceCommitStates(
    States: Iterable[dict[str, object]],
    ProfilesBySearchCandidate: Mapping[
        int,
        MandatoryAccessConflictProfile,
    ],
    MaximumStates: int,
) -> tuple[
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
]:
    """Commit up to the bound using exact legality and interface diversity."""
    if MaximumStates <= 0:
        raise ValueError("exact interface commit bound must be positive")
    Selected: list[dict[str, object]] = []
    Attrition: list[dict[str, object]] = []
    SeenOwnership: set[str] = set()
    for State in States:
        SearchCandidateIndex = int(State["CandidateIndex"])
        Profile = ProfilesBySearchCandidate.get(SearchCandidateIndex)
        Pattern = State.get("ClusterInterfacePlacement", {})
        OwnershipFingerprint = (
            str(Pattern.get("OwnershipFingerprint", ""))
            if isinstance(Pattern, dict)
            else ""
        )
        if not bool(State.get("ExactLegal")):
            Attrition.append({
                "SearchCandidateIndex": SearchCandidateIndex,
                "Classification": (
                    "geometric-overlap-illegal-placement"
                ),
                "InterfaceOwnershipFingerprint": OwnershipFingerprint,
            })
            continue
        if Profile is None or Profile.HasConflicts:
            Attrition.append({
                "SearchCandidateIndex": SearchCandidateIndex,
                "Classification": "mandatory-access-unsat",
                "InterfaceOwnershipFingerprint": OwnershipFingerprint,
                "MandatoryAccessOwnershipFingerprint": (
                    Profile.OwnershipFingerprint
                    if Profile is not None
                    else ""
                ),
                "MandatoryAccessConflictFingerprint": (
                    Profile.ConflictFingerprint
                    if Profile is not None
                    else ""
                ),
            })
            continue
        if OwnershipFingerprint in SeenOwnership:
            Attrition.append({
                "SearchCandidateIndex": SearchCandidateIndex,
                "Classification": "duplicate-access-topology",
                "InterfaceOwnershipFingerprint": OwnershipFingerprint,
            })
            continue
        if len(Selected) >= MaximumStates:
            Attrition.append({
                "SearchCandidateIndex": SearchCandidateIndex,
                "Classification": "pruned-by-scoring-budget",
                "InterfaceOwnershipFingerprint": OwnershipFingerprint,
            })
            continue
        SeenOwnership.add(OwnershipFingerprint)
        Selected.append(deepcopy(State))
    for CommitCandidateIndex, State in enumerate(Selected):
        State["SearchCandidateIndex"] = int(State["CandidateIndex"])
        State["CandidateIndex"] = CommitCandidateIndex
    return tuple(Selected), tuple(Attrition)

def BuildMandatoryAccessClaims(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, RoutingResourceClaims]:
    """Build the fixed pin-access claims that every detailed route must own."""
    Gates = (
        PlacedGates
        if isinstance(PlacedGates, (list, tuple))
        else tuple(PlacedGates)
    )
    RequiredSignals = frozenset(Signals)
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "mandatory-access-claims-start",
            "GateCount": len(Gates),
            "SignalCount": len(RequiredSignals),
        })
    NodesBySignal: dict[str, set[tuple[int, int, int]]] = {
        Signal: set() for Signal in RequiredSignals
    }
    for GateIndex, Gate in enumerate(Gates, start=1):
        if WorkCheck is not None and GateIndex % 32 == 0:
            WorkCheck({
                "Phase": "mandatory-access-claims-gates",
                "ProcessedGates": GateIndex,
                "GateCount": len(Gates),
                "SignalCount": len(RequiredSignals),
            })
        if Gate.OutputPin is not None and Gate.OutputDirection is not None:
            for Signal in Gate.Outputs:
                if Signal not in RequiredSignals:
                    continue
                NodesBySignal[Signal].update(
                    (
                        Gate.OutputPin[0] + Gate.OutputDirection[0] * Offset,
                        Gate.OutputPin[1] + Gate.OutputDirection[1] * Offset,
                        Gate.OutputPin[2] + Gate.OutputDirection[2] * Offset,
                    )
                    for Offset in range(
                        DefaultRedstoneRoutingTechnology.AccessLength
                    )
                )
        for InputIndex, Signal in enumerate(Gate.Inputs):
            if Signal not in RequiredSignals:
                continue
            Pin, Direction = GetGateInputAccess(Gate, InputIndex)
            NodesBySignal[Signal].update(
                (
                    Pin[0] + Direction[0] * Offset,
                    Pin[1] + Direction[1] * Offset,
                    Pin[2] + Direction[2] * Offset,
                )
                for Offset in range(
                    DefaultRedstoneRoutingTechnology.AccessLength
                )
            )
    ClaimBuilder = RoutingResourceGraph(
        ActualBlocks=frozenset(),
        ElectricalBlocks=frozenset(),
        SolidBlocks=frozenset(),
    )
    Claims: dict[str, RoutingResourceClaims] = {}
    NonEmptySignals = tuple(
        (Signal, Nodes)
        for Signal, Nodes in sorted(NodesBySignal.items())
        if Nodes
    )
    for SignalIndex, (Signal, Nodes) in enumerate(
        NonEmptySignals,
        start=1,
    ):
        if WorkCheck is not None and (
            SignalIndex == 1 or SignalIndex % 16 == 0
        ):
            WorkCheck({
                "Phase": "mandatory-access-claims-signals",
                "Signal": Signal,
                "ProcessedSignals": SignalIndex - 1,
                "SignalCount": len(NonEmptySignals),
            })
        if WorkCheck is None:
            Claims[Signal] = ClaimBuilder.BuildRouteClaims(Nodes)
            continue

        def CheckRouteClaims(
            Diagnostics: dict[str, object],
            *,
            CurrentSignal: str = Signal,
        ) -> None:
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "mandatory-access-route-claims",
                    "Signal": CurrentSignal,
                    "ClaimPhase": Diagnostics.get("Phase"),
                    **{
                        Key: Value
                        for Key, Value in Diagnostics.items()
                        if Key != "Phase"
                    },
                })

        Claims[Signal] = ClaimBuilder.BuildRouteClaims(
            Nodes,
            WorkCheck=CheckRouteClaims,
        )
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "mandatory-access-claims-complete",
            "GateCount": len(Gates),
            "SignalCount": len(Claims),
            "ClaimCount": sum(
                len(Claim.ResourceIds) for Claim in Claims.values()
            ),
        })
    return Claims

def MeasureMandatoryAccessConflictProfile(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> MandatoryAccessConflictProfile:
    """Measure fixed access ownership with rename-independent fingerprints."""
    Claims = BuildMandatoryAccessClaims(
        PlacedGates,
        Signals,
        WorkCheck=WorkCheck,
    )
    OwnershipRecords = tuple(
        (
            Signal,
            tuple(sorted(Claim.ResourceIds, key=str)),
        )
        for Signal, Claim in sorted(Claims.items())
    )
    AllResources = tuple(
        Resource
        for _Signal, Resources in OwnershipRecords
        for Resource in Resources
    )
    MinimumX = min(
        (Resource.Position[0] for Resource in AllResources),
        default=0,
    )
    MinimumY = min(
        (Resource.Position[1] for Resource in AllResources),
        default=0,
    )
    MinimumZ = min(
        (Resource.Position[2] for Resource in AllResources),
        default=0,
    )

    def NormalizeResource(
        Resource: RoutingResourceId,
    ) -> tuple[str, int, int, int]:
        X, Y, Z = Resource.Position
        return (
            Resource.Kind.value,
            X - MinimumX,
            Y - MinimumY,
            Z - MinimumZ,
        )

    NormalizedOwnershipBySignal = {
        Signal: tuple(sorted(
            NormalizeResource(Resource) for Resource in Resources
        ))
        for Signal, Resources in OwnershipRecords
    }

    def StableFingerprint(Value: object) -> str:
        return sha256(repr(Value).encode("utf-8")).hexdigest()

    OwnershipFingerprint = StableFingerprint(tuple(sorted(
        NormalizedOwnershipBySignal.values()
    )))
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "mandatory-access-conflicts-start",
            "SignalCount": len(Claims),
        })

    def CheckCrossConflicts(Diagnostics: dict[str, object]) -> None:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "mandatory-access-cross-conflicts",
                "ConflictPhase": Diagnostics.get("Phase"),
                **{
                    Key: Value
                    for Key, Value in Diagnostics.items()
                    if Key != "Phase"
                },
            })

    CrossConflictMap = FindClaimConflictsByResourceIndex(
        Claims,
        WorkCheck=CheckCrossConflicts if WorkCheck is not None else None,
    )
    SelfConflictMap = FindSelfClaimConflicts(Claims)
    CrossConflicts = tuple(sorted(
        (
            (Resource, tuple(sorted(Owners)))
            for Resource, Owners in CrossConflictMap.items()
        ),
        key=lambda Value: str(Value[0]),
    ))
    SelfConflicts = tuple(sorted(
        (
            (Resource, tuple(sorted(Owners)))
            for Resource, Owners in SelfConflictMap.items()
        ),
        key=lambda Value: str(Value[0]),
    ))
    AnonymousConflictRecords = tuple(sorted(
        (
            ConflictKind,
            NormalizeResource(Resource),
            tuple(sorted(
                StableFingerprint(
                    NormalizedOwnershipBySignal.get(Owner, ())
                )
                for Owner in Owners
            )),
        )
        for ConflictKind, Conflicts in (
            ("Cross", CrossConflicts),
            ("Self", SelfConflicts),
        )
        for Resource, Owners in Conflicts
    ))
    Result = MandatoryAccessConflictProfile(
        OwnershipFingerprint=OwnershipFingerprint,
        ConflictFingerprint=StableFingerprint(AnonymousConflictRecords),
        OwnershipRecords=OwnershipRecords,
        CrossConflicts=CrossConflicts,
        SelfConflicts=SelfConflicts,
    )
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "mandatory-access-conflicts-complete",
            "SignalCount": Result.SignalCount,
            "ClaimCount": Result.ClaimCount,
            "ExactConflictCount": Result.ExactConflictCount,
        })
    return Result

def CountMandatoryAccessSelfConflicts(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> int:
    """Count exact support/headroom aliases within mandatory pin accesses."""
    return len(FindSelfClaimConflicts(BuildMandatoryAccessClaims(
        PlacedGates,
        Signals,
        WorkCheck=WorkCheck,
    )))

def CountMandatoryAccessConflicts(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> int:
    """Count all fixed pin-access conflicts for the affected signal cut.

    A packed cluster can be individually legal for every signal while two
    different signals still claim the same electrical, support, or headroom
    resource.  Those conflicts are immutable to detailed routing and must be
    removed by the local placement repair before a candidate is published.
    """
    Claims = BuildMandatoryAccessClaims(
        PlacedGates,
        Signals,
        WorkCheck=WorkCheck,
    )

    def CheckConflicts(Diagnostics: dict[str, object]) -> None:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "mandatory-access-cross-conflicts",
                "ConflictPhase": Diagnostics.get("Phase"),
                **{
                    Key: Value
                    for Key, Value in Diagnostics.items()
                    if Key != "Phase"
                },
            })

    return (
        len(FindSelfClaimConflicts(Claims))
        + len(FindClaimConflicts(
            Claims,
            WorkCheck=CheckConflicts if WorkCheck is not None else None,
        ))
    )

def CountPackedAccessEscapeConflicts(
    PlacedGates: Iterable[Any],
    RepairSignals: Iterable[str],
) -> int:
    """Count pin pairs too close to expose independent routing portals.

    Exact mandatory claims cover the fixed access cells themselves, but two
    otherwise legal pins separated by less than one routing track can still
    leave no pair of electrically independent first portals.  Restrict this
    stronger placement metric to pairs involving the current typed repair cut
    so ordinary compact clusters retain their established geometry.
    """
    Gates = tuple(PlacedGates)
    Required = frozenset(RepairSignals)
    PinOwners: list[tuple[tuple[int, int, int], str, str]] = []
    for Gate in Gates:
        if Gate.OutputPin is not None:
            PinOwners.extend(
                (Gate.OutputPin, Signal, Gate.Name)
                for Signal in Gate.Outputs
            )
        PinOwners.extend(
            (Pin, Signal, Gate.Name)
            for Pin, Signal in zip(Gate.InputPins, Gate.Inputs)
        )
    NearConflicts = sum(
        1
        for Index, (FirstPin, FirstSignal, FirstGate) in enumerate(PinOwners)
        for SecondPin, SecondSignal, SecondGate in PinOwners[Index + 1 :]
        if FirstGate != SecondGate
        and FirstSignal != SecondSignal
        and (
            FirstSignal in Required
            or SecondSignal in Required
        )
        and abs(FirstPin[1] - SecondPin[1]) <= 1
        and (
            abs(FirstPin[0] - SecondPin[0])
            + abs(FirstPin[2] - SecondPin[2])
            < DefaultRedstoneRoutingTechnology.TrackPitch
        )
    )
    return CountMandatoryAccessConflicts(Gates, Required) + NearConflicts

def FindMandatoryAccessConflictSignals(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[object, tuple[str, ...]]:
    """Return immutable cross-signal access conflicts before routing begins.

    This is deliberately the same resource-claim model used by detailed
    routing.  It lets placement advance directly to a relocation recipe when
    no router can resolve a fixed pin-access collision.
    """
    return {
        Resource: Owners
        for Resource, Owners in MeasureMandatoryAccessConflictProfile(
            PlacedGates,
            Signals,
            WorkCheck=WorkCheck,
        ).CrossConflicts
    }

def RepairPackedClusterAccess(
    Names: tuple[str, ...] | list[str],
    InternalByName: dict[str, Any],
    LocalPositions: dict[str, tuple[int, int]],
    LocalRotations: dict[str, int],
    LocalMirrors: dict[str, bool],
    RequiredSignals: frozenset[str],
    BeamWidth: int,
    IncludeNearPortalConflicts: bool = False,
    NormalizeOrigin: bool = True,
    RequireAccessDistinctGeometry: bool = False,
    AccessDistinctVariant: int = 0,
    PriorityTerminalPositions: frozenset[
        tuple[int, int, int]
    ] = frozenset(),
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ) -> tuple[dict[str, tuple[int, int]], dict[str, bool], dict[str, int]]:
    """Repair fixed access claims inside one cluster without global spreading."""
    ClusterSignals = frozenset(
        Signal
        for Name in Names
        for Signal in (
            *InternalByName[Name].Inputs,
            *InternalByName[Name].Outputs,
        )
        if Signal in RequiredSignals
    )
    if not ClusterSignals:
        return LocalPositions, LocalMirrors, {}

    def BuildGates(
        State: tuple[tuple[str, int, int, bool], ...],
    ) -> list[Any]:
        Values = {
            Name: (X, Z, MirrorX)
            for Name, X, Z, MirrorX in State
        }
        return [
            BuildPlacedGate(
                InternalByName[Name],
                Values[Name][0],
                1,
                Values[Name][1],
                LocalRotations[Name],
                Values[Name][2],
            )
            for Name in Names
        ]

    BaselineState = tuple(
        (
            Name,
            LocalPositions[Name][0],
            LocalPositions[Name][1],
            LocalMirrors.get(Name, False),
        )
        for Name in Names
    )
    BaselineGates = BuildGates(BaselineState)
    BaselineMandatoryConflicts = CountMandatoryAccessConflicts(
        BaselineGates, ClusterSignals
    )
    BaselineConflicts = (
        CountPackedAccessEscapeConflicts(BaselineGates, ClusterSignals)
        if IncludeNearPortalConflicts
        else BaselineMandatoryConflicts
    )
    if BaselineConflicts == 0 and not RequireAccessDistinctGeometry:
        return LocalPositions, LocalMirrors, {}

    BaselineMinimumX = min(Gate.X for Gate in BaselineGates)
    BaselineMaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
        for Gate in BaselineGates
    )
    BaselineWidth = BaselineMaximumX - BaselineMinimumX
    MaximumWidth = max(BaselineWidth, 2 * BaselineWidth)
    SearchMinimumX = BaselineMinimumX - BaselineWidth
    SearchMaximumX = BaselineMaximumX + BaselineWidth
    BaselineMinimumZ = min(Gate.Z for Gate in BaselineGates)
    BaselineMaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
        for Gate in BaselineGates
    )
    BaselineDepth = BaselineMaximumZ - BaselineMinimumZ
    MaximumDepth = max(BaselineDepth, 2 * BaselineDepth)
    SearchMinimumZ = BaselineMinimumZ - BaselineDepth
    SearchMaximumZ = BaselineMaximumZ + BaselineDepth
    BaselinePosition = {
        Name: LocalPositions[Name] for Name in Names
    }
    EndpointNames = {
        Name
        for Name in Names
        if set((
            *InternalByName[Name].Inputs,
            *InternalByName[Name].Outputs,
        )) & ClusterSignals
    }
    PriorityEndpointNames = frozenset(
        Gate.Name
        for Gate in BaselineGates
        if (
            (
                Gate.OutputPin is not None
                and Gate.OutputPin in PriorityTerminalPositions
                and bool(set(Gate.Outputs) & ClusterSignals)
            )
            or any(
                Pin in PriorityTerminalPositions
                and Signal in ClusterSignals
                for Signal, Pin in zip(
                    Gate.Inputs,
                    Gate.InputPins,
                )
            )
        )
    )
    SearchOrder = tuple(sorted(
        EndpointNames,
        key=lambda Name: (
            Name not in PriorityEndpointNames,
            LocalPositions[Name][1],
            Name,
        ),
    ))

    def Score(
        State: tuple[tuple[str, int, int, bool], ...],
        Gates: list[Any] | None = None,
    ) -> tuple[object, ...]:
        Gates = BuildGates(State) if Gates is None else Gates
        MinimumX = min(Gate.X for Gate in Gates)
        MaximumX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            for Gate in Gates
        )
        MinimumZ = min(Gate.Z for Gate in Gates)
        MaximumZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            for Gate in Gates
        )
        Width = MaximumX - MinimumX
        Depth = MaximumZ - MinimumZ
        Displacement = sum(
            abs(Gate.X - BaselinePosition[Gate.Name][0])
            + abs(Gate.Z - BaselinePosition[Gate.Name][1])
            for Gate in Gates
        )
        ConflictCount = (
            CountPackedAccessEscapeConflicts(Gates, ClusterSignals)
            if IncludeNearPortalConflicts
            else CountMandatoryAccessConflicts(Gates, ClusterSignals)
        )
        StateByName = {
            Name: (X, Z, MirrorX)
            for Name, X, Z, MirrorX in State
        }
        PriorityEndpointUnchangedCount = sum(
            StateByName[Name]
            == (
                BaselinePosition[Name][0],
                BaselinePosition[Name][1],
                LocalMirrors.get(Name, False),
            )
            for Name in PriorityEndpointNames
        )
        GeometryChangePenalty = (
            PriorityEndpointUnchangedCount
            if PriorityEndpointNames
            else int(
                RequireAccessDistinctGeometry
                and State == BaselineState
            )
        )
        return (
            (
                ConflictCount,
                GeometryChangePenalty,
                Width * Depth,
                max(Width, Depth),
                Displacement,
                State,
            )
            if IncludeNearPortalConflicts
            else (
                ConflictCount,
                GeometryChangePenalty,
                Width,
                Displacement,
                State,
            )
        )

    Beam: list[tuple[tuple[object, ...], tuple[tuple[str, int, int, bool], ...]]] = [
        (Score(BaselineState, BaselineGates), BaselineState)
    ]
    CandidateEvaluationCount = 0
    for PassIndex in range(2):
        for GateIndex, Name in enumerate(SearchOrder):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "packed-access-repair",
                    "Pass": PassIndex,
                    "GateIndex": GateIndex,
                    "GateCount": len(SearchOrder),
                    "BaselineConflictCount": BaselineConflicts,
                })
            Candidates: dict[
                tuple[tuple[str, int, int, bool], ...],
                tuple[object, ...],
            ] = {}
            for _PreviousScore, PreviousState in Beam:
                Previous = {
                    GateName: (X, Z, MirrorX)
                    for GateName, X, Z, MirrorX in PreviousState
                }
                GateWidth = RotatedCellSize(
                    InternalByName[Name].Kind.value,
                    LocalRotations[Name],
                )[0]
                GateDepth = RotatedCellSize(
                    InternalByName[Name].Kind.value,
                    LocalRotations[Name],
                )[1]
                CandidateXValues = sorted(
                    range(
                        SearchMinimumX,
                        SearchMaximumX - GateWidth + 1,
                    ),
                    key=lambda CandidateX: (
                        abs(
                            CandidateX
                            - BaselinePosition[Name][0]
                        ),
                        CandidateX,
                    ),
                )[:33]
                CandidatePositions = [
                    (CandidateX, BaselinePosition[Name][1])
                    for CandidateX in CandidateXValues
                ]
                if IncludeNearPortalConflicts:
                    CandidateZValues = sorted(
                        range(
                            SearchMinimumZ,
                            SearchMaximumZ - GateDepth + 1,
                        ),
                        key=lambda CandidateZ: (
                            abs(
                                CandidateZ
                                - BaselinePosition[Name][1]
                            ),
                            CandidateZ,
                        ),
                    )[:33]
                    CandidatePositions.extend(
                        (BaselinePosition[Name][0], CandidateZ)
                        for CandidateZ in CandidateZValues
                    )
                    CandidatePositions.extend(
                        (
                            BaselinePosition[Name][0] + DeltaX,
                            BaselinePosition[Name][1] + DeltaZ,
                        )
                        for DeltaX in range(-6, 7)
                        for DeltaZ in range(-6, 7)
                        if abs(DeltaX) + abs(DeltaZ) <= 6
                    )
                for CandidateX, CandidateZ in dict.fromkeys(
                    CandidatePositions
                ):
                    for CandidateMirror in (False, True):
                        Candidate = dict(Previous)
                        Candidate[Name] = (
                            CandidateX,
                            CandidateZ,
                            CandidateMirror,
                        )
                        State = tuple(
                            (
                                GateName,
                                Candidate[GateName][0],
                                Candidate[GateName][1],
                                Candidate[GateName][2],
                            )
                            for GateName in Names
                        )
                        if State in Candidates:
                            continue
                        CandidateEvaluationCount += 1
                        if (
                            WorkCheck is not None
                            and CandidateEvaluationCount % 128 == 0
                        ):
                            WorkCheck({
                                "Phase": "packed-access-repair-candidate",
                                "Pass": PassIndex,
                                "GateIndex": GateIndex,
                                "GateCount": len(SearchOrder),
                                "EvaluatedCandidates": (
                                    CandidateEvaluationCount
                                ),
                                "CurrentFrontier": len(Candidates),
                            })
                        Gates = BuildGates(State)
                        if any(
                            PcbGatesConflict(First, Second)
                            for FirstIndex, First in enumerate(Gates)
                            for Second in Gates[FirstIndex + 1 :]
                        ):
                            continue
                        MinimumX = min(Gate.X for Gate in Gates)
                        MaximumX = max(
                            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
                            for Gate in Gates
                        )
                        MinimumZ = min(Gate.Z for Gate in Gates)
                        MaximumZ = max(
                            Gate.Z
                            + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
                            for Gate in Gates
                        )
                        if (
                            MaximumX - MinimumX > MaximumWidth
                            or MaximumZ - MinimumZ > MaximumDepth
                        ):
                            continue
                        Candidates[State] = Score(State, Gates)
            Beam = [
                (CandidateScore, State)
                for State, CandidateScore in sorted(
                    Candidates.items(), key=lambda Value: Value[1]
                )[:BeamWidth]
            ]
            if not Beam:
                break
            if (
                Beam[0][0][0] == 0
                and Beam[0][0][1] == 0
                and (
                    not RequireAccessDistinctGeometry
                    or Beam[0][1] != BaselineState
                )
            ):
                break
        if (
            Beam
            and Beam[0][0][0] == 0
            and Beam[0][0][1] == 0
            and (
                not RequireAccessDistinctGeometry
                or Beam[0][1] != BaselineState
            )
        ):
            break
    ExactLegalChangedBeam = [
        (CandidateScore, State)
        for CandidateScore, State in Beam
        if CandidateScore[0] == 0
        and CandidateScore[1] == 0
        and (
            not RequireAccessDistinctGeometry
            or State != BaselineState
        )
    ]
    SelectedBeam = (
        ExactLegalChangedBeam[
            AccessDistinctVariant % len(ExactLegalChangedBeam)
        ]
        if ExactLegalChangedBeam
        else (Beam[0] if Beam else None)
    )
    BestMandatoryConflicts = (
        CountMandatoryAccessConflicts(
            BuildGates(SelectedBeam[1]),
            ClusterSignals,
        )
        if SelectedBeam is not None
        else BaselineMandatoryConflicts
    )
    if SelectedBeam is None or BestMandatoryConflicts != 0:
        BestConflictCount = (
            SelectedBeam[0][0]
            if SelectedBeam is not None
            else BaselineConflicts
        )
        raise ValueError(
            "Could not legalize mandatory packed access claims "
            f"within width envelope: signals={','.join(sorted(ClusterSignals))}:"
            f"baseline={BaselineConflicts}:best={BestConflictCount}:"
            f"maximum_width={MaximumWidth}"
        )
    BestScore, BestState = SelectedBeam
    Best = {
        Name: (X, Z, MirrorX)
        for Name, X, Z, MirrorX in BestState
    }
    MinimumX = (
        min(X for X, _Z, _MirrorX in Best.values())
        if NormalizeOrigin
        else 0
    )
    MinimumZ = (
        min(Z for _X, Z, _MirrorX in Best.values())
        if NormalizeOrigin
        else 0
    )
    RepairedPositions = dict(LocalPositions)
    RepairedMirrors = dict(LocalMirrors)
    for Name in Names:
        RepairedPositions[Name] = (
            Best[Name][0] - MinimumX,
            Best[Name][1] - MinimumZ,
        )
        RepairedMirrors[Name] = Best[Name][2]
    BestGates = BuildGates(BestState)
    FinalWidth = (
        max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            for Gate in BestGates
        )
        - min(Gate.X for Gate in BestGates)
    )
    FinalDepth = (
        max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            for Gate in BestGates
        )
        - min(Gate.Z for Gate in BestGates)
    )
    return RepairedPositions, RepairedMirrors, {
        "BaselineConflictCount": BaselineConflicts,
        "FinalConflictCount": BestScore[0],
        "BaselineMandatoryConflictCount": BaselineMandatoryConflicts,
        "FinalMandatoryConflictCount": BestMandatoryConflicts,
        "BaselineWidth": BaselineWidth,
        "FinalWidth": FinalWidth,
        "MaximumWidth": MaximumWidth,
        "BaselineDepth": BaselineDepth,
        "FinalDepth": FinalDepth,
        "MaximumDepth": MaximumDepth,
        "NormalizedOrigin": NormalizeOrigin,
        "AccessDistinctVariant": AccessDistinctVariant,
        "AccessDistinctVariantCount": len(ExactLegalChangedBeam),
        "PriorityEndpointNames": sorted(PriorityEndpointNames),
        "PriorityTerminalPositions": [
            list(Position)
            for Position in sorted(PriorityTerminalPositions)
        ],
    }

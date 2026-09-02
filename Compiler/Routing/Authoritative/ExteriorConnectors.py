"""Exterior connector and boundary assignment search."""

from __future__ import annotations

from ..Components.Validation import BuildPhysicalPortSeamContractFingerprint

from ..Contracts.Component import PhysicalComponentBoundaryPortReservation

from ..Contracts.Component import PhysicalExteriorApertureFabric

from ..Contracts.Core import Position2

from ..Contracts.Core import Position3

from ..Contracts.PhysicalInterface import PhysicalGlobalAperturePathTemplate

from ..Contracts.PhysicalInterface import PhysicalPortApertureOptionFactor

from ..Contracts.PhysicalInterface import PhysicalPortLocalAccessFactor

from ..Contracts.PhysicalInterface import PhysicalPortLocalApertureSupport

from ..Interfaces.PhysicalClaims import ComponentClaimsConflict

from ..Reliability import BuildStableFingerprint

from ..ResourceGraph import RoutingResourceClaims

from ..Technology import DefaultRedstoneRoutingTechnology

from collections import Counter

from collections import defaultdict

from dataclasses import dataclass

from heapq import heappop

from heapq import heappush

from typing import Any

from typing import Callable

from typing import Iterable

from typing import Mapping

try:
    from RedstoneCompiler.RustRouting import GetRoutingThreadCount as GetRustRoutingThreadCount, RoutingContext as RustRoutingContext, SearchExteriorConnectorsBatchWithTelemetry as _SearchExteriorConnectorsBatchWithTelemetry
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import GetRoutingThreadCount as GetRustRoutingThreadCount, RoutingContext as RustRoutingContext, SearchExteriorConnectorsBatchWithTelemetry as _SearchExteriorConnectorsBatchWithTelemetry
    except Exception:
        RustRoutingContext = None
        _SearchExteriorConnectorsBatchWithTelemetry = None

        def GetRustRoutingThreadCount() -> int:
            return 1

from .CandidateCache import (
    TransformPlanarRoutingPosition,
)

@dataclass(frozen=True)
class PhysicalExteriorConnectorDistanceField:
    """Immutable sparse search contract for one exterior guide."""

    Targets: frozenset[Position3]
    EnvelopeMinimum: Position3
    EnvelopeMaximum: Position3
    BlockedGuideCells: frozenset[Position2]
    Bounds: tuple[int, int, int, int]
    ResourceGraphFingerprint: str
    ForeignClaimsFingerprint: str
    NextNodeByNode: tuple[tuple[Position3, Position3], ...]
    DistanceByNode: tuple[tuple[Position3, int], ...]
    BuildExpansionCount: int
    FieldFingerprint: str
    AllowedNodes: frozenset[Position3] = frozenset()
    AllowedEdges: frozenset[tuple[Position3, Position3]] = frozenset()
    ExteriorFabricFingerprint: str = ""
    Complete: bool = False

@dataclass(frozen=True)
class PhysicalExteriorConnectorPathResult:
    """Result of a shared-field lookup with an exact-search fallback."""

    Path: tuple[Position3, ...]
    UsedCanonicalField: bool
    UsedFallback: bool
    FallbackExpansionCount: int

@dataclass(frozen=True)
class FrozenPhysicalExteriorConnectorSearchRequest:
    """One immutable exterior search that can run outside the Python GIL."""

    Field: PhysicalExteriorConnectorDistanceField
    Start: Position3
    BlockedLocalNodes: frozenset[Position3]


def NativeExteriorConnectorSearchAvailable() -> bool:
    """Return whether the compiled exterior-connector batch is available."""
    return _SearchExteriorConnectorsBatchWithTelemetry is not None

def SearchFrozenPhysicalExteriorConnectorBatch(
    Requests: Iterable[FrozenPhysicalExteriorConnectorSearchRequest],
) -> tuple[tuple[PhysicalExteriorConnectorPathResult, ...], int]:
    """Search complete frozen exterior fabrics in deterministic request order.

    The caller must still validate redstone claims before accepting a path.
    Incomplete or custom-resource contracts deliberately remain on the Python
    exact path, so native work can never weaken route legality.
    """
    Values = tuple(Requests)
    if not Values:
        return (), 0
    if (
        not NativeExteriorConnectorSearchAvailable()
        or any(not Request.Field.Complete for Request in Values)
    ):
        return (), 0
    FieldsByFingerprint: dict[
        str, PhysicalExteriorConnectorDistanceField
    ] = {}
    FieldIndexByFingerprint: dict[str, int] = {}
    for Request in Values:
        Fingerprint = Request.Field.FieldFingerprint
        if Fingerprint not in FieldsByFingerprint:
            FieldIndexByFingerprint[Fingerprint] = len(
                FieldsByFingerprint
            )
            FieldsByFingerprint[Fingerprint] = Request.Field
    NativeFields = tuple(
        (
            tuple(sorted(Field.Targets)),
            tuple(Field.EnvelopeMinimum),
            tuple(Field.EnvelopeMaximum),
            tuple(sorted(Field.BlockedGuideCells)),
            tuple(Field.Bounds),
            tuple(sorted(Field.AllowedNodes)),
            tuple(sorted(Field.AllowedEdges)),
        )
        for Field in FieldsByFingerprint.values()
    )
    NativeRequests = tuple(
        (
            FieldIndexByFingerprint[Request.Field.FieldFingerprint],
            tuple(Request.Start),
            tuple(sorted(Request.BlockedLocalNodes)),
        )
        for Request in Values
    )
    NativeResults, ActiveWorkerCount = (
        _SearchExteriorConnectorsBatchWithTelemetry(
            NativeFields,
            NativeRequests,
        )
    )
    if len(NativeResults) != len(Values):
        raise RuntimeError("native exterior connector batch lost a request")
    return (
        tuple(
            PhysicalExteriorConnectorPathResult(
                Path=tuple(tuple(Position) for Position in Path),
                UsedCanonicalField=bool(UsedCanonicalField),
                UsedFallback=bool(UsedFallback),
                FallbackExpansionCount=int(FallbackExpansionCount),
            )
            for (
                Path,
                UsedCanonicalField,
                UsedFallback,
                FallbackExpansionCount,
            ) in NativeResults
        ),
        int(ActiveWorkerCount),
    )

def BuildPhysicalExteriorConnectorDistanceField(
    ResourceGraph: Any,
    Targets: frozenset[Position3],
    *,
    EnvelopeMinimum: Position3,
    EnvelopeMaximum: Position3,
    BlockedGuideCells: frozenset[Position2],
    Margin: int,
    EdgeIsLegal: Callable[[Position3, Position3], bool],
    Bounds: tuple[int, int, int, int] | None = None,
    ResourceGraphFingerprint: str = "",
    ForeignClaimsFingerprint: str = "",
    ExteriorFabric: PhysicalExteriorApertureFabric | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PhysicalExteriorConnectorDistanceField:
    """Freeze the placement-independent bounds and targets for one guide.

    Earlier code flooded the complete exterior rectangle once per guide and
    stored a predecessor for every reachable cell.  That made physical port
    preparation proportional to the whole placement area before a concrete
    port existed.  The authoritative contract is only the guide targets,
    keepout, bounds, resource identity, and foreign-claim identity.  Exact
    path work is now performed lazily for a requested physical attachment.
    """
    EffectiveMargin = max(1, int(Margin))
    ExteriorTargets = frozenset(
        Target
        for Target in Targets
        if (Target[0], Target[2]) not in BlockedGuideCells
        and (
            ExteriorFabric.AllowsNode(Target)
            if ExteriorFabric is not None
            else not (
                EnvelopeMinimum[0] <= Target[0] <= EnvelopeMaximum[0]
                and EnvelopeMinimum[2] <= Target[2] <= EnvelopeMaximum[2]
            )
        )
    )
    if Bounds is not None:
        MinimumX, MaximumX, MinimumZ, MaximumZ = Bounds
    elif ExteriorTargets:
        MinimumX = min(
            EnvelopeMinimum[0],
            *(Value[0] for Value in ExteriorTargets),
        ) - EffectiveMargin
        MaximumX = max(
            EnvelopeMaximum[0],
            *(Value[0] for Value in ExteriorTargets),
        ) + EffectiveMargin
        MinimumZ = min(
            EnvelopeMinimum[2],
            *(Value[2] for Value in ExteriorTargets),
        ) - EffectiveMargin
        MaximumZ = max(
            EnvelopeMaximum[2],
            *(Value[2] for Value in ExteriorTargets),
        ) + EffectiveMargin
    else:
        MinimumX = EnvelopeMinimum[0] - EffectiveMargin
        MaximumX = EnvelopeMaximum[0] + EffectiveMargin
        MinimumZ = EnvelopeMinimum[2] - EffectiveMargin
        MaximumZ = EnvelopeMaximum[2] + EffectiveMargin
    EffectiveBounds = (MinimumX, MaximumX, MinimumZ, MaximumZ)

    # Retain the legacy tuple members as empty immutable values while callers
    # migrate to the sparse contract.  They are deliberately excluded from
    # the identity: no route-search result is part of assembly preparation.
    NextNodeByNode: dict[Position3, Position3] = {}
    DistanceByNode: dict[Position3, int] = {}
    ExpansionCount = len(ExteriorTargets)
    if WorkCheck is not None and ExteriorTargets:
        WorkCheck({
            "Stage": "physical-port-global-aperture-contract",
            "FieldExpansionCount": ExpansionCount,
            "FieldVisitedNodeCount": 0,
            "GuideTargetCount": len(ExteriorTargets),
        })
    FieldFingerprint = BuildStableFingerprint((
        "physical-exterior-connector-search-contract-v2",
        tuple(sorted(ExteriorTargets)),
        EnvelopeMinimum,
        EnvelopeMaximum,
        tuple(sorted(BlockedGuideCells)),
        EffectiveBounds,
        ResourceGraphFingerprint,
        ForeignClaimsFingerprint,
        (
            ExteriorFabric.FabricFingerprint
            if ExteriorFabric is not None
            else ""
        ),
    ))
    return PhysicalExteriorConnectorDistanceField(
        Targets=ExteriorTargets,
        EnvelopeMinimum=EnvelopeMinimum,
        EnvelopeMaximum=EnvelopeMaximum,
        BlockedGuideCells=BlockedGuideCells,
        Bounds=EffectiveBounds,
        ResourceGraphFingerprint=ResourceGraphFingerprint,
        ForeignClaimsFingerprint=ForeignClaimsFingerprint,
        NextNodeByNode=tuple(sorted(NextNodeByNode.items())),
        DistanceByNode=tuple(sorted(DistanceByNode.items())),
        BuildExpansionCount=ExpansionCount,
        FieldFingerprint=FieldFingerprint,
        AllowedNodes=(
            ExteriorFabric.AllowedNodes
            if ExteriorFabric is not None
            else frozenset()
        ),
        AllowedEdges=(
            ExteriorFabric.AllowedEdges
            if ExteriorFabric is not None
            else frozenset()
        ),
        ExteriorFabricFingerprint=(
            ExteriorFabric.FabricFingerprint
            if ExteriorFabric is not None
            else ""
        ),
        Complete=bool(
            ExteriorFabric is not None and ExteriorFabric.Complete
        ),
    )

def SelectPhysicalExteriorConnectorPath(
    Field: PhysicalExteriorConnectorDistanceField,
    ResourceGraph: Any,
    Start: Position3,
    *,
    BlockedLocalNodes: frozenset[Position3],
    EdgeIsLegal: Callable[[Position3, Position3], bool],
    ValidateCandidate: Callable[[tuple[Position3, ...]], bool],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PhysicalExteriorConnectorPathResult:
    """Try sparse canonical portals, then complete bounded A* routing."""
    MinimumX, MaximumX, MinimumZ, MaximumZ = Field.Bounds

    def NodeIsLegal(Position: Position3) -> bool:
        X, _Y, Z = Position
        ExteriorOwned = (
            Position in Field.AllowedNodes
            if Field.ExteriorFabricFingerprint
            else not (
                Field.EnvelopeMinimum[0]
                <= X
                <= Field.EnvelopeMaximum[0]
                and Field.EnvelopeMinimum[2]
                <= Z
                <= Field.EnvelopeMaximum[2]
            )
        )
        return bool(
            MinimumX <= X <= MaximumX
            and MinimumZ <= Z <= MaximumZ
            and Position not in BlockedLocalNodes
            and (X, Z) not in Field.BlockedGuideCells
            and ExteriorOwned
        )

    def EdgeBelongsToSearchDomain(
        First: Position3,
        Second: Position3,
    ) -> bool:
        if Field.Complete:
            return tuple(sorted((First, Second))) in Field.AllowedEdges
        return ResourceGraph.BuildPrimitive(First, Second) is not None

    def BuildAxisOrderedCandidate(
        Target: Position3,
        FirstAxis: int,
    ) -> tuple[Position3, ...]:
        Current = Start
        Result = [Start]
        for Axis in (FirstAxis, 2 if FirstAxis == 0 else 0):
            Delta = 1 if Target[Axis] > Current[Axis] else -1
            while Current[Axis] != Target[Axis]:
                Values = list(Current)
                Values[Axis] += Delta
                Current = tuple(Values)
                Result.append(Current)
        return tuple(Result)

    OrderedTargets = tuple(sorted(
        Field.Targets,
        key=lambda Target: (
            abs(Target[0] - Start[0]) + abs(Target[2] - Start[2]),
            Target,
        ),
    ))
    for Target in OrderedTargets:
        for FirstAxis in (0, 2):
            Candidate = BuildAxisOrderedCandidate(Target, FirstAxis)
            if (
                all(NodeIsLegal(Node) for Node in Candidate[1:])
                and all(
                    EdgeBelongsToSearchDomain(First, Second)
                    and EdgeIsLegal(First, Second)
                    for First, Second in zip(Candidate, Candidate[1:])
                )
                and ValidateCandidate(Candidate)
            ):
                return PhysicalExteriorConnectorPathResult(
                    Path=Candidate,
                    UsedCanonicalField=True,
                    UsedFallback=False,
                    FallbackExpansionCount=0,
                )

    UseManhattanTargetHeuristic = len(Field.Targets) <= 16

    def TargetDistance(Position: Position3) -> int:
        if not UseManhattanTargetHeuristic:
            return 0
        return min(
            (
                abs(Target[0] - Position[0])
                + abs(Target[2] - Position[2])
                for Target in Field.Targets
            ),
            default=0,
        )

    Pending: list[tuple[int, int, Position3]] = []
    heappush(Pending, (TargetDistance(Start), 0, Start))
    Previous: dict[Position3, Position3 | None] = {Start: None}
    PathDistance: dict[Position3, int] = {Start: 0}
    ExpansionCount = 0

    def Reconstruct(End: Position3) -> tuple[Position3, ...]:
        Path = [End]
        while Previous[Path[-1]] is not None:
            Parent = Previous[Path[-1]]
            assert Parent is not None
            Path.append(Parent)
        Path.reverse()
        return tuple(Path)

    while Pending:
        _, CurrentDistance, Current = heappop(Pending)
        if CurrentDistance != PathDistance.get(Current):
            continue
        ExpansionCount += 1
        if WorkCheck is not None and ExpansionCount % 64 == 0:
            WorkCheck({
                "Stage": "physical-port-global-connector-fallback",
                "FallbackExpansionCount": ExpansionCount,
                "FallbackVisitedNodeCount": len(Previous),
                "GuideTargetCount": len(Field.Targets),
            })
        X, Y, Z = Current
        for Neighbor in (
            (X - 1, Y, Z),
            (X + 1, Y, Z),
            (X, Y, Z - 1),
            (X, Y, Z + 1),
        ):
            if (
                not NodeIsLegal(Neighbor)
                or not EdgeBelongsToSearchDomain(Current, Neighbor)
                or not EdgeIsLegal(Current, Neighbor)
            ):
                continue
            NeighborDistance = CurrentDistance + 1
            if NeighborDistance >= PathDistance.get(Neighbor, 1 << 60):
                continue
            Previous[Neighbor] = Current
            PathDistance[Neighbor] = NeighborDistance
            if Neighbor in Field.Targets:
                Candidate = Reconstruct(Neighbor)
                if ValidateCandidate(Candidate):
                    return PhysicalExteriorConnectorPathResult(
                        Path=Candidate,
                        UsedCanonicalField=False,
                        UsedFallback=True,
                        FallbackExpansionCount=ExpansionCount,
                    )
            heappush(Pending, (
                NeighborDistance + TargetDistance(Neighbor),
                NeighborDistance,
                Neighbor,
            ))
    return PhysicalExteriorConnectorPathResult(
        Path=(),
        UsedCanonicalField=False,
        UsedFallback=True,
        FallbackExpansionCount=ExpansionCount,
    )

def BuildPhysicalGlobalApertureSearchKey(
    Signal: str,
    Attachment: Position3,
    Direction: Position3,
    Layer: int,
    GuideCells: frozenset[Position2],
    ForeignClaimsFingerprint: str,
) -> tuple[object, ...]:
    """Identify exterior routing without importing a local access witness."""
    return (
        str(Signal),
        tuple(Attachment),
        tuple(Direction),
        int(Layer),
        frozenset(GuideCells),
        str(ForeignClaimsFingerprint),
    )

PhysicalGlobalAperturePlanarTransforms = (
    "Identity",
    "Rotate90",
    "Rotate180",
    "Rotate270",
    "MirrorX",
    "MirrorZ",
    "SwapXZ",
    "AntiSwapXZ",
)

MaximumPhysicalGlobalApertureTemplateCacheEntries = 2048

@dataclass(frozen=True)
class PreparedPhysicalGlobalApertureTransform:
    """Signal-static geometry for one exact planar aperture transform."""

    Transform: str
    Direction: Position3
    Targets: tuple[Position3, ...]
    EnvelopeCorners: tuple[Position3, ...]
    BlockedGuideCells: tuple[Position2, ...]
    ForeignWireCells: tuple[Position3, ...]
    ForeignSupportCells: tuple[Position3, ...]
    ForeignRequiredAirCells: tuple[Position3, ...]
    ForeignElectricalCells: tuple[Position3, ...]

@dataclass(frozen=True)
class PreparedPhysicalGlobalApertureStaticContract:
    """Pre-transformed aperture geometry shared by all attachments."""

    Layer: int
    TechnologyVersion: str
    TrackPitch: int
    TechnologyIdentity: str
    Transforms: tuple[PreparedPhysicalGlobalApertureTransform, ...]

def PreparePhysicalGlobalApertureStaticContract(
    Direction: Position3,
    Layer: int,
    Targets: frozenset[Position3],
    EnvelopeMinimum: Position3,
    EnvelopeMaximum: Position3,
    BlockedGuideCells: frozenset[Position2],
    ForeignClaims: RoutingResourceClaims | None,
    Technology: object,
) -> PreparedPhysicalGlobalApertureStaticContract:
    """Transform and sort signal-static aperture geometry exactly once."""
    ForeignClaims = ForeignClaims or RoutingResourceClaims()
    EnvelopeCorners = tuple(
        (X, Y, Z)
        for X in (EnvelopeMinimum[0], EnvelopeMaximum[0])
        for Y in (EnvelopeMinimum[1], EnvelopeMaximum[1])
        for Z in (EnvelopeMinimum[2], EnvelopeMaximum[2])
    )

    def TransformPositions(
        Positions: Iterable[Position3],
        Transform: str,
    ) -> tuple[Position3, ...]:
        return tuple(sorted(
            TransformPlanarRoutingPosition(Position, Transform)
            for Position in Positions
        ))

    return PreparedPhysicalGlobalApertureStaticContract(
        Layer=int(Layer),
        TechnologyVersion=str(getattr(
            Technology,
            "TechnologyVersion",
            "",
        )),
        TrackPitch=int(getattr(
            Technology,
            "TrackPitch",
            DefaultRedstoneRoutingTechnology.TrackPitch,
        )),
        TechnologyIdentity=repr(Technology),
        Transforms=tuple(
            PreparedPhysicalGlobalApertureTransform(
                Transform=Transform,
                Direction=TransformPlanarRoutingPosition(
                    tuple(map(int, Direction)),
                    Transform,
                ),
                Targets=TransformPositions(Targets, Transform),
                EnvelopeCorners=TransformPositions(
                    EnvelopeCorners,
                    Transform,
                ),
                BlockedGuideCells=tuple(sorted(
                    (
                        Transformed[0],
                        Transformed[2],
                    )
                    for Transformed in (
                        TransformPlanarRoutingPosition(
                            (int(X), 0, int(Z)),
                            Transform,
                        )
                        for X, Z in BlockedGuideCells
                    )
                )),
                ForeignWireCells=TransformPositions(
                    ForeignClaims.WireCells,
                    Transform,
                ),
                ForeignSupportCells=TransformPositions(
                    ForeignClaims.SupportCells,
                    Transform,
                ),
                ForeignRequiredAirCells=TransformPositions(
                    ForeignClaims.RequiredAirCells,
                    Transform,
                ),
                ForeignElectricalCells=TransformPositions(
                    ForeignClaims.ElectricalCells,
                    Transform,
                ),
            )
            for Transform in PhysicalGlobalAperturePlanarTransforms
        ),
    )

def InvertPlanarRoutingTransform(Transform: str) -> str:
    """Return the inverse of one supported planar routing transform."""
    Inverse = {
        "Identity": "Identity",
        "Rotate90": "Rotate270",
        "Rotate180": "Rotate180",
        "Rotate270": "Rotate90",
        "MirrorX": "MirrorX",
        "MirrorZ": "MirrorZ",
        "SwapXZ": "SwapXZ",
        "AntiSwapXZ": "AntiSwapXZ",
    }.get(str(Transform))
    if Inverse is None:
        raise ValueError(f"unknown planar routing transform: {Transform}")
    return Inverse

def BuildPortablePhysicalGlobalApertureContract(
    Attachment: Position3,
    Direction: Position3,
    Layer: int,
    Targets: frozenset[Position3],
    EnvelopeMinimum: Position3,
    EnvelopeMaximum: Position3,
    BlockedGuideCells: frozenset[Position2],
    ForeignClaims: RoutingResourceClaims | None,
    Technology: object,
    PreparedStaticContract: (
        PreparedPhysicalGlobalApertureStaticContract | None
    ) = None,
) -> tuple[str, tuple[object, ...], str]:
    """Canonicalize the complete exterior-aperture dependency contract."""
    Prepared = (
        PreparedStaticContract
        or PreparePhysicalGlobalApertureStaticContract(
            Direction,
            Layer,
            Targets,
            EnvelopeMinimum,
            EnvelopeMaximum,
            BlockedGuideCells,
            ForeignClaims,
            Technology,
        )
    )
    # Direction is the first transform-varying field in the serialized
    # contract.  Any transform whose direction does not have the minimum
    # representation cannot become canonical, regardless of the much larger
    # target/claim geometry that follows it.  Restricting to the tied
    # direction class preserves the exact fingerprint while avoiding six of
    # eight large relative-contract materializations for cardinal seams.
    MinimumDirectionRepresentation = min(
        repr(Value.Direction) for Value in Prepared.Transforms
    )
    EligibleTransforms = tuple(
        Value
        for Value in Prepared.Transforms
        if repr(Value.Direction) == MinimumDirectionRepresentation
    )
    Candidates = []
    for PreparedTransform in EligibleTransforms:
        Transform = PreparedTransform.Transform
        TransformedAttachment = TransformPlanarRoutingPosition(
            tuple(map(int, Attachment)),
            Transform,
        )
        TransformedBlockedAttachment = TransformPlanarRoutingPosition(
            (int(Attachment[0]), 0, int(Attachment[2])),
            Transform,
        )

        def RelativeTransformed(Position: Position3) -> Position3:
            return tuple(
                int(Position[Index]) - int(TransformedAttachment[Index])
                for Index in range(3)
            )

        TransformedEnvelopeCorners = tuple(
            RelativeTransformed(Position)
            for Position in PreparedTransform.EnvelopeCorners
        )
        TransformedEnvelopeMinimum = tuple(
            min(Position[Index] for Position in TransformedEnvelopeCorners)
            for Index in range(3)
        )
        TransformedEnvelopeMaximum = tuple(
            max(Position[Index] for Position in TransformedEnvelopeCorners)
            for Index in range(3)
        )

        Contract = (
            "physical-global-aperture-template-v1",
            PreparedTransform.Direction,
            Prepared.Layer,
            tuple(
                RelativeTransformed(Position)
                for Position in PreparedTransform.Targets
            ),
            TransformedEnvelopeMinimum,
            TransformedEnvelopeMaximum,
            tuple(
                (
                    int(X) - int(TransformedBlockedAttachment[0]),
                    int(Z) - int(TransformedBlockedAttachment[2]),
                )
                for X, Z in PreparedTransform.BlockedGuideCells
            ),
            tuple(
                RelativeTransformed(Position)
                for Position in PreparedTransform.ForeignWireCells
            ),
            tuple(
                RelativeTransformed(Position)
                for Position in PreparedTransform.ForeignSupportCells
            ),
            tuple(
                RelativeTransformed(Position)
                for Position in PreparedTransform.ForeignRequiredAirCells
            ),
            tuple(
                RelativeTransformed(Position)
                for Position in PreparedTransform.ForeignElectricalCells
            ),
            Prepared.TechnologyVersion,
            Prepared.TrackPitch,
            Prepared.TechnologyIdentity,
        )
        Candidates.append((Contract, Transform))
    CanonicalContract, CanonicalTransform = min(
        Candidates,
        key=lambda Value: repr(Value[0]),
    )
    return (
        BuildStableFingerprint(CanonicalContract),
        CanonicalContract,
        CanonicalTransform,
    )

def NormalizePhysicalGlobalAperturePath(
    Path: Iterable[Position3],
    Attachment: Position3,
    Transform: str,
) -> tuple[Position3, ...]:
    """Move one absolute witness into its canonical aperture coordinates."""
    return tuple(
        TransformPlanarRoutingPosition(
            tuple(
                int(Position[Index]) - int(Attachment[Index])
                for Index in range(3)
            ),
            Transform,
        )
        for Position in Path
    )

def MaterializePhysicalGlobalAperturePath(
    CanonicalPath: Iterable[Position3],
    Attachment: Position3,
    Transform: str,
) -> tuple[Position3, ...]:
    """Translate a canonical aperture witness into the current placement."""
    Inverse = InvertPlanarRoutingTransform(Transform)
    return tuple(
        TransformPlanarRoutingPosition(
            Position,
            Inverse,
            Attachment,
        )
        for Position in CanonicalPath
    )

def RetainPhysicalGlobalAperturePathTemplate(
    Cache: dict[str, Any],
    Template: PhysicalGlobalAperturePathTemplate,
    MaximumEntries: int = MaximumPhysicalGlobalApertureTemplateCacheEntries,
) -> None:
    """Retain one positive witness in deterministic insertion order."""
    if MaximumEntries < 1:
        raise ValueError("MaximumEntries must be positive")
    Cache.pop(Template.ContractFingerprint, None)
    Cache[Template.ContractFingerprint] = Template
    while len(Cache) > MaximumEntries:
        del Cache[next(iter(Cache))]

def SelectPhysicalFactorBranchSignal(
    DomainSizes: Mapping[str, int],
    NoGoodClauses: Iterable[frozenset[tuple[str, str]]],
) -> str:
    """Branch first on variables participating in learned exact cuts."""
    Signals = frozenset(map(str, DomainSizes))
    Degrees = Counter()
    for Clause in NoGoodClauses:
        ClauseSignals = frozenset(
            str(Signal)
            for Signal, _Fingerprint in Clause
            if str(Signal) in Signals
        )
        if len(ClauseSignals) < 2:
            continue
        for Signal in ClauseSignals:
            Degrees[Signal] += 1
    return min(
        Signals,
        key=lambda Signal: (
            -Degrees[Signal],
            int(DomainSizes[Signal]),
            Signal,
        ),
    )

def BuildPhysicalBoundaryPortAssignmentFingerprint(
    Reservations: Iterable[PhysicalComponentBoundaryPortReservation],
) -> str:
    """Identify one exact global-only component boundary assignment."""
    return BuildStableFingerprint((
        "physical-component-boundary-assignment-v1",
        tuple(sorted(
            (
                str(Value.Signal),
                str(Value.Direction),
                int(Value.Capacity),
                tuple(Value.Attachment),
                tuple(tuple(Position) for Position in Value.GlobalPath),
                tuple(sorted(map(str, getattr(
                    Value.GlobalClaims,
                    "ResourceIds",
                    (),
                )))),
                str(Value.ChannelContractFingerprint),
                str(Value.GlobalContractFingerprint),
                str(Value.ApertureContractFingerprint),
                str(Value.ReservationFingerprint),
            )
            for Value in Reservations
        )),
    ))

def BuildCachedPhysicalBoundaryOptionIdentity(
    Value: PhysicalComponentBoundaryPortReservation,
    Cache: dict[int, tuple[object, ...]],
) -> tuple[object, ...]:
    """Return one cached deterministic identity for a boundary option."""
    CacheKey = id(Value)
    Cached = Cache.get(CacheKey)
    if Cached is not None:
        return Cached
    Identity = (
        str(Value.Direction),
        int(Value.Capacity),
        tuple(Value.Attachment),
        tuple(tuple(Position) for Position in Value.GlobalPath),
        tuple(sorted(map(str, getattr(Value.GlobalClaims, "ResourceIds", ())))),
        str(Value.ChannelContractFingerprint),
        str(Value.GlobalContractFingerprint),
        str(Value.ApertureContractFingerprint),
        str(Value.ReservationFingerprint),
    )
    Cache[CacheKey] = Identity
    return Identity

def BuildCachedPhysicalBoundaryNoGoodKeys(
    Value: PhysicalComponentBoundaryPortReservation,
    Cache: dict[int, frozenset[tuple[str, str]]],
) -> frozenset[tuple[str, str]]:
    """Return cached clause keys exposed by one boundary option."""
    CacheKey = id(Value)
    Cached = Cache.get(CacheKey)
    if Cached is not None:
        return Cached
    Keys = frozenset((
        (Value.Signal, Value.GlobalContractFingerprint),
        (Value.Signal, Value.ApertureContractFingerprint),
        (Value.Signal, Value.ReservationFingerprint),
    ))
    Cache[CacheKey] = Keys
    return Keys

def IterPhysicalBoundaryPortAssignments(
    DomainsBySignal: Mapping[
        str, Iterable[PhysicalComponentBoundaryPortReservation]
    ],
    *,
    LocalAccessFactorsBySignal: Mapping[
        str, Iterable[PhysicalPortLocalAccessFactor]
    ] | None = None,
    ApertureFactorsBySignal: Mapping[
        str, Iterable[PhysicalPortApertureOptionFactor]
    ] | None = None,
    LocalApertureSupportBySignal: Mapping[
        str, Iterable[PhysicalPortLocalApertureSupport]
    ] | None = None,
    CertifiedLocalNoGoodClauses: Iterable[
        frozenset[tuple[str, str]]
    ] = (),
    LearnedLocalSeamNoGoodClauses: Iterable[
        frozenset[tuple[str, str]]
    ] = (),
    PortSolverCacheKey: str = "",
    PreferredGlobalContractsBySignal: Mapping[str, str] | None = None,
    RejectedGlobalApertureClauses: Iterable[
        frozenset[tuple[str, str]]
    ] = (),
    RejectedGlobalApertureFingerprintsBySignal: Mapping[
        str, Iterable[str]
    ] | None = None,
    PriorityInnermostSignals: Iterable[str] = (),
    CertifiedNoGoodProjectionOnly: bool = False,
    PersistentPairSupportCache: dict[str, bool] | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> Iterable[tuple[PhysicalComponentBoundaryPortReservation, ...]]:
    """Enumerate capacity-compatible tuples using global ownership only.

    Global claims remain the authoritative assignment variables.  When the
    prepared local/aperture support relation is supplied, a tuple is yielded
    only if it has at least one pairwise-compatible local support witness.
    That witness is existential and discarded: local compilation still owns
    the internal choice after the global boundary has been frozen.
    """
    BoundaryOptionIdentityCache: dict[int, tuple[object, ...]] = {}
    BoundaryOptionKeysCache: dict[int, frozenset[tuple[str, str]]] = {}
    OptionIdentity = lambda Value: BuildCachedPhysicalBoundaryOptionIdentity(
        Value, BoundaryOptionIdentityCache
    )
    BoundaryNoGoodKeys = lambda Value: BuildCachedPhysicalBoundaryNoGoodKeys(
        Value, BoundaryOptionKeysCache
    )

    PreferredGlobalContracts = {
        str(Signal): str(Fingerprint)
        for Signal, Fingerprint in (
            PreferredGlobalContractsBySignal or {}
        ).items()
        if str(Fingerprint)
    }
    RejectedApertureClausesSource = RejectedGlobalApertureClauses
    RejectedAperturesBySignalSource = (
        RejectedGlobalApertureFingerprintsBySignal
        if RejectedGlobalApertureFingerprintsBySignal is not None
        else {}
    )

    def CurrentRejectedApertureClauses() -> tuple[
        frozenset[tuple[str, str]], ...
    ]:
        return tuple(
            frozenset(
                (str(Signal), str(Fingerprint))
                for Signal, Fingerprint in Clause
            )
            for Clause in RejectedApertureClausesSource
            if Clause
        )

    def ApertureIsRejected(
        Value: PhysicalComponentBoundaryPortReservation,
    ) -> bool:
        return Value.ApertureContractFingerprint in frozenset(map(
            str,
            RejectedAperturesBySignalSource.get(Value.Signal, ()),
        ))

    Domains: dict[
        str, tuple[PhysicalComponentBoundaryPortReservation, ...]
    ] = {}
    for Signal, Values in DomainsBySignal.items():
        ByIdentity = {}
        for Value in Values:
            if str(Value.Signal) != str(Signal):
                raise ValueError(
                    "boundary port reservation stored under wrong signal"
                )
            if int(Value.Capacity) <= 0:
                continue
            ByIdentity.setdefault(OptionIdentity(Value), Value)
        SignalName = str(Signal)
        Preferred = PreferredGlobalContracts.get(SignalName, "")
        Domains[SignalName] = tuple(sorted(
            ByIdentity.values(),
            key=lambda Value: (
                0
                if Preferred
                and Value.GlobalContractFingerprint == Preferred
                else 1,
                OptionIdentity(Value),
            ),
        ))
    if not Domains or any(not Values for Values in Domains.values()):
        return

    LocalFactors = {
        str(Signal): tuple(Values)
        for Signal, Values in (LocalAccessFactorsBySignal or {}).items()
    }
    ApertureFactors = {
        str(Signal): tuple(Values)
        for Signal, Values in (ApertureFactorsBySignal or {}).items()
    }
    Supports = {
        str(Signal): tuple(Values)
        for Signal, Values in (LocalApertureSupportBySignal or {}).items()
    }
    UsePreparedLocalSupport = bool(
        LocalFactors and ApertureFactors and Supports
    )
    if CertifiedNoGoodProjectionOnly and not UsePreparedLocalSupport:
        raise ValueError(
            "certified no-good projection requires prepared local support"
        )
    CertifiedLocalNoGoods = tuple(CertifiedLocalNoGoodClauses)
    LearnedLocalNoGoodsSource = LearnedLocalSeamNoGoodClauses
    LocalSupportFeasibilityCache: dict[
        tuple[object, ...],
        tuple[
            tuple[tuple[tuple[str, str], ...], ...],
            tuple[PhysicalPortLocalAccessFactor, ...] | None,
        ],
    ] = {}
    LocalSupportSubproblemCache: dict[
        tuple[object, ...],
        tuple[PhysicalPortLocalAccessFactor, ...] | None,
    ] = {}
    LocalFactorNoGoodKeysCache: dict[
        int, frozenset[tuple[str, str]]
    ] = {}
    LocalFactorIdentityCache: dict[int, tuple[object, ...]] = {}
    LocalFactorDomainIdentityCache: dict[
        tuple[int, ...], tuple[tuple[object, ...], ...]
    ] = {}
    BoundaryPrefixExpansionCount = 0
    LocalSupportSearchExpansionCount = 0
    BoundaryPairSupportCheckCount = 0
    PersistentPairSupportCacheHitCount = 0
    PersistentPairSupportCacheStoreCount = 0
    PrioritySignalRank = {
        str(Signal): Index + 1
        for Index, Signal in enumerate(PriorityInnermostSignals)
    }
    ConstantDomainKeys = (
        frozenset(
            (
                Signal,
                "local-signal-domain:" + PortSolverCacheKey,
            )
            for Signal in Domains
        )
        if PortSolverCacheKey
        else frozenset()
    )
    def CurrentLocalNoGoods(
        GeneralProofNoGoods: tuple[
            frozenset[tuple[str, str]], ...
        ] | None = None,
    ) -> tuple[
        frozenset[tuple[str, str]], ...
    ]:
        if GeneralProofNoGoods is None:
            GeneralProofNoGoods = CurrentRejectedApertureClauses()
        LearnedLocalNoGoods = tuple(
            Clause
            for Clause in LearnedLocalNoGoodsSource
            if Clause
            and all(
                str(Fingerprint).startswith(
                    "local-seam-contract-v1:"
                )
                for _Signal, Fingerprint in Clause
            )
        )
        return tuple((
            *CertifiedLocalNoGoods,
            *GeneralProofNoGoods,
            *LearnedLocalNoGoods,
        ))

    def BoundaryApertureMatchIdentity(Value: Any) -> tuple[object, ...]:
        return (
            str(Value.Direction),
            int(Value.Capacity),
            tuple(Value.Attachment),
            tuple(Value.GlobalPath),
            str(Value.ChannelContractFingerprint),
            str(Value.GlobalContractFingerprint),
            str(Value.ApertureContractFingerprint),
        )

    ApertureFingerprintsByIdentity: dict[
        tuple[str, tuple[object, ...]], set[str]
    ] = {}
    for Signal, Values in ApertureFactors.items():
        for Value in Values:
            ApertureFingerprintsByIdentity.setdefault(
                (Signal, BoundaryApertureMatchIdentity(Value)),
                set(),
            ).add(str(Value.ApertureOptionFingerprint))
    SupportedLocalFingerprintsByAperture: dict[
        tuple[str, str], list[str]
    ] = {}
    for Signal, Values in Supports.items():
        for Value in Values:
            Key = (Signal, str(Value.ApertureOptionFingerprint))
            Fingerprint = str(Value.LocalAccessFingerprint)
            Domain = SupportedLocalFingerprintsByAperture.setdefault(
                Key,
                [],
            )
            if Fingerprint not in Domain:
                Domain.append(Fingerprint)
    LocalFactorsByAccessFingerprint: dict[
        tuple[str, str], list[PhysicalPortLocalAccessFactor]
    ] = {}
    for Signal, Values in LocalFactors.items():
        for Value in Values:
            LocalFactorsByAccessFingerprint.setdefault(
                (Signal, str(Value.LocalAccessFingerprint)),
                [],
            ).append(Value)
    CompiledLocalNoGoodDomainCache: dict[
        tuple[tuple[tuple[str, str], ...], ...],
        tuple[
            dict[str, frozenset[tuple[str, str]]],
            dict[tuple[str, str], frozenset[tuple[str, str]]],
            tuple[frozenset[tuple[str, str]], ...],
            bool,
        ],
    ] = {}
    LocalSupportNoGoodContextCache: dict[
        tuple[tuple[tuple[str, str], ...], ...],
        tuple[
            dict[str, frozenset[tuple[str, str]]],
            dict[tuple[str, str], frozenset[tuple[str, str]]],
            tuple[frozenset[tuple[str, str]], ...],
            bool,
            tuple[tuple[tuple[str, str], ...], ...],
            str,
            frozenset[tuple[str, str]],
        ],
    ] = {}
    LiveProjectedLocalDomainCache: dict[
        tuple[str, tuple[int, ...]],
        tuple[PhysicalPortLocalAccessFactor, ...],
    ] = {}

    def MatchingApertureFingerprint(
        Boundary: PhysicalComponentBoundaryPortReservation,
    ) -> str:
        Matches = ApertureFingerprintsByIdentity.get((
            str(Boundary.Signal),
            BoundaryApertureMatchIdentity(Boundary),
        ), set())
        if len(Matches) != 1:
            return ""
        return next(iter(Matches))

    def LocalFactorKeys(
        Value: PhysicalPortLocalAccessFactor,
    ) -> frozenset[tuple[str, str]]:
        CacheKey = id(Value)
        Cached = LocalFactorNoGoodKeysCache.get(CacheKey)
        if Cached is not None:
            return Cached
        Keys = frozenset((
            (Value.Signal, Value.LocalContractFingerprint),
            (
                Value.Signal,
                BuildPhysicalPortSeamContractFingerprint(Value),
            ),
            (
                Value.Signal,
                "fabric-domain:" + Value.FabricDomainFingerprint,
            ),
            (
                Value.Signal,
                "local-factor-domain:"
                + PortSolverCacheKey
                + ":"
                + Value.FabricDomainFingerprint,
            ),
            (
                Value.Signal,
                "local-signal-domain:" + PortSolverCacheKey,
            ),
        ))
        LocalFactorNoGoodKeysCache[CacheKey] = Keys
        return Keys

    def LocalFactorIdentity(
        Value: PhysicalPortLocalAccessFactor,
    ) -> tuple[object, ...]:
        """Identify every input used by local support compatibility."""
        CacheKey = id(Value)
        Cached = LocalFactorIdentityCache.get(CacheKey)
        if Cached is not None:
            return Cached
        Claims = Value.LocalClaims
        Identity = (
            str(Value.Signal),
            str(Value.LocalAccessFingerprint),
            str(Value.LocalContractFingerprint),
            BuildPhysicalPortSeamContractFingerprint(Value),
            str(Value.FabricDomainFingerprint),
            tuple(sorted(Claims.WireCells)),
            tuple(sorted(Claims.SupportCells)),
            tuple(sorted(Claims.RequiredAirCells)),
            tuple(sorted(Claims.ElectricalCells)),
        )
        LocalFactorIdentityCache[CacheKey] = Identity
        return Identity

    def LocalFactorDomainIdentity(
        Values: Iterable[PhysicalPortLocalAccessFactor],
    ) -> tuple[tuple[object, ...], ...]:
        ValuesTuple = tuple(Values)
        CacheKey = tuple(map(id, ValuesTuple))
        Cached = LocalFactorDomainIdentityCache.get(CacheKey)
        if Cached is not None:
            return Cached
        if CertifiedNoGoodProjectionOnly:
            # Projection-only boundary support cannot observe local claim
            # geometry.  Its complete state is the set of proof keys supplied
            # by each factor, so geometry-distinct factors with the same keys
            # are one exact CSP value rather than separate search branches.
            Identity = tuple(sorted(
                {
                    ("proof-key-projection", *tuple(sorted(
                        LocalFactorKeys(Value)
                    )))
                    for Value in ValuesTuple
                },
                key=repr,
            ))
        else:
            Identity = tuple(sorted(
                (LocalFactorIdentity(Value) for Value in ValuesTuple),
                key=repr,
            ))
        LocalFactorDomainIdentityCache[CacheKey] = Identity
        return Identity

    def CanonicalLocalSupportDomain(
        Values: Iterable[PhysicalPortLocalAccessFactor],
    ) -> tuple[PhysicalPortLocalAccessFactor, ...]:
        """Remove values indistinguishable to the active support contract."""
        ValuesTuple = tuple(Values)
        if not CertifiedNoGoodProjectionOnly:
            return ValuesTuple
        ByProjection = {}
        for Value in ValuesTuple:
            ByProjection.setdefault(
                tuple(sorted(LocalFactorKeys(Value))),
                Value,
            )
        return tuple(
            ByProjection[Projection]
            for Projection in sorted(ByProjection, key=repr)
        )

    def ProjectLocalSupportDomainToLiveNoGoods(
        Values: Iterable[PhysicalPortLocalAccessFactor],
        ReferencedKeys: frozenset[tuple[str, str]],
    ) -> tuple[PhysicalPortLocalAccessFactor, ...]:
        """Remove projection-only aliases invisible to every live clause."""
        ValuesTuple = tuple(Values)
        if not CertifiedNoGoodProjectionOnly:
            return ValuesTuple
        ByProjection = {}
        for Value in ValuesTuple:
            Projection = tuple(sorted(
                LocalFactorKeys(Value) & ReferencedKeys
            ))
            ByProjection.setdefault(Projection, Value)
        return tuple(
            ByProjection[Projection]
            for Projection in sorted(ByProjection, key=repr)
        )

    def CompileLocalSupportNoGoodContext(
        LocalNoGoods: tuple[
            frozenset[tuple[str, str]], ...
        ] | None = None,
    ) -> tuple[
        dict[str, frozenset[tuple[str, str]]],
        dict[tuple[str, str], frozenset[tuple[str, str]]],
        tuple[frozenset[tuple[str, str]], ...],
        bool,
        tuple[tuple[tuple[str, str], ...], ...],
        str,
        frozenset[tuple[str, str]],
    ]:
        """Compile one immutable local proof context for a support pass."""
        if LocalNoGoods is None:
            LocalNoGoods = CurrentLocalNoGoods()
        LocalNoGoodIdentity = tuple(sorted(
            tuple(sorted(Clause)) for Clause in LocalNoGoods
        ))
        CachedContext = LocalSupportNoGoodContextCache.get(
            LocalNoGoodIdentity
        )
        if CachedContext is not None:
            return CachedContext
        Compiled = CompiledLocalNoGoodDomainCache.get(LocalNoGoodIdentity)
        if Compiled is None:
            MutableForbiddenLocalKeysBySignal: dict[
                str, set[tuple[str, str]]
            ] = {}
            MutableResidualLocalBinaryByKey: dict[
                tuple[str, str], set[tuple[str, str]]
            ] = defaultdict(set)
            ResidualHigherOrderLocalNoGoods = []
            ConstantLocalNoGood = False
            for Clause in LocalNoGoods:
                Residual = frozenset(Clause - ConstantDomainKeys)
                if not Residual:
                    ConstantLocalNoGood = True
                elif len(Residual) == 1:
                    Key = next(iter(Residual))
                    MutableForbiddenLocalKeysBySignal.setdefault(
                        Key[0],
                        set(),
                    ).add(Key)
                elif len(Residual) == 2:
                    First, Second = tuple(Residual)
                    MutableResidualLocalBinaryByKey[First].add(Second)
                    MutableResidualLocalBinaryByKey[Second].add(First)
                else:
                    ResidualHigherOrderLocalNoGoods.append(Residual)
            Compiled = (
                {
                    Signal: frozenset(Values)
                    for Signal, Values
                    in MutableForbiddenLocalKeysBySignal.items()
                },
                {
                    Key: frozenset(Values)
                    for Key, Values
                    in MutableResidualLocalBinaryByKey.items()
                },
                tuple(ResidualHigherOrderLocalNoGoods),
                ConstantLocalNoGood,
            )
            CompiledLocalNoGoodDomainCache[
                LocalNoGoodIdentity
            ] = Compiled
        LocalNoGoodFingerprint = BuildStableFingerprint((
            "physical-local-support-no-good-context-v1",
            LocalNoGoodIdentity,
        ))
        ReferencedNoGoodKeys = frozenset(
            Key for Clause in LocalNoGoods for Key in Clause
        )
        CachedContext = (
            *Compiled,
            LocalNoGoodIdentity,
            LocalNoGoodFingerprint,
            ReferencedNoGoodKeys,
        )
        LocalSupportNoGoodContextCache[
            LocalNoGoodIdentity
        ] = CachedContext
        return CachedContext

    def ProjectLocalSupportDomainForContext(
        Values: Iterable[PhysicalPortLocalAccessFactor],
        LocalNoGoodFingerprint: str,
        ReferencedNoGoodKeys: frozenset[tuple[str, str]],
    ) -> tuple[PhysicalPortLocalAccessFactor, ...]:
        ValuesTuple = tuple(Values)
        CacheKey = (
            LocalNoGoodFingerprint,
            tuple(map(id, ValuesTuple)),
        )
        Cached = LiveProjectedLocalDomainCache.get(CacheKey)
        if Cached is not None:
            return Cached
        Cached = ProjectLocalSupportDomainToLiveNoGoods(
            ValuesTuple,
            ReferencedNoGoodKeys,
        )
        LiveProjectedLocalDomainCache[CacheKey] = Cached
        return Cached

    # Precompute the exact aperture-to-local-access relation.  Learned clauses
    # remain live, so the pair-support memo below includes their current domain
    # identity and never reuses an answer after that monotonic domain changes.
    BoundaryLocalFactorDomains: dict[
        tuple[str, int],
        tuple[PhysicalPortLocalAccessFactor, ...],
    ] = {}
    for Signal, BoundaryValues in Domains.items():
        for Boundary in BoundaryValues:
            ApertureFingerprint = MatchingApertureFingerprint(Boundary)
            SupportedLocalFingerprints = (
                SupportedLocalFingerprintsByAperture.get(
                    (Signal, ApertureFingerprint),
                    (),
                )
                if ApertureFingerprint
                else ()
            )
            BoundaryLocalFactorDomains[(
                Signal,
                id(Boundary),
            )] = CanonicalLocalSupportDomain(
                Value
                for Fingerprint in SupportedLocalFingerprints
                for Value in LocalFactorsByAccessFingerprint.get(
                    (Signal, Fingerprint),
                    (),
                )
            )
    BoundaryPersistentSupportIdentityCache: dict[
        tuple[str, int], str
    ] = {}

    def CompleteBoundaryPersistentSupportIdentity(
        Boundary: PhysicalComponentBoundaryPortReservation,
    ) -> str:
        Key = (str(Boundary.Signal), id(Boundary))
        Cached = BoundaryPersistentSupportIdentityCache.get(Key)
        if Cached is not None:
            return Cached
        Cached = BuildStableFingerprint((
            "physical-boundary-local-support-input-v1",
            str(Boundary.Signal),
            OptionIdentity(Boundary),
            LocalFactorDomainIdentity(BoundaryLocalFactorDomains.get(
                Key,
                (),
            )),
        ))
        BoundaryPersistentSupportIdentityCache[Key] = Cached
        return Cached
    DirectBoundaryPairSupportCache: dict[
        tuple[
            tuple[tuple[tuple[str, str], ...], ...],
            tuple[tuple[str, int], ...],
        ], bool
    ] = {}
    GlobalBoundaryPairCompatibilityCache: dict[
        tuple[int, int], bool
    ] = {}
    DirectLocalNoGoodCompilationCache: dict[
        tuple[tuple[tuple[str, str], ...], ...],
        tuple[
            tuple[frozenset[tuple[str, str]], ...],
            bool,
        ],
    ] = {}
    DirectLocalNoGoodFingerprintCache: dict[
        tuple[tuple[tuple[str, str], ...], ...], str
    ] = {}
    ProjectedBoundarySupportDataCache: dict[
        tuple[
            tuple[tuple[tuple[str, str], ...], ...],
            str,
            int,
        ],
        tuple[
            tuple[PhysicalPortLocalAccessFactor, ...],
            tuple[object, ...],
            str,
        ],
    ] = {}
    ProjectedDomainMaskCache: dict[
        tuple[
            tuple[tuple[tuple[str, str], ...], ...],
            str,
        ],
        tuple[
            tuple[frozenset[tuple[str, str]], ...],
            int,
            dict[tuple[str, str], int],
        ],
    ] = {}
    BoundaryPairLocalSupportEvaluationCount = 0
    BoundaryPairLocalSupportCacheHitCount = 0

    def CompileDirectLocalNoGoods(
        LocalNoGoods: tuple[
            frozenset[tuple[str, str]], ...
        ] | None = None,
    ) -> tuple[
        tuple[frozenset[tuple[str, str]], ...],
        bool,
        tuple[tuple[tuple[str, str], ...], ...],
    ]:
        if LocalNoGoods is None:
            LocalNoGoods = CurrentLocalNoGoods()
        LocalNoGoodIdentity = tuple(sorted(
            tuple(sorted(Clause)) for Clause in LocalNoGoods
        ))
        Cached = DirectLocalNoGoodCompilationCache.get(
            LocalNoGoodIdentity
        )
        if Cached is not None:
            return (*Cached, LocalNoGoodIdentity)
        Residuals = tuple(
            frozenset(Clause - ConstantDomainKeys)
            for Clause in LocalNoGoods
        )
        Compiled = (
            tuple(
                Residual for Residual in Residuals
                if Residual and len(Residual) <= 2
            ),
            any(not Residual for Residual in Residuals),
        )
        DirectLocalNoGoodCompilationCache[LocalNoGoodIdentity] = Compiled
        return (*Compiled, LocalNoGoodIdentity)

    def BoundaryOptionPairHasDirectLocalSupport(
        First: PhysicalComponentBoundaryPortReservation,
        Second: PhysicalComponentBoundaryPortReservation,
        DirectLocalContext: tuple[
            tuple[frozenset[tuple[str, str]], ...],
            bool,
            tuple[tuple[tuple[str, str], ...], ...],
        ] | None = None,
    ) -> bool:
        """Resolve exact two-option support without entering the local DFS."""
        nonlocal PersistentPairSupportCacheHitCount
        nonlocal PersistentPairSupportCacheStoreCount
        nonlocal BoundaryPairLocalSupportEvaluationCount
        nonlocal BoundaryPairLocalSupportCacheHitCount
        if not UsePreparedLocalSupport:
            return True
        (
            DirectLocalNoGoods,
            DirectLocalDomainUnsatisfiable,
            LocalNoGoodIdentity,
        ) = (
            DirectLocalContext
            if DirectLocalContext is not None
            else CompileDirectLocalNoGoods()
        )
        if CertifiedNoGoodProjectionOnly and not LocalNoGoodIdentity:
            return True
        ReferencedDirectLocalKeys = frozenset(
            Key for Clause in DirectLocalNoGoods for Key in Clause
        )
        LocalNoGoodFingerprint = DirectLocalNoGoodFingerprintCache.get(
            LocalNoGoodIdentity
        )
        if LocalNoGoodFingerprint is None:
            LocalNoGoodFingerprint = BuildStableFingerprint((
                "physical-boundary-local-no-good-domain-v1",
                LocalNoGoodIdentity,
            ))
            DirectLocalNoGoodFingerprintCache[
                LocalNoGoodIdentity
            ] = LocalNoGoodFingerprint
        if CertifiedNoGoodProjectionOnly:
            def ProjectedBoundarySupportData(
                Boundary: PhysicalComponentBoundaryPortReservation,
            ) -> tuple[
                tuple[PhysicalPortLocalAccessFactor, ...],
                tuple[object, ...],
                str,
            ]:
                CacheKey = (
                    LocalNoGoodIdentity,
                    str(Boundary.Signal),
                    id(Boundary),
                )
                Cached = ProjectedBoundarySupportDataCache.get(CacheKey)
                if Cached is not None:
                    return Cached
                Domain = ProjectLocalSupportDomainToLiveNoGoods(
                    BoundaryLocalFactorDomains.get(
                        (str(Boundary.Signal), id(Boundary)),
                        (),
                    ),
                    ReferencedDirectLocalKeys,
                )
                Identity = (
                    str(Boundary.Signal),
                    tuple(sorted(
                        BoundaryNoGoodKeys(Boundary)
                        & ReferencedDirectLocalKeys
                    )),
                    tuple(sorted(
                        {
                            tuple(sorted(
                                LocalFactorKeys(Value)
                                & ReferencedDirectLocalKeys
                            ))
                            for Value in Domain
                        },
                        key=repr,
                    )),
                )
                Fingerprint = BuildStableFingerprint((
                    "physical-boundary-projected-support-option-v1",
                    LocalNoGoodFingerprint,
                    Identity,
                ))
                Cached = (Domain, Identity, Fingerprint)
                ProjectedBoundarySupportDataCache[CacheKey] = Cached
                return Cached

            (
                FirstDomain,
                FirstSupportIdentity,
                FirstSupportFingerprint,
            ) = ProjectedBoundarySupportData(First)
            (
                SecondDomain,
                SecondSupportIdentity,
                SecondSupportFingerprint,
            ) = ProjectedBoundarySupportData(Second)
            OptionPairKey = tuple(sorted((
                FirstSupportFingerprint,
                SecondSupportFingerprint,
            )))
        else:
            FirstDomain = BoundaryLocalFactorDomains.get(
                (str(First.Signal), id(First)),
                (),
            )
            SecondDomain = BoundaryLocalFactorDomains.get(
                (str(Second.Signal), id(Second)),
                (),
            )
            FirstSupportIdentity = (
                CompleteBoundaryPersistentSupportIdentity(First)
            )
            SecondSupportIdentity = (
                CompleteBoundaryPersistentSupportIdentity(Second)
            )
            FirstSupportFingerprint = FirstSupportIdentity
            SecondSupportFingerprint = SecondSupportIdentity
            OptionPairKey = tuple(sorted((
                FirstSupportFingerprint,
                SecondSupportFingerprint,
            )))
        PairKey = (LocalNoGoodIdentity, OptionPairKey)
        if PairKey in DirectBoundaryPairSupportCache:
            BoundaryPairLocalSupportCacheHitCount += 1
            return DirectBoundaryPairSupportCache[PairKey]
        BoundaryPairLocalSupportEvaluationCount += 1
        PersistentPairKey = BuildStableFingerprint((
            "physical-boundary-direct-local-pair-support-v3",
            LocalNoGoodFingerprint,
            bool(CertifiedNoGoodProjectionOnly),
            OptionPairKey,
        ))
        PersistentSupported = (
            PersistentPairSupportCache.get(PersistentPairKey)
            if PersistentPairSupportCache is not None
            else None
        )
        if PersistentSupported is not None:
            PersistentPairSupportCacheHitCount += 1
            DirectBoundaryPairSupportCache[PairKey] = PersistentSupported
            return PersistentSupported
        Supported = False
        if (
            not DirectLocalDomainUnsatisfiable
            and FirstDomain
            and SecondDomain
        ):
            if CertifiedNoGoodProjectionOnly:
                # Projection proofs intentionally ignore local claim geometry,
                # but they must preserve every complete unary/binary no-good.
                # Compile the second factor domain to integer key masks so one
                # exact first-factor check replaces the Cartesian pair scan.
                # For a residual two-key clause, a second factor is rejected
                # precisely when it supplies every key not already supplied by
                # the two boundary options and the selected first factor.
                if len(FirstDomain) > len(SecondDomain):
                    FirstDomain, SecondDomain = SecondDomain, FirstDomain
                    FirstSupportFingerprint, SecondSupportFingerprint = (
                        SecondSupportFingerprint,
                        FirstSupportFingerprint,
                    )
                DomainMaskKey = (
                    LocalNoGoodIdentity,
                    SecondSupportFingerprint,
                )
                CompiledSecondDomain = ProjectedDomainMaskCache.get(
                    DomainMaskKey
                )
                if CompiledSecondDomain is None:
                    SecondFactorKeys = tuple(map(
                        LocalFactorKeys,
                        SecondDomain,
                    ))
                    SecondDomainMask = (1 << len(SecondDomain)) - 1
                    SecondMaskByKey: dict[tuple[str, str], int] = {}
                    for SecondIndex, Keys in enumerate(SecondFactorKeys):
                        Bit = 1 << SecondIndex
                        for Key in Keys:
                            SecondMaskByKey[Key] = (
                                SecondMaskByKey.get(Key, 0) | Bit
                            )
                    CompiledSecondDomain = (
                        SecondFactorKeys,
                        SecondDomainMask,
                        SecondMaskByKey,
                    )
                    ProjectedDomainMaskCache[
                        DomainMaskKey
                    ] = CompiledSecondDomain
                (
                    _SecondFactorKeys,
                    SecondDomainMask,
                    SecondMaskByKey,
                ) = CompiledSecondDomain
                BoundaryKeys = (
                    BoundaryNoGoodKeys(First)
                    | BoundaryNoGoodKeys(Second)
                )
                for FirstFactor in FirstDomain:
                    SelectedKeys = BoundaryKeys | LocalFactorKeys(FirstFactor)
                    RejectedSecondMask = 0
                    for Clause in DirectLocalNoGoods:
                        MissingKeys = Clause - SelectedKeys
                        if not MissingKeys:
                            RejectedSecondMask = SecondDomainMask
                            break
                        if len(MissingKeys) == 1:
                            RejectedSecondMask |= SecondMaskByKey.get(
                                next(iter(MissingKeys)),
                                0,
                            )
                        else:
                            FirstMissingKey, SecondMissingKey = tuple(
                                MissingKeys
                            )
                            RejectedSecondMask |= (
                                SecondMaskByKey.get(FirstMissingKey, 0)
                                & SecondMaskByKey.get(SecondMissingKey, 0)
                            )
                        if RejectedSecondMask == SecondDomainMask:
                            break
                    if RejectedSecondMask != SecondDomainMask:
                        Supported = True
                        break
            else:
                Supported = any(
                    not any(
                        Clause <= (
                            BoundaryNoGoodKeys(First)
                            | BoundaryNoGoodKeys(Second)
                            | LocalFactorKeys(FirstFactor)
                            | LocalFactorKeys(SecondFactor)
                        )
                        for Clause in DirectLocalNoGoods
                    )
                    and not ComponentClaimsConflict(
                        FirstFactor.LocalClaims,
                        SecondFactor.LocalClaims,
                    )
                    for FirstFactor in FirstDomain
                    for SecondFactor in SecondDomain
                )
        DirectBoundaryPairSupportCache[PairKey] = Supported
        if PersistentPairSupportCache is not None:
            PersistentPairSupportCache[PersistentPairKey] = Supported
            PersistentPairSupportCacheStoreCount += 1
        return Supported

    def BoundaryOptionsHaveCompatibleGlobalClaims(
        First: PhysicalComponentBoundaryPortReservation,
        Second: PhysicalComponentBoundaryPortReservation,
    ) -> bool:
        """Return exact pair compatibility for the global boundary CSP."""
        PairKey = tuple(sorted((id(First), id(Second))))
        Cached = GlobalBoundaryPairCompatibilityCache.get(PairKey)
        if Cached is not None:
            return Cached
        Compatible = not ComponentClaimsConflict(
            First.GlobalClaims,
            Second.GlobalClaims,
        )
        GlobalBoundaryPairCompatibilityCache[PairKey] = Compatible
        return Compatible

    def BoundaryTupleHasLocalSupport(
        Boundaries: tuple[
            PhysicalComponentBoundaryPortReservation, ...
        ],
        LocalSupportContext: tuple[
            dict[str, frozenset[tuple[str, str]]],
            dict[tuple[str, str], frozenset[tuple[str, str]]],
            tuple[frozenset[tuple[str, str]], ...],
            bool,
            tuple[tuple[tuple[str, str], ...], ...],
            str,
            frozenset[tuple[str, str]],
        ] | None = None,
    ) -> bool:
        nonlocal LocalSupportSearchExpansionCount
        if not UsePreparedLocalSupport:
            return True
        if LocalSupportContext is None:
            LocalSupportContext = CompileLocalSupportNoGoodContext()
        (
            ForbiddenLocalKeysBySignal,
            ResidualLocalBinaryByKey,
            ResidualHigherOrderLocalNoGoodsTuple,
            ConstantLocalNoGood,
            LocalNoGoodIdentity,
            LocalNoGoodFingerprint,
            ReferencedNoGoodKeys,
        ) = LocalSupportContext
        if CertifiedNoGoodProjectionOnly and not LocalNoGoodIdentity:
            return True
        DomainsByLocalSignal = {}
        for Boundary in Boundaries:
            Signal = str(Boundary.Signal)
            Domain = BoundaryLocalFactorDomains.get(
                (Signal, id(Boundary)),
                (),
            )
            if not Domain:
                return False
            DomainsByLocalSignal[Signal] = Domain
        if not Boundaries:
            DomainsByLocalSignal = {
                str(Signal): CanonicalLocalSupportDomain(Values)
                for Signal, Values in LocalFactors.items()
            }
            if any(
                not Values for Values in DomainsByLocalSignal.values()
            ):
                return False

        def ViolatesResidualLocalNoGood(
            Keys: frozenset[tuple[str, str]],
        ) -> bool:
            if any(
                Keys.intersection(
                    ResidualLocalBinaryByKey.get(Key, set())
                )
                for Key in Keys
            ):
                return True
            return any(
                Clause <= Keys
                for Clause in ResidualHigherOrderLocalNoGoodsTuple
            )

        def LocalFactorExtendsWithoutNoGood(
            FactorKeys: frozenset[tuple[str, str]],
            SelectedKeys: frozenset[tuple[str, str]],
        ) -> bool:
            """Check only clauses whose last key can enter with this factor."""
            CombinedKeys = SelectedKeys | FactorKeys
            if any(
                CombinedKeys.intersection(
                    ResidualLocalBinaryByKey.get(Key, set())
                )
                for Key in FactorKeys
            ):
                return False
            return not any(
                Clause <= CombinedKeys
                for Clause in ResidualHigherOrderLocalNoGoodsTuple
                if Clause.intersection(FactorKeys)
            )
        if ConstantLocalNoGood:
            return False
        # Aperture names are not local CSP state.  Different global aperture
        # prefixes frequently project to the exact same local factor domains;
        # key the retained proof by that canonical projection so those aliases
        # share one complete existential-support result.
        BoundaryKeys = frozenset(
            Key for Boundary in Boundaries
            for Key in BoundaryNoGoodKeys(Boundary)
        )
        DomainsByLocalSignal = {
            Signal: ProjectLocalSupportDomainForContext(
                Values,
                LocalNoGoodFingerprint,
                ReferencedNoGoodKeys,
            )
            for Signal, Values in DomainsByLocalSignal.items()
        }
        RelevantBoundaryKeys = BoundaryKeys & ReferencedNoGoodKeys
        StateIdentity = (
            tuple(sorted(RelevantBoundaryKeys)),
            tuple(
                (
                    Signal,
                    LocalFactorDomainIdentity(
                        DomainsByLocalSignal[Signal]
                    ),
                )
                for Signal in sorted(DomainsByLocalSignal)
            ),
        )
        Cached = LocalSupportFeasibilityCache.get(StateIdentity)
        if Cached is not None:
            CachedNoGoodIdentity, CachedWitness = Cached
            if frozenset(CachedNoGoodIdentity).issubset(
                frozenset(LocalNoGoodIdentity)
            ):
                if CachedWitness is None:
                    # A complete empty support domain stays empty while
                    # proof-qualified clauses only grow.
                    return False
                CachedWitnessKeys = ConstantDomainKeys | frozenset(
                    Key
                    for Value in CachedWitness
                    for Key in LocalFactorKeys(Value)
                )
                if not ViolatesResidualLocalNoGood(
                    CachedWitnessKeys - ConstantDomainKeys
                ) and not any(
                    CachedWitnessKeys.intersection(Values)
                    for Values in ForbiddenLocalKeysBySignal.values()
                ):
                    # Preserve a positive existential certificate until a
                    # newly learned clause actually intersects it.  This is
                    # the retained local-support frontier for the immutable
                    # aperture prefix; unrelated cores no longer replay its
                    # complete DFS.
                    return True

        def LocalSearch(
            Remaining: tuple[str, ...],
            Selected: tuple[PhysicalPortLocalAccessFactor, ...],
            SelectedKeys: frozenset[tuple[str, str]],
        ) -> tuple[PhysicalPortLocalAccessFactor, ...] | None:
            nonlocal LocalSupportSearchExpansionCount
            # Exact higher-order support DP.  The key includes the complete
            # live no-good identity, every remaining factor domain, and exact
            # selected claim geometry.  It therefore reuses suffix proofs
            # across aperture-prefix aliases without weakening complete
            # higher-order clauses or physical claim compatibility.
            SubproblemKey = (
                LocalNoGoodFingerprint,
                tuple(
                    (
                        Signal,
                        LocalFactorDomainIdentity(
                            DomainsByLocalSignal[Signal]
                        ),
                    )
                    for Signal in sorted(Remaining)
                ),
                (
                    ()
                    if CertifiedNoGoodProjectionOnly
                    else tuple(sorted(
                        (
                            LocalFactorIdentity(Value)
                            for Value in Selected
                        ),
                        key=repr,
                    ))
                ),
                tuple(sorted(SelectedKeys)),
                bool(CertifiedNoGoodProjectionOnly),
            )
            if SubproblemKey in LocalSupportSubproblemCache:
                return LocalSupportSubproblemCache[SubproblemKey]
            LocalSupportSearchExpansionCount += 1
            if (
                WorkCheck is not None
                and LocalSupportSearchExpansionCount % 64 == 0
            ):
                WorkCheck({
                    "Stage": "physical-port-boundary-support-propagation",
                    "BoundaryPrefixExpansionCount": (
                        BoundaryPrefixExpansionCount
                    ),
                    "LocalSupportSearchExpansionCount": (
                        LocalSupportSearchExpansionCount
                    ),
                    "LocalSupportStateCount": len(
                        LocalSupportFeasibilityCache
                    ),
                    "ProjectedLocalSupportDomainCountBySignal": {
                        Signal: len(Values)
                        for Signal, Values
                        in sorted(DomainsByLocalSignal.items())
                    },
                    "ImplicitForeignTransitDomainCount": 0,
                })
            if not Remaining:
                LocalSupportSubproblemCache[SubproblemKey] = Selected
                return Selected
            CompatibleDomains = {}
            for Signal in Remaining:
                Values = tuple(
                    Value
                    for Value in DomainsByLocalSignal[Signal]
                    if not (
                        LocalFactorKeys(Value)
                        & ForbiddenLocalKeysBySignal.get(Signal, set())
                    )
                    if (
                        CertifiedNoGoodProjectionOnly
                        or all(
                            not ComponentClaimsConflict(
                                Value.LocalClaims,
                                Existing.LocalClaims,
                            )
                            for Existing in Selected
                        )
                    )
                    and LocalFactorExtendsWithoutNoGood(
                        LocalFactorKeys(Value),
                        SelectedKeys,
                    )
                )
                if not Values:
                    LocalSupportSubproblemCache[SubproblemKey] = None
                    return None
                CompatibleDomains[Signal] = Values
            Signal = min(
                Remaining,
                key=lambda Value: (
                    len(CompatibleDomains[Value]),
                    Value,
                ),
            )
            NextRemaining = tuple(
                Value for Value in Remaining if Value != Signal
            )
            for Value in CompatibleDomains[Signal]:
                Witness = LocalSearch(
                    NextRemaining,
                    (*Selected, Value),
                    SelectedKeys | LocalFactorKeys(Value),
                )
                if Witness is not None:
                    LocalSupportSubproblemCache[SubproblemKey] = Witness
                    return Witness
            LocalSupportSubproblemCache[SubproblemKey] = None
            return None

        Witness = LocalSearch(
            tuple(sorted(DomainsByLocalSignal)),
            (),
            ConstantDomainKeys | RelevantBoundaryKeys,
        )
        LocalSupportFeasibilityCache[StateIdentity] = (
            LocalNoGoodIdentity,
            Witness,
        )
        return Witness is not None

    def CompatibleWithSelected(
        Candidate: PhysicalComponentBoundaryPortReservation,
        Selected: tuple[PhysicalComponentBoundaryPortReservation, ...],
    ) -> bool:
        return all(
            not ComponentClaimsConflict(
                Candidate.GlobalClaims,
                Value.GlobalClaims,
            )
            for Value in Selected
        )

    def ViolatesRejectedGlobalApertureClause(
        Selected: tuple[PhysicalComponentBoundaryPortReservation, ...],
    ) -> bool:
        SelectedKeys = frozenset((
            *ConstantDomainKeys,
            *(
                (Value.Signal, Value.GlobalContractFingerprint)
                for Value in Selected
            ),
            *(
                (Value.Signal, Value.ApertureContractFingerprint)
                for Value in Selected
            ),
            *(
                (Value.Signal, Value.ReservationFingerprint)
                for Value in Selected
            ),
        ))
        return any(
            Clause <= SelectedKeys
            for Clause in CurrentRejectedApertureClauses()
        )

    def PropagateRejectedBoundaryApertureClauses(
        Remaining: tuple[str, ...],
        Selected: tuple[PhysicalComponentBoundaryPortReservation, ...],
        CandidateDomains: dict[
            str, tuple[PhysicalComponentBoundaryPortReservation, ...]
        ],
    ) -> dict[
        str, tuple[PhysicalComponentBoundaryPortReservation, ...]
    ] | None:
        """Enforce unary/binary learned aperture cuts to a fixed point."""
        nonlocal BoundaryPairSupportCheckCount
        BaseKeys = frozenset((
            *ConstantDomainKeys,
            *((Value.Signal, Value.GlobalContractFingerprint)
              for Value in Selected),
            *((Value.Signal, Value.ApertureContractFingerprint)
              for Value in Selected),
            *((Value.Signal, Value.ReservationFingerprint)
              for Value in Selected),
        ))

        def OptionKeys(
            Value: PhysicalComponentBoundaryPortReservation,
        ) -> frozenset[tuple[str, str]]:
            return BoundaryNoGoodKeys(Value)

        CurrentRejectedClauses = CurrentRejectedApertureClauses()
        ResidualClauses = tuple(
            Residual
            for Clause in CurrentRejectedClauses
            for Residual in (frozenset(Clause - BaseKeys),)
            if Residual and len(Residual) <= 2
        )
        DirectLocalContext = CompileDirectLocalNoGoods(
            CurrentLocalNoGoods(CurrentRejectedClauses)
        )
        MutableDomains = {
            Signal: list(Values)
            for Signal, Values in CandidateDomains.items()
        }
        Changed = True
        while Changed:
            Changed = False
            for Signal in Remaining:
                Retained = []
                for Option in MutableDomains[Signal]:
                    BoundaryPairSupportCheckCount += 1
                    if (
                        WorkCheck is not None
                        and BoundaryPairSupportCheckCount % 64 == 0
                    ):
                        WorkCheck({
                            "Stage": (
                                "physical-port-boundary-pair-"
                                "support-propagation"
                            ),
                            "BoundaryPrefixExpansionCount": (
                                BoundaryPrefixExpansionCount
                            ),
                            "BoundaryPairSupportCheckCount": (
                                BoundaryPairSupportCheckCount
                            ),
                            "LocalSupportSearchExpansionCount": (
                                LocalSupportSearchExpansionCount
                            ),
                            "LocalSupportStateCount": len(
                                LocalSupportFeasibilityCache
                            ),
                            "PersistentBoundaryPairSupportCacheHitCount": (
                                PersistentPairSupportCacheHitCount
                            ),
                            "PersistentBoundaryPairSupportCacheStoreCount": (
                                PersistentPairSupportCacheStoreCount
                            ),
                            "PersistentBoundaryPairSupportCacheEntryCount": (
                                len(PersistentPairSupportCache)
                                if PersistentPairSupportCache is not None
                                else 0
                            ),
                            "BoundaryPairLocalSupportEvaluationCount": (
                                BoundaryPairLocalSupportEvaluationCount
                            ),
                            "BoundaryPairLocalSupportCacheHitCount": (
                                BoundaryPairLocalSupportCacheHitCount
                            ),
                            "ImplicitForeignTransitDomainCount": 0,
                        })
                    Keys = OptionKeys(Option)
                    if any(
                        Clause <= Keys for Clause in ResidualClauses
                    ):
                        continue
                    Supported = True
                    for OtherSignal in Remaining:
                        if OtherSignal == Signal:
                            continue
                        if not any(
                            not any(
                                Clause <= (Keys | OptionKeys(Other))
                                for Clause in ResidualClauses
                            )
                            and BoundaryOptionsHaveCompatibleGlobalClaims(
                                Option,
                                Other,
                            )
                            and (
                                not CertifiedNoGoodProjectionOnly
                                or BoundaryOptionPairHasDirectLocalSupport(
                                    Option,
                                    Other,
                                    DirectLocalContext,
                                )
                            )
                            for Other in MutableDomains[OtherSignal]
                        ):
                            Supported = False
                            break
                    if Supported:
                        Retained.append(Option)
                if len(Retained) != len(MutableDomains[Signal]):
                    MutableDomains[Signal] = Retained
                    Changed = True
                    if not Retained:
                        return None
        return {
            Signal: tuple(Values)
            for Signal, Values in MutableDomains.items()
        }

    def Search(
        Remaining: tuple[str, ...],
        Selected: tuple[PhysicalComponentBoundaryPortReservation, ...],
        LocalSupportContext: tuple[
            dict[str, frozenset[tuple[str, str]]],
            dict[tuple[str, str], frozenset[tuple[str, str]]],
            tuple[frozenset[tuple[str, str]], ...],
            bool,
            tuple[tuple[tuple[str, str], ...], ...],
            str,
            frozenset[tuple[str, str]],
        ] | None = None,
    ) -> Iterable[tuple[PhysicalComponentBoundaryPortReservation, ...]]:
        nonlocal BoundaryPrefixExpansionCount
        BoundaryPrefixExpansionCount += 1
        if WorkCheck is not None and BoundaryPrefixExpansionCount % 64 == 0:
            WorkCheck({
                "Stage": "physical-port-global-boundary-propagation",
                "BoundaryPrefixExpansionCount": BoundaryPrefixExpansionCount,
                "LocalSupportSearchExpansionCount": (
                    LocalSupportSearchExpansionCount
                ),
                "LocalSupportStateCount": len(
                    LocalSupportFeasibilityCache
                ),
                "ImplicitForeignTransitDomainCount": 0,
            })
        if LocalSupportContext is None:
            LocalSupportContext = CompileLocalSupportNoGoodContext()
        if (
            (Selected or CertifiedNoGoodProjectionOnly)
            and not BoundaryTupleHasLocalSupport(
                Selected,
                LocalSupportContext,
            )
        ):
            return
        if not Remaining:
            yield tuple(sorted(
                Selected,
                key=lambda Value: str(Value.Signal),
            ))
            return
        CompatibleDomains = {
            Signal: tuple(
                Value
                for Value in Domains[Signal]
                if not ApertureIsRejected(Value)
                and CompatibleWithSelected(Value, Selected)
                and (
                    not CertifiedNoGoodProjectionOnly
                    or BoundaryTupleHasLocalSupport(tuple(sorted(
                        (*Selected, Value),
                        key=lambda Candidate: str(Candidate.Signal),
                    )), LocalSupportContext)
                )
            )
            for Signal in Remaining
        }
        if any(not Values for Values in CompatibleDomains.values()):
            return
        CompatibleDomains = PropagateRejectedBoundaryApertureClauses(
            Remaining,
            Selected,
            CompatibleDomains,
        )
        if CompatibleDomains is None:
            return
        Signal = min(
            Remaining,
            key=lambda Value: (
                PrioritySignalRank.get(Value, 0),
                len(CompatibleDomains[Value]),
                tuple(
                    OptionIdentity(Option)
                    for Option in CompatibleDomains[Value]
                ),
                Value,
            ),
        )
        NextRemaining = tuple(
            Value for Value in Remaining if Value != Signal
        )
        for Option in CompatibleDomains[Signal]:
            NextSelected = (*Selected, Option)
            if (
                any(ApertureIsRejected(Value) for Value in NextSelected)
                or ViolatesRejectedGlobalApertureClause(NextSelected)
            ):
                continue
            # The local support relation is a projection constraint on the
            # global boundary CSP.  Enforce it as soon as every determinant
            # currently selected is known instead of waiting for a complete
            # aperture tuple.  This preserves global ownership while pruning
            # whole unsupported subtrees before they become speculative plans.
            if not BoundaryTupleHasLocalSupport(
                tuple(sorted(
                    NextSelected,
                    key=lambda Value: str(Value.Signal),
                )),
                LocalSupportContext,
            ):
                continue
            # No proof source can mutate while an unsatisfiable subtree runs,
            # so its complete recursive search shares this immutable context.
            # Once an assignment is yielded, the caller may learn another
            # monotonic clause before resuming; refresh at every yield boundary
            # before requesting the next assignment from that child.
            Child = Search(
                NextRemaining,
                NextSelected,
                LocalSupportContext,
            )
            for Assignment in Child:
                yield Assignment
                LocalSupportContext = CompileLocalSupportNoGoodContext()

    yield from Search(tuple(sorted(Domains)), ())

def SelectPhysicalBoundaryPortAssignment(
    DomainsBySignal: Mapping[
        str, Iterable[PhysicalComponentBoundaryPortReservation]
    ],
    *,
    RejectedAssignmentFingerprints: Iterable[str] = (),
) -> tuple[PhysicalComponentBoundaryPortReservation, ...] | None:
    """Return the first globally legal tuple not rejected as a whole."""
    Rejected = frozenset(map(str, RejectedAssignmentFingerprints))
    return next((
        Values
        for Values in IterPhysicalBoundaryPortAssignments(DomainsBySignal)
        if BuildPhysicalBoundaryPortAssignmentFingerprint(Values)
        not in Rejected
    ), None)

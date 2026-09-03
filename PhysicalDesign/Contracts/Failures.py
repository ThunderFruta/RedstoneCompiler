"""Typed physical-routing failure records and repair recommendations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
import json
from math import isfinite


class RoutingFailureReason(str, Enum):
    InvalidCellContract = "InvalidCellContract"
    NoPinAccessPattern = "NoPinAccessPattern"
    PlacementOverlap = "PlacementOverlap"
    GlobalCapacityOverflow = "GlobalCapacityOverflow"
    NoConnectedGlobalRoute = "NoConnectedGlobalRoute"
    TrackAssignmentConflict = "TrackAssignmentConflict"
    NoLegalLayerTransition = "NoLegalLayerTransition"
    DetailedSearchExhausted = "DetailedSearchExhausted"
    ElectricalConflict = "ElectricalConflict"
    SupportConflict = "SupportConflict"
    HeadroomConflict = "HeadroomConflict"
    NoRepeaterSite = "NoRepeaterSite"
    FinalDrcViolation = "FinalDrcViolation"
    RuntimeBudgetExceeded = "RuntimeBudgetExceeded"
    Stagnated = "Stagnated"
    LocalClaimConflict = "LocalClaimConflict"
    LocalClaimDisconnected = "LocalClaimDisconnected"
    NoBoundaryEscape = "NoBoundaryEscape"
    PartialTreeExtensionFailed = "PartialTreeExtensionFailed"
    ClusterEntranceBudgetExceeded = "ClusterEntranceBudgetExceeded"
    OrganizationPolicyViolation = "OrganizationPolicyViolation"
    LocalMaterialBudgetExceeded = "LocalMaterialBudgetExceeded"
    MultiSourceStagnated = "MultiSourceStagnated"
    BoundaryEscapeInfeasible = "BoundaryEscapeInfeasible"
    GlobalCongestionUnresolved = "GlobalCongestionUnresolved"
    DetailedCongestionUnresolved = "DetailedCongestionUnresolved"
    RepeaterAccessInfeasible = "RepeaterAccessInfeasible"
    ClusterInterfaceUnsatisfiable = "ClusterInterfaceUnsatisfiable"
    ClusterInterfaceArchitectureUnsatisfiable = (
        "ClusterInterfaceArchitectureUnsatisfiable"
    )
    ClusterInterfacePortfolioExhausted = (
        "ClusterInterfacePortfolioExhausted"
    )
    ClusterInterfaceSolveIncomplete = "ClusterInterfaceSolveIncomplete"
    ClusterInterfaceInvariantViolation = (
        "ClusterInterfaceInvariantViolation"
    )
    PhysicalComponentAssemblyIncomplete = (
        "PhysicalComponentAssemblyIncomplete"
    )
    ComponentAccessCertificationIncomplete = (
        "ComponentAccessCertificationIncomplete"
    )
    ComponentTerminalAccessUnsatisfiable = (
        "ComponentTerminalAccessUnsatisfiable"
    )
    ComponentPerimeterSeamUnsatisfiable = (
        "ComponentPerimeterSeamUnsatisfiable"
    )
    ComponentPortBankCapacityUnsatisfiable = (
        "ComponentPortBankCapacityUnsatisfiable"
    )
    ComponentAccessCertificateIdentityMismatch = (
        "ComponentAccessCertificateIdentityMismatch"
    )
    ComponentPortAssignmentUnsatisfiable = (
        "ComponentPortAssignmentUnsatisfiable"
    )
    ComponentChannelCapacityUnsatisfiable = (
        "ComponentChannelCapacityUnsatisfiable"
    )
    ComponentLocalCompilationUnsatisfiable = (
        "ComponentLocalCompilationUnsatisfiable"
    )
    ComponentAssemblyIdentityMismatch = (
        "ComponentAssemblyIdentityMismatch"
    )
    ComponentDetailedRoutingFailed = (
        "ComponentDetailedRoutingFailed"
    )


class RoutingAssignmentCutClassification(str, Enum):
    """Known assignment-cut classes while retaining future typed values."""

    Unclassified = "unclassified-assignment-cut"
    SaturatedBoundaryCut = "saturated-boundary-cut"
    MandatoryAccessSelfConflict = "mandatory-access-self-conflict"
    SparseRegionRouteCut = "sparse-region-route-cut"
    DetailedCongestionCut = "detailed-congestion-cut"
    CandidateStarvationPlacementConflict = (
        "candidate-starvation-placement-conflict"
    )
    MandatoryBoundaryCapacityCut = "mandatory-boundary-capacity-cut"
    PortalCoveragePairConflict = "portal-coverage-pair-conflict"
    HigherOrderPlacementConflict = "higher-order-placement-conflict"
    PairwiseIncompatibility = "pairwise-incompatibility"
    NoCandidate = "no-candidate"
    WorkBudgetExhaustion = "work-budget-exhaustion"
    LargerMatchingFailure = "larger-matching-failure"
    StackedPlacementConflict = "stacked-placement-conflict"
    MultiPairPlacementConflict = "multi-pair-placement-conflict"
    RelocatedHigherOrderConflict = "relocated-higher-order-conflict"
    RelocatedLargerMatchingFailure = "relocated-larger-matching-failure"
    RelocatedMultiPairConflict = "relocated-multi-pair-conflict"
    RelocatedPairwiseIncompatibility = (
        "relocated-pairwise-incompatibility"
    )
    CompleteCandidateSetAssignmentFailure = (
        "complete candidate set assignment failure"
    )

    @classmethod
    def _missing_(
        cls,
        Value: object,
    ) -> "RoutingAssignmentCutClassification | None":
        # Failure artifacts are a forward-compatible diagnostic boundary.
        # Preserve a newly introduced classification as a typed string enum
        # instead of silently replacing its exact value with "unclassified".
        if not isinstance(Value, str) or not Value:
            return None
        Member = str.__new__(cls, Value)
        Member._name_ = "ForwardCompatible"
        Member._value_ = Value
        return Member


def _NormalizeRoutingAssignmentCutJson(Value: object) -> object:
    """Return deterministic JSON-compatible data for a failure snapshot."""
    if Value is None or isinstance(Value, str | bool | int):
        return Value
    if isinstance(Value, float):
        return Value if isfinite(Value) else str(Value)
    if isinstance(Value, Enum):
        return _NormalizeRoutingAssignmentCutJson(Value.value)
    if isinstance(Value, Mapping):
        return {
            str(Key): _NormalizeRoutingAssignmentCutJson(Item)
            for Key, Item in Value.items()
        }
    if isinstance(Value, tuple | list):
        return [
            _NormalizeRoutingAssignmentCutJson(Item)
            for Item in Value
        ]
    if isinstance(Value, set | frozenset):
        Normalized = [
            _NormalizeRoutingAssignmentCutJson(Item)
            for Item in Value
        ]
        return sorted(
            Normalized,
            key=lambda Item: json.dumps(
                Item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )
    return str(Value)


def _CanonicalRoutingAssignmentCutJson(Value: Mapping[str, object]) -> str:
    """Serialize one complete conflict graph with a stable byte identity."""
    Normalized = _NormalizeRoutingAssignmentCutJson(Value)
    return json.dumps(
        Normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _RoutingAssignmentCutSequence(Value: object) -> tuple[object, ...]:
    if not isinstance(Value, tuple | list | set | frozenset):
        return ()
    return tuple(Value)


def _RoutingAssignmentCutSignals(Value: object) -> tuple[str, ...]:
    return tuple(sorted({
        str(Signal)
        for Signal in _RoutingAssignmentCutSequence(Value)
        if Signal is not None and str(Signal)
    }))


def _RoutingAssignmentCutEdges(
    Value: object,
) -> tuple[tuple[str, str], ...]:
    Edges = set()
    for RawEdge in _RoutingAssignmentCutSequence(Value):
        Edge = _RoutingAssignmentCutSequence(RawEdge)
        if len(Edge) != 2:
            continue
        First, Second = str(Edge[0]), str(Edge[1])
        if not First or not Second:
            continue
        Edges.add(tuple(sorted((First, Second))))
    return tuple(sorted(Edges))


def _RoutingAssignmentCutCandidateCounts(
    Value: object,
) -> tuple[tuple[str, int], ...]:
    if not isinstance(Value, Mapping):
        return ()
    Counts = []
    for Signal, Count in Value.items():
        try:
            NormalizedCount = int(Count)
        except (TypeError, ValueError, OverflowError):
            continue
        Counts.append((str(Signal), NormalizedCount))
    return tuple(sorted(Counts))


def _RoutingAssignmentCutResourceHotspots(
    Value: object,
) -> tuple[tuple[int, int, int], ...]:
    Hotspots = set()
    for RawPosition in _RoutingAssignmentCutSequence(Value):
        Position = _RoutingAssignmentCutSequence(RawPosition)
        if len(Position) != 3:
            continue
        try:
            Hotspots.add(tuple(int(Coordinate) for Coordinate in Position))
        except (TypeError, ValueError, OverflowError):
            continue
    return tuple(sorted(Hotspots))


def _RoutingAssignmentCutDiagnosticSources(
    Diagnostics: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    Sources: list[Mapping[str, object]] = [Diagnostics]
    State = Diagnostics.get("RoutingEscalationState")
    if isinstance(State, Mapping):
        Sources.append(State)
    History = Diagnostics.get("EscalationHistory", ())
    if isinstance(History, tuple | list):
        Sources.extend(
            Entry
            for Entry in reversed(History)
            if isinstance(Entry, Mapping)
        )
    return tuple(Sources)


def _RoutingAssignmentCutFirstText(
    Sources: tuple[Mapping[str, object], ...],
    *Keys: str,
) -> str:
    for Source in Sources:
        for Key in Keys:
            Value = Source.get(Key)
            if Value is not None and str(Value):
                return str(Value)
    return ""


def _RoutingAssignmentCutFirstValue(
    Sources: tuple[Mapping[str, object], ...],
    *Keys: str,
) -> object:
    for Source in Sources:
        for Key in Keys:
            if Key in Source:
                return Source[Key]
    return ()


@dataclass(frozen=True)
class RoutingAssignmentCut:
    """Immutable assignment-cut evidence detached from mutable diagnostics."""

    Classification: RoutingAssignmentCutClassification
    ConflictGraphJson: str
    ConflictFingerprint: str = ""
    CandidateFingerprint: str = ""
    EffectiveWorkFingerprint: str = ""
    RelocationSignals: tuple[str, ...] = ()
    PriorityRelocationSignals: tuple[str, ...] = ()
    ConflictSignals: tuple[str, ...] = ()
    NoCandidateSignals: tuple[str, ...] = ()
    PairwiseConflictEdges: tuple[tuple[str, str], ...] = ()
    CandidateCounts: tuple[tuple[str, int], ...] = ()
    ResourceHotspots: tuple[tuple[int, int, int], ...] = ()
    PriorityRelocationTerminals: tuple[
        tuple[int, int, int], ...
    ] = ()
    SourceCandidateId: str = ""
    MandatoryAccessOwnershipFingerprint: str = ""
    AuthoritativeAccessDomainFingerprint: str = ""
    CompleteAssignmentCutProof: bool = False

    @property
    def AccessTopologyFingerprint(self) -> str:
        """Prefer exact portal-stem domains over placement-only ownership."""
        return (
            self.AuthoritativeAccessDomainFingerprint
            or self.MandatoryAccessOwnershipFingerprint
        )

    @property
    def ConflictGraph(self) -> dict[str, object]:
        """Return a fresh JSON graph without exposing mutable record state."""
        Value = json.loads(self.ConflictGraphJson)
        return Value if isinstance(Value, dict) else {}

    @classmethod
    def FromFailure(
        cls,
        Failure: "RoutingFailure",
        *,
        SourceCandidateId: str = "",
        MandatoryAccessOwnershipFingerprint: str = "",
    ) -> "RoutingAssignmentCut | None":
        """Snapshot all available assignment-cut evidence from one failure."""
        Diagnostics = (
            Failure.Diagnostics
            if isinstance(Failure.Diagnostics, Mapping)
            else {}
        )
        RawConflictGraph = Diagnostics.get("ConflictGraph")
        if isinstance(RawConflictGraph, Mapping):
            ConflictGraph = dict(RawConflictGraph)
        elif any(
            Key in Diagnostics
            for Key in (
                "Classification",
                "ConflictSignals",
                "NoCandidateSignals",
                "PairwiseIncompatibleEdges",
                "CandidateCounts",
                "ResourceHotspots",
            )
        ):
            # Some terminal assignment failures expose the graph fields at
            # diagnostic top level. Retain that entire payload so new graph
            # fields are never dropped by this typed projection.
            ConflictGraph = dict(Diagnostics)
        else:
            ConflictGraph = {}

        Sources = _RoutingAssignmentCutDiagnosticSources(Diagnostics)
        ClassificationText = str(
            ConflictGraph.get(
                "Classification",
                _RoutingAssignmentCutFirstText(
                    Sources,
                    "ConflictClassification",
                ),
            )
            or RoutingAssignmentCutClassification.Unclassified.value
        )
        RelocationSignals = _RoutingAssignmentCutSignals(
            ConflictGraph.get(
                "RelocationSignals",
                _RoutingAssignmentCutFirstValue(
                    Sources,
                    "RelocationSignals",
                ),
            )
        )
        PriorityRelocationSignals = _RoutingAssignmentCutSignals(
            ConflictGraph.get(
                "PriorityRelocationSignals",
                _RoutingAssignmentCutFirstValue(
                    Sources,
                    "PriorityRelocationSignals",
                ),
            )
        )
        GraphConflictSignals = _RoutingAssignmentCutSignals(
            ConflictGraph.get(
                "ConflictSignals",
                _RoutingAssignmentCutFirstValue(
                    Sources,
                    "ConflictSignals",
                    "AffectedSignals",
                ),
            )
        )
        ConflictSignals = tuple(sorted({
            *GraphConflictSignals,
            *(str(Signal) for Signal in Failure.AffectedNets),
        }))
        NoCandidateSignals = _RoutingAssignmentCutSignals(
            ConflictGraph.get(
                "NoCandidateSignals",
                _RoutingAssignmentCutFirstValue(
                    Sources,
                    "NoCandidateSignals",
                ),
            )
        )
        # Pair edges are authoritative placement constraints, not general
        # progress metadata.  In particular, a candidate-domain scan records
        # a provisional pair before recursively regenerating both endpoints.
        # If that regeneration is interrupted by an unrelated empty domain,
        # the history still contains the provisional edge even though the
        # expanded pair was never confirmed.  Only promote a pair explicitly
        # carried by the terminal conflict graph; completed repeated-pair
        # failures already report that edge in their current graph.
        PairwiseConflictEdges = _RoutingAssignmentCutEdges(
            ConflictGraph.get("PairwiseIncompatibleEdges", ())
        )
        CandidateCounts = _RoutingAssignmentCutCandidateCounts(
            ConflictGraph.get(
                "CandidateCounts",
                _RoutingAssignmentCutFirstValue(
                    Sources,
                    "CandidateCounts",
                ),
            )
        )
        ResourceHotspots = _RoutingAssignmentCutResourceHotspots(
            ConflictGraph.get(
                "ResourceHotspots",
                _RoutingAssignmentCutFirstValue(
                    Sources,
                    "ResourceHotspots",
                ),
            )
        )
        PriorityRelocationTerminals = (
            _RoutingAssignmentCutResourceHotspots(
                ConflictGraph.get(
                    "PriorityRelocationTerminals",
                    _RoutingAssignmentCutFirstValue(
                        Sources,
                        "PriorityRelocationTerminals",
                    ),
                )
            )
        )
        CandidateFingerprint = _RoutingAssignmentCutFirstText(
            Sources,
            "CandidateFingerprint",
            "CandidateFailureFingerprint",
        )
        EffectiveWorkFingerprint = _RoutingAssignmentCutFirstText(
            Sources,
            "EffectiveWorkFingerprint",
        )
        CanonicalGraphJson = _CanonicalRoutingAssignmentCutJson(ConflictGraph)
        ConflictFingerprint = _RoutingAssignmentCutFirstText(
            Sources,
            "ConflictFingerprint",
        )
        if not ConflictFingerprint and ConflictGraph:
            ConflictFingerprint = sha256(
                CanonicalGraphJson.encode("utf-8")
            ).hexdigest()[:16]
        EffectiveSourceCandidateId = (
            SourceCandidateId
            or _RoutingAssignmentCutFirstText(
                Sources,
                "SourceCandidateId",
                "CandidateId",
                "PlacementCandidate",
            )
        )
        EffectiveOwnershipFingerprint = (
            MandatoryAccessOwnershipFingerprint
            or _RoutingAssignmentCutFirstText(
                Sources,
                "MandatoryAccessOwnershipFingerprint",
            )
        )
        PatternSearch = Diagnostics.get(
            "ClusterInterfacePatternSearch",
            {},
        )
        if not isinstance(PatternSearch, Mapping):
            PatternSearch = {}
        AuthoritativeAccessDomainFingerprint = str(
            Diagnostics.get(
                "AuthoritativeCutAccessDomainFingerprint",
                PatternSearch.get(
                    "AuthoritativeCutAccessDomainFingerprint",
                    "",
                ),
            )
        )
        CompleteAssignmentCutProof = bool(
            Diagnostics.get(
                "CompleteAssignmentCutProof",
                ConflictGraph.get(
                    "CompleteAssignmentCutProof",
                    False,
                ),
            )
        )
        HasEvidence = bool(
            ConflictGraph
            or Failure.Reason == RoutingFailureReason.TrackAssignmentConflict
            or ConflictSignals
            or RelocationSignals
            or PriorityRelocationSignals
            or NoCandidateSignals
            or PairwiseConflictEdges
            or CandidateCounts
            or ResourceHotspots
            or PriorityRelocationTerminals
            or CandidateFingerprint
            or ConflictFingerprint
            or EffectiveWorkFingerprint
            or AuthoritativeAccessDomainFingerprint
        )
        if not HasEvidence:
            return None
        return cls(
            Classification=RoutingAssignmentCutClassification(
                ClassificationText
            ),
            ConflictGraphJson=CanonicalGraphJson,
            ConflictFingerprint=ConflictFingerprint,
            CandidateFingerprint=CandidateFingerprint,
            EffectiveWorkFingerprint=EffectiveWorkFingerprint,
            RelocationSignals=RelocationSignals,
            PriorityRelocationSignals=PriorityRelocationSignals,
            ConflictSignals=ConflictSignals,
            NoCandidateSignals=NoCandidateSignals,
            PairwiseConflictEdges=PairwiseConflictEdges,
            CandidateCounts=CandidateCounts,
            ResourceHotspots=ResourceHotspots,
            PriorityRelocationTerminals=(
                PriorityRelocationTerminals
            ),
            SourceCandidateId=EffectiveSourceCandidateId,
            MandatoryAccessOwnershipFingerprint=(
                EffectiveOwnershipFingerprint
            ),
            AuthoritativeAccessDomainFingerprint=(
                AuthoritativeAccessDomainFingerprint
            ),
            CompleteAssignmentCutProof=CompleteAssignmentCutProof,
        )

    def ToDictionary(self) -> dict[str, object]:
        """Return a stable, fully JSON-serializable diagnostic projection."""
        return {
            "Classification": self.Classification.value,
            "ConflictGraph": self.ConflictGraph,
            "ConflictGraphJson": self.ConflictGraphJson,
            "ConflictFingerprint": self.ConflictFingerprint,
            "CandidateFingerprint": self.CandidateFingerprint,
            "EffectiveWorkFingerprint": self.EffectiveWorkFingerprint,
            "RelocationSignals": list(self.RelocationSignals),
            "PriorityRelocationSignals": list(
                self.PriorityRelocationSignals
            ),
            "ConflictSignals": list(self.ConflictSignals),
            "NoCandidateSignals": list(self.NoCandidateSignals),
            "PairwiseConflictEdges": [
                list(Edge) for Edge in self.PairwiseConflictEdges
            ],
            "CandidateCounts": {
                Signal: Count for Signal, Count in self.CandidateCounts
            },
            "ResourceHotspots": [
                list(Position) for Position in self.ResourceHotspots
            ],
            "PriorityRelocationTerminals": [
                list(Position)
                for Position in self.PriorityRelocationTerminals
            ],
            "SourceCandidateId": self.SourceCandidateId,
            "MandatoryAccessOwnershipFingerprint": (
                self.MandatoryAccessOwnershipFingerprint
            ),
            "AuthoritativeAccessDomainFingerprint": (
                self.AuthoritativeAccessDomainFingerprint
            ),
            "CompleteAssignmentCutProof": (
                self.CompleteAssignmentCutProof
            ),
        }


@dataclass(frozen=True)
class RoutingFailure:
    """Machine-readable stage failure with bounded legal repair actions."""

    Reason: RoutingFailureReason
    Stage: str
    AffectedNets: tuple[str, ...] = ()
    Resources: tuple[str, ...] = ()
    Locations: tuple[tuple[int, int, int], ...] = ()
    RepairActions: tuple[str, ...] = ()
    Detail: str = ""
    Diagnostics: dict[str, object] | None = None

    def ToDictionary(self) -> dict[str, object]:
        return asdict(self)


class RoutingStageError(ValueError):
    """Exception wrapper retaining typed failure information."""

    def __init__(self, Failure: RoutingFailure):
        self.Failure = Failure
        Context = []
        if Failure.AffectedNets:
            Context.append(f"nets={','.join(Failure.AffectedNets)}")
        if Failure.Resources:
            Context.append(f"resources={','.join(Failure.Resources)}")
        if Failure.Locations:
            Context.append(f"locations={Failure.Locations}")
        super().__init__(
            f"{Failure.Stage}:{Failure.Reason.value}: {Failure.Detail}"
            + (f" ({'; '.join(Context)})" if Context else "")
        )

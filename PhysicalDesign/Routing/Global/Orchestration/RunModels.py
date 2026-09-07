"""Typed run values and phase-independent control results."""

from __future__ import annotations

from ....Contracts.Core import Position3
from ....Contracts.PlacementAccessHandoff import PlacementPinAccessStageObservation

from ....Runtime.Reliability import BuildStableFingerprint

from ....Runtime.Reliability import RoutingDeadline

from ....Resources.ResourceGraph import IndexedRoutingResourceGraph

from ....Resources.ResourceGraph import RoutingResourceClaims

from ....Resources.ResourceGraph import RoutingResourceId

from dataclasses import dataclass

from dataclasses import field

from typing import Any

from typing import Iterable

@dataclass(frozen=True)
class RawTrackAssignmentValue:
    """One immutable ordinary or local value before native assignment.

    The normal router materializes these values immediately before invoking
    the Rust MRV assignment.  Keeping the physical claims and objective in a
    typed value lets a pre-route portfolio publish the *same* finite domain
    without pretending that one candidate's selected result is a proof for a
    different placement.
    """

    Signal: str
    CandidateId: str
    Claims: RoutingResourceClaims
    MaterialCost: int
    FootprintGrowth: int
    Length: int
    BendCount: int
    ViaCount: int
    ValueKind: str = "ordinary"

    def __post_init__(self) -> None:
        if not self.Signal or not self.CandidateId:
            raise ValueError("raw track-assignment values require identities")
        if self.ValueKind not in {"ordinary", "local-claim"}:
            raise ValueError("raw track-assignment value kind is invalid")
        if min(
            self.MaterialCost,
            self.FootprintGrowth,
            self.Length,
            self.BendCount,
            self.ViaCount,
        ) < 0:
            raise ValueError("raw track-assignment objective cannot be negative")

    @property
    def ResourceIds(self) -> tuple[str, ...]:
        return tuple(sorted(map(str, self.Claims.ResourceIds)))

    def Encode(
        self,
        Indexed: IndexedRoutingResourceGraph,
    ) -> tuple[Any, ...]:
        """Return the existing Rust value tuple with no policy translation."""
        Wire, Support, Air, Electrical = Indexed.EncodeClaims(self.Claims)
        return (
            self.Signal,
            self.CandidateId,
            list(Wire),
            list(Support),
            list(Air),
            list(Electrical),
            self.MaterialCost,
            self.FootprintGrowth,
            self.Length,
            self.BendCount,
            self.ViaCount,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "CandidateId": self.CandidateId,
            "ValueKind": self.ValueKind,
            "MaterialCost": self.MaterialCost,
            "FootprintGrowth": self.FootprintGrowth,
            "Length": self.Length,
            "BendCount": self.BendCount,
            "ViaCount": self.ViaCount,
            "ResourceIds": list(self.ResourceIds),
        }

@dataclass(frozen=True)
class RawTrackAssignmentBaseClaim:
    """One immutable same-signal claim supplied to the native base mask."""

    Signal: str
    ClaimId: str
    Claims: RoutingResourceClaims

    def __post_init__(self) -> None:
        if not self.Signal or not self.ClaimId:
            raise ValueError("raw assignment base claims require identities")

    def Encode(
        self,
        Indexed: IndexedRoutingResourceGraph,
    ) -> tuple[Any, ...]:
        Wire, Support, Air, Electrical = Indexed.EncodeClaims(self.Claims)
        return (
            self.Signal,
            list(Wire),
            list(Support),
            list(Air),
            list(Electrical),
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "ClaimId": self.ClaimId,
            "ResourceIds": sorted(map(str, self.Claims.ResourceIds)),
        }

@dataclass(frozen=True)
class RawTrackAssignmentDomain:
    """A complete-or-typed-incomplete native track-assignment input domain.

    Resource indices are intentionally local to this domain.  Placement
    alternatives describe mutually exclusive physical worlds, so a future
    aggregate native selector must choose one domain before comparing its
    resource masks; flattening their signals into one assignment would demand
    that every placement route simultaneously.
    """

    ResourcePositions: tuple[Position3, ...]
    Values: tuple[RawTrackAssignmentValue, ...]
    BaseClaims: tuple[RawTrackAssignmentBaseClaim, ...]
    CandidateCounts: tuple[tuple[str, int], ...]
    CandidateDomainFingerprint: str
    LocalClaimDomainFingerprint: str
    PlacementFingerprint: str
    ResourceGraphFingerprint: str
    PortalDomainFingerprint: str
    Complete: bool
    IncompleteReason: str = ""
    PinAccessDomainFingerprint: str = ""
    PinAccessWitnessFingerprint: str = ""
    PinAccessHandoffObservation: PlacementPinAccessStageObservation | None = None
    MaximumAssignmentExpansions: int = 1
    MinimizeMaximumRoutingLayer: bool = False
    Diagnostics: tuple[tuple[str, object], ...] = ()
    # The native assignment API is attached as an execution handle only.  It
    # is deliberately excluded from every physical identity: raw domains from
    # different placement worlds retain local resource indices and may carry
    # different routing contexts, while their frozen proof fingerprints stay
    # purely geometric.
    NativeAssignmentContext: Any | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.ResourcePositions != tuple(sorted(set(self.ResourcePositions))):
            raise ValueError("raw assignment resources must be sorted and unique")
        if self.MaximumAssignmentExpansions < 1:
            raise ValueError("raw assignment requires a positive work cap")
        if self.Complete and self.IncompleteReason:
            raise ValueError("complete raw assignment domain has an incomplete reason")
        Keys = tuple((Value.Signal, Value.CandidateId) for Value in self.Values)
        if len(Keys) != len(set(Keys)):
            raise ValueError("raw assignment domain repeats a value identity")
        CandidateSignals = tuple(Signal for Signal, _Count in self.CandidateCounts)
        if CandidateSignals != tuple(sorted(set(CandidateSignals))):
            raise ValueError("raw assignment candidate counts must be sorted")
        if any(Count < 0 for _Signal, Count in self.CandidateCounts):
            raise ValueError("raw assignment candidate counts cannot be negative")
        IndexedPositions = frozenset(self.ResourcePositions)
        for Value in (*self.Values, *self.BaseClaims):
            Claims = Value.Claims
            if any(
                Position not in IndexedPositions
                for Cells in (
                    Claims.WireCells,
                    Claims.SupportCells,
                    Claims.RequiredAirCells,
                    Claims.ElectricalCells,
                )
                for Position in Cells
            ):
                raise ValueError("raw assignment claim is outside its index")

    @property
    def IndexedResources(self) -> IndexedRoutingResourceGraph:
        return IndexedRoutingResourceGraph(
            ResourcePositions=self.ResourcePositions,
            PositionIndices={
                Position: Index
                for Index, Position in enumerate(self.ResourcePositions)
            },
        )

    @property
    def DomainFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "raw-track-assignment-domain-v1",
            "ResourcePositions": self.ResourcePositions,
            "Values": [Value.ToDictionary() for Value in self.Values],
            "BaseClaims": [Claim.ToDictionary() for Claim in self.BaseClaims],
            "CandidateCounts": self.CandidateCounts,
            "CandidateDomainFingerprint": self.CandidateDomainFingerprint,
            "LocalClaimDomainFingerprint": self.LocalClaimDomainFingerprint,
            "PlacementFingerprint": self.PlacementFingerprint,
            "ResourceGraphFingerprint": self.ResourceGraphFingerprint,
            "PortalDomainFingerprint": self.PortalDomainFingerprint,
            "PinAccessDomainFingerprint": (
                self.PinAccessDomainFingerprint
            ),
            "PinAccessWitnessFingerprint": (
                self.PinAccessWitnessFingerprint
            ),
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "MaximumAssignmentExpansions": self.MaximumAssignmentExpansions,
            "MinimizeMaximumRoutingLayer": self.MinimizeMaximumRoutingLayer,
        })

    def NativeCandidateValues(self) -> list[tuple[Any, ...]]:
        Indexed = self.IndexedResources
        return [Value.Encode(Indexed) for Value in self.Values]

    def NativeBaseValues(self) -> list[tuple[Any, ...]]:
        Indexed = self.IndexedResources
        return [Claim.Encode(Indexed) for Claim in self.BaseClaims]

    def SelectedCapacityResourceIds(
        self,
        SelectedCandidateIds: Iterable[tuple[str, str]],
    ) -> tuple[str, ...]:
        SelectedKeys = frozenset(
            (str(Signal), str(CandidateId))
            for Signal, CandidateId in SelectedCandidateIds
        )
        KnownKeys = frozenset(
            (Value.Signal, Value.CandidateId) for Value in self.Values
        )
        Unknown = SelectedKeys - KnownKeys
        if Unknown:
            raise ValueError("raw assignment selected an unknown value")
        return tuple(sorted({
            ResourceId
            for Value in self.Values
            if (Value.Signal, Value.CandidateId) in SelectedKeys
            for ResourceId in Value.ResourceIds
        }))

    def ToDictionary(self) -> dict[str, object]:
        return {
            "DomainFingerprint": self.DomainFingerprint,
            "ResourceCount": len(self.ResourcePositions),
            "ValueCount": len(self.Values),
            "BaseClaimCount": len(self.BaseClaims),
            "CandidateCounts": [list(Value) for Value in self.CandidateCounts],
            "CandidateDomainFingerprint": self.CandidateDomainFingerprint,
            "LocalClaimDomainFingerprint": self.LocalClaimDomainFingerprint,
            "PlacementFingerprint": self.PlacementFingerprint,
            "ResourceGraphFingerprint": self.ResourceGraphFingerprint,
            "PortalDomainFingerprint": self.PortalDomainFingerprint,
            "PinAccessDomainFingerprint": (
                self.PinAccessDomainFingerprint
            ),
            "PinAccessWitnessFingerprint": (
                self.PinAccessWitnessFingerprint
            ),
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "MaximumAssignmentExpansions": self.MaximumAssignmentExpansions,
            "MinimizeMaximumRoutingLayer": self.MinimizeMaximumRoutingLayer,
            "Diagnostics": dict(self.Diagnostics),
        }

class RawTrackAssignmentDomainPrepared(RuntimeError):
    """Internal control transfer after immutable native inputs are frozen."""

    def __init__(self, Domain: RawTrackAssignmentDomain) -> None:
        super().__init__("raw track-assignment domain prepared")
        self.Domain = Domain

class OptionalPortalSeedSliceExpired(RuntimeError):
    """The optional portal-seed hint exhausted only its private time slice."""

class CandidateDomainPairScanExpired(RuntimeError):
    """Optional candidate-domain diagnosis exhausted its private time slice."""

@dataclass(frozen=True)
class ClusterLeaseCandidateRealizabilityNogood:
    """One access template disproven by authoritative candidate generation."""

    Signal: str
    PatternFingerprint: str
    CandidateFailureFingerprint: str

    def ToDictionary(self) -> dict[str, str]:
        return {
            "Signal": self.Signal,
            "PatternFingerprint": self.PatternFingerprint,
            "CandidateFailureFingerprint": (
                self.CandidateFailureFingerprint
            ),
        }

@dataclass(frozen=True)
class MandatoryPortalTupleSelfConflictEvidence:
    """One fully enumerated net-wide portal domain with no legal tuple."""

    Signal: str
    CompletePortalTupleCount: int
    EvaluatedPortalTupleCount: int
    TerminalPortalDomainCounts: tuple[int, ...]
    ConflictResources: tuple[RoutingResourceId, ...]
    PortalDomainCertificateFingerprint: str = ""
    PhysicalAssemblyPlanFingerprint: str = ""
    ResourceGraphFingerprint: str = ""
    TechnologyFingerprint: str = ""
    PlacementFingerprint: str = ""
    InterfaceFingerprint: str = ""
    SeamFingerprint: str = ""
    PortalRequestDomainFingerprint: str = ""
    ExactAttachmentValidationFingerprint: str = ""

    def __post_init__(self) -> None:
        if self.CompletePortalTupleCount < 1:
            raise ValueError("CompletePortalTupleCount must be positive")
        if (
            self.EvaluatedPortalTupleCount
            < self.CompletePortalTupleCount
        ):
            raise ValueError(
                "an incomplete portal tuple sample is not an exact "
                "conflict proof"
            )

    def AnonymousRecord(self) -> dict[str, object]:
        """Return translation- and identifier-independent proof geometry."""
        Positions = tuple(
            Resource.Position
            for Resource in self.ConflictResources
        )
        MinimumX = min(
            (Position[0] for Position in Positions),
            default=0,
        )
        MinimumY = min(
            (Position[1] for Position in Positions),
            default=0,
        )
        MinimumZ = min(
            (Position[2] for Position in Positions),
            default=0,
        )
        return {
            "TerminalPortalDomainCounts": sorted(
                int(Count)
                for Count in self.TerminalPortalDomainCounts
            ),
            "CompletePortalTupleCount": (
                self.CompletePortalTupleCount
            ),
            "ConflictResources": tuple(sorted(
                (
                    str(Resource.Kind.value),
                    (
                        Resource.Position[0] - MinimumX,
                        Resource.Position[1] - MinimumY,
                        Resource.Position[2] - MinimumZ,
                    ),
                )
                for Resource in self.ConflictResources
            )),
        }

@dataclass(frozen=True)
class RepeatedWorkTransition:
    """Bounded response when an escalation reproduces identical work."""

    Action: str
    SkipStrictPortalReservation: bool
    Deadline: RoutingDeadline

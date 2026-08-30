"""Physical boundary and authoritative-routing contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .Core import Position2, Position3
from .Component import (
    ComponentCutAccessFeasibilityCertificate,
    ComponentRoutingProblem,
    ComponentTerminalAccessCandidate,
    ComponentTerminalAccessDomain,
    PhysicalComponentAssemblyPlan,
)
from ..ChannelPlanner import ChannelPlan
from ..ResourceGraph import RoutingResourceClaims

@dataclass(frozen=True)
class PreparedPhysicalComponentAssembly:
    """Typed result of authoritative physical assembly preparation."""

    Plan: PhysicalComponentAssemblyPlan
    Problem: ComponentRoutingProblem
    GlobalGuidePlan: Any = field(
        default=None,
        compare=False,
        repr=False,
    )
    PortFactorDomain: PreparedPhysicalComponentPortFactorDomain | None = field(
        default=None,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True)
class PhysicalSignalRouteDomainDescriptorProgressState:
    """Exact completed-descriptor set for one physical exterior signal."""

    Signal: str
    PreSiblingDomainFingerprint: str
    RequestDomainFingerprint: str
    DescriptorUniverseFingerprint: str
    DescriptorCount: int
    CompletedDescriptorFingerprints: frozenset[str]

    def __post_init__(self) -> None:
        if (
            not self.Signal
            or not self.PreSiblingDomainFingerprint
            or not self.RequestDomainFingerprint
            or not self.DescriptorUniverseFingerprint
            or self.DescriptorCount < 1
        ):
            raise ValueError(
                "physical descriptor progress identity is incomplete"
            )
        if len(self.CompletedDescriptorFingerprints) > self.DescriptorCount:
            raise ValueError(
                "physical descriptor progress exceeds its universe"
            )

    @property
    def UniverseIdentity(self) -> tuple[object, ...]:
        return (
            self.Signal,
            self.PreSiblingDomainFingerprint,
            self.RequestDomainFingerprint,
            self.DescriptorUniverseFingerprint,
            self.DescriptorCount,
        )


@dataclass(frozen=True)
class PhysicalGlobalPlanDescriptorProgressState:
    """Identity-closed descriptor progress emitted by global planning."""

    PlanFingerprint: str
    ApertureDomainFingerprint: str
    Signals: tuple[PhysicalSignalRouteDomainDescriptorProgressState, ...]

    def __post_init__(self) -> None:
        if not self.PlanFingerprint or not self.ApertureDomainFingerprint:
            raise ValueError(
                "physical descriptor resume state is unidentified"
            )
        SignalNames = tuple(Value.Signal for Value in self.Signals)
        if not SignalNames or len(set(SignalNames)) != len(SignalNames):
            raise ValueError(
                "physical descriptor resume signals must be unique"
            )

    @property
    def CompletedDescriptorCount(self) -> int:
        return sum(
            len(Value.CompletedDescriptorFingerprints)
            for Value in self.Signals
        )

    @property
    def UniverseIdentities(self) -> tuple[tuple[object, ...], ...]:
        return tuple(Value.UniverseIdentity for Value in self.Signals)


@dataclass(frozen=True)
class PhysicalGlobalPlanResumeCursor:
    """Opaque, identity-bound cursor emitted by authoritative global routing."""

    CursorFingerprint: str
    PlanFingerprint: str
    ApertureDomainFingerprint: str
    CompletedWork: int
    State: Any = field(default=None, compare=False, repr=False)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CursorFingerprint": self.CursorFingerprint,
            "PlanFingerprint": self.PlanFingerprint,
            "ApertureDomainFingerprint": (
                self.ApertureDomainFingerprint
            ),
            "CompletedWork": self.CompletedWork,
        }


@dataclass(frozen=True)
class PhysicalGlobalPlanContinuationState:
    """Retained exact-channel frontier for one incomplete assembly plan."""

    StateFingerprint: str
    PlanFingerprint: str
    RequestDependencyFingerprints: tuple[tuple[str, str], ...]
    RemainingRequestCounts: tuple[tuple[str, int], ...]
    CorridorDomainFingerprints: tuple[tuple[str, str], ...]
    CertificateFingerprints: tuple[str, ...]
    CompletedWork: int
    Complete: bool = False
    ResumeCursor: PhysicalGlobalPlanResumeCursor | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "StateFingerprint": self.StateFingerprint,
            "PlanFingerprint": self.PlanFingerprint,
            "RequestDependencyFingerprints": dict(
                self.RequestDependencyFingerprints
            ),
            "RemainingRequestCounts": dict(self.RemainingRequestCounts),
            "CorridorDomainFingerprints": dict(
                self.CorridorDomainFingerprints
            ),
            "CertificateFingerprints": list(
                self.CertificateFingerprints
            ),
            "CompletedWork": self.CompletedWork,
            "Complete": self.Complete,
            "ResumeCursor": (
                self.ResumeCursor.ToDictionary()
                if self.ResumeCursor is not None
                else None
            ),
        }


@dataclass(frozen=True)
class RetainedPhysicalGlobalPlanFrontierEntry:
    """One fairly scheduled, proof-neutral incomplete physical plan."""

    Assembly: PreparedPhysicalComponentAssembly = field(
        compare=False,
        repr=False,
    )
    Continuation: PhysicalGlobalPlanContinuationState
    EnqueuedSequence: int
    LastScheduledSequence: int = -1
    ScheduleCount: int = 0
    AccumulatedCompletedWork: int = 0

    @property
    def PlanFingerprint(self) -> str:
        return self.Assembly.Plan.PlanFingerprint

    def ToDictionary(self) -> dict[str, object]:
        return {
            "PlanFingerprint": self.PlanFingerprint,
            "PortAssignmentFingerprint": (
                self.Assembly.Plan.PortAssignmentFingerprint
            ),
            "Continuation": self.Continuation.ToDictionary(),
            "EnqueuedSequence": self.EnqueuedSequence,
            "LastScheduledSequence": self.LastScheduledSequence,
            "ScheduleCount": self.ScheduleCount,
            "AccumulatedCompletedWork": self.AccumulatedCompletedWork,
            "Rejected": False,
        }


class PhysicalComponentAssemblyPrepared(RuntimeError):
    """Internal control transfer after port-first ownership is frozen."""

    def __init__(
        self,
        Assembly: PreparedPhysicalComponentAssembly,
    ) -> None:
        super().__init__("physical component assembly prepared")
        self.Assembly = Assembly

@dataclass(frozen=True)
class PhysicalPortSeamFactor:
    """One immutable local/global seam shared by physical port options."""

    FabricAttachment: Position3
    Attachment: Position3
    LocalPath: tuple[Position3, ...]
    GlobalPath: tuple[Position3, ...]
    Claims: RoutingResourceClaims
    SeamFingerprint: str
    OwnedCandidateFingerprints: tuple[str, ...] = ()


@dataclass(frozen=True)
class PhysicalPortLaneFactor:
    """Complete access and seam factor for one physical port lane."""

    Signal: str
    Direction: str
    Capacity: int
    OwnedTerminals: tuple[Position3, ...]
    Domains: tuple[ComponentTerminalAccessDomain, ...]
    CandidateDomains: tuple[
        tuple[ComponentTerminalAccessCandidate, ...], ...
    ]
    FabricDomainFingerprint: str
    Seams: tuple[PhysicalPortSeamFactor, ...]
    GuideCells: frozenset[tuple[int, int]]
    ExternalTerminals: tuple[Position3, ...]


@dataclass(frozen=True)
class PhysicalPortLocalAccessFactor:
    """One local-only component access choice, independent of its aperture."""

    Signal: str
    Direction: str
    Capacity: int
    OwnedTerminals: tuple[Position3, ...]
    OwnedTerminalFingerprints: tuple[str, ...]
    OwnedAccessCandidates: tuple[
        ComponentTerminalAccessCandidate, ...
    ]
    FabricDomainFingerprint: str
    FabricAttachment: Position3
    LocalPath: tuple[Position3, ...]
    LocalClaims: RoutingResourceClaims
    OwnedCandidateFingerprints: tuple[str, ...]
    LocalContractFingerprint: str
    LocalAccessFingerprint: str
    SeamContractFingerprint: str = ""


@dataclass(frozen=True)
class PhysicalPortApertureOptionFactor:
    """One globally owned physical aperture, independent of local access."""

    Signal: str
    Direction: str
    Capacity: int
    Attachment: Position3
    GlobalPath: tuple[Position3, ...]
    GlobalClaims: RoutingResourceClaims
    ChannelContractFingerprint: str
    GlobalContractFingerprint: str
    ApertureContractFingerprint: str
    ApertureOptionFingerprint: str


@dataclass(frozen=True)
class PhysicalPortExteriorFixedClaimCertificate:
    """Complete unary proof for claims shared by every exterior route.

    Portal alternatives may add geometry, but they can never remove the
    projected whole-design access paths or the selected component aperture.
    A conflict in this monotone fixed core therefore rejects the aperture
    before a whole physical assembly plan is materialized.
    """

    CertificateFingerprint: str
    Signal: str
    ApertureOptionFingerprint: str
    ApertureContractFingerprint: str
    PlacementFingerprint: str
    InterfaceFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    FixedClaimsFingerprint: str
    FrozenClaimsFingerprint: str
    Complete: bool
    Feasible: bool
    SelfConflictResources: tuple[str, ...] = ()
    FrozenConflictSignals: tuple[str, ...] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": (
                "physical-port-exterior-fixed-claim-certificate-v1"
            ),
            "CertificateFingerprint": self.CertificateFingerprint,
            "Signal": self.Signal,
            "ApertureOptionFingerprint": (
                self.ApertureOptionFingerprint
            ),
            "ApertureContractFingerprint": (
                self.ApertureContractFingerprint
            ),
            "PlacementFingerprint": self.PlacementFingerprint,
            "InterfaceFingerprint": self.InterfaceFingerprint,
            "ResourceGraphFingerprint": self.ResourceGraphFingerprint,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "FixedClaimsFingerprint": self.FixedClaimsFingerprint,
            "FrozenClaimsFingerprint": self.FrozenClaimsFingerprint,
            "Complete": self.Complete,
            "Feasible": self.Feasible,
            "SelfConflictResources": list(self.SelfConflictResources),
            "FrozenConflictSignals": list(self.FrozenConflictSignals),
        }


@dataclass(frozen=True)
class PhysicalGlobalAperturePathTemplate:
    """One positive exterior path reusable under a rigid planar transform.

    The canonical contract carries every physical dependency used to build
    the witness.  A consumer still has to materialize and validate the path
    against its current resource graph; this object is never a negative
    reachability proof.
    """

    ContractFingerprint: str
    CanonicalContract: tuple[object, ...]
    CanonicalPath: tuple[Position3, ...]
    SourcePlacementFingerprint: str = ""


@dataclass(frozen=True)
class PhysicalPortLocalApertureSupport:
    """An exact certified edge between local access and global aperture."""

    Signal: str
    LocalAccessFingerprint: str
    ApertureOptionFingerprint: str
    SourceSeamFingerprint: str
    ReservationFingerprint: str
    SupportFingerprint: str


@dataclass(frozen=True)
class PhysicalPortCorridorFactor:
    """One exact authoritative global route tied to one port option.

    Unlike a coarse guide, this factor owns the candidate's exact claims.
    ``RequestDependencyFingerprint`` identifies every input on which the
    authoritative candidate request depended; consumers may not reuse the
    factor under a different request domain.
    """

    Signal: str
    PortReservationFingerprint: str
    PortGlobalContractFingerprint: str
    RequestDependencyFingerprint: str
    RouteCandidateId: str
    RouteCandidateFingerprint: str
    NormalizedIdentityFingerprint: str
    Layer: int
    Nodes: frozenset[Position3]
    Claims: RoutingResourceClaims
    Candidate: Any = field(default=None, compare=False, repr=False)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "PortReservationFingerprint": (
                self.PortReservationFingerprint
            ),
            "PortGlobalContractFingerprint": (
                self.PortGlobalContractFingerprint
            ),
            "RequestDependencyFingerprint": (
                self.RequestDependencyFingerprint
            ),
            "RouteCandidateId": self.RouteCandidateId,
            "RouteCandidateFingerprint": self.RouteCandidateFingerprint,
            "NormalizedIdentityFingerprint": (
                self.NormalizedIdentityFingerprint
            ),
            "Layer": self.Layer,
            "Nodes": [list(Value) for Value in sorted(self.Nodes)],
            "ClaimCount": len(self.Claims.ResourceIds),
        }


@dataclass(frozen=True)
class PhysicalPortCorridorDomain:
    """Finite exact corridor domain for one physical port reservation."""

    DomainFingerprint: str
    Signal: str
    PortReservationFingerprint: str
    PortGlobalContractFingerprint: str
    RequestDependencyFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    Factors: tuple[PhysicalPortCorridorFactor, ...]
    Complete: bool
    # Non-empty only after an external proof establishes that factors from
    # domains carrying the same value are portable across their original
    # request dependencies. Ordinary captured plan domains leave this empty.
    PortableRequestFamilyFingerprint: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "DomainFingerprint": self.DomainFingerprint,
            "Signal": self.Signal,
            "PortReservationFingerprint": (
                self.PortReservationFingerprint
            ),
            "PortGlobalContractFingerprint": (
                self.PortGlobalContractFingerprint
            ),
            "RequestDependencyFingerprint": (
                self.RequestDependencyFingerprint
            ),
            "ResourceGraphFingerprint": self.ResourceGraphFingerprint,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "Factors": [Value.ToDictionary() for Value in self.Factors],
            "Complete": self.Complete,
            "PortableRequestFamilyFingerprint": (
                self.PortableRequestFamilyFingerprint
            ),
        }


@dataclass(frozen=True)
class PhysicalComponentApertureFactor:
    """One selected immutable component crossing in an assembly contract."""

    Signal: str
    PortReservationFingerprint: str
    PortGlobalContractFingerprint: str
    ChannelReservationFingerprint: str
    PassageNodes: frozenset[Position3]
    ClaimsFingerprint: str
    ApertureFingerprint: str


@dataclass(frozen=True)
class CertifiedPhysicalComponentApertureDomain:
    """Complete selected apertures over one stable component keepout core."""

    DomainFingerprint: str
    ComponentDomainFingerprint: str
    StableKeepoutCoreFingerprint: str
    StableKeepoutCoreNodes: frozenset[Position3]
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    CrossingSignals: tuple[str, ...]
    Factors: tuple[PhysicalComponentApertureFactor, ...]
    Complete: bool


@dataclass
class PhysicalComponentPortCspState:
    """Persistent monotonic search state for one prepared port domain."""

    DomainFingerprint: str
    RejectedReservationsBySignal: frozenset[tuple[str, str]] = frozenset()
    RejectedReservationSets: frozenset[
        frozenset[tuple[str, str]]
    ] = frozenset()
    RejectedAssignmentFingerprints: frozenset[str] = frozenset()
    DeferredAssignmentFingerprints: frozenset[str] = frozenset()
    FailedAssignmentStates: set[Any] = field(default_factory=set)
    FailedApertureRestrictionStates: set[Any] = field(
        default_factory=set
    )
    OptionDomainPropagationCache: dict[Any, Any] = field(
        default_factory=dict
    )
    LatestOptionDomainsByState: dict[Any, Any] = field(
        default_factory=dict
    )
    PortClaimCompatibilityCache: dict[Any, bool] = field(
        default_factory=dict
    )
    PortContractClaimsCache: dict[Any, Any] = field(
        default_factory=dict
    )
    PortNoGoodKeyCache: dict[Any, Any] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class PhysicalLocalPortPairProofRecord:
    """One exact complete local pair-support UNSAT proof."""

    CurrentSignal: str
    CurrentContract: str
    CompleteSignal: str
    CompleteContract: str
    ProofDomainFingerprint: str
    ProofFingerprint: str
    Status: str
    Complete: bool
    Feasible: bool | None


@dataclass(frozen=True)
class PhysicalLocalPortPairSupportCertificate:
    """One complete proof-derived row of the local port support relation.

    The row fixes ``RowContract`` and covers every contract in
    ``ColumnContracts``.  Consumers may prune ``UnsupportedPairs`` only when
    all preparation and fabric identities match and ``Complete`` is true.
    """

    CertificateFingerprint: str
    PreparedDomainFingerprint: str
    PortSolverCacheKey: str
    ComponentGraphFingerprint: str
    FabricFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    RowSignal: str
    RowContract: str
    ColumnSignal: str
    ColumnContracts: tuple[str, ...]
    LocalProofContextFingerprint: str
    PairProofRecords: tuple[PhysicalLocalPortPairProofRecord, ...]
    Complete: bool

    @property
    def UnsupportedPairs(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            (self.RowContract, Value.CurrentContract)
            for Value in self.PairProofRecords
        )

    @property
    def ProofFingerprints(self) -> tuple[str, ...]:
        return tuple(sorted({
            Value.ProofFingerprint for Value in self.PairProofRecords
        }))


@dataclass(frozen=True)
class PhysicalComponentLocalFactorProjection:
    """Cheap normalized local-access domain for one retained placement.

    Signal identifiers and absolute coordinates are intentionally absent from
    every identity.  The resource and technology fingerprints remain strict
    because a local impossibility proof is not portable across a different
    routing technology or resource graph.
    """

    ProjectionFingerprint: str
    ComponentTopologyFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    InterfaceContractFingerprint: str
    LocalFactorDomainFingerprint: str
    SignalFactorIdentities: tuple[tuple[str, tuple[str, ...]], ...]
    Complete: bool

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": (
                "physical-component-local-factor-projection-v1"
            ),
            "ProjectionFingerprint": self.ProjectionFingerprint,
            "ComponentTopologyFingerprint": (
                self.ComponentTopologyFingerprint
            ),
            "ResourceGraphFingerprint": self.ResourceGraphFingerprint,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "InterfaceContractFingerprint": (
                self.InterfaceContractFingerprint
            ),
            "LocalFactorDomainFingerprint": (
                self.LocalFactorDomainFingerprint
            ),
            "SignalFactorIdentities": [
                {
                    "SignalIdentity": SignalIdentity,
                    "FactorIdentities": list(FactorIdentities),
                }
                for SignalIdentity, FactorIdentities
                in self.SignalFactorIdentities
            ],
            "Complete": self.Complete,
        }


@dataclass(frozen=True)
class PhysicalComponentLocalFactorUnsatCertificate:
    """Complete local-factor UNSAT proof detached from signal names."""

    CertificateFingerprint: str
    ProjectionFingerprint: str
    ComponentTopologyFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    InterfaceContractFingerprint: str
    LocalFactorDomainFingerprint: str
    CoreSignalFactorIdentities: tuple[
        tuple[str, tuple[str, ...]], ...
    ]
    ProofFingerprint: str
    ProofKind: str
    Complete: bool

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": (
                "physical-component-local-factor-unsat-certificate-v1"
            ),
            "CertificateFingerprint": self.CertificateFingerprint,
            "ProjectionFingerprint": self.ProjectionFingerprint,
            "ComponentTopologyFingerprint": (
                self.ComponentTopologyFingerprint
            ),
            "ResourceGraphFingerprint": self.ResourceGraphFingerprint,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "InterfaceContractFingerprint": (
                self.InterfaceContractFingerprint
            ),
            "LocalFactorDomainFingerprint": (
                self.LocalFactorDomainFingerprint
            ),
            "CoreSignalFactorIdentities": [
                {
                    "SignalIdentity": SignalIdentity,
                    "FactorIdentities": list(FactorIdentities),
                }
                for SignalIdentity, FactorIdentities
                in self.CoreSignalFactorIdentities
            ],
            "ProofFingerprint": self.ProofFingerprint,
            "ProofKind": self.ProofKind,
            "Complete": self.Complete,
        }


@dataclass(frozen=True)
class PhysicalComponentLocalFactorProjectionComparison:
    """Deterministic proof-to-placement comparison before global planning."""

    ComparisonFingerprint: str
    IdentityCompatible: bool
    ExactDomainMatch: bool
    CoreFactorMatchCount: int
    CoreFactorCount: int
    CanPrune: bool
    RejectionReason: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": (
                "physical-component-local-factor-comparison-v1"
            ),
            "ComparisonFingerprint": self.ComparisonFingerprint,
            "IdentityCompatible": self.IdentityCompatible,
            "ExactDomainMatch": self.ExactDomainMatch,
            "CoreFactorMatchCount": self.CoreFactorMatchCount,
            "CoreFactorCount": self.CoreFactorCount,
            "CanPrune": self.CanPrune,
            "RejectionReason": self.RejectionReason,
        }


@dataclass(frozen=True)
class PhysicalSignalApertureCandidateDomainIdentity:
    """Signal-local route domain over a stable core, before sibling filtering."""

    DomainFingerprint: str
    StableDomainFingerprint: str
    Signal: str
    ApertureFingerprint: str
    PortGlobalContractFingerprint: str
    ChannelReservationFingerprint: str
    RequestDependencyFingerprint: str
    StableKeepoutCoreFingerprint: str
    BlockedNodesFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    CoverageCursor: int
    Complete: bool


@dataclass(frozen=True)
class PreparedPhysicalSignalLocalFactorDomain:
    """Immutable, placement-independent access facts for one signal."""

    Signal: str
    LocalIdentityFingerprint: str
    ComponentTopologyFingerprint: str
    TerminalContractFingerprint: str
    LocalGeometryFingerprint: str
    LocalClaimsFingerprint: str
    TechnologyFingerprint: str
    Complete: bool
    Feasible: bool
    LocalAccessFactors: tuple[PhysicalPortLocalAccessFactor, ...]
    LocalSupportFacts: tuple[str, ...]


@dataclass(frozen=True)
class PhysicalSignalLocalFactorReuseEntry:
    """Parent-owned publication record for one complete local domain."""

    LocalIdentityFingerprint: str
    Domain: PreparedPhysicalSignalLocalFactorDomain
    SourcePlacementFingerprint: str


@dataclass(frozen=True)
class PreparedPhysicalComponentPortFactorDomain:
    """Complete pre-assignment physical port factor-domain boundary."""

    DomainFingerprint: str
    PlacementFingerprint: str
    ComponentGraphFingerprint: str
    ResourceGraphFingerprint: str
    GuideFingerprint: str
    AccessCertificateFingerprint: str
    AccessCertificatePlacementFingerprint: str
    AccessCertificateResourceGraphFingerprint: str
    AccessCertificateComponentGraphFingerprint: str
    Problem: ComponentRoutingProblem
    CoarsePlan: ChannelPlan
    AccessCertificate: ComponentCutAccessFeasibilityCertificate
    ChannelReservations: tuple[PhysicalComponentChannelReservation, ...]
    LaneFactorsBySignal: tuple[
        tuple[str, tuple[PhysicalPortLaneFactor, ...]], ...
    ]
    DiagnosticsBySignal: tuple[tuple[str, dict[str, object]], ...]
    FabricOrigin: Position3
    MinimumPlacementY: int
    ComponentEnvelopeMinimum: Position3
    ComponentEnvelopeMaximum: Position3
    FabricAdjacency: tuple[tuple[Position3, tuple[Position3, ...]], ...]
    ComponentKeepoutNodes: frozenset[Position3]
    ComponentKeepoutGuideCellsByLayer: tuple[
        tuple[int, frozenset[Position2]], ...
    ]
    LaneFactorExpansionCount: int
    AccessFactorExpansionCount: int
    SeamFactorExpansionCount: int
    GlobalConnectorSearchCount: int
    GlobalConnectorCacheHitCount: int
    GlobalConnectorExpansionCount: int
    GlobalGuideFieldBuildCount: int
    GlobalGuideFieldExpansionCount: int
    GlobalGuideFieldHitCount: int
    GlobalGuideFieldCanonicalPathCount: int
    GlobalGuideFieldFallbackCount: int
    GlobalConnectorPortableCacheHitCount: int
    GlobalConnectorPortableCacheValidationRejectCount: int
    GlobalConnectorPortableCacheStoreCount: int
    Complete: bool
    Feasible: bool
    PreparationStageTimings: tuple[tuple[str, float], ...] = ()
    LocalAccessFactorsBySignal: tuple[
        tuple[str, tuple[PhysicalPortLocalAccessFactor, ...]], ...
    ] = ()
    ApertureFactorsBySignal: tuple[
        tuple[str, tuple[PhysicalPortApertureOptionFactor, ...]], ...
    ] = ()
    LocalApertureSupportBySignal: tuple[
        tuple[str, tuple[PhysicalPortLocalApertureSupport, ...]], ...
    ] = ()
    LocalApertureSupportsByOption: tuple[
        tuple[
            tuple[str, str],
            tuple[PhysicalPortLocalApertureSupport, ...],
        ], ...
    ] = ()
    SignalLocalFactorDomains: tuple[
        tuple[str, PreparedPhysicalSignalLocalFactorDomain], ...
    ] = ()
    LocalFactorCacheHitSignals: tuple[str, ...] = ()
    LocalFactorRebuiltSignals: tuple[str, ...] = ()
    LocalFactorPreparationElapsedSeconds: float = 0.0
    ExteriorFactorPreparationElapsedSeconds: float = 0.0
    FactorPreparationTimings: tuple[tuple[str, float], ...] = ()
    PhysicalLocalSeamEligibilityCacheHitCount: int = 0
    PhysicalLocalSeamEligibilityCacheMissCount: int = 0
    PhysicalLocalSeamEligibilityCacheStoreCount: int = 0
    ExteriorFixedClaimCertificates: tuple[
        PhysicalPortExteriorFixedClaimCertificate, ...
    ] = ()
    # Complete global-only candidate reservations.  This additive field lets
    # assembly planning publish its authoritative boundary choices without
    # coupling them back to component-local access witnesses.
    BoundaryPortReservationsBySignal: tuple[
        tuple[
            str,
            tuple[PhysicalComponentBoundaryPortReservation, ...],
        ], ...
    ] = ()
    FeedthroughEndpointDomains: tuple[
        PreparedPhysicalComponentFeedthroughEndpointDomain, ...
    ] = ()
    # Authoritative exterior fabric, detailed region, and joint capacity
    # ledger identities.  These remain optional only for legacy preparations.
    ExteriorFabricSetFingerprint: str = ""
    ExteriorRegionFingerprint: str = ""
    ExteriorCapacityLedgerFingerprint: str = ""
    ExteriorFabrics: tuple[PhysicalExteriorApertureFabric, ...] = ()
    NativeConnectorBatchWorkItems: int = 0
    NativeConnectorBatchActiveWorkerCount: int = 0


@dataclass(frozen=True)
class PhysicalComponentSymbolicPortPairCertificate:
    """Complete local net-state compatibility relation for two port domains."""

    DomainFingerprint: str
    PreparedDomainFingerprint: str
    PlacementFingerprint: str
    ComponentGraphFingerprint: str
    FabricFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    AccessCertificateFingerprint: str
    InterfaceFingerprint: str
    LocalAccessDomainFingerprint: str
    SeamDomainFingerprint: str
    SignalPair: tuple[str, str]
    LocalAccessFingerprintsBySignal: tuple[
        tuple[str, tuple[str, ...]], ...
    ]
    SeamFingerprintByLocalAccess: tuple[
        tuple[str, str, str], ...
    ]
    SeamFingerprintsBySignal: tuple[
        tuple[str, tuple[str, ...]], ...
    ]
    UnsupportedUnaryLocalAccess: tuple[tuple[str, str], ...]
    UnsupportedLocalAccessPairs: tuple[
        tuple[tuple[str, str], tuple[str, str]], ...
    ]
    UnsupportedUnarySeams: tuple[tuple[str, str], ...]
    UnsupportedSeamPairs: tuple[
        tuple[tuple[str, str], tuple[str, str]], ...
    ]
    NetStateCacheKeys: tuple[tuple[str, str, str], ...]
    NetStateBindings: tuple[tuple[str, str, str, str], ...]
    NetStateDomainFingerprint: str
    ProofFingerprint: str
    Complete: bool

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": (
                "physical-component-symbolic-port-pair-certificate-v1"
            ),
            "DomainFingerprint": self.DomainFingerprint,
            "PreparedDomainFingerprint": self.PreparedDomainFingerprint,
            "PlacementFingerprint": self.PlacementFingerprint,
            "ComponentGraphFingerprint": self.ComponentGraphFingerprint,
            "FabricFingerprint": self.FabricFingerprint,
            "ResourceGraphFingerprint": self.ResourceGraphFingerprint,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "AccessCertificateFingerprint": (
                self.AccessCertificateFingerprint
            ),
            "InterfaceFingerprint": self.InterfaceFingerprint,
            "LocalAccessDomainFingerprint": (
                self.LocalAccessDomainFingerprint
            ),
            "SeamDomainFingerprint": self.SeamDomainFingerprint,
            "SignalPair": list(self.SignalPair),
            "LocalAccessFingerprintsBySignal": {
                Signal: list(Fingerprints)
                for Signal, Fingerprints
                in self.LocalAccessFingerprintsBySignal
            },
            "SeamFingerprintByLocalAccess": [
                list(Value) for Value in self.SeamFingerprintByLocalAccess
            ],
            "SeamFingerprintsBySignal": {
                Signal: list(Fingerprints)
                for Signal, Fingerprints
                in self.SeamFingerprintsBySignal
            },
            "UnsupportedUnarySeams": [
                list(Value) for Value in self.UnsupportedUnarySeams
            ],
            "UnsupportedUnaryLocalAccess": [
                list(Value) for Value
                in self.UnsupportedUnaryLocalAccess
            ],
            "UnsupportedLocalAccessPairs": [
                [list(First), list(Second)]
                for First, Second in self.UnsupportedLocalAccessPairs
            ],
            "UnsupportedSeamPairs": [
                [list(First), list(Second)]
                for First, Second in self.UnsupportedSeamPairs
            ],
            "NetStateCacheKeys": [
                list(Value) for Value in self.NetStateCacheKeys
            ],
            "NetStateBindings": [
                list(Value) for Value in self.NetStateBindings
            ],
            "NetStateDomainFingerprint": (
                self.NetStateDomainFingerprint
            ),
            "ProofFingerprint": self.ProofFingerprint,
            "Complete": self.Complete,
        }


@dataclass(frozen=True)
class PhysicalComponentSymbolicHigherOrderCertificate:
    """Complete exact seam-support relation for three or more port domains."""

    DomainFingerprint: str
    PreparedDomainFingerprint: str
    PlacementFingerprint: str
    ComponentGraphFingerprint: str
    FabricFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    AccessCertificateFingerprint: str
    InterfaceFingerprint: str
    LocalAccessDomainFingerprint: str
    SeamDomainFingerprint: str
    SignalDomain: tuple[str, ...]
    LocalAccessFingerprintsBySignal: tuple[
        tuple[str, tuple[str, ...]], ...
    ]
    SeamFingerprintByLocalAccess: tuple[
        tuple[str, str, str], ...
    ]
    SeamFingerprintsBySignal: tuple[
        tuple[str, tuple[str, ...]], ...
    ]
    SupportedLocalAccessTuples: tuple[
        tuple[tuple[str, str], ...], ...
    ]
    SupportedSeamTuples: tuple[
        tuple[tuple[str, str], ...], ...
    ]
    NetStateCacheKeys: tuple[tuple[str, str, str], ...]
    NetStateBindings: tuple[tuple[str, str, str, str], ...]
    NetStateDomainFingerprint: str
    ProofFingerprint: str
    CompatibilityCheckCount: int
    Complete: bool

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": (
                "physical-component-symbolic-higher-order-certificate-v1"
            ),
            "DomainFingerprint": self.DomainFingerprint,
            "SignalDomain": list(self.SignalDomain),
            "SupportedLocalAccessTuples": [
                [list(Value) for Value in TupleValue]
                for TupleValue in self.SupportedLocalAccessTuples
            ],
            "SupportedSeamTuples": [
                [list(Value) for Value in TupleValue]
                for TupleValue in self.SupportedSeamTuples
            ],
            "NetStateDomainFingerprint": self.NetStateDomainFingerprint,
            "ProofFingerprint": self.ProofFingerprint,
            "CompatibilityCheckCount": self.CompatibilityCheckCount,
            "Complete": self.Complete,
        }


@dataclass(frozen=True)
class FrozenPhysicalComponentPostClosurePortalHandoff:
    """Exact post-closure exterior fabric shared by two routing stages.

    This is deliberately an identity contract, not a portable portal cache.
    Physical assembly preparation publishes the closed region once and the
    subsequent authoritative global-channel pass must consume that same
    region, access set, column set, and portal domain.
    """

    PreparationDomainFingerprint: str
    PlacementFingerprint: str
    ComponentGraphFingerprint: str
    ResourceGraphFingerprint: str
    ExteriorRegionFingerprint: str
    RawPortalGeometryCache: Any = field(compare=False, repr=False)

"""Published routing results and retained run-resource contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .Core import Position3, RoutingStaticGeometry
from .Placement import ClusterInterfaceAssignment
from .Component import (
    ComponentCutAccessFeasibilityCertificate,
    ComponentRoutingProblem,
    PhysicalComponentAssemblyPlan,
    RoutedComponentTemplate,
)
from .PhysicalInterface import (
    CertifiedPhysicalComponentApertureDomain,
    FrozenPhysicalComponentPostClosurePortalHandoff,
    PhysicalComponentPortCspState,
    PhysicalComponentSymbolicHigherOrderCertificate,
    PhysicalComponentSymbolicPortPairCertificate,
    PhysicalLocalPortPairSupportCertificate,
    PhysicalPortCorridorDomain,
    PhysicalSignalLocalFactorReuseEntry,
    PreparedPhysicalComponentAssembly,
    PreparedPhysicalComponentPortFactorDomain,
    RetainedPhysicalGlobalPlanFrontierEntry,
)
from ..ChannelPlanner import ChannelPlan, RoutingStageMetrics
from ..ResourceGraph import PinAccessSelection, RoutingAssignment
from ..Technology import DefaultRedstoneRoutingTechnology
from ..TrackAssignment import TrackAssignment

@dataclass
class RoutedDesign:
    Module: object
    PlacedGates: list[Any]
    Wires: list[Position3]
    Supports: list[Position3]
    Repeaters: dict[Position3, str]
    NetWires: dict[str, list[Position3]]
    SupportBlock: str = "minecraft:light_gray_concrete"
    TraceSupportBlocks: tuple[str, ...] = ()
    TemplateAccessBySignal: dict[str, set[Position3]] = field(default_factory=dict)
    RoutingMetrics: RoutingStageMetrics | None = None
    GlobalPlan: ChannelPlan | None = None
    TrackAssignment: TrackAssignment | None = None
    TechnologyVersion: str = DefaultRedstoneRoutingTechnology.TechnologyVersion
    EffectivePolicy: dict[str, object] = field(default_factory=dict)
    ResourceGraphVersion: str = ""
    ResourceGraphNodeCount: int = 0
    ResourceGraphEdgeCount: int = 0
    ResourceOwnershipCounts: dict[str, int] = field(default_factory=dict)
    RepeaterReservationCount: int = 0
    ZeroResourceConflicts: bool = False
    RoutingAssignment: RoutingAssignment | None = None
    PortalCount: int = 0
    RouteCandidateCount: int = 0
    CandidateRequestCount: int = 0
    CandidateExpansionLimit: int = 0
    AssignmentExpansionCount: int = 0
    RoutingStageTimings: dict[str, float] = field(default_factory=dict)
    RepeaterOptimizationDiagnostics: dict[str, object] = field(
        default_factory=dict
    )
    GlobalGuideDiagnostics: dict[str, object] = field(default_factory=dict)
    RoutingControlEffectiveness: dict[str, object] = field(default_factory=dict)
    FrozenNetSignals: tuple[str, ...] = ()
    NegotiatedRoutingDiagnostics: dict[str, object] = field(default_factory=dict)
    RoutingFootprintDiagnostics: dict[str, object] = field(default_factory=dict)

@dataclass
class RoutingResources:
    StaticGeometry: RoutingStaticGeometry
    ResourceGraph: Any = None
    RustContexts: dict[tuple[int, int, int, int, int], Any] = field(
        default_factory=dict
    )
    RawPortalGeometryCaches: tuple[Any, ...] = field(
        default_factory=tuple,
        compare=False,
        repr=False,
    )
    PhysicalGlobalApertureTemplateCache: dict[str, Any] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PreparedPortalDomainCaches: tuple[Any, ...] = field(
        default_factory=tuple,
        compare=False,
        repr=False,
    )
    ClusterBoundaryLeaseOwnershipFingerprints: dict[int, str] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PreparedClusterInterfaceAssignment: (
        ClusterInterfaceAssignment | None
    ) = field(
        default=None,
        compare=False,
        repr=False,
    )
    FrozenClusterInterfaceAssignment: (
        ClusterInterfaceAssignment | None
    ) = field(
        default=None,
        compare=False,
        repr=False,
    )
    FrozenPreparedPortalDomainCache: Any = field(
        default=None,
        compare=False,
        repr=False,
    )
    FrozenInterfaceGlobalCandidateCache: Any = field(
        default=None,
        compare=False,
        repr=False,
    )
    FrozenInterfaceGlobalCandidateMetadata: Any = field(
        default=None,
        compare=False,
        repr=False,
    )
    FrozenInterfaceGlobalCandidatePlacementIdentity: int = field(
        default=0,
        compare=False,
        repr=False,
    )
    PreparedComponentRoutingProblem: ComponentRoutingProblem | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    PreparedComponentAccessCertificate: (
        ComponentCutAccessFeasibilityCertificate | None
    ) = field(
        default=None,
        compare=False,
        repr=False,
    )
    PreparedPhysicalComponentAssembly: (
        PreparedPhysicalComponentAssembly | None
    ) = field(
        default=None,
        compare=False,
        repr=False,
    )
    PreparedPhysicalComponentPortFactorDomain: (
        PreparedPhysicalComponentPortFactorDomain | None
    ) = field(
        default=None,
        compare=False,
        repr=False,
    )
    FrozenPhysicalComponentPostClosurePortalHandoff: (
        FrozenPhysicalComponentPostClosurePortalHandoff | None
    ) = field(
        default=None,
        compare=False,
        repr=False,
    )
    PreparedPhysicalComponentUnboundProblem: (
        ComponentRoutingProblem | None
    ) = field(
        default=None,
        compare=False,
        repr=False,
    )
    FrozenPhysicalComponentAssemblyPlan: (
        PhysicalComponentAssemblyPlan | None
    ) = field(
        default=None,
        compare=False,
        repr=False,
    )
    FrozenPhysicalComponentGlobalGuidePlan: Any = field(
        default=None,
        compare=False,
        repr=False,
    )
    PhysicalGlobalRouteTreeResultCache: dict[str, Any] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalGlobalAssignmentArcCompatibilityCache: dict[
        tuple[str, str], bool
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalGlobalAssignmentArcIndex: Any = field(
        default=None,
        compare=False,
        repr=False,
    )
    PhysicalGlobalMandatoryPortalPairCertificateCache: dict[
        str, Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalBoundaryMandatoryPortalFactorCertificateCache: dict[
        str, Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalBoundaryMandatoryPortalFactorDomainCache: dict[
        tuple[str, str, str], Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalBoundaryMandatoryPortalPairRelationCache: dict[
        str, Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalBoundaryMandatoryPortalPairStateIndexCache: dict[
        str, Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentPortOptionDomainCache: dict[
        str, Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentFactorPortOptionDomainCache: dict[
        tuple[str, str], Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentPortOptionArcSupportCache: dict[
        str, Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentPortLaneArcSupportCache: dict[
        str, Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentPortCspStateCache: dict[
        str, PhysicalComponentPortCspState
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentBoundaryAssignmentIteratorCache: dict[
        str, Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentAssemblyPlanDomainFingerprint: str = field(
        default="",
        compare=False,
        repr=False,
    )
    PhysicalComponentAssemblyPlanClauseStateByDomain: dict[
        str, tuple[str, int]
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentAssemblyPlanFingerprintsByDomain: dict[
        str, set[str]
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentBoundaryTraversalPrioritySignals: tuple[str, ...] = (
        field(
            default_factory=tuple,
            compare=False,
            repr=False,
        )
    )
    PhysicalComponentBoundaryTraversalEpoch: int = field(
        default=0,
        compare=False,
        repr=False,
    )
    PhysicalComponentBoundaryTraversalCursor: int = field(
        default=0,
        compare=False,
        repr=False,
    )
    PhysicalComponentLocalInterfaceFactorProofCache: dict[
        tuple[str, str, str, str], Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentLocalPortfolioContextCache: dict[
        tuple[str, str, str], Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalLocalPortPairSupportCertificateCache: dict[
        str, PhysicalLocalPortPairSupportCertificate
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalPortCorridorDomainCache: dict[
        str, PhysicalPortCorridorDomain
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalSignalRouteDomainContinuationCache: dict[str, Any] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentApertureDomainCache: dict[
        str, CertifiedPhysicalComponentApertureDomain
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PreferredPhysicalComponentGlobalContractsBySignal: dict[
        str, str
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PreferredPhysicalComponentApertureContractsBySignal: dict[
        str, str
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentAperturePortalSlackBySignal: dict[
        str, dict[str, tuple[int, int]]
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PreferredPhysicalComponentPortReservationsBySignal: dict[
        str, str
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    RetainedPhysicalGlobalPlanFrontier: dict[
        str, RetainedPhysicalGlobalPlanFrontierEntry
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    DeferredPhysicalComponentPortAssignmentFingerprints: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    PhysicalGlobalPlanFrontierScheduleSequence: int = field(
        default=0,
        compare=False,
        repr=False,
    )
    PreparingPhysicalComponentGlobalChannels: bool = field(
        default=False,
        compare=False,
        repr=False,
    )
    PhysicalComponentExactGlobalChannelSignals: frozenset[str] = field(
        default_factory=frozenset,
        compare=False,
        repr=False,
    )
    RejectedPhysicalComponentPortAssignmentFingerprints: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    PhysicalComponentSymbolicNetStateCache: dict[str, Any] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentSymbolicUnaryApertureClauseCache: dict[
        str,
        tuple[
            frozenset[frozenset[tuple[str, str]]],
            dict[str, Any],
        ],
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    # This cache deliberately has no placement identity.  Entries are only
    # published by the parent after complete signal-local preparation.
    PhysicalSignalLocalFactorDomainCache: dict[
        str, PhysicalSignalLocalFactorReuseEntry
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentSymbolicPortPairCertificateCache: dict[
        str, PhysicalComponentSymbolicPortPairCertificate
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentSymbolicPairCompatibilityIndexCache: dict[
        str, Any
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    PhysicalComponentSymbolicHigherOrderCertificateCache: dict[
        str, PhysicalComponentSymbolicHigherOrderCertificate
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    RejectedPhysicalComponentBoundaryAssignmentFingerprints: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    RejectedPhysicalComponentPortReservationsBySignal: dict[
        str, set[str]
    ] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    RejectedPhysicalComponentPortReservationSets: set[
        frozenset[tuple[str, str]]
    ] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    RejectedPhysicalComponentLocalSeamReservationSets: set[
        frozenset[tuple[str, str]]
    ] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    ForbiddenPhysicalComponentGlobalCandidateSets: set[
        frozenset[tuple[str, str]]
    ] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    RejectedPhysicalGlobalRequestApertureFactorSets: set[
        frozenset[tuple[str, str]]
    ] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    RejectedPhysicalComponentAssemblyPlanFingerprints: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    RejectedPhysicalComponentAssemblyChoiceFingerprints: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    FrozenRoutedComponentTemplate: RoutedComponentTemplate | None = field(
        default=None,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True)
class PinAccessIssue:
    """One terminal that cannot escape through static placement geometry."""

    Signal: str
    Source: Position3
    Target: Position3


@dataclass(frozen=True)
class PinAccessReport:
    """Static reachability result produced before detailed routing."""

    CheckedTargets: int
    Issues: tuple[PinAccessIssue, ...]
    Selections: tuple[PinAccessSelection, ...] = ()

    @property
    def Passed(self) -> bool:
        return not self.Issues

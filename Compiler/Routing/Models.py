"""Shared data contracts for physical routing stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from .ChannelPlanner import ChannelPlan, RoutingStageMetrics
from .Technology import DefaultRedstoneRoutingTechnology
from .TrackAssignment import TrackAssignment
from .ResourceGraph import (
    PinAccessSelection,
    RoutingAssignment,
    RoutingResourceClaims,
    RoutingResourceId,
)

Position2 = tuple[int, int]
Position3 = tuple[int, int, int]


@dataclass(frozen=True)
class InterClusterChannelLane:
    """One capacity-one physical lane inside a bounded cluster channel."""

    Layer: int
    Direction: str
    Cells: tuple[Position3, ...]
    IngressNodes: tuple[Position3, ...]
    PhysicalClaims: RoutingResourceClaims
    ClaimsFingerprint: str

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Layer": self.Layer,
            "Direction": self.Direction,
            "Cells": [list(Value) for Value in self.Cells],
            "IngressNodes": [
                list(Value) for Value in self.IngressNodes
            ],
            "PhysicalClaims": {
                "WireCells": len(self.PhysicalClaims.WireCells),
                "SupportCells": len(
                    self.PhysicalClaims.SupportCells
                ),
                "RequiredAirCells": len(
                    self.PhysicalClaims.RequiredAirCells
                ),
                "ElectricalCells": len(
                    self.PhysicalClaims.ElectricalCells
                ),
            },
            "ClaimsFingerprint": self.ClaimsFingerprint,
        }


@dataclass(frozen=True)
class InterClusterRoutingChannel:
    """Connected physical capacity added around one dense component."""

    ChannelFingerprint: str
    AffectedClusters: tuple[int, ...]
    AffectedSignals: tuple[str, ...]
    InsertedBoundaryStrips: tuple[
        tuple[str, int, int, int], ...
    ]
    ClusterTranslations: tuple[
        tuple[int, Position3], ...
    ]
    Lanes: tuple[InterClusterChannelLane, ...]
    PhysicalModel: str = "bounded-inter-cluster-channel-v1"
    InterfaceDeckLayer: int | None = None
    TrackPitch: int = 3
    ChannelClearanceTracks: int = 0
    MaximumAffectedClusters: int = 3
    MaximumBoundaryStrips: int = 2
    ComponentId: int | None = None
    InterfaceFingerprint: str = ""
    DeclaredFeedthroughSignals: tuple[str, ...] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ChannelFingerprint": self.ChannelFingerprint,
            "AffectedClusters": list(self.AffectedClusters),
            "AffectedSignals": list(self.AffectedSignals),
            "InsertedBoundaryStrips": [
                {
                    "Axis": Axis,
                    "Coordinate": Coordinate,
                    "Minimum": Minimum,
                    "Maximum": Maximum,
                }
                for Axis, Coordinate, Minimum, Maximum
                in self.InsertedBoundaryStrips
            ],
            "ClusterTranslations": [
                {
                    "Cluster": Cluster,
                    "Delta": list(Delta),
                }
                for Cluster, Delta in self.ClusterTranslations
            ],
            "Lanes": [Lane.ToDictionary() for Lane in self.Lanes],
            "PhysicalModel": self.PhysicalModel,
            "InterfaceDeckLayer": self.InterfaceDeckLayer,
            "TrackPitch": self.TrackPitch,
            "ChannelClearanceTracks": self.ChannelClearanceTracks,
            "MaximumAffectedClusters": self.MaximumAffectedClusters,
            "MaximumBoundaryStrips": self.MaximumBoundaryStrips,
            "ComponentId": self.ComponentId,
            "InterfaceFingerprint": self.InterfaceFingerprint,
            "DeclaredFeedthroughSignals": list(
                self.DeclaredFeedthroughSignals
            ),
        }


@dataclass(frozen=True)
class ClusterInterfacePlacementState:
    """One coherent retained placement/orientation state."""

    StateFingerprint: str
    ClusterTransforms: tuple[tuple[str, int, bool], ...] = ()
    ChangedClusterCount: int = 0
    LocalRouteFingerprint: str = ""
    Footprint: int = 0
    Hpwl: int = 0
    PeakBoundaryPressure: int = 0
    TotalBoundaryPressure: int = 0
    InterfaceTopologyFingerprint: str = ""
    ChannelFingerprint: str = ""
    InterClusterChannel: InterClusterRoutingChannel | None = None

    def ToDictionary(self) -> dict[str, object]:
        return {
            "StateFingerprint": self.StateFingerprint,
            "ClusterTransforms": [
                {
                    "Cluster": Cluster,
                    "Rotation": Rotation,
                    "MirrorX": MirrorX,
                }
                for Cluster, Rotation, MirrorX in self.ClusterTransforms
            ],
            "ChangedClusterCount": self.ChangedClusterCount,
            "LocalRouteFingerprint": self.LocalRouteFingerprint,
            "Footprint": self.Footprint,
            "Hpwl": self.Hpwl,
            "PeakBoundaryPressure": self.PeakBoundaryPressure,
            "TotalBoundaryPressure": self.TotalBoundaryPressure,
            "InterfaceTopologyFingerprint": (
                self.InterfaceTopologyFingerprint
            ),
            "ChannelFingerprint": self.ChannelFingerprint,
            "InterClusterChannel": (
                self.InterClusterChannel.ToDictionary()
                if self.InterClusterChannel is not None
                else None
            ),
        }


@dataclass(frozen=True)
class ClusterInterfacePortfolioStateAudit:
    """Disposition of one requested bounded interface placement state."""

    StateIndex: int
    Classification: str
    PlacementStateFingerprint: str = ""
    InterfaceTopologyFingerprint: str = ""
    Detail: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "StateIndex": self.StateIndex,
            "Classification": self.Classification,
            "PlacementStateFingerprint": self.PlacementStateFingerprint,
            "InterfaceTopologyFingerprint": (
                self.InterfaceTopologyFingerprint
            ),
            "Detail": self.Detail,
        }


@dataclass(frozen=True)
class ClusterInterfaceTerminalDomain:
    """One identifier-independent terminal access domain."""

    TerminalFingerprint: str
    CandidateClaimFingerprints: tuple[str, ...]

    @property
    def CandidateCount(self) -> int:
        return len(self.CandidateClaimFingerprints)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TerminalFingerprint": self.TerminalFingerprint,
            "CandidateCount": self.CandidateCount,
            "CandidateClaimFingerprints": list(
                self.CandidateClaimFingerprints
            ),
        }


@dataclass(frozen=True)
class ClusterInterfaceConflictComponent:
    """Connected terminal domains coupled by capacity-one resources."""

    ComponentFingerprint: str
    TerminalDomainIndices: tuple[int, ...]
    ConflictingResourceFingerprints: tuple[str, ...] = ()
    IncompatibleDomainEdges: tuple[
        tuple[int, int, int, int, str], ...
    ] = ()
    IncompatibleDomainEdgeCount: int = 0
    WitnessesComplete: bool = True

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ComponentFingerprint": self.ComponentFingerprint,
            "TerminalDomainIndices": list(self.TerminalDomainIndices),
            "ConflictingResourceFingerprints": list(
                self.ConflictingResourceFingerprints
            ),
            "IncompatibleDomainEdges": [
                {
                    "FirstDomain": FirstDomain,
                    "FirstCandidate": FirstCandidate,
                    "SecondDomain": SecondDomain,
                    "SecondCandidate": SecondCandidate,
                    "ResourceFingerprint": ResourceFingerprint,
                }
                for (
                    FirstDomain,
                    FirstCandidate,
                    SecondDomain,
                    SecondCandidate,
                    ResourceFingerprint,
                ) in self.IncompatibleDomainEdges
            ],
            "IncompatibleDomainEdgeCount": (
                self.IncompatibleDomainEdgeCount
            ),
            "WitnessesComplete": self.WitnessesComplete,
        }


@dataclass(frozen=True)
class ClusterInterfaceRealizabilityNogood:
    """One structurally selected access pattern disproven by route search."""

    PlacementStateFingerprint: str
    ComponentFingerprint: str
    Signal: str
    TerminalPatternFingerprint: str
    CandidateDomainFingerprint: str
    RouteFailureFingerprint: str
    RejectedAssignmentFingerprint: str

    @property
    def PatternFingerprint(self) -> str:
        return self.TerminalPatternFingerprint

    @property
    def CandidateFailureFingerprint(self) -> str:
        return self.RouteFailureFingerprint

    def StructuralIdentity(self) -> tuple[str, ...]:
        return (
            self.PlacementStateFingerprint,
            self.ComponentFingerprint,
            self.TerminalPatternFingerprint,
            self.CandidateDomainFingerprint,
            self.RouteFailureFingerprint,
            self.RejectedAssignmentFingerprint,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "PlacementStateFingerprint": (
                self.PlacementStateFingerprint
            ),
            "ComponentFingerprint": self.ComponentFingerprint,
            "DiagnosticSignal": self.Signal,
            "TerminalPatternFingerprint": (
                self.TerminalPatternFingerprint
            ),
            "CandidateDomainFingerprint": (
                self.CandidateDomainFingerprint
            ),
            "RouteFailureFingerprint": self.RouteFailureFingerprint,
            "RejectedAssignmentFingerprint": (
                self.RejectedAssignmentFingerprint
            ),
        }


@dataclass(frozen=True)
class ComponentRoutabilityCore:
    """A complete physical ownership conflict suitable for placement repair."""

    CoreFingerprint: str
    Signals: tuple[str, ...]
    PlacementStateFingerprint: str
    ComponentStateFingerprint: str
    DomainFingerprint: str
    BlockingResources: tuple[str, ...] = ()
    BlockingPorts: tuple[Position3, ...] = ()

    def StructuralIdentity(self) -> tuple[object, ...]:
        return (
            self.CoreFingerprint,
            self.Signals,
            self.PlacementStateFingerprint,
            self.ComponentStateFingerprint,
            self.DomainFingerprint,
            self.BlockingResources,
            self.BlockingPorts,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CoreFingerprint": self.CoreFingerprint,
            "Signals": list(self.Signals),
            "PlacementStateFingerprint": self.PlacementStateFingerprint,
            "ComponentStateFingerprint": self.ComponentStateFingerprint,
            "DomainFingerprint": self.DomainFingerprint,
            "BlockingResources": list(self.BlockingResources),
            "BlockingPorts": [list(Position) for Position in self.BlockingPorts],
            "Complete": True,
        }


@dataclass(frozen=True)
class ClusterInterfaceStateProof:
    """Terminal proof for one retained coherent placement state."""

    PlacementStateFingerprint: str
    Status: str
    ComponentStateFingerprint: str = ""
    ComponentVariant: int = -1
    ComponentSelectionFingerprint: str = ""
    ChannelFingerprint: str = ""
    TransformFingerprint: str = ""
    OwnershipUnsatCoreFingerprint: str = ""
    OwnershipUnsatSignals: tuple[str, ...] = ()
    RoutabilityCore: ComponentRoutabilityCore | None = None
    AssignmentFingerprints: tuple[str, ...] = ()
    RealizabilityNogoods: tuple[
        ClusterInterfaceRealizabilityNogood, ...
    ] = ()
    DomainFingerprint: str = ""
    ExpansionCount: int = 0
    DomainComplete: bool = False
    OwnershipComplete: bool = False
    RealizabilityComplete: bool = False
    Exhaustive: bool = False

    def StructuralIdentity(self) -> tuple[object, ...]:
        return (
            self.PlacementStateFingerprint,
            self.ComponentStateFingerprint,
            self.ComponentVariant,
            self.ComponentSelectionFingerprint,
            self.Status,
            self.ChannelFingerprint,
            self.TransformFingerprint,
            self.OwnershipUnsatCoreFingerprint,
            (
                self.RoutabilityCore.StructuralIdentity()
                if self.RoutabilityCore is not None
                else None
            ),
            self.AssignmentFingerprints,
            tuple(
                Nogood.StructuralIdentity()
                for Nogood in self.RealizabilityNogoods
            ),
            self.DomainFingerprint,
            self.ExpansionCount,
            self.DomainComplete,
            self.OwnershipComplete,
            self.RealizabilityComplete,
            self.Exhaustive,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "PlacementStateFingerprint": (
                self.PlacementStateFingerprint
            ),
            "ComponentStateFingerprint": (
                self.ComponentStateFingerprint
            ),
            "ComponentVariant": self.ComponentVariant,
            "ComponentSelectionFingerprint": (
                self.ComponentSelectionFingerprint
            ),
            "Status": self.Status,
            "ChannelFingerprint": self.ChannelFingerprint,
            "TransformFingerprint": self.TransformFingerprint,
            "OwnershipUnsatCoreFingerprint": (
                self.OwnershipUnsatCoreFingerprint
            ),
            "OwnershipUnsatSignals": list(
                self.OwnershipUnsatSignals
            ),
            "ComponentRoutabilityCore": (
                self.RoutabilityCore.ToDictionary()
                if self.RoutabilityCore is not None
                else None
            ),
            "AssignmentFingerprints": list(
                self.AssignmentFingerprints
            ),
            "RealizabilityNogoods": [
                Nogood.ToDictionary()
                for Nogood in self.RealizabilityNogoods
            ],
            "DomainFingerprint": self.DomainFingerprint,
            "ExpansionCount": self.ExpansionCount,
            "DomainComplete": self.DomainComplete,
            "OwnershipComplete": self.OwnershipComplete,
            "RealizabilityComplete": self.RealizabilityComplete,
            "Exhaustive": self.Exhaustive,
        }


@dataclass(frozen=True)
class ClusterInterfaceProblem:
    """One bounded, capacity-one cluster boundary assignment problem.

    This contract is intentionally independent of signal identifiers.  Signal
    names remain in the diagnostic payload at the call site, while the
    fingerprint describes terminal-domain cardinalities, ownership topology,
    and the selected bounded placement state.
    """

    ComponentFingerprint: str
    PlacementVariantFingerprint: str
    OwnershipFingerprint: str
    TerminalDomainSizes: tuple[int, ...]
    MandatoryClaimCount: int
    DomainComplete: bool = False
    DomainCandidateCount: int = 0
    MaximumClusterVariants: int = 6
    MaximumRepairClusters: int = 3
    PlacementStates: tuple[ClusterInterfacePlacementState, ...] = ()
    TerminalDomains: tuple[ClusterInterfaceTerminalDomain, ...] = ()
    ConflictComponents: tuple[ClusterInterfaceConflictComponent, ...] = ()
    PolicyFingerprint: str = ""
    LocalRouteFingerprint: str = ""
    ChannelFingerprint: str = ""
    InterClusterChannel: InterClusterRoutingChannel | None = None

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ComponentFingerprint": self.ComponentFingerprint,
            "PlacementVariantFingerprint": (
                self.PlacementVariantFingerprint
            ),
            "OwnershipFingerprint": self.OwnershipFingerprint,
            "TerminalDomainSizes": list(self.TerminalDomainSizes),
            "MandatoryClaimCount": self.MandatoryClaimCount,
            "DomainComplete": self.DomainComplete,
            "DomainCandidateCount": self.DomainCandidateCount,
            "MaximumClusterVariants": self.MaximumClusterVariants,
            "MaximumRepairClusters": self.MaximumRepairClusters,
            "PlacementStates": [
                State.ToDictionary() for State in self.PlacementStates
            ],
            "TerminalDomains": [
                Domain.ToDictionary() for Domain in self.TerminalDomains
            ],
            "ConflictComponents": [
                Component.ToDictionary()
                for Component in self.ConflictComponents
            ],
            "PolicyFingerprint": self.PolicyFingerprint,
            "LocalRouteFingerprint": self.LocalRouteFingerprint,
            "ChannelFingerprint": self.ChannelFingerprint,
            "InterClusterChannel": (
                self.InterClusterChannel.ToDictionary()
                if self.InterClusterChannel is not None
                else None
            ),
        }


@dataclass(frozen=True)
class ClusterInterfaceAssignment:
    """Authoritative result of one cluster-interface capacity assignment."""

    Problem: ClusterInterfaceProblem
    Feasible: bool
    AssignmentFingerprint: str = ""
    OwnershipAssignmentFingerprint: str = ""
    Objective: tuple[object, ...] = ()
    UnsatisfiedTerminalCount: int = 0

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Problem": self.Problem.ToDictionary(),
            "Feasible": self.Feasible,
            "AssignmentFingerprint": self.AssignmentFingerprint,
            "OwnershipAssignmentFingerprint": (
                self.OwnershipAssignmentFingerprint
            ),
            "Objective": list(self.Objective),
            "UnsatisfiedTerminalCount": self.UnsatisfiedTerminalCount,
        }


@dataclass(frozen=True)
class ClusterInterfacePortfolioProblem:
    """One bounded joint choice over placement and interface assignments."""

    PlacementStates: tuple[ClusterInterfacePlacementState, ...]
    MaximumPlacementStates: int = 6
    MaximumAffectedClusters: int = 3
    StateAudits: tuple[ClusterInterfacePortfolioStateAudit, ...] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "PlacementStates": [
                State.ToDictionary() for State in self.PlacementStates
            ],
            "MaximumPlacementStates": self.MaximumPlacementStates,
            "MaximumAffectedClusters": self.MaximumAffectedClusters,
            "StateAudits": [
                Audit.ToDictionary() for Audit in self.StateAudits
            ],
        }


@dataclass(frozen=True)
class ClusterInterfacePortfolioAssignment:
    """Selected coherent placement plus exact terminal ownership assignment."""

    Problem: ClusterInterfacePortfolioProblem
    SelectedPlacementStateFingerprint: str
    InterfaceAssignment: ClusterInterfaceAssignment
    Objective: tuple[object, ...] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Problem": self.Problem.ToDictionary(),
            "SelectedPlacementStateFingerprint": (
                self.SelectedPlacementStateFingerprint
            ),
            "InterfaceAssignment": (
                self.InterfaceAssignment.ToDictionary()
            ),
            "Objective": list(self.Objective),
        }


class ClusterInterfaceAssignmentPrepared(RuntimeError):
    """Internal control transfer after exact interface ownership is frozen."""

    def __init__(self, Assignment: ClusterInterfaceAssignment) -> None:
        super().__init__("cluster interface assignment prepared")
        self.Assignment = Assignment


@dataclass(frozen=True)
class TrackAssignmentPreparation:
    """Immutable pre-route capacity result for one fixed placement."""

    Success: bool
    SelectedCandidateIds: tuple[tuple[str, str], ...]
    CandidateCounts: tuple[tuple[str, int], ...]
    ConflictSignals: tuple[str, ...]
    ConflictResourceIndices: tuple[int, ...]
    ExpansionCount: int
    Complete: bool
    IncompleteReason: str = ""
    Diagnostics: tuple[tuple[str, object], ...] = ()
    # Complete placement-local trees may be selected as first-class values in
    # the same bounded native assignment as ordinary portal/track candidates.
    # Keep their identities explicit so the frozen final handoff can verify
    # the exact combined domain without reconstructing a claim-release step.
    SelectedLocalClaimChoiceIds: tuple[tuple[str, str], ...] = ()
    LocalClaimDomainFingerprint: str = ""
    # The final route must rebuild the exact same candidate-value universe
    # that produced this frozen selection.  Candidate IDs alone are not a
    # sufficient contract because a regenerated value could retain its ID
    # while claiming different physical resources.
    CandidateDomainFingerprint: str = ""
    # Exact physical claims owned by the selected ordinary portal/track
    # candidates and selected complete local-tree values.  The pre-route
    # interface selector consumes this identity when it compares component
    # templates; it must not treat a locally solved candidate as a resource-
    # free witness merely because its access-fabric assignment was deferred.
    SelectedCapacityResourceIds: tuple[str, ...] = ()
    # Preserve the logical owner of the exact compact claims selected before
    # detailed routing.  The selected-world tree builder uses peer ownership
    # as immutable blockers while allowing a signal to reuse its own access
    # stub and guide resources.
    SelectedCapacityClaimsByOwner: tuple[
        tuple[str, RoutingResourceClaims], ...
    ] = ()
    # A nonempty key is the one immutable conditional interface geometry
    # selected by the exact raw assignment.  It is published explicitly so
    # placement can materialize only that geometry after the solve.
    SelectedConditionalTemplateKey: str = ""
    # The complete named pre-route contract selected by the capacity solver.
    # It includes core, interface, and layer requirements when applicable.
    SelectedContractRequirements: tuple[tuple[str, str], ...] = ()
    # Selected non-routable contract values (for example, access stubs) are
    # retained separately so placement can reconstruct its frozen geometry
    # handoff without exposing them as detailed-route candidate IDs.
    SelectedContractClaimChoiceIds: tuple[tuple[str, str], ...] = ()
    # A route-guide factor fixes the portal tuple, layer, axis, lane, and
    # finite corridor used by the later detailed-tree materialization.  It is
    # selected in the one pre-route capacity solve but is not itself a routed
    # tree candidate.
    SelectedRouteGuideFactorChoiceIds: tuple[tuple[str, str], ...] = ()
    # Lossless selected-world handoff for the immutable guide shape.  The
    # descriptor is owned by AuthoritativePlanner to avoid duplicating the
    # portal model here; it is excluded from equality and summarized below.
    SelectedRouteGuideFactorDescriptors: tuple[
        tuple[str, str, Any], ...
    ] = field(default=(), compare=False, repr=False)
    SelectedRouteGuideFactorCertificates: tuple[
        tuple[str, str, Any], ...
    ] = field(default=(), compare=False, repr=False)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Success": self.Success,
            "SelectedCandidateIds": [list(Value) for Value in self.SelectedCandidateIds],
            "CandidateCounts": [list(Value) for Value in self.CandidateCounts],
            "ConflictSignals": list(self.ConflictSignals),
            "ConflictResourceIndices": list(self.ConflictResourceIndices),
            "ExpansionCount": self.ExpansionCount,
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "Diagnostics": dict(self.Diagnostics),
            "SelectedLocalClaimChoiceIds": [
                list(Value) for Value in self.SelectedLocalClaimChoiceIds
            ],
            "LocalClaimDomainFingerprint": self.LocalClaimDomainFingerprint,
            "CandidateDomainFingerprint": self.CandidateDomainFingerprint,
            "SelectedCapacityResourceIds": list(
                self.SelectedCapacityResourceIds
            ),
            "SelectedCapacityClaimsByOwner": {
                Signal: sorted(map(str, Claims.ResourceIds))
                for Signal, Claims in self.SelectedCapacityClaimsByOwner
            },
            "SelectedConditionalTemplateKey": (
                self.SelectedConditionalTemplateKey
            ),
            "SelectedContractRequirements": [
                list(Value) for Value in self.SelectedContractRequirements
            ],
            "SelectedContractClaimChoiceIds": [
                list(Value) for Value in self.SelectedContractClaimChoiceIds
            ],
            "SelectedRouteGuideFactorChoiceIds": [
                list(Value)
                for Value in self.SelectedRouteGuideFactorChoiceIds
            ],
            "SelectedRouteGuideFactorDescriptors": [
                {
                    "Signal": Signal,
                    "FactorId": FactorId,
                    "Layer": int(Descriptor.Layer),
                    "Axis": str(Descriptor.Axis),
                    "Lane": int(Descriptor.Lane),
                    "Guide": [
                        list(Value) for Value in sorted(Descriptor.Guide)
                    ],
                    "GuideExpansion": int(Descriptor.GuideExpansion),
                    "RoutingY": int(Descriptor.RoutingY),
                    "SourcePortalId": str(
                        Descriptor.SourcePortal.PortalId
                    ),
                    "TargetPortalIds": [
                        str(Value.PortalId)
                        for Value in Descriptor.TargetPortals
                    ],
                }
                for Signal, FactorId, Descriptor
                in self.SelectedRouteGuideFactorDescriptors
            ],
            "SelectedRouteGuideFactorCertificates": [
                {
                    "Signal": Signal,
                    "FactorId": FactorId,
                    **Certificate.ToDictionary(),
                }
                for Signal, FactorId, Certificate
                in self.SelectedRouteGuideFactorCertificates
            ],
        }


class TrackAssignmentPrepared(RuntimeError):
    """Internal control transfer after pre-route capacity assignment."""

    def __init__(self, Preparation: TrackAssignmentPreparation) -> None:
        super().__init__("track assignment prepared")
        self.Preparation = Preparation


@dataclass(frozen=True)
class PlacementAccessEscapeStub:
    """One exact terminal-to-fabric escape with capacity-one claims."""

    Terminal: Position3
    Ingress: Position3
    Path: tuple[Position3, ...]
    PhysicalClaims: RoutingResourceClaims
    CapacityResourceIds: tuple[RoutingResourceId, ...]
    Complete: bool
    IncompleteReason: str = ""
    ChoiceId: str = ""
    PhysicalClaimsFingerprint: str = ""
    PhysicalClaimsDeferred: bool = False

    def __post_init__(self) -> None:
        if not self.ChoiceId:
            object.__setattr__(
                self,
                "ChoiceId",
                BuildPlacementAccessEscapeStubChoiceId(self),
            )
        if not self.PhysicalClaimsFingerprint:
            object.__setattr__(
                self,
                "PhysicalClaimsFingerprint",
                BuildPlacementAccessEscapeStubClaimsFingerprint(self),
            )
        if self.PhysicalClaimsDeferred and (
            self.PhysicalClaims.WireCells != frozenset(self.Path)
            or self.PhysicalClaims.SupportCells
            or self.PhysicalClaims.ElectricalCells
        ):
            raise ValueError(
                "deferred access claims must retain only their exact path "
                "and required-air cells"
            )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Terminal": list(self.Terminal),
            "Ingress": list(self.Ingress),
            "Path": [list(Value) for Value in self.Path],
            "PhysicalClaims": {
                "WireCells": len(self.PhysicalClaims.WireCells),
                "SupportCells": len(self.PhysicalClaims.SupportCells),
                "RequiredAirCells": len(
                    self.PhysicalClaims.RequiredAirCells
                ),
                "ElectricalCells": len(
                    self.PhysicalClaims.ElectricalCells
                ),
            },
            "CapacityResourceIds": [
                str(Value) for Value in self.CapacityResourceIds
            ],
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "ChoiceId": self.ChoiceId,
            "PhysicalClaimsFingerprint": self.PhysicalClaimsFingerprint,
            "PhysicalClaimsDeferred": self.PhysicalClaimsDeferred,
        }


def BuildPlacementAccessEscapeStubChoiceId(
    Stub: PlacementAccessEscapeStub,
) -> str:
    """Return the stable exact-physical choice id for one access stub."""
    ExistingChoiceId = str(getattr(Stub, "ChoiceId", ""))
    if ExistingChoiceId:
        return ExistingChoiceId
    Path = tuple(Stub.Path)
    Terminal = tuple(getattr(Stub, "Terminal", Path[0]))
    Ingress = tuple(getattr(Stub, "Ingress", Path[-1]))
    return sha256(repr((
        "placement-access-escape-stub-choice-v1",
        Terminal,
        Ingress,
        Path,
    )).encode("utf-8")).hexdigest()[:16]


def BuildPlacementAccessEscapeStubClaimsFingerprint(
    Stub: PlacementAccessEscapeStub,
) -> str:
    Claims = Stub.PhysicalClaims
    return sha256(repr((
        "placement-access-escape-stub-claims-v1",
        tuple(sorted(Claims.WireCells)),
        tuple(sorted(Claims.SupportCells)),
        tuple(sorted(Claims.RequiredAirCells)),
        tuple(sorted(Claims.ElectricalCells)),
    )).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class PlacementAccessTerminalDomain:
    """Complete finite escape domain for one placed signal terminal."""

    Signal: str
    Terminal: Position3
    EscapeStubs: tuple[PlacementAccessEscapeStub, ...]
    Complete: bool
    IncompleteReason: str = ""
    # A compact pre-route aggregate cannot use the transient transformed
    # terminal coordinate as its variable identity: every core/template
    # member has different physical coordinates for the same logical pin.
    # This role is stable within a signal profile (root or ordered target).
    LogicalKey: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "Terminal": list(self.Terminal),
            "LogicalKey": self.LogicalKey,
            "EscapeStubs": [
                Value.ToDictionary() for Value in self.EscapeStubs
            ],
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
        }


@dataclass(frozen=True)
class AccessContractBounds:
    """Measured XZ extent of one finite placement-access contract.

    ``Bounds`` is derived from the actual materialized candidate domain:
    fabric nodes, declared ingress nodes, and every retained terminal-stub
    path.  It deliberately does not substitute a nominal ring envelope for
    those positions.  ``DeclaredOuterBounds`` remains separate because the
    authoritative router also reserves an explicitly declared shell even
    where that shell has no materialized node in a particular resource graph.

    The type is audit-only.  It does not constrain candidate generation,
    selection, or detailed routing.
    """

    Bounds: tuple[int, int, int, int] | None
    RoutingRegionBounds: tuple[int, int, int, int] | None
    DeclaredOuterBounds: tuple[int, int, int, int] | None
    PositionCount: int
    FabricNodeCount: int
    IngressNodeCount: int
    StubCount: int
    StubPathPositionCount: int

    @staticmethod
    def _UnionBounds(
        First: tuple[int, int, int, int] | None,
        Second: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, int, int] | None:
        if First is None:
            return Second
        if Second is None:
            return First
        return (
            min(First[0], Second[0]),
            min(First[1], Second[1]),
            max(First[2], Second[2]),
            max(First[3], Second[3]),
        )

    @staticmethod
    def _NormalizeBounds(
        Bounds: object,
    ) -> tuple[int, int, int, int] | None:
        if Bounds is None:
            return None
        if not isinstance(Bounds, tuple) or len(Bounds) != 4:
            raise ValueError("access contract bounds are invalid")
        Normalized = tuple(int(Value) for Value in Bounds)
        if (
            Normalized[0] > Normalized[2]
            or Normalized[1] > Normalized[3]
        ):
            raise ValueError("access contract bounds are inverted")
        return Normalized

    @classmethod
    def FromPlacementAccessFabric(
        cls,
        Fabric: Any,
        *,
        Domains: tuple[PlacementAccessTerminalDomain, ...] | None = None,
    ) -> "AccessContractBounds":
        """Measure one complete or selected finite fabric domain.

        Supplying ``Domains`` permits the final detailed-routing audit to
        measure the frozen selected stubs, while the fabric property below
        records the complete pre-route candidate domain.  Neither operation
        mutates the fabric or changes which values the solver can choose.
        """
        DomainValues = (
            tuple(getattr(Fabric, "TerminalDomains", ()))
            if Domains is None
            else tuple(Domains)
        )
        Nodes = tuple(getattr(Fabric, "Nodes", ()))
        Ingresses = tuple(getattr(Fabric, "IngressNodes", ()))
        StubPaths = tuple(
            tuple(Stub.Path)
            for Domain in DomainValues
            for Stub in Domain.EscapeStubs
        )
        Positions = frozenset(
            Position
            for Position in (*Nodes, *Ingresses)
        ) | frozenset(
            Position
            for Path in StubPaths
            for Position in Path
        )
        Bounds = (
            (
                min(Position[0] for Position in Positions),
                min(Position[2] for Position in Positions),
                max(Position[0] for Position in Positions),
                max(Position[2] for Position in Positions),
            )
            if Positions
            else None
        )
        DeclaredOuterBounds = cls._NormalizeBounds(
            getattr(Fabric, "OuterBounds", None)
        )
        return cls(
            Bounds=Bounds,
            RoutingRegionBounds=cls._UnionBounds(
                Bounds,
                DeclaredOuterBounds,
            ),
            DeclaredOuterBounds=DeclaredOuterBounds,
            PositionCount=len(Positions),
            FabricNodeCount=len(Nodes),
            IngressNodeCount=len(Ingresses),
            StubCount=sum(
                len(Domain.EscapeStubs)
                for Domain in DomainValues
            ),
            StubPathPositionCount=sum(
                len(Path) for Path in StubPaths
            ),
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Bounds": list(self.Bounds) if self.Bounds is not None else None,
            "RoutingRegionBounds": (
                list(self.RoutingRegionBounds)
                if self.RoutingRegionBounds is not None
                else None
            ),
            "DeclaredOuterBounds": (
                list(self.DeclaredOuterBounds)
                if self.DeclaredOuterBounds is not None
                else None
            ),
            "PositionCount": self.PositionCount,
            "FabricNodeCount": self.FabricNodeCount,
            "IngressNodeCount": self.IngressNodeCount,
            "StubCount": self.StubCount,
            "StubPathPositionCount": self.StubPathPositionCount,
        }


@dataclass(frozen=True)
class DetailedRoutingBounds:
    """Audit record for the XZ canvas passed to detailed routing.

    The record mirrors the authoritative planner's region expansion: merge
    the placed macro hull with the selected access contract, then apply the
    already-chosen scalar search margins.  It is intentionally descriptive;
    the router remains the owner of its bounds and behavior.
    """

    CoreBounds: tuple[int, int, int, int]
    AccessContractBounds: AccessContractBounds
    RoutingRegionBounds: tuple[int, int, int, int]
    SearchMarginX: int
    SearchMarginZ: int
    CanvasBounds: tuple[int, int, int, int]

    @classmethod
    def FromCoreAndAccessContract(
        cls,
        CoreBounds: tuple[int, int, int, int],
        AccessContractBoundsValue: AccessContractBounds,
        *,
        SearchMarginX: int,
        SearchMarginZ: int,
    ) -> "DetailedRoutingBounds":
        if SearchMarginX < 0 or SearchMarginZ < 0:
            raise ValueError("detailed routing search margins are invalid")
        NormalizedCoreBounds = AccessContractBounds._NormalizeBounds(CoreBounds)
        if NormalizedCoreBounds is None:
            raise ValueError("detailed routing requires core bounds")
        RoutingRegionBounds = AccessContractBounds._UnionBounds(
            NormalizedCoreBounds,
            AccessContractBoundsValue.RoutingRegionBounds,
        )
        if RoutingRegionBounds is None:
            raise RuntimeError("detailed routing bounds lost its placed core")
        return cls(
            CoreBounds=NormalizedCoreBounds,
            AccessContractBounds=AccessContractBoundsValue,
            RoutingRegionBounds=RoutingRegionBounds,
            SearchMarginX=int(SearchMarginX),
            SearchMarginZ=int(SearchMarginZ),
            CanvasBounds=(
                RoutingRegionBounds[0] - int(SearchMarginX),
                RoutingRegionBounds[1] - int(SearchMarginZ),
                RoutingRegionBounds[2] + int(SearchMarginX),
                RoutingRegionBounds[3] + int(SearchMarginZ),
            ),
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CoreBounds": list(self.CoreBounds),
            "AccessContractBounds": self.AccessContractBounds.ToDictionary(),
            "RoutingRegionBounds": list(self.RoutingRegionBounds),
            "SearchMarginX": self.SearchMarginX,
            "SearchMarginZ": self.SearchMarginZ,
            "CanvasBounds": list(self.CanvasBounds),
        }


@dataclass(frozen=True)
class FrozenPerFaceRoutingEnvelope:
    """One selected derived-access canvas consumed verbatim by routing.

    The derived compact path decides this finite physical contract before the
    authoritative capacity solve.  It records the actual horizontal canvas,
    the usable vertical interval, the selected logical layers, and how much
    perimeter material belongs to each face.  It is intentionally separate
    from the older ``AccessRingTrackCount`` scalar: that scalar remains a
    compatibility summary, while this type is the route-stage authority.

    Bounds use inclusive coordinates, matching the resource graph and the
    existing placement-access contracts.
    """

    RoutingRegionBounds: tuple[int, int, int, int]
    CanvasBounds: tuple[int, int, int, int]
    YBounds: tuple[int, int]
    PermittedLayers: tuple[int, ...]
    PerimeterFaceTrackCounts: tuple[tuple[str, int], ...]
    EnvelopeFingerprint: str

    def __post_init__(self) -> None:
        MinimumX, MinimumZ, MaximumX, MaximumZ = self.RoutingRegionBounds
        CanvasMinimumX, CanvasMinimumZ, CanvasMaximumX, CanvasMaximumZ = (
            self.CanvasBounds
        )
        MinimumY, MaximumY = self.YBounds
        if MinimumX > MaximumX or MinimumZ > MaximumZ:
            raise ValueError("frozen routing region bounds are inverted")
        if (
            CanvasMinimumX > MinimumX
            or CanvasMinimumZ > MinimumZ
            or CanvasMaximumX < MaximumX
            or CanvasMaximumZ < MaximumZ
        ):
            raise ValueError(
                "frozen routing canvas must enclose its routing region"
            )
        if MinimumY > MaximumY:
            raise ValueError("frozen routing Y bounds are inverted")
        if not self.PermittedLayers or self.PermittedLayers != tuple(
            range(len(self.PermittedLayers))
        ):
            raise ValueError("frozen routing layers must be contiguous")
        FaceNames = tuple(Face for Face, _Count in self.PerimeterFaceTrackCounts)
        if FaceNames != ("north", "south", "west", "east"):
            raise ValueError("frozen routing face tracks are not canonical")
        if any(Count < 0 for _Face, Count in self.PerimeterFaceTrackCounts):
            raise ValueError("frozen routing face tracks are invalid")
        if not self.EnvelopeFingerprint:
            raise ValueError("frozen routing envelope requires a fingerprint")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "RoutingRegionBounds": list(self.RoutingRegionBounds),
            "CanvasBounds": list(self.CanvasBounds),
            "YBounds": list(self.YBounds),
            "PermittedLayers": list(self.PermittedLayers),
            "PerimeterFaceTrackCounts": {
                Face: Count
                for Face, Count in self.PerimeterFaceTrackCounts
            },
            "EnvelopeFingerprint": self.EnvelopeFingerprint,
        }


@dataclass(frozen=True)
class PlacementAccessFabric:
    """Immutable placement-wide routing fabric built before capacity solve."""

    FabricFingerprint: str
    Nodes: tuple[Position3, ...]
    Edges: tuple[tuple[Position3, Position3], ...]
    IngressNodes: tuple[Position3, ...]
    PhysicalClaims: RoutingResourceClaims
    CapacityResourceIds: tuple[RoutingResourceId, ...]
    TerminalDomains: tuple[PlacementAccessTerminalDomain, ...]
    TopologyKind: str
    Complete: bool
    AccessRingTrackCount: int = 0
    AccessRingFingerprint: str = ""
    # A selected derived-perimeter placement owns an exact asymmetric outer
    # routing box, stored as inclusive ``(min_x, min_z, max_x, max_z)``.
    # Legacy fabrics deliberately leave this unset and retain their existing
    # scalar-margin behavior.
    OuterBounds: tuple[int, int, int, int] | None = None
    ActiveFaces: tuple[str, ...] = ()
    PerimeterSlotAssignmentFingerprint: str = ""
    FrozenRoutingEnvelope: FrozenPerFaceRoutingEnvelope | None = None
    # Bounded legal escape construction is pre-route proof work.  These
    # counters make an incomplete factor auditable without treating its cap
    # as a topology or routing retry.
    LegalEscapeExpansionCount: int = 0
    LegalEscapeExpansionLimit: int | None = None
    LegalEscapeWorkLimitKind: str = ""
    LegalEscapeDirectionStateUpperBound: int | None = None
    NativeEscapeKernelUsed: bool = False
    NativeEscapeKernelCallCount: int = 0
    NativeEscapeKernelExpansionCount: int = 0
    NativeEscapeKernelComplete: bool = True
    NativeEscapeKernelElapsedSeconds: float = 0.0
    NativeEscapeSharedBatchUsed: bool = False
    NativeEscapeSharedBatchElapsedSeconds: float = 0.0
    NativeEscapeFallbackUsed: bool = False
    NativeClaimBatchWorkItems: int = 0
    NativeClaimBatchWorkerCount: int = 0
    NativeClaimBatchElapsedSeconds: float = 0.0
    DominatedEscapeStubCount: int = 0
    IncompleteReason: str = ""
    Technology: Any = field(default=None, compare=False, repr=False)

    @property
    def AccessContractBounds(self) -> AccessContractBounds:
        """Return the full finite pre-route access-domain extent for audit."""
        return AccessContractBounds.FromPlacementAccessFabric(self)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "FabricFingerprint": self.FabricFingerprint,
            "NodeCount": len(self.Nodes),
            "EdgeCount": len(self.Edges),
            "IngressNodes": [list(Value) for Value in self.IngressNodes],
            "PhysicalClaims": {
                "WireCells": len(self.PhysicalClaims.WireCells),
                "SupportCells": len(self.PhysicalClaims.SupportCells),
                "RequiredAirCells": len(
                    self.PhysicalClaims.RequiredAirCells
                ),
                "ElectricalCells": len(
                    self.PhysicalClaims.ElectricalCells
                ),
            },
            "CapacityResourceIds": [
                str(Value) for Value in self.CapacityResourceIds
            ],
            "AccessContractBounds": self.AccessContractBounds.ToDictionary(),
            "TerminalDomains": [
                Value.ToDictionary() for Value in self.TerminalDomains
            ],
            "TopologyKind": self.TopologyKind,
            "AccessRingTrackCount": self.AccessRingTrackCount,
            "AccessRingFingerprint": self.AccessRingFingerprint,
            "OuterBounds": (
                list(self.OuterBounds)
                if self.OuterBounds is not None
                else None
            ),
            "ActiveFaces": list(self.ActiveFaces),
            "PerimeterSlotAssignmentFingerprint": (
                self.PerimeterSlotAssignmentFingerprint
            ),
            "FrozenRoutingEnvelope": (
                self.FrozenRoutingEnvelope.ToDictionary()
                if self.FrozenRoutingEnvelope is not None
                else None
            ),
            "LegalEscapeExpansionCount": self.LegalEscapeExpansionCount,
            "LegalEscapeExpansionLimit": self.LegalEscapeExpansionLimit,
            "LegalEscapeWorkLimitKind": self.LegalEscapeWorkLimitKind,
            "LegalEscapeDirectionStateUpperBound": (
                self.LegalEscapeDirectionStateUpperBound
            ),
            "NativeEscapeKernelUsed": self.NativeEscapeKernelUsed,
            "NativeEscapeKernelCallCount": (
                self.NativeEscapeKernelCallCount
            ),
            "NativeEscapeKernelExpansionCount": (
                self.NativeEscapeKernelExpansionCount
            ),
            "NativeEscapeKernelComplete": self.NativeEscapeKernelComplete,
            "NativeEscapeKernelElapsedSeconds": (
                self.NativeEscapeKernelElapsedSeconds
            ),
            "NativeEscapeSharedBatchUsed": (
                self.NativeEscapeSharedBatchUsed
            ),
            "NativeEscapeSharedBatchElapsedSeconds": (
                self.NativeEscapeSharedBatchElapsedSeconds
            ),
            "NativeEscapeFallbackUsed": self.NativeEscapeFallbackUsed,
            "NativeClaimBatchWorkItems": self.NativeClaimBatchWorkItems,
            "NativeClaimBatchWorkerCount": self.NativeClaimBatchWorkerCount,
            "NativeClaimBatchElapsedSeconds": (
                self.NativeClaimBatchElapsedSeconds
            ),
            "DominatedEscapeStubCount": self.DominatedEscapeStubCount,
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
        }


@dataclass(frozen=True)
class PlacementAccessAssignment:
    """One frozen capacity-one escape selection for a fixed fabric."""

    FabricFingerprint: str
    AssignmentFingerprint: str
    SelectedStubIndices: tuple[tuple[str, Position3, int], ...]
    CapacityResourceIds: tuple[RoutingResourceId, ...]
    ExpansionCount: int
    Success: bool
    Complete: bool
    ConflictSignals: tuple[str, ...] = ()
    FrontierSignals: tuple[str, ...] = ()
    MaximumRoutedSignalCount: int = 0
    FirstUnroutableSignal: str = ""
    IncompleteReason: str = ""
    SignalRoutes: tuple[tuple[str, tuple[Position3, ...]], ...] = ()
    # Complete placement-local trees selected by the same immutable access
    # factor.  Their actual claims remain on the placement; this field is the
    # frozen ownership handoff used to restore them after fabric construction.
    SelectedLocalRouteSignals: tuple[str, ...] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "FabricFingerprint": self.FabricFingerprint,
            "AssignmentFingerprint": self.AssignmentFingerprint,
            "SelectedStubIndices": [
                {
                    "Signal": Signal,
                    "Terminal": list(Terminal),
                    "StubIndex": StubIndex,
                }
                for Signal, Terminal, StubIndex in self.SelectedStubIndices
            ],
            "CapacityResourceIds": [
                str(Value) for Value in self.CapacityResourceIds
            ],
            "ExpansionCount": self.ExpansionCount,
            "Success": self.Success,
            "Complete": self.Complete,
            "ConflictSignals": list(self.ConflictSignals),
            "FrontierSignals": list(self.FrontierSignals),
            "MaximumRoutedSignalCount": self.MaximumRoutedSignalCount,
            "FirstUnroutableSignal": self.FirstUnroutableSignal,
            "IncompleteReason": self.IncompleteReason,
            "SignalRoutes": [
                {
                    "Signal": Signal,
                    "Nodes": [list(Position) for Position in Nodes],
                }
                for Signal, Nodes in self.SignalRoutes
            ],
            "SelectedLocalRouteSignals": list(
                self.SelectedLocalRouteSignals
            ),
        }


@dataclass(frozen=True)
class ComponentRoutingFabric:
    """Finite capacity-one graph owned by one routed component backend."""

    FabricFingerprint: str
    Nodes: tuple[Position3, ...]
    Edges: tuple[tuple[Position3, Position3], ...]
    IngressNodes: tuple[Position3, ...]
    TopologyKind: str
    Complete: bool
    IncompleteReason: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "FabricFingerprint": self.FabricFingerprint,
            "NodeCount": len(self.Nodes),
            "EdgeCount": len(self.Edges),
            "IngressNodes": [list(Value) for Value in self.IngressNodes],
            "TopologyKind": self.TopologyKind,
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
        }


@dataclass(frozen=True)
class ComponentTerminalAccessCandidate:
    """One complete terminal-to-fabric or passive escape witness."""

    CandidateFingerprint: str
    Attachment: Position3
    Path: tuple[Position3, ...]
    Claims: RoutingResourceClaims
    Layer: int = 0
    Cost: int = 0


@dataclass(frozen=True)
class ComponentTerminalAccessDomain:
    """Identifier-independent finite access domain for one terminal."""

    Signal: str
    Terminal: Position3
    TerminalRole: str
    TerminalFingerprint: str
    Candidates: tuple[ComponentTerminalAccessCandidate, ...]
    Complete: bool = True

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "TerminalRole": self.TerminalRole,
            "TerminalFingerprint": self.TerminalFingerprint,
            "CandidateCount": len(self.Candidates),
            "Complete": self.Complete,
        }


@dataclass(frozen=True)
class ComponentPerimeterPortCandidate:
    """One guide-independent local access tuple ending at a perimeter seam."""

    CandidateFingerprint: str
    Signal: str
    Direction: str
    FabricDomainFingerprint: str
    OwnedTerminals: tuple[Position3, ...]
    OwnedCandidateFingerprints: tuple[str, ...]
    FabricAttachment: Position3
    Attachment: Position3
    LocalPath: tuple[Position3, ...]
    Claims: RoutingResourceClaims
    Layer: int
    Capacity: int = 1

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CandidateFingerprint": self.CandidateFingerprint,
            "Signal": self.Signal,
            "Direction": self.Direction,
            "FabricDomainFingerprint": self.FabricDomainFingerprint,
            "OwnedTerminals": [
                list(Value) for Value in self.OwnedTerminals
            ],
            "OwnedCandidateFingerprints": list(
                self.OwnedCandidateFingerprints
            ),
            "FabricAttachment": list(self.FabricAttachment),
            "Attachment": list(self.Attachment),
            "LocalPath": [list(Value) for Value in self.LocalPath],
            "Layer": self.Layer,
            "Capacity": self.Capacity,
            "ClaimCount": len(self.Claims.ResourceIds),
        }


@dataclass(frozen=True)
class ComponentPortBankDomain:
    """Complete guide-independent perimeter candidate domain for one port."""

    Signal: str
    Direction: str
    Candidates: tuple[ComponentPerimeterPortCandidate, ...]
    Complete: bool = True

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "Direction": self.Direction,
            "CandidateCount": len(self.Candidates),
            "CandidateFingerprints": [
                Value.CandidateFingerprint for Value in self.Candidates
            ],
            "Complete": self.Complete,
        }


@dataclass(frozen=True)
class ComponentCutAccessFeasibilityCertificate:
    """Complete local terminal and seam proof for one placed component cut."""

    CertificateFingerprint: str
    StructuralFingerprint: str
    PlacementFingerprint: str
    ComponentGraphFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    ComponentId: int | None
    EnvelopeMinimum: Position3
    EnvelopeMaximum: Position3
    BoundedStemContractFingerprint: str
    PortDomains: tuple[ComponentPortBankDomain, ...]
    Complete: bool
    Feasible: bool
    ProofKind: str = ""
    AffectedSignals: tuple[str, ...] = ()
    Diagnostics: dict[str, object] = field(default_factory=dict)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": (
                "component-cut-access-feasibility-certificate-v1"
            ),
            "CertificateFingerprint": self.CertificateFingerprint,
            "StructuralFingerprint": self.StructuralFingerprint,
            "PlacementFingerprint": self.PlacementFingerprint,
            "ComponentGraphFingerprint": self.ComponentGraphFingerprint,
            "ResourceGraphFingerprint": self.ResourceGraphFingerprint,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ComponentId": self.ComponentId,
            "EnvelopeMinimum": list(self.EnvelopeMinimum),
            "EnvelopeMaximum": list(self.EnvelopeMaximum),
            "BoundedStemContractFingerprint": (
                self.BoundedStemContractFingerprint
            ),
            "PortDomains": [
                Value.ToDictionary() for Value in self.PortDomains
            ],
            "Complete": self.Complete,
            "Feasible": self.Feasible,
            "ProofKind": self.ProofKind,
            "AffectedSignals": list(self.AffectedSignals),
            "Diagnostics": self.Diagnostics,
        }


@dataclass(frozen=True)
class ComponentForeignTransitDomain:
    """Finite routed witnesses preserving one foreign net across a component."""

    Signal: str
    PartitionAxis: str
    PartitionFingerprint: str
    Candidates: tuple["RoutedComponentNet", ...]
    Complete: bool = True
    Diagnostics: dict[str, object] = field(default_factory=dict)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "PartitionAxis": self.PartitionAxis,
            "PartitionFingerprint": self.PartitionFingerprint,
            "CandidateCount": len(self.Candidates),
            "Complete": self.Complete,
            "Diagnostics": self.Diagnostics,
        }


@dataclass(frozen=True)
class ComponentFeedthroughContract:
    """One explicitly declared capacity-one path across a closed component."""

    Signal: str
    EndpointPairs: tuple[tuple[Position3, Position3], ...]
    Capacity: int = 1
    ReservedPathNodes: tuple[Position3, ...] = ()
    Claims: Any = None
    ReservationFingerprint: str = ""
    EndpointDomainFingerprint: str = ""
    EndpointCandidateFingerprint: str = ""
    EndpointCandidateCount: int = 0
    EndpointPrescreenRetainedCandidateCount: int = 0
    EndpointPrescreenRejectedCandidateCount: int = 0

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "EndpointPairs": [
                {
                    "Entry": list(Entry),
                    "Exit": list(Exit),
                }
                for Entry, Exit in self.EndpointPairs
            ],
            "Capacity": self.Capacity,
            "ReservedPathNodes": [
                list(Value) for Value in self.ReservedPathNodes
            ],
            "ReservationFingerprint": self.ReservationFingerprint,
            "EndpointDomainFingerprint": self.EndpointDomainFingerprint,
            "EndpointCandidateFingerprint": (
                self.EndpointCandidateFingerprint
            ),
            "EndpointCandidateCount": self.EndpointCandidateCount,
            "EndpointPrescreenRetainedCandidateCount": (
                self.EndpointPrescreenRetainedCandidateCount
            ),
            "EndpointPrescreenRejectedCandidateCount": (
                self.EndpointPrescreenRejectedCandidateCount
            ),
        }


@dataclass(frozen=True)
class PhysicalComponentFeedthroughEndpointCandidate:
    """One layer-exact interior passage before exterior seam binding."""

    CandidateFingerprint: str
    Layer: int
    Entry: Position3
    Exit: Position3
    ReservedPathNodes: tuple[Position3, ...]
    Claims: RoutingResourceClaims

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CandidateFingerprint": self.CandidateFingerprint,
            "Layer": self.Layer,
            "Entry": list(self.Entry),
            "Exit": list(self.Exit),
            "ReservedPathNodes": [
                list(Value) for Value in self.ReservedPathNodes
            ],
        }


@dataclass(frozen=True)
class PreparedPhysicalComponentFeedthroughEndpointDomain:
    """Complete port-independent endpoint/path domain for one feedthrough."""

    DomainFingerprint: str
    Signal: str
    Layer: int
    FabricFingerprint: str
    ResourceGraphFingerprint: str
    Candidates: tuple[PhysicalComponentFeedthroughEndpointCandidate, ...]
    Complete: bool

    def ToDictionary(self) -> dict[str, object]:
        return {
            "DomainFingerprint": self.DomainFingerprint,
            "Signal": self.Signal,
            "Layer": self.Layer,
            "FabricFingerprint": self.FabricFingerprint,
            "ResourceGraphFingerprint": self.ResourceGraphFingerprint,
            "CandidateCount": len(self.Candidates),
            "Complete": self.Complete,
            "CandidateFingerprints": [
                Value.CandidateFingerprint for Value in self.Candidates
            ],
        }


@dataclass(frozen=True)
class ComponentInterfacePort:
    """One owned logical net exported through a closed physical boundary."""

    Signal: str
    Direction: str
    OwnedTerminals: tuple[Position3, ...]
    ExternalTerminalCount: int
    Capacity: int = 1

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "Direction": self.Direction,
            "OwnedTerminals": [
                list(Value) for Value in self.OwnedTerminals
            ],
            "ExternalTerminalCount": self.ExternalTerminalCount,
            "Capacity": self.Capacity,
        }


@dataclass(frozen=True)
class PhysicalComponentPortReservation:
    """One exact local-to-global seam selected before component routing."""

    Signal: str
    Direction: str
    OwnedTerminals: tuple[Position3, ...]
    OwnedTerminalFingerprints: tuple[str, ...]
    OwnedCandidateFingerprints: tuple[str, ...]
    FabricDomainFingerprint: str
    FabricAttachment: Position3
    Attachment: Position3
    LocalPath: tuple[Position3, ...]
    GlobalPath: tuple[Position3, ...]
    Claims: Any
    LocalClaims: Any = None
    GlobalClaims: Any = None
    OwnedAccessCandidates: tuple[
        ComponentTerminalAccessCandidate, ...
    ] = ()
    Capacity: int = 1
    ReservationFingerprint: str = ""
    CertifiedLocalContractFingerprint: str = ""
    CertifiedSeamContractFingerprint: str = ""
    CertifiedSupportReservationFingerprint: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "Direction": self.Direction,
            "OwnedTerminals": [
                list(Value) for Value in self.OwnedTerminals
            ],
            "OwnedTerminalFingerprints": list(
                self.OwnedTerminalFingerprints
            ),
            "OwnedCandidateFingerprints": list(
                self.OwnedCandidateFingerprints
            ),
            "FabricDomainFingerprint": self.FabricDomainFingerprint,
            "FabricAttachment": list(self.FabricAttachment),
            "Attachment": list(self.Attachment),
            "LocalPath": [list(Value) for Value in self.LocalPath],
            "GlobalPath": [list(Value) for Value in self.GlobalPath],
            "SeamOwnership": {
                "LocalNodeCount": len(self.LocalPath),
                "GlobalNodeCount": len(self.GlobalPath),
                "SharedAttachment": list(self.Attachment),
            },
            "OwnedAccessCandidates": [
                {
                    "CandidateFingerprint": Value.CandidateFingerprint,
                    "Attachment": list(Value.Attachment),
                    "Path": [list(Position) for Position in Value.Path],
                    "Layer": Value.Layer,
                    "Cost": Value.Cost,
                }
                for Value in self.OwnedAccessCandidates
            ],
            "Capacity": self.Capacity,
            "ReservationFingerprint": self.ReservationFingerprint,
            "CertifiedLocalContractFingerprint": (
                self.CertifiedLocalContractFingerprint
            ),
            "CertifiedSeamContractFingerprint": (
                self.CertifiedSeamContractFingerprint
            ),
            "CertifiedSupportReservationFingerprint": (
                self.CertifiedSupportReservationFingerprint
            ),
        }


@dataclass(frozen=True)
class PhysicalComponentBoundaryPortReservation:
    """Globally owned boundary reservation selected before local support.

    This contract deliberately excludes component-local paths, fabric
    attachments, and owned-access candidates.  It is therefore safe to use as
    the authoritative input to a later, separately selected local support.
    """

    Signal: str
    Direction: str
    Attachment: Position3
    GlobalPath: tuple[Position3, ...]
    GlobalClaims: Any
    Capacity: int = 1
    ChannelContractFingerprint: str = ""
    GlobalContractFingerprint: str = ""
    ApertureContractFingerprint: str = ""
    ReservationFingerprint: str = ""

    def __post_init__(self) -> None:
        if self.Capacity <= 0:
            raise ValueError("boundary port capacity must be positive")
        if self.GlobalPath and self.GlobalPath[0] != self.Attachment:
            raise ValueError(
                "boundary port global path must start at its attachment"
            )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "Direction": self.Direction,
            "Attachment": list(self.Attachment),
            "GlobalPath": [list(Value) for Value in self.GlobalPath],
            "GlobalClaimCount": len(getattr(
                self.GlobalClaims,
                "ResourceIds",
                (),
            )),
            "Capacity": self.Capacity,
            "ChannelContractFingerprint": (
                self.ChannelContractFingerprint
            ),
            "GlobalContractFingerprint": (
                self.GlobalContractFingerprint
            ),
            "ApertureContractFingerprint": (
                self.ApertureContractFingerprint
            ),
            "ReservationFingerprint": self.ReservationFingerprint,
        }


@dataclass(frozen=True)
class PhysicalExteriorApertureFabric:
    """Complete finite exterior graph reserved before local compilation.

    ``FabricFingerprint`` is structural and deliberately excludes signal
    names. ``SignalBindingFingerprint`` separately identifies the exact
    signal-to-guide/ingress relation used by an assembly plan.
    """

    EnvelopeMinimum: Position3
    EnvelopeMaximum: Position3
    Layer: int
    RoutingY: int
    ExteriorPerimeterColumns: frozenset[Position2]
    SignalGuideIngressGeometry: tuple[
        tuple[tuple[Position2, ...], tuple[Position3, ...]], ...
    ]
    SignalGuideIngressBindings: tuple[
        tuple[str, tuple[Position2, ...], tuple[Position3, ...]], ...
    ]
    DeclaredPortalIngressNodes: frozenset[Position3]
    KeepoutColumns: frozenset[Position2]
    KeepoutNodes: frozenset[Position3]
    AllowedColumns: frozenset[Position2]
    AllowedNodes: frozenset[Position3]
    AllowedEdges: frozenset[tuple[Position3, Position3]]
    Adjacency: tuple[tuple[Position3, tuple[Position3, ...]], ...]
    Complete: bool
    RegionFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    GuideIdentityFingerprint: str
    SignalBindingFingerprint: str
    FabricFingerprint: str

    def __post_init__(self) -> None:
        if any(
            self.EnvelopeMinimum[Index] > self.EnvelopeMaximum[Index]
            for Index in range(3)
        ):
            raise ValueError("exterior fabric envelope is inverted")
        if any(
            First >= Second
            for First, Second in self.AllowedEdges
        ):
            raise ValueError("exterior fabric edges must be normalized")
        if any(
            First not in self.AllowedNodes
            or Second not in self.AllowedNodes
            for First, Second in self.AllowedEdges
        ):
            raise ValueError("exterior fabric edge escapes allowed nodes")
        ExpectedAdjacency: dict[Position3, set[Position3]] = {
            Node: set() for Node in self.AllowedNodes
        }
        for First, Second in self.AllowedEdges:
            ExpectedAdjacency[First].add(Second)
            ExpectedAdjacency[Second].add(First)
        CanonicalAdjacency = tuple(
            (Node, tuple(sorted(Neighbors)))
            for Node, Neighbors in sorted(ExpectedAdjacency.items())
        )
        if self.Adjacency != CanonicalAdjacency:
            raise ValueError("exterior fabric adjacency differs from edges")
        if not self.DeclaredPortalIngressNodes <= self.AllowedNodes:
            raise ValueError("exterior fabric omits a declared ingress")
        if self.Complete and not all((
            self.RegionFingerprint,
            self.ResourceGraphFingerprint,
            self.TechnologyFingerprint,
            self.GuideIdentityFingerprint,
            self.SignalBindingFingerprint,
            self.FabricFingerprint,
        )):
            raise ValueError(
                "complete exterior fabric requires every graph identity"
            )

    def AllowsNode(self, Node: Position3) -> bool:
        return tuple(Node) in self.AllowedNodes

    def AllowsEdge(self, First: Position3, Second: Position3) -> bool:
        Edge = (tuple(First), tuple(Second))
        if Edge[1] < Edge[0]:
            Edge = (Edge[1], Edge[0])
        return Edge in self.AllowedEdges

    def Neighbors(self, Node: Position3) -> tuple[Position3, ...]:
        return dict(self.Adjacency).get(tuple(Node), ())

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "physical-exterior-aperture-fabric-v2",
            "EnvelopeMinimum": list(self.EnvelopeMinimum),
            "EnvelopeMaximum": list(self.EnvelopeMaximum),
            "Layer": self.Layer,
            "RoutingY": self.RoutingY,
            "AllowedColumnCount": len(self.AllowedColumns),
            "AllowedNodeCount": len(self.AllowedNodes),
            "AllowedEdgeCount": len(self.AllowedEdges),
            "DeclaredPortalIngressNodes": [
                list(Value)
                for Value in sorted(self.DeclaredPortalIngressNodes)
            ],
            "Complete": self.Complete,
            "RegionFingerprint": self.RegionFingerprint,
            "ResourceGraphFingerprint": self.ResourceGraphFingerprint,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "GuideIdentityFingerprint": self.GuideIdentityFingerprint,
            "SignalBindingFingerprint": self.SignalBindingFingerprint,
            "FabricFingerprint": self.FabricFingerprint,
        }


@dataclass(frozen=True)
class PhysicalComponentSelectedLocalPortSupport:
    """Identity-only reference to local support for one boundary reservation."""

    Signal: str
    BoundaryReservationFingerprint: str
    LocalContractFingerprint: str
    LocalAccessFingerprint: str
    SupportFingerprint: str

    def __post_init__(self) -> None:
        RequiredValues = (
            self.Signal,
            self.BoundaryReservationFingerprint,
            self.LocalContractFingerprint,
            self.LocalAccessFingerprint,
            self.SupportFingerprint,
        )
        if any(not Value for Value in RequiredValues):
            raise ValueError(
                "selected local port support identities must be nonempty"
            )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "BoundaryReservationFingerprint": (
                self.BoundaryReservationFingerprint
            ),
            "LocalContractFingerprint": self.LocalContractFingerprint,
            "LocalAccessFingerprint": self.LocalAccessFingerprint,
            "SupportFingerprint": self.SupportFingerprint,
        }


@dataclass(frozen=True)
class PhysicalComponentChannelReservation:
    """One selected exact global route owned by an assembly plan."""

    Signal: str
    Layer: int
    GuideCells: tuple[tuple[int, int], ...]
    ResourceIds: tuple[str, ...]
    Claims: Any
    Capacity: int = 1
    FeedthroughComponentIds: tuple[int, ...] = ()
    ReservationFingerprint: str = ""
    # A guide is only a search preference.  Once authoritative detailed
    # routing has selected a candidate, these nodes and identities are the
    # immutable physical channel contract consumed by local compilation.
    ReservedPathNodes: tuple[Position3, ...] = ()
    RouteCandidateId: str = ""
    RouteCandidateFingerprint: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "Layer": self.Layer,
            "GuideCells": [list(Value) for Value in self.GuideCells],
            "ResourceIds": list(self.ResourceIds),
            "ClaimCount": len(getattr(
                self.Claims,
                "ResourceIds",
                (),
            )),
            "Capacity": self.Capacity,
            "FeedthroughComponentIds": list(
                self.FeedthroughComponentIds
            ),
            "ReservationFingerprint": self.ReservationFingerprint,
            "ReservedPathNodes": [
                list(Value) for Value in self.ReservedPathNodes
            ],
            "RouteCandidateId": self.RouteCandidateId,
            "RouteCandidateFingerprint": (
                self.RouteCandidateFingerprint
            ),
        }


@dataclass(frozen=True)
class PhysicalComponentAssemblyPlan:
    """Complete physical port, channel, and ownership contract."""

    PlanFingerprint: str
    PortAssignmentFingerprint: str
    PlacementFingerprint: str
    ComponentGraphFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    InterfaceFingerprint: str
    ComponentId: int | None
    EnvelopeMinimum: Position3
    EnvelopeMaximum: Position3
    KeepoutClaims: Any
    Ports: tuple[PhysicalComponentPortReservation, ...]
    Channels: tuple[PhysicalComponentChannelReservation, ...]
    # Non-exclusive guide/capacity domains used by global planning.  Unlike
    # Channels, these have no selected candidate and confer no resource
    # ownership on the local compiler.
    Corridors: tuple[PhysicalComponentChannelReservation, ...] = ()
    # One immutable, layer-exact obstacle domain shared by coarse guide
    # planning, detailed candidate generation, and assembly validation.  These
    # are routable wire nodes whose claims conflict with the closed component;
    # consumers must not inflate them into an all-layer X/Z prism.
    GlobalKeepoutNodes: tuple[Position3, ...] = ()
    GlobalKeepoutFingerprint: str = ""
    Feedthroughs: tuple[ComponentFeedthroughContract, ...] = ()
    AssemblyChoiceFingerprint: str = ""
    StageOrder: tuple[str, ...] = (
        "PhysicalBoundaryPlanning",
        "AuthoritativeGlobalReserve",
        "LocalSupportBinding",
        "ClosedComponentCompilation",
        "AuthoritativeDetailedRouting",
    )
    Complete: bool = True
    AccessCertificateFingerprint: str = ""
    LocalAccessDomainFingerprint: str = ""
    # Exact exterior-routing identities carried across preparation, global
    # reservation, and the local-compilation handoff.  Empty values preserve
    # compatibility for fixtures created before this additive contract.
    ExteriorFabricSetFingerprint: str = ""
    ExteriorRegionFingerprint: str = ""
    ExteriorCapacityLedgerFingerprint: str = ""
    ExteriorFabrics: tuple[PhysicalExteriorApertureFabric, ...] = ()
    # Authoritative port-first representation.  ``Ports`` remains available
    # while transitional planner and local-compiler callers are migrated.
    GlobalBoundaryPorts: tuple[
        PhysicalComponentBoundaryPortReservation, ...
    ] = ()
    SelectedLocalPortSupports: tuple[
        PhysicalComponentSelectedLocalPortSupport, ...
    ] = ()

    @property
    def DeclaredFeedthroughSignals(self) -> frozenset[str]:
        return frozenset(
            Value.Signal for Value in self.Feedthroughs
        )

    @property
    def PlanningChannels(
        self,
    ) -> tuple[PhysicalComponentChannelReservation, ...]:
        """Return exact channels plus unbound corridor domains by signal."""
        ExactSignals = frozenset(Value.Signal for Value in self.Channels)
        return tuple((*self.Channels, *(
            Value for Value in self.Corridors
            if Value.Signal not in ExactSignals
        )))

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "physical-component-assembly-plan-v1",
            "PlanFingerprint": self.PlanFingerprint,
            "PortAssignmentFingerprint": (
                self.PortAssignmentFingerprint
            ),
            "PlacementFingerprint": self.PlacementFingerprint,
            "ComponentGraphFingerprint": (
                self.ComponentGraphFingerprint
            ),
            "ResourceGraphFingerprint": (
                self.ResourceGraphFingerprint
            ),
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "InterfaceFingerprint": self.InterfaceFingerprint,
            "ComponentId": self.ComponentId,
            "EnvelopeMinimum": list(self.EnvelopeMinimum),
            "EnvelopeMaximum": list(self.EnvelopeMaximum),
            "Ports": [Value.ToDictionary() for Value in self.Ports],
            "GlobalBoundaryPorts": [
                Value.ToDictionary() for Value in self.GlobalBoundaryPorts
            ],
            "SelectedLocalPortSupports": [
                Value.ToDictionary()
                for Value in self.SelectedLocalPortSupports
            ],
            "Channels": [
                Value.ToDictionary() for Value in self.Channels
            ],
            "Corridors": [
                Value.ToDictionary() for Value in self.Corridors
            ],
            "GlobalKeepoutNodes": [
                list(Value) for Value in self.GlobalKeepoutNodes
            ],
            "GlobalKeepoutFingerprint": (
                self.GlobalKeepoutFingerprint
            ),
            "Feedthroughs": [
                Value.ToDictionary() for Value in self.Feedthroughs
            ],
            "AssemblyChoiceFingerprint": self.AssemblyChoiceFingerprint,
            "AccessCertificateFingerprint": (
                self.AccessCertificateFingerprint
            ),
            "LocalAccessDomainFingerprint": (
                self.LocalAccessDomainFingerprint
            ),
            "ExteriorFabricSetFingerprint": (
                self.ExteriorFabricSetFingerprint
            ),
            "ExteriorRegionFingerprint": self.ExteriorRegionFingerprint,
            "ExteriorCapacityLedgerFingerprint": (
                self.ExteriorCapacityLedgerFingerprint
            ),
            "ExteriorFabrics": [
                Value.ToDictionary() for Value in self.ExteriorFabrics
            ],
            "StageOrder": list(self.StageOrder),
            "Complete": self.Complete,
            "ImplicitForeignTransitDomainCount": 0,
        }


@dataclass(frozen=True)
class ClosedComponentInterface:
    """Complete ownership boundary consumed by the local component solver."""

    InterfaceFingerprint: str
    ComponentId: int | None
    OwnedSignals: tuple[str, ...]
    Ports: tuple[ComponentInterfacePort, ...]
    Feedthroughs: tuple[ComponentFeedthroughContract, ...] = ()
    PhysicalPortReservations: tuple[
        PhysicalComponentPortReservation, ...
    ] = ()
    PhysicalAssemblyPlanFingerprint: str = ""
    Complete: bool = True

    @property
    def DeclaredFeedthroughSignals(self) -> frozenset[str]:
        return frozenset(
            Value.Signal for Value in self.Feedthroughs
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "closed-component-interface-v1",
            "InterfaceFingerprint": self.InterfaceFingerprint,
            "ComponentId": self.ComponentId,
            "OwnedSignals": list(self.OwnedSignals),
            "Ports": [Port.ToDictionary() for Port in self.Ports],
            "Feedthroughs": [
                Value.ToDictionary() for Value in self.Feedthroughs
            ],
            "PhysicalPortReservations": [
                Value.ToDictionary()
                for Value in self.PhysicalPortReservations
            ],
            "PhysicalAssemblyPlanFingerprint": (
                self.PhysicalAssemblyPlanFingerprint
            ),
            "Complete": self.Complete,
            "ImplicitForeignTransitDomainCount": 0,
        }


@dataclass(frozen=True)
class ComponentRoutingProblem:
    """Complete finite routing problem for one bounded dense component."""

    ProblemFingerprint: str
    PlacementFingerprint: str
    LocalTemplateFingerprint: str
    SelectedClusters: tuple[int, ...]
    ComponentSignals: tuple[str, ...]
    LocalClaims: tuple[Any, ...]
    Fabric: ComponentRoutingFabric
    OwnedTerminalDomains: tuple[ComponentTerminalAccessDomain, ...]
    ExternalContinuationTerminals: tuple[
        tuple[str, Position3, str], ...
    ]
    ForeignEscapeDomains: tuple[ComponentTerminalAccessDomain, ...]
    MaximumPowerDistance: int
    DomainComplete: bool
    ResourceGraph: Any = field(
        default=None,
        compare=False,
        repr=False,
    )
    MaximumWork: int = 250_000
    ImmutableClaims: tuple[Any, ...] = ()
    ExternalContinuationDomains: tuple[
        ComponentTerminalAccessDomain, ...
    ] = ()
    ForeignTransitDomains: tuple[
        ComponentForeignTransitDomain, ...
    ] = ()
    Interface: ClosedComponentInterface | None = None
    PhysicalAssemblyPlan: PhysicalComponentAssemblyPlan | None = None
    ReservedGlobalClaimsBySignal: tuple[
        tuple[str, Any], ...
    ] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ProblemFingerprint": self.ProblemFingerprint,
            "PlacementFingerprint": self.PlacementFingerprint,
            "LocalTemplateFingerprint": self.LocalTemplateFingerprint,
            "SelectedClusterCount": len(self.SelectedClusters),
            "ComponentSignalCount": len(self.ComponentSignals),
            "ComponentSignals": list(self.ComponentSignals),
            "LocalClaimCount": len(self.LocalClaims),
            "ImmutableClaimCount": len(self.ImmutableClaims),
            "Fabric": self.Fabric.ToDictionary(),
            "OwnedTerminalDomains": [
                Value.ToDictionary()
                for Value in self.OwnedTerminalDomains
            ],
            "ExternalContinuationCount": len(
                self.ExternalContinuationTerminals
            ),
            "ExternalContinuationDomainCount": len(
                self.ExternalContinuationDomains
            ),
            "ExternalContinuationDomains": [
                Value.ToDictionary()
                for Value in self.ExternalContinuationDomains
            ],
            "ForeignEscapeDomains": [
                Value.ToDictionary()
                for Value in self.ForeignEscapeDomains
            ],
            "ForeignTransitDomains": [
                Value.ToDictionary()
                for Value in self.ForeignTransitDomains
            ],
            "Interface": (
                self.Interface.ToDictionary()
                if self.Interface is not None
                else None
            ),
            "PhysicalAssemblyPlan": (
                self.PhysicalAssemblyPlan.ToDictionary()
                if self.PhysicalAssemblyPlan is not None
                else None
            ),
            "ReservedGlobalClaimCount": sum(
                len(getattr(Claims, "ResourceIds", ()))
                for _Signal, Claims
                in self.ReservedGlobalClaimsBySignal
            ),
            "MaximumPowerDistance": self.MaximumPowerDistance,
            "DomainComplete": self.DomainComplete,
            "MaximumWork": self.MaximumWork,
        }


@dataclass(frozen=True)
class RoutedComponentNet:
    """One complete signal tree inside a routed component."""

    Signal: str
    Root: Position3
    Nodes: frozenset[Position3]
    Edges: frozenset[tuple[Position3, Position3]]
    WireCells: frozenset[Position3]
    SupportCells: frozenset[Position3]
    Repeaters: tuple[tuple[Position3, str], ...]
    Claims: RoutingResourceClaims
    CoveredTerminals: tuple[Position3, ...]
    ExportedPorts: tuple[Position3, ...]
    NetFingerprint: str


@dataclass(frozen=True)
class RoutedComponentTemplate:
    """Validated immutable physical fragment handed to global routing."""

    ProblemFingerprint: str
    PlacementFingerprint: str
    LocalTemplateFingerprint: str
    FabricFingerprint: str
    RoutedTemplateFingerprint: str
    Nets: tuple[RoutedComponentNet, ...]
    ForeignEscapeReservations: tuple[
        tuple[str, Position3, ComponentTerminalAccessCandidate], ...
    ]
    ExportedPorts: tuple[tuple[str, Position3], ...]
    Claims: RoutingResourceClaims
    ExportedPortFingerprint: str
    ClaimsFingerprint: str
    ProofFingerprint: str
    ExpansionCount: int
    Diagnostics: dict[str, object] = field(default_factory=dict)
    ExternalContinuationReservations: tuple[
        tuple[str, Position3, ComponentTerminalAccessCandidate], ...
    ] = ()
    ForeignTransitReservations: tuple[RoutedComponentNet, ...] = ()
    InterfaceFingerprint: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ProblemFingerprint": self.ProblemFingerprint,
            "PlacementFingerprint": self.PlacementFingerprint,
            "LocalTemplateFingerprint": self.LocalTemplateFingerprint,
            "FabricFingerprint": self.FabricFingerprint,
            "RoutedTemplateFingerprint": self.RoutedTemplateFingerprint,
            "NetCount": len(self.Nets),
            "NetFingerprints": [
                Value.NetFingerprint for Value in self.Nets
            ],
            "ForeignEscapeReservationCount": len(
                self.ForeignEscapeReservations
            ),
            "ForeignEscapeReservations": [
                {
                    "Signal": Signal,
                    "Terminal": list(Terminal),
                    "Port": list(Candidate.Attachment),
                    "PathLength": len(Candidate.Path),
                    "CandidateFingerprint": (
                        Candidate.CandidateFingerprint
                    ),
                }
                for Signal, Terminal, Candidate
                in self.ForeignEscapeReservations
            ],
            "ExternalContinuationReservations": [
                {
                    "Signal": Signal,
                    "Terminal": list(Terminal),
                    "Port": list(Candidate.Attachment),
                    "PathLength": len(Candidate.Path),
                    "CandidateFingerprint": (
                        Candidate.CandidateFingerprint
                    ),
                }
                for Signal, Terminal, Candidate
                in self.ExternalContinuationReservations
            ],
            "ForeignTransitReservations": [
                {
                    "NetFingerprint": Value.NetFingerprint,
                    "NodeCount": len(Value.Nodes),
                    "CoveredPorts": [
                        list(Position)
                        for Position in Value.CoveredTerminals
                    ],
                }
                for Value in self.ForeignTransitReservations
            ],
            "ExportedPorts": [
                {"Signal": Signal, "Position": list(Position)}
                for Signal, Position in self.ExportedPorts
            ],
            "ExportedPortFingerprint": self.ExportedPortFingerprint,
            "ClaimsFingerprint": self.ClaimsFingerprint,
            "ProofFingerprint": self.ProofFingerprint,
            "ExpansionCount": self.ExpansionCount,
            "InterfaceFingerprint": self.InterfaceFingerprint,
            "Diagnostics": self.Diagnostics,
        }


@dataclass(frozen=True)
class ComponentRoutingSolveResult:
    """Typed feasible, exhaustive, or incomplete component solve result."""

    Status: str
    Template: RoutedComponentTemplate | None = None
    ProofFingerprint: str = ""
    ExpansionCount: int = 0
    Detail: str = ""
    Diagnostics: dict[str, object] = field(default_factory=dict)

    @property
    def Feasible(self) -> bool:
        return self.Status == "feasible" and self.Template is not None

    @property
    def Exhaustive(self) -> bool:
        return self.Status == "architectural-unsatisfiable"


class ComponentRoutingProblemPrepared(RuntimeError):
    """Internal control transfer after complete component domains exist."""

    def __init__(self, Problem: ComponentRoutingProblem) -> None:
        super().__init__("component routing problem prepared")
        self.Problem = Problem


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
    # Reuse the exact immutable placement geometry during the one physical
    # truth-table simulation. Rebuilding routing resources after the routed
    # design has already passed authoritative validation duplicates a costly
    # geometry proof inside the same absolute deadline.
    SimulationActualBlocks: frozenset[Position3] = frozenset()
    SimulationElectricalBlocks: frozenset[Position3] = frozenset()
    SimulationSolidBlocks: frozenset[Position3] = frozenset()
    PhysicalDeliveryMap: dict[str, frozenset[tuple[str, int]]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class RoutingStaticGeometry:
    ActualBlocks: frozenset[Position3]
    ElectricalBlocks: frozenset[Position3]
    SolidBlocks: frozenset[Position3] = frozenset()
    TemplateElectricalBlocks: frozenset[Position3] = frozenset()
    # An opaque block directly above a redstone torch is strongly powered in
    # Java Edition.  It cannot be silently created as support for unrelated
    # routed dust.
    TorchPoweredSupportBlocks: frozenset[Position3] = frozenset()


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
    # A one-shot pre-route compact selection materializes an exact finite
    # route-candidate domain before it selects one member.  The selected
    # member must hand those *same* candidate objects to detailed routing;
    # regenerating a smaller request window would silently replace the proof
    # with a different domain.  Entries are keyed by the domain fingerprint
    # and retain both candidates and their deterministic lane metadata.
    FrozenRawTrackAssignmentCandidateCaches: dict[str, Any] = field(
        default_factory=dict,
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

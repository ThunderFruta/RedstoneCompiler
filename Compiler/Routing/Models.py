"""Shared data contracts for physical routing stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ChannelPlanner import ChannelPlan, RoutingStageMetrics
from .Technology import DefaultRedstoneRoutingTechnology
from .TrackAssignment import TrackAssignment
from .ResourceGraph import (
    PinAccessSelection,
    RoutingAssignment,
    RoutingResourceClaims,
)

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
class ClusterInterfaceStateProof:
    """Terminal proof for one retained coherent placement state."""

    PlacementStateFingerprint: str
    Status: str
    ChannelFingerprint: str = ""
    TransformFingerprint: str = ""
    OwnershipUnsatCoreFingerprint: str = ""
    OwnershipUnsatSignals: tuple[str, ...] = ()
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
            self.Status,
            self.ChannelFingerprint,
            self.TransformFingerprint,
            self.OwnershipUnsatCoreFingerprint,
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
            "Status": self.Status,
            "ChannelFingerprint": self.ChannelFingerprint,
            "TransformFingerprint": self.TransformFingerprint,
            "OwnershipUnsatCoreFingerprint": (
                self.OwnershipUnsatCoreFingerprint
            ),
            "OwnershipUnsatSignals": list(
                self.OwnershipUnsatSignals
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
            "AccessCertificateFingerprint": (
                self.AccessCertificateFingerprint
            ),
            "LocalAccessDomainFingerprint": (
                self.LocalAccessDomainFingerprint
            ),
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


@dataclass(frozen=True)
class RoutingStaticGeometry:
    ActualBlocks: frozenset[Position3]
    ElectricalBlocks: frozenset[Position3]
    SolidBlocks: frozenset[Position3] = frozenset()
    TemplateElectricalBlocks: frozenset[Position3] = frozenset()


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
    UnsupportedPairs: frozenset[tuple[str, str]]
    ProofFingerprints: tuple[str, ...]
    Complete: bool


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
    # Complete global-only candidate reservations.  This additive field lets
    # assembly planning publish its authoritative boundary choices without
    # coupling them back to component-local access witnesses.
    BoundaryPortReservationsBySignal: tuple[
        tuple[
            str,
            tuple[PhysicalComponentBoundaryPortReservation, ...],
        ], ...
    ] = ()


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

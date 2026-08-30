"""Closed-component routing contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .Core import Position3
from ..ResourceGraph import RoutingResourceClaims

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
    PortalIngressEnvelopeBounds: tuple[
        tuple[Position3, Position3, Position3], ...
    ]
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
            Minimum[Index] > Maximum[Index]
            for _Ingress, Minimum, Maximum
            in self.PortalIngressEnvelopeBounds
            for Index in range(3)
        ):
            raise ValueError("portal ingress envelope is inverted")
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
            "SchemaVersion": "physical-exterior-aperture-fabric-v3",
            "EnvelopeMinimum": list(self.EnvelopeMinimum),
            "EnvelopeMaximum": list(self.EnvelopeMaximum),
            "PortalIngressEnvelopeBounds": [
                [list(Ingress), list(Minimum), list(Maximum)]
                for Ingress, Minimum, Maximum
                in self.PortalIngressEnvelopeBounds
            ],
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
    RepeaterInputFacings: tuple[tuple[Position3, str], ...]
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

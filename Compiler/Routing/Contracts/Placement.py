"""Placement and cluster-interface routing contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .Core import Position3
from ..ResourceGraph import RoutingResourceClaims, RoutingResourceId

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
        }


@dataclass(frozen=True)
class PlacementAccessTerminalDomain:
    """Complete finite escape domain for one placed signal terminal."""

    Signal: str
    Terminal: Position3
    EscapeStubs: tuple[PlacementAccessEscapeStub, ...]
    Complete: bool
    IncompleteReason: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "Terminal": list(self.Terminal),
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
    # Bounded legal escape construction is pre-route proof work.  These
    # counters make an incomplete factor auditable without treating its cap
    # as a topology or routing retry.
    LegalEscapeExpansionCount: int = 0
    LegalEscapeExpansionLimit: int | None = None
    LegalEscapeWorkLimitKind: str = ""
    LegalEscapeDirectionStateUpperBound: int | None = None
    IncompleteReason: str = ""
    # Phase-one straight access is a catalog-derived immutable witness. It is
    # execution-neutral metadata here: the existing fabric fingerprint and
    # selected physical geometry remain unchanged for parity.
    PinAccessWitness: Any = field(default=None, compare=False, repr=False)
    FixedPinAccessSolve: Any = field(default=None, compare=False, repr=False)
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
            "LegalEscapeExpansionCount": self.LegalEscapeExpansionCount,
            "LegalEscapeExpansionLimit": self.LegalEscapeExpansionLimit,
            "LegalEscapeWorkLimitKind": self.LegalEscapeWorkLimitKind,
            "LegalEscapeDirectionStateUpperBound": (
                self.LegalEscapeDirectionStateUpperBound
            ),
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "PinAccessWitness": (
                self.PinAccessWitness.ToDictionary()
                if self.PinAccessWitness is not None
                else None
            ),
            "FixedPinAccessSolve": (
                self.FixedPinAccessSolve.ToDictionary()
                if self.FixedPinAccessSolve is not None
                else None
            ),
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

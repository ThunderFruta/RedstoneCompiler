"""Shared data contracts for physical routing stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ChannelPlanner import ChannelPlan, RoutingStageMetrics
from .Technology import DefaultRedstoneRoutingTechnology
from .TrackAssignment import TrackAssignment
from .ResourceGraph import PinAccessSelection, RoutingAssignment

Position3 = tuple[int, int, int]


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


@dataclass
class RoutingResources:
    StaticGeometry: RoutingStaticGeometry
    ResourceGraph: Any = None
    RustContexts: dict[tuple[int, int, int, int, int], Any] = field(
        default_factory=dict
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

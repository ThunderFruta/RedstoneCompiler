"""Small public orchestrator for final PCB placement commit."""

from __future__ import annotations

from typing import Any, Callable
from App.Telemetry import TelemetryWork
from Compiler.Routing.Policy import ClusteringPolicy, NandPackingPolicy, PlacementPolicy
from Compiler.Routing.Failures import RoutingAssignmentCut
from .Clusters import PcbPlacement
from .Constraints import PlacementAssignmentConstraintSet
from .CommitState import PlacementCommitState
from .CommitPreparation import (
    InitializePlacementCommit,
    BuildClusterPlacementLayouts,
    PrepareExactPlacementSearch,
    EvaluateExactPlacementStates,
    PrepareTerminalPlacement,
    CommitTerminalPlacement,
    BuildCommittedPlacedDesign,
)
from .CommitRouting import (
    RouteCommittedClusterTemplates,
    FinalizePlacementCommit,
)


def PlacePcbGraph(Netlist: Any, RoutingSpacing: int=0, PlacementPolicy: PlacementPolicy | None=None, PackingPolicy: NandPackingPolicy | None=None, ClusterPolicy: ClusteringPolicy | None=None, MaximumBoundaryTerminals: int | None=None, MaximumEntrancesPerSignal: int | None=None, RelocationSignals: frozenset[str]=frozenset(), RelocationPrioritySignals: frozenset[str]=frozenset(), RequiredRelocationSignals: frozenset[str]=frozenset(), RelocationVariant: int=0, JointPlacementCandidateIndex: int=0, AssignmentCut: RoutingAssignmentCut | None=None, AssignmentConstraints: PlacementAssignmentConstraintSet=PlacementAssignmentConstraintSet(), CoordinatedCandidateDiversificationSignals: frozenset[str]=frozenset(), EnableClusterLocalRouteReuse: bool=False, EnableClusterBoundaryLeases: bool=False, EnableClusterInterfacePlacementFeasibility: bool=False, CutDrivenClusterRefinementSignals: frozenset[str] | None=None, FixedConnectivityClusters: tuple[tuple[str, ...], ...]=(), EnableInternalPinBankGeometryRepair: bool=False, InternalPinBankGeometryRepairSignals: frozenset[str]=frozenset(), FocusedCutEpochPlacement: bool=False, TopologyCutFrontier: tuple[RoutingAssignmentCut, ...]=(), MandatoryAccessPreScreenOnly: bool=False, PlacementScoringOnly: bool=False, PreferAccessRingTerminals: bool=False, UseDerivedPerimeterTerminals: bool=False, DerivedTerminalLayoutVariantIndex: int=0, WorkCheck: Callable[[dict[str, object]], None] | None=None) -> PcbPlacement:
    """Place a NAND graph through explicit bounded commit phases."""
    Context = PlacementCommitState(Netlist=Netlist, RoutingSpacing=RoutingSpacing, PlacementPolicy=PlacementPolicy, PackingPolicy=PackingPolicy, ClusterPolicy=ClusterPolicy, MaximumBoundaryTerminals=MaximumBoundaryTerminals, MaximumEntrancesPerSignal=MaximumEntrancesPerSignal, RelocationSignals=RelocationSignals, RelocationPrioritySignals=RelocationPrioritySignals, RequiredRelocationSignals=RequiredRelocationSignals, RelocationVariant=RelocationVariant, JointPlacementCandidateIndex=JointPlacementCandidateIndex, AssignmentCut=AssignmentCut, AssignmentConstraints=AssignmentConstraints, CoordinatedCandidateDiversificationSignals=CoordinatedCandidateDiversificationSignals, EnableClusterLocalRouteReuse=EnableClusterLocalRouteReuse, EnableClusterBoundaryLeases=EnableClusterBoundaryLeases, EnableClusterInterfacePlacementFeasibility=EnableClusterInterfacePlacementFeasibility, CutDrivenClusterRefinementSignals=CutDrivenClusterRefinementSignals, FixedConnectivityClusters=FixedConnectivityClusters, EnableInternalPinBankGeometryRepair=EnableInternalPinBankGeometryRepair, InternalPinBankGeometryRepairSignals=InternalPinBankGeometryRepairSignals, FocusedCutEpochPlacement=FocusedCutEpochPlacement, TopologyCutFrontier=TopologyCutFrontier, MandatoryAccessPreScreenOnly=MandatoryAccessPreScreenOnly, PlacementScoringOnly=PlacementScoringOnly, PreferAccessRingTerminals=PreferAccessRingTerminals, UseDerivedPerimeterTerminals=UseDerivedPerimeterTerminals, DerivedTerminalLayoutVariantIndex=DerivedTerminalLayoutVariantIndex, WorkCheck=WorkCheck)
    with TelemetryWork("placement topology"):
        InitializePlacementCommit(Context)
    with TelemetryWork("cluster layouts"):
        BuildClusterPlacementLayouts(Context)
    with TelemetryWork("prepare placement search"):
        PrepareExactPlacementSearch(Context)
    with TelemetryWork("evaluate placement states"):
        EvaluateExactPlacementStates(Context)
    with TelemetryWork("prepare terminals"):
        PrepareTerminalPlacement(Context)
    with TelemetryWork("commit terminals"):
        Completed = CommitTerminalPlacement(Context)
    if Completed is not None:
        return Completed
    with TelemetryWork("materialize placed cells"):
        BuildCommittedPlacedDesign(Context)
    with TelemetryWork("route local cluster templates"):
        RouteCommittedClusterTemplates(Context)
    with TelemetryWork("finalize placement"):
        return FinalizePlacementCommit(Context)

"""Explicit mutable state for one placement commit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PlacementCommitState:
    """State shared only by the bounded phases of one placement."""

    Netlist: Any
    RoutingSpacing: Any
    PlacementPolicy: Any
    PackingPolicy: Any
    ClusterPolicy: Any
    MaximumBoundaryTerminals: Any
    MaximumEntrancesPerSignal: Any
    RelocationSignals: Any
    RelocationPrioritySignals: Any
    RequiredRelocationSignals: Any
    RelocationVariant: Any
    JointPlacementCandidateIndex: Any
    AssignmentCut: Any
    AssignmentConstraints: Any
    CoordinatedCandidateDiversificationSignals: Any
    EnableClusterLocalRouteReuse: Any
    EnableClusterBoundaryLeases: Any
    EnableClusterInterfacePlacementFeasibility: Any
    CutDrivenClusterRefinementSignals: Any
    FixedConnectivityClusters: Any
    EnableInternalPinBankGeometryRepair: Any
    InternalPinBankGeometryRepairSignals: Any
    FocusedCutEpochPlacement: Any
    TopologyCutFrontier: Any
    MandatoryAccessPreScreenOnly: Any
    PlacementScoringOnly: Any
    PreferAccessRingTerminals: Any
    UseDerivedPerimeterTerminals: Any
    DerivedTerminalLayoutVariantIndex: Any
    WorkCheck: Any


def SetPlacementCommitState(
    State: PlacementCommitState,
    Name: str,
    Value: Any,
) -> Any:
    """Assign and return a value used by a stateful expression."""
    setattr(State, Name, Value)
    return Value

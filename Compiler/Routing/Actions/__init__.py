"""Small, reusable routing-stage actions."""

from .ConflictRepair import AnalyzeFlatRouteConflicts, FindFlatRouteConflicts
from .Geometry import (
    AreConnected,
    BuildElectricalExclusions,
    BuildPlacedCellGeometry,
    BuildRoutingResources,
    ForkRoutingResourcesWithSharedStaticGeometry,
    LoadRoutingTemplates,
    NeighborPositions,
    ValidatePlacedCellElectricalIsolation,
)
from .Repeaters import (
    EstimateRouteMaterialCost,
    MaterializeReservedRepeaters,
    PruneRedundantRepeaterReservations,
    PruneUnneededMaterializedRepeaters,
    PropagateRoutePower,
    RepeaterFacing,
)
from .Validation import (
    BuildPhysicalGraphs,
    SimplifyNetTrees,
    ValidatePhysicalRoutes,
    ValidateTemplateIsolation,
)

__all__ = [
    "AnalyzeFlatRouteConflicts",
    "AreConnected",
    "BuildElectricalExclusions",
    "BuildPhysicalGraphs",
    "BuildPlacedCellGeometry",
    "MaterializeReservedRepeaters",
    "PruneRedundantRepeaterReservations",
    "PruneUnneededMaterializedRepeaters",
    "PropagateRoutePower",
    "BuildRoutingResources",
    "ForkRoutingResourcesWithSharedStaticGeometry",
    "EstimateRouteMaterialCost",
    "FindFlatRouteConflicts",
    "LoadRoutingTemplates",
    "NeighborPositions",
    "ValidatePlacedCellElectricalIsolation",
    "RepeaterFacing",
    "SimplifyNetTrees",
    "ValidatePhysicalRoutes",
    "ValidateTemplateIsolation",
]

"""Small public orchestrator for placement and routing."""

from __future__ import annotations

from time import monotonic
from typing import Any, Callable
from PhysicalDesign.Routing.Pcb import RoutePcbDesign
from PhysicalDesign.Contracts.Results import RoutedDesign
from PhysicalDesign.Routing.Planning.LocalFirst import BuildLocalFirstSnapshot, MeasurePlacementRoutingFeedback
from PhysicalDesign.Redstone.Actions import ValidatePlacedCellElectricalIsolation
from PhysicalDesign.Redstone.Actions.Geometry import BuildRoutingResources
from PhysicalDesign.Policy import DefaultPhysicalDesignPolicy, PhysicalDesignPolicy
from PhysicalDesign.Policy import ExecutionStrategyForRequest, PolicyForRoutingStrategy, RoutingStrategy
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology, RedstoneRoutingTechnology
from Compiler.Synthesis.Validation import ValidateNandOnlyDesign
from PhysicalDesign.Placement.Core.Commit.Commit import PlacePcbGraph
from .Candidates import ApplyRoutingRuntimeBudget
from .Results import (
    MeasurePcbDesign,
    PcbProgress,
    PcbResult,
    PublishPlacementFlowResult,
)
from .State import PlacementFlowServices, PlacementFlowState
from .Setup import (
    InitializePlacementFlow,
    GeneratePlacementCandidates,
    PreparePlacementRouting,
)
from .PhysicalFlow import (
    RunPhysicalComponentFlow,
)
from .CandidateRouting import (
    RoutePlacementCandidates,
)


def PlaceAndRoutePcb(Netlist: Any, ProgressCallback: Callable[[PcbProgress], None] | None=None, Policy: PhysicalDesignPolicy=DefaultPhysicalDesignPolicy, Technology: RedstoneRoutingTechnology=DefaultRedstoneRoutingTechnology, Strategy: RoutingStrategy | str | None=None, RoutedValidationCallback: Callable[[RoutedDesign], None] | None=None, RoutingDeadlineSeconds: float | None=None, StageCallback: Callable[[str], None] | None=None) -> PcbResult:
    """Run the explicitly selected router without an automatic fallback."""
    RequestedStrategy = RoutingStrategy.Parse(Strategy or RoutingStrategy.Default)
    UsedStrategy = ExecutionStrategyForRequest(RequestedStrategy)
    ActivePolicy = Policy if Policy != DefaultPhysicalDesignPolicy and UsedStrategy == RoutingStrategy.Default else PolicyForRoutingStrategy(UsedStrategy)
    ActivePolicy = ApplyRoutingRuntimeBudget(ActivePolicy, RoutingDeadlineSeconds)
    return _PlaceAndRoutePcbWithPolicy(Netlist, ProgressCallback=ProgressCallback, Policy=ActivePolicy, Technology=Technology, RequestedStrategy=RequestedStrategy, UsedStrategy=UsedStrategy, RoutedValidationCallback=RoutedValidationCallback, StageCallback=StageCallback)


def _PlaceAndRoutePcbWithPolicy(Netlist: Any, ProgressCallback: Callable[[PcbProgress], None] | None, Policy: PhysicalDesignPolicy, Technology: RedstoneRoutingTechnology, RequestedStrategy: RoutingStrategy, UsedStrategy: RoutingStrategy, RoutedValidationCallback: Callable[[RoutedDesign], None] | None=None, Services: PlacementFlowServices | None=None, StageCallback: Callable[[str], None] | None=None) -> PcbResult:
    """Execute placement and routing through explicit typed phases."""
    ActiveServices = Services or PlacementFlowServices(BuildLocalFirstSnapshot=BuildLocalFirstSnapshot, BuildRoutingResources=BuildRoutingResources, MeasurePcbDesign=MeasurePcbDesign, MeasurePlacementRoutingFeedback=MeasurePlacementRoutingFeedback, PlacePcbGraph=PlacePcbGraph, RoutePcbDesign=RoutePcbDesign, ValidateNandOnlyDesign=ValidateNandOnlyDesign, ValidatePlacedCellElectricalIsolation=ValidatePlacedCellElectricalIsolation, monotonic=monotonic)
    Context = PlacementFlowState(Netlist=Netlist, ProgressCallback=ProgressCallback, Policy=Policy, Technology=Technology, RequestedStrategy=RequestedStrategy, UsedStrategy=UsedStrategy, RoutedValidationCallback=RoutedValidationCallback, Services=ActiveServices)
    if StageCallback is not None:
        StageCallback("initial placement")
    Completed = InitializePlacementFlow(Context)
    if Completed is not None:
        return Completed
    if StageCallback is not None:
        StageCallback("placement candidate generation")
    GeneratePlacementCandidates(Context)
    if StageCallback is not None:
        StageCallback("routing resource preparation")
    PreparePlacementRouting(Context)
    if StageCallback is not None:
        StageCallback("physical component interface planning")
    RunPhysicalComponentFlow(Context)
    if StageCallback is not None:
        StageCallback("placement candidate routing")
    RoutePlacementCandidates(Context)
    if StageCallback is not None:
        StageCallback("routing result publication")
    return PublishPlacementFlowResult(Context)

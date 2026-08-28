"""Authoritative multilayer routing for PCB-style NAND placement."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from heapq import heappop, heappush
import os
from time import monotonic
import traceback
from typing import Any, Callable, Iterable

from Compiler.Placement.Core.Clusters import PcbPlacement
from ..Placement.Geometry import GetGateInputAccess
from ..Placement.Rotation import RotatedCellSize
try:
    from ..RustRouting import GetRoutingThreadCount
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import GetRoutingThreadCount
    except Exception:

        def GetRoutingThreadCount() -> int:
            return 1
from .Actions import (
    BuildPhysicalGraphs,
    BuildRoutingResources,
    FindFlatRouteConflicts,
    MaterializeReservedRepeaters,
    ValidatePhysicalRoutes,
    ValidateTemplateIsolation,
)
from .ChannelPlanner import MeasureRoutingStage
from .Contracts.Placement import (
    AccessContractBounds,
    ClusterInterfaceAssignment,
    ClusterInterfaceAssignmentPrepared,
    DetailedRoutingBounds,
    TrackAssignmentPreparation,
    TrackAssignmentPrepared,
    ClusterInterfaceRealizabilityNogood,
)
from .Contracts.Component import (
    ComponentRoutingProblem,
    ComponentRoutingProblemPrepared,
)
from .Contracts.PhysicalInterface import (
    PhysicalComponentAssemblyPrepared,
    PreparedPhysicalComponentPortFactorDomain,
    PreparedPhysicalComponentAssembly,
)
from .Contracts.Results import RoutedDesign
from .Reliability import RoutingDeadline
from .Failures import RoutingStageError
from .Failures import RoutingFailure, RoutingFailureReason
from .ResourceGraph import (
    FindClaimConflicts,
    BuildRoutingEnvelope,
    NormalizeRoutingEdge,
    RoutingReservation,
    RoutingResourceId,
    RoutingResourceKind,
)
from .TrackAssignment import TrackAssignment
from .Technology import DefaultRedstoneRoutingTechnology
from .Policy import (
    BuildRoutingAttemptPolicies,
    DefaultPhysicalDesignPolicy,
    PhysicalDesignPolicy,
    RoutingAttemptPolicy,
)
from .Workers.DetailedRouting import RoutePcbNets
from .Authoritative.RunModels import (
    RawTrackAssignmentDomain,
    RawTrackAssignmentDomainPrepared,
)


def ClusterBoundaryLeaseStateSliceSeconds(
    LeaseStateCount: int,
    LeaseVariant: int,
) -> float | None:
    """Bound proof-state time so a repaired geometry retains route time."""
    if LeaseStateCount <= 1:
        return None
    # Raw portal/guide preparation is immutable and measured separately. The
    # two ownership proofs only need enough time to reach an authoritative
    # capacity-one result; the remaining shared deadline belongs to the
    # cut-scoped repair that those proofs authorize.
    return 8.0 if LeaseVariant == 0 else 2.5


def ClusterBoundaryLeaseEndgameReserveSeconds(
    LeaseStateCount: int,
) -> float:
    """Keep time for the paired repair's placement and first route attempt."""
    return 12.0 if LeaseStateCount > 1 else 0.0

def CompactRoutedTrees(
    Placement: PcbPlacement,
    Routed: RoutedDesign,
    AccessLength: int = 3,
    Resources: Any | None = None,
    Deadline: RoutingDeadline | None = None,
) -> RoutedDesign:
    """Remove routed loops and branches that reach no required terminal."""
    def CheckDeadline(Phase: str, **Diagnostics: object) -> None:
        if Deadline is not None:
            Deadline.RaiseIfExpired(
                "RouteCompaction",
                {"Phase": Phase, **Diagnostics},
            )

    def CheckWork(
        Phase: str,
    ) -> Callable[[dict[str, object]], None] | None:
        if Deadline is None:
            return None
        return lambda Diagnostics: CheckDeadline(Phase, Work=Diagnostics)

    CheckDeadline("start")
    Placed = Placement.Placed
    if Resources is None:
        Resources = BuildRoutingResources(
            Placed,
            WorkCheck=CheckWork("resource-construction"),
        )
    ActualBlocks = set(Resources.StaticGeometry.ActualBlocks)
    TemplateElectricalBlocks = set(
        Resources.StaticGeometry.TemplateElectricalBlocks
    )
    SolidBlocks = set(Resources.StaticGeometry.SolidBlocks)
    Producers = {
        Signal: Gate
        for Gate in Placed.PlacedGates
        for Signal in Gate.Outputs
    }
    Targets: dict[str, list[tuple[int, int, int]]] = {}
    for Gate in Placed.PlacedGates:
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Pin, _Direction = GetGateInputAccess(Gate, InputIndex)
            Targets.setdefault(Signal, []).append(
                Pin
            )
    AccessBySignal: dict[str, set[tuple[int, int, int]]] = {
        Signal: set()
        for Signal in Producers
    }
    for Gate in Placed.PlacedGates:
        if Gate.OutputPin is not None and Gate.OutputDirection is not None:
            X, Y, Z = Gate.OutputPin
            DeltaX, DeltaY, DeltaZ = Gate.OutputDirection
            for Signal in Gate.Outputs:
                AccessBySignal[Signal].update(
                    (
                        X + DeltaX * Offset,
                        Y + DeltaY * Offset,
                        Z + DeltaZ * Offset,
                    )
                    for Offset in range(AccessLength)
                )
        for Index, Signal in enumerate(Gate.Inputs):
            Pin, Direction = GetGateInputAccess(Gate, Index)
            X, Y, Z = Pin
            DeltaX, DeltaY, DeltaZ = Direction
            AccessBySignal.setdefault(Signal, set()).update(
                (
                    X + DeltaX * Offset,
                    Y + DeltaY * Offset,
                    Z + DeltaZ * Offset,
                )
                for Offset in range(AccessLength)
            )
    NetWires = {
        Signal: set(Positions)
        for Signal, Positions in Routed.NetWires.items()
    }

    def BuildFootprintDiagnostics(
        WireBySignal: dict[str, set[tuple[int, int, int]]],
        SupportPositions: Iterable[tuple[int, int, int]],
        RepeaterPositions: Iterable[tuple[int, int, int]],
    ) -> dict[str, object]:
        SupportSet = set(SupportPositions)
        RepeaterSet = set(RepeaterPositions)
        PerSignal = {
            Signal: BuildRoutingEnvelope(
                Positions,
                (
                    (X, Y - 1, Z)
                    for X, Y, Z in Positions
                    if (X, Y - 1, Z) in SupportSet
                ),
                (Position for Position in Positions if Position in RepeaterSet),
            ).ToDictionary()
            for Signal, Positions in sorted(WireBySignal.items())
        }
        Aggregate = BuildRoutingEnvelope(
            (Position for Positions in WireBySignal.values() for Position in Positions),
            SupportSet,
            RepeaterSet,
        ).ToDictionary()
        return {"Aggregate": Aggregate, "PerSignal": PerSignal}

    BeforeFootprint = BuildFootprintDiagnostics(
        NetWires,
        Routed.Supports,
        Routed.Repeaters,
    )
    Graphs = BuildPhysicalGraphs(
        NetWires,
        ActualBlocks,
        set(Routed.Supports),
        SolidBlocks,
        WorkCheck=CheckWork("initial-physical-graphs"),
    )
    for Signal, Graph in Graphs.items():
        CheckDeadline("prune-signal", Signal=Signal)
        Root = Producers[Signal].OutputPin
        Parents = {Root: None}
        BestPathCost = {Root: (0, 0, 0)}
        IncomingDirection = {Root: None}
        Pending = [(0, 0, 0, Root)]
        ExpandedNodes = 0
        while Pending:
            Hops, VerticalTransitions, Bends, Current = heappop(Pending)
            if BestPathCost.get(Current) != (Hops, VerticalTransitions, Bends):
                continue
            ExpandedNodes += 1
            if ExpandedNodes % 256 == 0:
                CheckDeadline(
                    "prune-expansion",
                    Signal=Signal,
                    ExpandedNodes=ExpandedNodes,
                )
            for Neighbor in sorted(Graph[Current]):
                Direction = (
                    Neighbor[0] - Current[0],
                    Neighbor[1] - Current[1],
                    Neighbor[2] - Current[2],
                )
                CandidateCost = (
                    Hops + 1,
                    VerticalTransitions + (Current[1] != Neighbor[1]),
                    Bends + (
                        IncomingDirection[Current] is not None
                        and IncomingDirection[Current] != Direction
                    ),
                )
                if CandidateCost >= BestPathCost.get(
                    Neighbor,
                    (float("inf"), float("inf"), float("inf")),
                ):
                    continue
                Parents[Neighbor] = Current
                IncomingDirection[Neighbor] = Direction
                BestPathCost[Neighbor] = CandidateCost
                heappush(Pending, (*CandidateCost, Neighbor))
        Required = {Root}
        PinAccessPositions = (
            (
                *Routed.GlobalPlan.Profiles[Signal].SourceAccessPath,
                *(
                    Position
                    for Path in Routed.GlobalPlan.Profiles[
                        Signal
                    ].TargetAccessPaths.values()
                    for Position in Path
                ),
            )
            if Routed.GlobalPlan is not None
            and Signal in Routed.GlobalPlan.Profiles
            else ()
        )
        RequiredTargets = tuple(Targets.get(Signal, ()))
        for TargetIndex, Target in enumerate((
            *RequiredTargets,
            *PinAccessPositions,
        )):
            CheckDeadline("retain-required-path", Signal=Signal)
            if Target not in Parents:
                TargetKind = (
                    "logical-target"
                    if TargetIndex < len(RequiredTargets)
                    else "profile-access"
                )
                raise ValueError(
                    "PCB route compaction disconnected net "
                    f"{Signal}: {TargetKind}={Target}, root={Root}, "
                    f"target-in-graph={Target in Graph}, "
                    f"root-in-graph={Root in Graph}, "
                    f"reachable={len(Parents)}, graph={len(Graph)}"
                )
            Current = Target
            while Current is not None:
                Required.add(Current)
                Current = Parents[Current]
        NetWires[Signal].intersection_update(Required)

    Supports: set[tuple[int, int, int]] = set()
    SupportPositionCount = 0
    for Signal, Positions in NetWires.items():
        for X, Y, Z in Positions:
            SupportPositionCount += 1
            if SupportPositionCount % 256 == 0:
                CheckDeadline(
                    "support-rebuild",
                    Signal=Signal,
                    ProcessedPositions=SupportPositionCount,
                )
            Supports.add((X, Y - 1, Z))
    Supports.difference_update(ActualBlocks)
    CheckDeadline("rebuild-physical-graphs")
    PhysicalGraphs = BuildPhysicalGraphs(
        NetWires,
        ActualBlocks,
        Supports,
        SolidBlocks,
        WorkCheck=CheckWork("rebuild-physical-graphs"),
    )
    CheckDeadline("physical-validation")
    ValidatePhysicalRoutes(
        PhysicalGraphs,
        Producers,
        Targets,
        WorkCheck=CheckWork("physical-validation"),
    )
    CheckDeadline("template-isolation-validation")
    ValidateTemplateIsolation(
        NetWires,
        ActualBlocks,
        TemplateElectricalBlocks,
        SolidBlocks,
        Producers,
        Targets,
        AccessBySignal,
        WorkCheck=CheckWork("template-isolation-validation"),
    )
    TrackAssignmentValue = Routed.TrackAssignment
    if TrackAssignmentValue is None:
        raise ValueError("Routed design is missing authoritative track assignment")
    CheckDeadline("claim-rebuild")
    FinalClaims = {}
    for Signal, Positions in NetWires.items():
        CheckDeadline("claim-rebuild", Signal=Signal)
        FinalClaims[Signal] = Resources.ResourceGraph.BuildRouteClaims(
            Positions,
            WorkCheck=CheckWork("claim-rebuild"),
        )
    FinalConflicts = FindClaimConflicts(
        FinalClaims,
        WorkCheck=CheckWork("claim-conflict-validation"),
    )
    if FinalConflicts:
        First = min(FinalConflicts, key=str)
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.FinalDrcViolation,
                Stage="CleanupDrc",
                AffectedNets=FinalConflicts[First],
                Resources=(str(First),),
                Locations=(First.Position,),
                Detail="cleaned route violates authoritative capacity-one claims",
            )
        )
    FinalOwners = defaultdict(list)
    FinalTracks = {}
    OwnershipResourceCount = 0
    OwnershipEdgeCount = 0
    for Signal, Track in TrackAssignmentValue.Tracks.items():
        CheckDeadline("ownership-rebuild", Signal=Signal)
        ReservedResources = frozenset(
            Resource
            for Resource in FinalClaims[Signal].ResourceIds
            if Resource.Kind != RoutingResourceKind.Electrical
        )
        for Resource in ReservedResources:
            OwnershipResourceCount += 1
            if OwnershipResourceCount % 256 == 0:
                CheckDeadline(
                    "ownership-resources",
                    Signal=Signal,
                    ProcessedResources=OwnershipResourceCount,
                )
            FinalOwners[Resource].append(Signal)
        Graph = PhysicalGraphs[Signal]
        OwnedEdges = set()
        for Position, Neighbors in Graph.items():
            for Neighbor in Neighbors:
                OwnershipEdgeCount += 1
                if OwnershipEdgeCount % 256 == 0:
                    CheckDeadline(
                        "ownership-edges",
                        Signal=Signal,
                        ProcessedEdges=OwnershipEdgeCount,
                    )
                if Position < Neighbor:
                    OwnedEdges.add(NormalizeRoutingEdge(Position, Neighbor))
        FinalTracks[Signal] = replace(
            Track,
            ReservedResources=ReservedResources,
            OwnedNodes=frozenset(NetWires[Signal]),
            OwnedEdges=frozenset(OwnedEdges),
        )
    TrackAssignmentValue = TrackAssignment(
        Tracks=FinalTracks,
        ResourceOwners={
            Resource: tuple(Signals)
            for Resource, Signals in FinalOwners.items()
        },
    )
    CheckDeadline("repeater-materialization")
    RepeaterPruningDiagnostics: dict[str, dict[str, object]] = {}
    Repeaters = MaterializeReservedRepeaters(
        NetWires,
        Producers,
        Targets,
        PhysicalGraphs,
        TrackAssignmentValue.Tracks,
        WorkCheck=CheckWork("repeater-materialization"),
        PruningDiagnostics=RepeaterPruningDiagnostics,
    )
    MaterializedTracks = {}
    for Signal, Track in TrackAssignmentValue.Tracks.items():
        ExistingReservations = {
            Reservation.Position: Reservation
            for Reservation in Track.RepeaterReservations
        }
        SignalRepeaters = {
            Position: Facing
            for Position, Facing in Repeaters.items()
            if Position in NetWires[Signal]
        }
        MaterializedReservations = tuple(
            ExistingReservations.get(
                Position,
                RoutingReservation(
                    Signal=Signal,
                    Resource=RoutingResourceId(
                        RoutingResourceKind.Wire,
                        Position,
                    ),
                    Position=Position,
                    Purpose="FallbackRepeater",
                    Facing=Facing,
                ),
            )
            for Position, Facing in sorted(SignalRepeaters.items())
        )
        MaterializedTracks[Signal] = replace(
            Track,
            RepeaterSites=frozenset(
                (Position[0], Position[2])
                for Position in SignalRepeaters
            ),
            RepeaterReservations=MaterializedReservations,
        )
    TrackAssignmentValue = TrackAssignment(
        Tracks=MaterializedTracks,
        ResourceOwners=TrackAssignmentValue.ResourceOwners,
    )
    Wires = set().union(*NetWires.values()) if NetWires else set()
    PreviousMetrics = Routed.RoutingMetrics
    ChannelPlanValue = Routed.GlobalPlan
    if ChannelPlanValue is None:
        raise ValueError("Routed design is missing authoritative global plan")
    RoutingAssignmentValue = (
        replace(
            Routed.RoutingAssignment,
            ResourceOwners=TrackAssignmentValue.ResourceOwners,
        )
        if Routed.RoutingAssignment is not None
        else None
    )
    CheckDeadline("complete")
    AfterFootprint = BuildFootprintDiagnostics(
        NetWires,
        Supports,
        Repeaters,
    )
    BeforeAggregate = BeforeFootprint["Aggregate"]
    AfterAggregate = AfterFootprint["Aggregate"]
    RoutingFootprintDiagnostics = {
        "BeforeCompaction": BeforeFootprint,
        "AfterCompaction": AfterFootprint,
        "CompactionSavings": {
            Name: BeforeAggregate[Name] - AfterAggregate[Name]
            for Name in (
                "RouteBlockCount",
                "SupportBlockCount",
                "RepeaterCount",
                "Footprint",
                "Width",
                "Depth",
                "Height",
            )
        },
    }
    SelectedCandidates = (
        Routed.RoutingAssignment.SelectedCandidates
        if Routed.RoutingAssignment is not None
        else {}
    )
    PerSignalShape = {
        Signal: {
            "TerminalAccessLength": len(AccessBySignal.get(Signal, ())),
            "Length": Candidate.Length,
            "Bends": Candidate.BendCount,
            "Vias": Candidate.ViaCount,
            "Repeaters": len(Candidate.RepeaterReservations),
            "RouteBlockSavings": (
                BeforeFootprint["PerSignal"].get(Signal, {})
                .get("RouteBlockCount", 0)
                - AfterFootprint["PerSignal"].get(Signal, {})
                .get("RouteBlockCount", 0)
            ),
        }
        for Signal, Candidate in sorted(SelectedCandidates.items())
    }
    RoutingFootprintDiagnostics["RouteShape"] = {
        "Aggregate": {
            "TerminalAccessLength": sum(
                Value["TerminalAccessLength"]
                for Value in PerSignalShape.values()
            ),
            "Bends": sum(Value["Bends"] for Value in PerSignalShape.values()),
            "Vias": sum(Value["Vias"] for Value in PerSignalShape.values()),
        },
        "PerSignal": PerSignalShape,
    }
    return RoutedDesign(
        Module=Placed.Module,
        PlacedGates=Placed.PlacedGates,
        Wires=sorted(Wires),
        Supports=sorted(Supports),
        Repeaters=Repeaters,
        NetWires={Signal: sorted(Positions) for Signal, Positions in NetWires.items()},
        SupportBlock=Routed.SupportBlock,
        TemplateAccessBySignal=AccessBySignal,
        RoutingMetrics=MeasureRoutingStage(
            (
                PreviousMetrics.Stage
                if PreviousMetrics is not None
                else "Cleanup"
            ),
            NetWires,
            ChannelPlanValue,
            ReroutedNets=(
                PreviousMetrics.ReroutedNets
                if PreviousMetrics is not None
                else 0
            ),
            ConflictCount=(
                PreviousMetrics.ConflictCount
                if PreviousMetrics is not None
                else 0
            ),
            Iterations=(
                PreviousMetrics.Iterations
                if PreviousMetrics is not None
                else ()
            ),
        ),
        GlobalPlan=ChannelPlanValue,
        TrackAssignment=TrackAssignmentValue,
        TechnologyVersion=Routed.TechnologyVersion,
        EffectivePolicy=Routed.EffectivePolicy,
        ResourceGraphVersion=Routed.ResourceGraphVersion,
        ResourceGraphNodeCount=Routed.ResourceGraphNodeCount,
        ResourceGraphEdgeCount=Routed.ResourceGraphEdgeCount,
        ResourceOwnershipCounts=dict(
            Counter(
                Resource.Kind.value
                for Claims in FinalClaims.values()
                for Resource in Claims.ResourceIds
            )
        ),
        RepeaterReservationCount=sum(
            len(Track.RepeaterReservations)
            for Track in TrackAssignmentValue.Tracks.values()
        ),
        RepeaterOptimizationDiagnostics=RepeaterPruningDiagnostics,
        ZeroResourceConflicts=Routed.ZeroResourceConflicts,
        RoutingAssignment=RoutingAssignmentValue,
        PortalCount=Routed.PortalCount,
        RouteCandidateCount=Routed.RouteCandidateCount,
        CandidateRequestCount=Routed.CandidateRequestCount,
        CandidateExpansionLimit=Routed.CandidateExpansionLimit,
        AssignmentExpansionCount=Routed.AssignmentExpansionCount,
        RoutingStageTimings=Routed.RoutingStageTimings,
        GlobalGuideDiagnostics=Routed.GlobalGuideDiagnostics,
        RoutingControlEffectiveness=Routed.RoutingControlEffectiveness,
        NegotiatedRoutingDiagnostics=Routed.NegotiatedRoutingDiagnostics,
        RoutingFootprintDiagnostics=RoutingFootprintDiagnostics,
    )


def _BuildPlacementAccessRoutingBoundsAudit(
    Placement: PcbPlacement,
    Fabric: Any,
    *,
    SearchMargin: int,
) -> DetailedRoutingBounds | None:
    """Measure the existing router canvas without changing its contract.

    ``RouteAuthoritativeResources`` narrows a legacy
    ``PlacementAccessAssignment`` to its selected stub before it expands the
    detailed-routing region.  Mirror that *read-only* projection here so the
    audit record describes the same region, while the fabric's own
    ``AccessContractBounds`` still describes the complete pre-route domain.
    """
    Gates = tuple(getattr(Placement.Placed, "PlacedGates", ()))
    if not Gates:
        return None
    CoreBounds = (
        min(int(Gate.X) for Gate in Gates),
        min(int(Gate.Z) for Gate in Gates),
        max(
            int(Gate.X) + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
            for Gate in Gates
        ),
        max(
            int(Gate.Z) + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
            for Gate in Gates
        ),
    )
    Assignment = getattr(
        Placement.Placed,
        "PlacementAccessAssignment",
        getattr(Placement, "PlacementAccessAssignment", None),
    )
    SelectedStubIndices = {
        (str(Signal), tuple(Terminal)): int(StubIndex)
        for Signal, Terminal, StubIndex in getattr(
            Assignment,
            "SelectedStubIndices",
            (),
        )
    }
    Domains = tuple(
        replace(
            Domain,
            EscapeStubs=(
                Domain.EscapeStubs[
                    SelectedStubIndices[(str(Domain.Signal), tuple(Domain.Terminal))]
                ],
            ),
        )
        if (str(Domain.Signal), tuple(Domain.Terminal))
        in SelectedStubIndices
        else Domain
        for Domain in getattr(Fabric, "TerminalDomains", ())
    )
    AccessBounds = AccessContractBounds.FromPlacementAccessFabric(
        Fabric,
        Domains=Domains,
    )
    return DetailedRoutingBounds.FromCoreAndAccessContract(
        CoreBounds,
        AccessBounds,
        SearchMarginX=SearchMargin,
        SearchMarginZ=SearchMargin,
    )


def RoutePcbAttempt(
    Placement: PcbPlacement,
    Configuration: RoutingAttemptPolicy,
    Resources: Any | None = None,
    ProgressCallback: Callable[[int, int], None] | None = None,
    StatusCallback: Callable[[str], None] | None = None,
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy,
    Deadline: RoutingDeadline | None = None,
    PreparePortalGeometryOnly: bool = False,
    PrepareTrackAssignmentOnly: bool = False,
    PrepareRawTrackAssignmentDomainOnly: bool = False,
    FrozenTrackAssignmentPreparation: TrackAssignmentPreparation | None = None,
    ValidateClusterInterfaceForeignAccessOnly: bool = False,
    ValidatePhysicalComponentForeignPortalSupportOnly: bool = False,
    PrepareClusterInterfaceAssignmentOnly: bool = False,
    PrepareComponentRoutingProblemOnly: bool = False,
    PreparePhysicalComponentAssemblyOnly: bool = False,
    PreparePhysicalComponentPortFactorDomainOnly: bool = False,
    DeferClusterBoundaryLeaseUntilCapacityPrecheck: bool = False,
    UnboundOwnedSignalFrontierProofCallback: Callable[
        [ComponentRoutingProblem], None
    ] | None = None,
    RequireCompleteClusterInterfaceDomain: bool = False,
    ClusterInterfaceRealizabilityNogoods: tuple[
        ClusterInterfaceRealizabilityNogood, ...
    ] = (),
    ClusterInterfaceStateFingerprint: str = "",
    ClusterInterfaceLocalRouteFingerprint: str = "",
    ForbiddenClusterInterfaceAssignmentFingerprints: (
        frozenset[str]
    ) = frozenset(),
    ClusterInterfaceFrozenPatternFingerprints: (
        dict[str, str] | None
    ) = None,
    ClusterInterfaceFrozenReservations: tuple[Any, ...] = (),
) -> RoutedDesign:
    """Run the single authoritative routing configuration."""
    def CheckRoutingDeadline(
        Stage: str,
        Diagnostics: dict[str, object] | None = None,
    ) -> None:
        if Deadline is not None:
            Deadline.RaiseIfExpired(Stage, Diagnostics)

    def CheckRoutingWork(
        Stage: str,
    ) -> Callable[[dict[str, object]], None] | None:
        if Deadline is None:
            return None
        return lambda Diagnostics: CheckRoutingDeadline(Stage, Diagnostics)

    SearchMargin = Configuration.SearchMargin
    PlacementAccessFabric = getattr(
        Placement,
        "PlacementAccessFabric",
        None,
    )
    if (
        PlacementAccessFabric is not None
        and getattr(
            PlacementAccessFabric,
            "TopologyKind",
            "",
        ) == "derived-perimeter-access-v1"
    ):
        # The pre-route selector has already committed a finite perimeter
        # contract.  Letting detailed routing reopen the legacy search margin
        # would make its compactness objective fictitious and silently add
        # exterior geometry after the only capacity solve.  The ring's
        # physical pitch and selected track count are the exact allowed
        # exterior margin for this frozen candidate.
        FabricTechnology = (
            getattr(PlacementAccessFabric, "Technology", None)
            or DefaultRedstoneRoutingTechnology
        )
        SearchMargin = (
            int(PlacementAccessFabric.AccessRingTrackCount)
            * int(FabricTechnology.TrackPitch)
        )
    GuidePenalty = Configuration.GuidePenalty
    DetourRatio = Configuration.MaximumDetourRatio
    DetourAllowance = Configuration.MaximumDetourAllowance
    Iterations = Configuration.MaximumIterations
    OrderMode = Configuration.OrderMode
    LeaseReservationVariant = Placement.ClusterBoundaryLeaseVariant
    if Resources is None:
        Resources = BuildRoutingResources(
            Placement.Placed,
            WorkCheck=CheckRoutingWork("RoutingResourceConstruction"),
        )
    CompletedRoutingPasses = 0
    MaximumRoutingHeight = 2 * Placement.LayerCount + 2
    RouteLayers = Placement.Placed.RouteLayers or {}
    OriginalIndex = {
        Signal: Index
        for Index, Signal in enumerate(Placement.SignalOrder)
    }
    FrozenNetWires = Placement.Placed.FrozenNetWires or {}
    SignalOrder = sorted(
        (Signal for Signal in Placement.SignalOrder if Signal not in FrozenNetWires),
        key=lambda Signal: (
            -RouteLayers.get(Signal, 0),
            (
                -OriginalIndex[Signal]
                if OrderMode == "Reverse"
                else OriginalIndex[Signal]
            ),
        ),
    )

    def ReportStatus(Stage: str) -> None:
        if StatusCallback is not None:
            StatusCallback(Stage)

    def RouteWithProgress(
        PlacedValue: PlacedDesign,
        ResourcesValue: Any,
        AccessLengthValue: int,
    ) -> RoutedDesign:
        """Run one authoritative route and report its internal stages."""
        nonlocal CompletedRoutingPasses
        LastCompleted = 0
        BestConflictCount: int | None = None

        def ReportIteration(Completed: int, Total: int) -> None:
            nonlocal LastCompleted
            EffectiveTotal = max(1, Total)
            # The final unit belongs to cleanup/compaction below.  Never let
            # an inner planner callback publish 100% before that work passes.
            EffectiveCompleted = min(Completed, EffectiveTotal - 1)
            LastCompleted = max(LastCompleted, EffectiveCompleted)
            StageName = {
                0: "authoritative resource graph",
                1: "capacity-aware global guide planning",
                2: "authoritative portal generation",
                3: "negotiated route-tree construction",
                4: "capacity-one route-tree validation",
                5: "authoritative validation and materialization",
            }.get(Completed, "authoritative routing")
            ReportStatus(StageName)
            if ProgressCallback is not None:
                ProgressCallback(
                    CompletedRoutingPasses + EffectiveCompleted,
                    CompletedRoutingPasses + EffectiveTotal,
                )

        def ReportIterationDiagnostic(Metrics: Any, FailedSignal: str | None) -> None:
            nonlocal BestConflictCount
            if Metrics.ConflictCount > 0:
                BestConflictCount = (
                    Metrics.ConflictCount
                    if BestConflictCount is None
                    else min(BestConflictCount, Metrics.ConflictCount)
                )
            if FailedSignal is not None:
                Outcome = f"repairing {FailedSignal}"
            else:
                Outcome = f"{Metrics.ConflictCount} conflicts"
            BestProgress = (
                "best incomplete"
                if BestConflictCount is None
                else f"best {BestConflictCount} conflicts"
            )
            ReportStatus(
                f"{Metrics.Stage.lower()} | {Outcome} | "
                f"{BestProgress} | "
                f"avg {Metrics.AverageLength:.1f}, "
                f"bends {Metrics.BendCount}, vias {Metrics.ViaCount}"
                + (
                    f", nets {','.join(Metrics.ConflictSignals)}"
                    if Metrics.ConflictSignals
                    else ""
                )
            )

        try:
            Routed = RoutePcbNets(
                PlacedValue,
                SignalOrder=SignalOrder,
                SearchMarginX=SearchMargin,
                SearchMarginZ=SearchMargin,
                MaximumRoutingHeight=MaximumRoutingHeight,
                AccessLength=AccessLengthValue,
                ElectricalClearance=0,
                MaximumIterations=Iterations,
                MaximumDetourRatio=DetourRatio,
                MaximumDetourAllowance=DetourAllowance,
                Resources=ResourcesValue,
                RouteGuidePenalty=GuidePenalty,
                IterationProgressCallback=ReportIteration,
                IterationDiagnosticCallback=ReportIterationDiagnostic,
                Policy=Policy,
                SkipStrictPortalReservation=False,
                ReservationVariant=LeaseReservationVariant,
                PreparePortalGeometryOnly=PreparePortalGeometryOnly,
                PrepareTrackAssignmentOnly=PrepareTrackAssignmentOnly,
                PrepareRawTrackAssignmentDomainOnly=(
                    PrepareRawTrackAssignmentDomainOnly
                ),
                FrozenTrackAssignmentPreparation=(
                    FrozenTrackAssignmentPreparation
                ),
                ValidateClusterInterfaceForeignAccessOnly=(
                    ValidateClusterInterfaceForeignAccessOnly
                ),
                ValidatePhysicalComponentForeignPortalSupportOnly=(
                    ValidatePhysicalComponentForeignPortalSupportOnly
                ),
                PrepareClusterInterfaceAssignmentOnly=(
                    PrepareClusterInterfaceAssignmentOnly
                ),
                PrepareComponentRoutingProblemOnly=(
                    PrepareComponentRoutingProblemOnly
                ),
                PreparePhysicalComponentAssemblyOnly=(
                    PreparePhysicalComponentAssemblyOnly
                ),
                PreparePhysicalComponentPortFactorDomainOnly=(
                    PreparePhysicalComponentPortFactorDomainOnly
                ),
                DeferClusterBoundaryLeaseUntilCapacityPrecheck=(
                    DeferClusterBoundaryLeaseUntilCapacityPrecheck
                ),
                UnboundOwnedSignalFrontierProofCallback=(
                    UnboundOwnedSignalFrontierProofCallback
                ),
                RequireCompleteClusterInterfaceDomain=(
                    RequireCompleteClusterInterfaceDomain
                ),
                ClusterInterfaceRealizabilityNogoods=(
                    ClusterInterfaceRealizabilityNogoods
                ),
                ClusterInterfaceStateFingerprint=(
                    ClusterInterfaceStateFingerprint
                ),
                ClusterInterfaceLocalRouteFingerprint=(
                    ClusterInterfaceLocalRouteFingerprint
                ),
                ForbiddenClusterInterfaceAssignmentFingerprints=(
                    ForbiddenClusterInterfaceAssignmentFingerprints
                ),
                ClusterInterfaceFrozenPatternFingerprints=(
                    ClusterInterfaceFrozenPatternFingerprints
                ),
                ClusterInterfaceFrozenReservations=(
                    ClusterInterfaceFrozenReservations
                ),
                Deadline=Deadline,
            )
            return Routed
        finally:
            CompletedRoutingPasses += LastCompleted

    AccessLength = Policy.Placement.PinEscapeLength
    ReportStatus(f"routing original placement access {AccessLength}")
    Routed = RouteWithProgress(
        Placement.Placed,
        Resources,
        AccessLength,
    )
    if PreparePhysicalComponentPortFactorDomainOnly:
        if not isinstance(
            Routed,
            PreparedPhysicalComponentPortFactorDomain,
        ):
            raise RuntimeError(
                "physical component eligibility preparation returned "
                "no factor domain"
            )
        return Routed
    if bool(getattr(
        Resources,
        "PreparingPhysicalComponentGlobalChannels",
        False,
    )):
        if not isinstance(Routed, RoutedDesign):
            raise RuntimeError(
                "physical global channel preparation returned no routed design"
            )
        # This is an immutable corridor contract, not a completed whole-design
        # route. Compaction and whole-design materialization belong to the
        # authoritative detailed-routing pass after local template handoff.
        return Routed
    LocalNetBranches = Placement.Placed.LocalNetBranches or {}
    CompactionAccessLength = (
        max(AccessLength, 3)
        if (Placement.Placed.LocalRouteClaims or ())
        else AccessLength
    )
    ReportStatus(f"compacting access length {CompactionAccessLength}")
    if Deadline is not None:
        Deadline.RaiseIfExpired("RouteCompaction", {"Phase": "before"})
    Routed = CompactRoutedTrees(
        Placement,
        Routed,
        AccessLength=CompactionAccessLength,
        Resources=Resources,
        Deadline=Deadline,
    )
    if Deadline is not None:
        Deadline.RaiseIfExpired("RouteCompaction", {"Phase": "after"})
    if FrozenNetWires:
        NetWires = {}
        for Signal, Positions in Routed.NetWires.items():
            CheckRoutingDeadline(
                "FrozenNetMerge",
                {"Phase": "detailed-net", "Signal": Signal},
            )
            NetWires[Signal] = set(Positions)
        for Signal, Positions in FrozenNetWires.items():
            CheckRoutingDeadline(
                "FrozenNetMerge",
                {"Phase": "frozen-net", "Signal": Signal},
            )
            NetWires[Signal] = set(Positions)
        Conflicts, _ConflictCounts = FindFlatRouteConflicts(
            NetWires,
            WorkCheck=CheckRoutingWork("FrozenNetConflictValidation"),
        )
        if Conflicts:
            raise ValueError(
                "Frozen local NAND routes conflict with detailed routes: "
                + ", ".join(map(str, sorted(Conflicts)))
            )
        Routed.NetWires = {
            Signal: sorted(Positions)
            for Signal, Positions in sorted(NetWires.items())
        }
        MergedWires: set[tuple[int, int, int]] = set()
        MergedSupports: set[tuple[int, int, int]] = set()
        MergedPositionCount = 0
        for Signal, Positions in NetWires.items():
            for X, Y, Z in Positions:
                MergedPositionCount += 1
                if MergedPositionCount % 256 == 0:
                    CheckRoutingDeadline(
                        "FrozenNetMaterialization",
                        {
                            "Signal": Signal,
                            "ProcessedPositions": MergedPositionCount,
                        },
                    )
                MergedWires.add((X, Y, Z))
                MergedSupports.add((X, Y - 1, Z))
        Routed.Wires = sorted(MergedWires)
        Routed.Supports = sorted(MergedSupports)
        Routed.RoutingMetrics = MeasureRoutingStage(
            Routed.RoutingMetrics.Stage if Routed.RoutingMetrics else "Cleanup",
            NetWires,
            Routed.GlobalPlan,
            ReroutedNets=(
                Routed.RoutingMetrics.ReroutedNets
                if Routed.RoutingMetrics
                else 0
            ),
            ConflictCount=0,
            Iterations=(
                Routed.RoutingMetrics.Iterations
                if Routed.RoutingMetrics
                else ()
            ),
        )
        FinalClaims = {}
        for Signal, Positions in NetWires.items():
            CheckRoutingDeadline(
                "FrozenNetClaimRebuild",
                {"Signal": Signal},
            )
            FinalClaims[Signal] = Resources.ResourceGraph.BuildRouteClaims(
                Positions,
                WorkCheck=CheckRoutingWork("FrozenNetClaimRebuild"),
            )
        ClaimConflicts = FindClaimConflicts(
            FinalClaims,
            WorkCheck=CheckRoutingWork("FrozenNetClaimValidation"),
        )
        if ClaimConflicts:
            Resource = min(ClaimConflicts, key=str)
            raise ValueError(
                f"Merged local ownership conflicts at {Resource}: "
                + ",".join(ClaimConflicts[Resource])
            )
        Owners = defaultdict(list)
        OwnershipResourceCount = 0
        for Signal, Claims in FinalClaims.items():
            for Resource in Claims.ResourceIds:
                OwnershipResourceCount += 1
                if OwnershipResourceCount % 256 == 0:
                    CheckRoutingDeadline(
                        "FrozenNetOwnershipRebuild",
                        {
                            "Signal": Signal,
                            "ProcessedResources": OwnershipResourceCount,
                        },
                    )
                if Resource.Kind != RoutingResourceKind.Electrical:
                    Owners[Resource].append(Signal)
        if Routed.TrackAssignment is not None:
            Routed.TrackAssignment = TrackAssignment(
                Tracks=Routed.TrackAssignment.Tracks,
                ResourceOwners={
                    Resource: tuple(sorted(Signals))
                    for Resource, Signals in Owners.items()
                },
            )
        if Routed.RoutingAssignment is not None:
            Routed.RoutingAssignment = replace(
                Routed.RoutingAssignment,
                ResourceOwners=(
                    Routed.TrackAssignment.ResourceOwners
                    if Routed.TrackAssignment is not None
                    else {}
                ),
            )
    Routed.FrozenNetSignals = tuple(sorted(FrozenNetWires))
    Routed.RoutingControlEffectiveness["FrozenLocalNets"] = {
        Signal: len(Positions)
        for Signal, Positions in sorted(FrozenNetWires.items())
    }
    Routed.RoutingControlEffectiveness["LocalNetBranches"] = {
        Signal: len(Positions)
        for Signal, Positions in sorted(LocalNetBranches.items())
    }
    Routed.RoutingControlEffectiveness["LocalRouteOwnership"] = {
        "ClaimCount": len(Placement.Placed.LocalRouteClaims or ()),
        "ConnectedTargetCount": sum(
            len(set(Claim.ConnectedTargets))
            for Claim in (Placement.Placed.LocalRouteClaims or ())
        ),
        "PreOwnedNodeCount": sum(
            len(Claim.Nodes) for Claim in (Placement.Placed.LocalRouteClaims or ())
        ),
        "PartialSignals": sorted(
            set(LocalNetBranches) - set(FrozenNetWires)
        ),
        "CompleteSignals": sorted(FrozenNetWires),
    }
    Routed.RoutingControlEffectiveness["Organization"] = {
        "Enabled": Policy.Organization.Enabled,
        "PreferredXLayer": Policy.Organization.PreferredXLayer,
        "PreferredZLayer": Policy.Organization.PreferredZLayer,
        "BridgeLayers": list(Policy.Organization.BridgeLayers),
        "ForeignIslandTraversalAllowed": (
            Policy.Organization.AllowForeignIslandTraversal
        ),
        "ClusterEntranceBudget": Policy.Organization.MaximumClusterEntrances,
        "PerSignalEntranceBudget": (
            Policy.Organization.MaximumClusterEntrancesPerSignal
        ),
        "ClusterEntranceCount": sum(
            len(Claim.BoundaryNodes)
            for Claim in (Placement.Placed.LocalRouteClaims or ())
        ),
        "BoundedEscapes": [],
        "IslandCrossings": 0,
    }
    if PlacementAccessFabric is not None:
        DetailedBounds = _BuildPlacementAccessRoutingBoundsAudit(
            Placement,
            PlacementAccessFabric,
            SearchMargin=SearchMargin,
        )
        Routed.RoutingControlEffectiveness["AccessContractBounds"] = (
            PlacementAccessFabric.AccessContractBounds.ToDictionary()
        )
        Routed.RoutingControlEffectiveness["DetailedRoutingBounds"] = (
            DetailedBounds.ToDictionary()
            if DetailedBounds is not None
            else None
        )
    if Deadline is not None:
        Deadline.RaiseIfExpired("RoutingFinalization")
    ReportStatus("authoritative route complete")
    if ProgressCallback is not None:
        FinalCompleted = CompletedRoutingPasses + 1
        ProgressCallback(FinalCompleted, FinalCompleted)
    return Routed

def BuildPcbRoutingConfigurations(
    Placement: PcbPlacement,
) -> tuple[RoutingAttemptPolicy, ...]:
    """Return the single authoritative routing attempt."""
    del Placement
    return BuildRoutingAttemptPolicies()


def ClusterBoundaryLeaseStateCount(
    Placement: PcbPlacement,
    *,
    HasFrozenTrackAssignment: bool = False,
) -> int:
    """Select the bounded lease portfolio for one placed geometry.

    A dense original placement needs multiple ownership states to establish
    whether its access conflict is structural.  Once a complete assignment
    cut has already produced a new exact joint placement, replaying that
    proof portfolio would divide the new geometry's first route attempt into
    three tiny slices.  The repaired geometry instead receives one
    authoritative state; any new cut returns to placement for the next
    access-distinct geometry.

    A routed component has already discharged the dense interface proof and
    frozen its physical claims.  Its handoff is therefore an ordinary global
    route over the remaining nets, not another dense lease portfolio.  The
    same rule applies when the caller supplies a frozen pre-placement track
    witness: revisiting lease variants would be a second route attempt with
    a different ownership state, not consumption of the selected contract.
    """
    if HasFrozenTrackAssignment:
        return 1
    if getattr(
        Placement.Placed,
        "RoutedComponentTemplates",
        (),
    ):
        return 1
    LeaseTerminalCount = sum(
        1 + len(Request.TargetTerminals)
        for Request in getattr(
            Placement.Placed,
            "ClusterBoundaryLeaseRequests",
            (),
        )
    )
    JointDiagnostics = (
        getattr(Placement.Placed, "LocalRouteDiagnostics", {}) or {}
    ).get("__JointClusterPlacement__", {})
    SerializedConstraints = (
        JointDiagnostics.get("ActiveAssignmentConstraints", {})
        if isinstance(JointDiagnostics, dict)
        else {}
    )
    HasStructuredJointRepair = bool(
        isinstance(SerializedConstraints, dict)
        and (
            SerializedConstraints.get("PairwiseConflictEdges")
            or SerializedConstraints.get("HigherOrderSignalSets")
        )
    )
    return 1 if HasStructuredJointRepair else (
        3 if LeaseTerminalCount >= 16 else 1
    )


def PrepareClusterInterfaceAssignment(
    Placement: PcbPlacement,
    *,
    Resources: Any,
    Policy: PhysicalDesignPolicy,
    Deadline: RoutingDeadline,
    RealizabilityNogoods: tuple[
        ClusterInterfaceRealizabilityNogood, ...
    ] = (),
    StateFingerprint: str = "",
    LocalRouteFingerprint: str = "",
    DeferClusterBoundaryLeaseUntilCapacityPrecheck: bool = False,
    ForbiddenAssignmentFingerprints: frozenset[str] = frozenset(),
    FrozenPatternFingerprints: dict[str, str] | None = None,
    FrozenReservations: tuple[Any, ...] = (),
    ProgressCallback: Callable[[int, int], None] | None = None,
    StatusCallback: Callable[[str], None] | None = None,
    RequireCompleteDomain: bool = True,
) -> ClusterInterfaceAssignment:
    """Prove boundary ownership and bounded access-tree realizability."""
    Resources.PreparedClusterInterfaceAssignment = None
    Resources.FrozenClusterInterfaceAssignment = None
    Resources.FrozenPreparedPortalDomainCache = None
    if RealizabilityNogoods or ForbiddenAssignmentFingerprints:
        # The geometry cache remains valid, but a prepared ownership cache
        # contains the exact access pattern that the new no-good or complete
        # ownership-combination exclusion rejects.
        Resources.PreparedPortalDomainCaches = ()
    Configuration = BuildPcbRoutingConfigurations(Placement)[0]
    try:
        RoutePcbAttempt(
            replace(Placement, ClusterBoundaryLeaseVariant=0),
            Configuration,
            Resources=Resources,
            ProgressCallback=ProgressCallback,
            StatusCallback=StatusCallback,
            Policy=Policy,
            Deadline=Deadline,
            PrepareClusterInterfaceAssignmentOnly=True,
            RequireCompleteClusterInterfaceDomain=(
                RequireCompleteDomain
            ),
            ClusterInterfaceRealizabilityNogoods=(
                RealizabilityNogoods
            ),
            ClusterInterfaceStateFingerprint=StateFingerprint,
            ClusterInterfaceLocalRouteFingerprint=(
                LocalRouteFingerprint
            ),
            ForbiddenClusterInterfaceAssignmentFingerprints=(
                ForbiddenAssignmentFingerprints
            ),
            ClusterInterfaceFrozenPatternFingerprints=(
                FrozenPatternFingerprints
            ),
            ClusterInterfaceFrozenReservations=FrozenReservations,
        )
    except ClusterInterfaceAssignmentPrepared as Prepared:
        if not Resources.PreparedPortalDomainCaches:
            raise RuntimeError(
                "cluster interface assignment was prepared without its "
                "immutable portal-domain cache"
            )
        Resources.FrozenPreparedPortalDomainCache = (
            Resources.PreparedPortalDomainCaches[-1]
        )
        return Prepared.Assignment
    except RoutingStageError as Error:
        Diagnostics = dict(Error.Failure.Diagnostics or {})
        RejectedAssignment = (
            Resources.PreparedClusterInterfaceAssignment
        )
        if (
            RejectedAssignment is not None
            and "RejectedInterfaceAssignment" not in Diagnostics
        ):
            Diagnostics["RejectedInterfaceAssignment"] = (
                RejectedAssignment.ToDictionary()
            )
        PatternSearch = Diagnostics.get(
            "ClusterInterfacePatternSearch",
            {},
        )
        Incomplete = bool(
            Diagnostics.get("BudgetExhausted", False)
            or Diagnostics.get("BoundedPortalSlice", False)
            or (
                isinstance(PatternSearch, dict)
                and PatternSearch.get("BudgetExhausted", False)
            )
            or Deadline.IsExpired()
        )
        DomainComplete = bool(
            RequireCompleteDomain
            and not Incomplete
        )
        raise RoutingStageError(replace(
            Error.Failure,
            Reason=(
                RoutingFailureReason.ClusterInterfaceSolveIncomplete
                if Incomplete
                else RoutingFailureReason.ClusterInterfaceUnsatisfiable
            ),
            Stage=(
                "ClusterInterfaceSolveIncomplete"
                if Incomplete
                else "ClusterInterfaceUnsatisfiable"
            ),
            RepairActions=(),
            Diagnostics={
                **Diagnostics,
                "ClusterInterfaceDomainComplete": DomainComplete,
                "ClusterInterfacePreparationClassification": {
                    "RequireCompleteDomain": RequireCompleteDomain,
                    "DeadlineExpired": Deadline.IsExpired(),
                    "BudgetExhausted": bool(
                        Diagnostics.get("BudgetExhausted", False)
                    ),
                    "BoundedPortalSlice": bool(
                        Diagnostics.get("BoundedPortalSlice", False)
                    ),
                    "PatternSearchBudgetExhausted": bool(
                        isinstance(PatternSearch, dict)
                        and PatternSearch.get(
                            "BudgetExhausted",
                            False,
                        )
                    ),
                },
                "InterfaceSolve": {
                    "Complete": not Incomplete,
                    "DomainComplete": DomainComplete,
                    "ExecutableRepairAllowed": False,
                },
            },
        )) from Error
    raise RuntimeError(
        "cluster interface preparation returned without an assignment"
    )


def PrepareTrackAssignment(
    Placement: PcbPlacement,
    *,
    Resources: Any,
    Policy: PhysicalDesignPolicy,
    Deadline: RoutingDeadline,
) -> TrackAssignmentPreparation:
    """Build portal/track domains and stop before route-tree construction."""
    Configuration = BuildPcbRoutingConfigurations(Placement)[0]
    try:
        RoutePcbAttempt(
            Placement,
            Configuration,
            Resources=Resources,
            Policy=Policy,
            Deadline=Deadline,
            PrepareTrackAssignmentOnly=True,
        )
    except TrackAssignmentPrepared as Prepared:
        return Prepared.Preparation
    raise RuntimeError(
        "track assignment preparation returned without a capacity result"
    )


def PrepareRawTrackAssignmentDomain(
    Placement: PcbPlacement,
    *,
    Resources: Any,
    Policy: PhysicalDesignPolicy,
    Deadline: RoutingDeadline,
) -> RawTrackAssignmentDomain:
    """Materialize one exact candidate domain without solving it yet.

    This is the bridge between a fixed placement portfolio and the aggregate
    raw-template selector.  It does not route, retry, or choose a track
    assignment; it only freezes the exact values the existing authoritative
    preparer would otherwise immediately submit to Rust.
    """
    Configuration = BuildPcbRoutingConfigurations(Placement)[0]
    try:
        RoutePcbAttempt(
            Placement,
            Configuration,
            Resources=Resources,
            Policy=Policy,
            Deadline=Deadline,
            PrepareRawTrackAssignmentDomainOnly=True,
        )
    except RawTrackAssignmentDomainPrepared as Prepared:
        return Prepared.Domain
    raise RuntimeError(
        "raw track-assignment preparation returned without a domain"
    )


def PrepareComponentRoutingProblem(
    Placement: PcbPlacement,
    *,
    Resources: Any,
    Policy: PhysicalDesignPolicy,
    Deadline: RoutingDeadline,
    StateFingerprint: str = "",
    LocalRouteFingerprint: str = "",
    ProgressCallback: Callable[[int, int], None] | None = None,
    StatusCallback: Callable[[str], None] | None = None,
) -> ComponentRoutingProblem:
    """Prepare complete finite component domains without route ownership."""
    Resources.PreparedComponentRoutingProblem = None
    Configuration = BuildPcbRoutingConfigurations(Placement)[0]
    try:
        RoutePcbAttempt(
            replace(Placement, ClusterBoundaryLeaseVariant=0),
            Configuration,
            Resources=Resources,
            ProgressCallback=ProgressCallback,
            StatusCallback=StatusCallback,
            Policy=Policy,
            Deadline=Deadline,
            PrepareComponentRoutingProblemOnly=True,
            RequireCompleteClusterInterfaceDomain=True,
            ClusterInterfaceStateFingerprint=StateFingerprint,
            ClusterInterfaceLocalRouteFingerprint=(
                LocalRouteFingerprint
            ),
        )
    except ComponentRoutingProblemPrepared as Prepared:
        if Resources.PreparedComponentRoutingProblem is not Prepared.Problem:
            raise RuntimeError(
                "component problem preparation lost its typed result"
            )
        return Prepared.Problem
    raise RuntimeError(
        "component problem preparation returned a routed design"
    )


def PreparePhysicalComponentEligibility(
    Placement: PcbPlacement,
    *,
    Resources: Any,
    Policy: PhysicalDesignPolicy,
    Deadline: RoutingDeadline,
    StateFingerprint: str = "",
    LocalRouteFingerprint: str = "",
    DeferClusterBoundaryLeaseUntilCapacityPrecheck: bool = False,
    ProgressCallback: Callable[[int, int], None] | None = None,
    StatusCallback: Callable[[str], None] | None = None,
    UnboundOwnedSignalFrontierProofCallback: Callable[
        [ComponentRoutingProblem], None
    ] | None = None,
) -> PreparedPhysicalComponentPortFactorDomain:
    """Freeze the complete physical port domain before assignment search."""
    Resources.PreparedPhysicalComponentPortFactorDomain = None
    Resources.PreparedPhysicalComponentAssembly = None
    Resources.PreparedPhysicalComponentUnboundProblem = None
    Resources.PreparedComponentRoutingProblem = None
    Resources.PreparedClusterInterfaceAssignment = None
    Resources.FrozenClusterInterfaceAssignment = None
    Resources.FrozenPhysicalComponentAssemblyPlan = None
    Resources.FrozenPhysicalComponentGlobalGuidePlan = None
    Resources.FrozenPreparedPortalDomainCache = None
    Resources.PreparedPortalDomainCaches = ()
    Configuration = BuildPcbRoutingConfigurations(Placement)[0]
    Prepared = RoutePcbAttempt(
        replace(Placement, ClusterBoundaryLeaseVariant=0),
        Configuration,
        Resources=Resources,
        ProgressCallback=ProgressCallback,
        StatusCallback=StatusCallback,
        Policy=Policy,
        Deadline=Deadline,
        PreparePhysicalComponentAssemblyOnly=True,
        PreparePhysicalComponentPortFactorDomainOnly=True,
        DeferClusterBoundaryLeaseUntilCapacityPrecheck=(
            DeferClusterBoundaryLeaseUntilCapacityPrecheck
        ),
        UnboundOwnedSignalFrontierProofCallback=(
            UnboundOwnedSignalFrontierProofCallback
        ),
        RequireCompleteClusterInterfaceDomain=True,
        ClusterInterfaceStateFingerprint=StateFingerprint,
        ClusterInterfaceLocalRouteFingerprint=LocalRouteFingerprint,
    )
    if not isinstance(Prepared, PreparedPhysicalComponentPortFactorDomain):
        raise RuntimeError(
            "physical component eligibility lost its typed factor domain"
        )
    if Resources.PreparedPhysicalComponentPortFactorDomain is not Prepared:
        raise RuntimeError(
            "physical component eligibility resource identity mismatch"
        )
    Resources.PreparedPhysicalComponentUnboundProblem = Prepared.Problem
    Resources.PreparedComponentAccessCertificate = (
        Prepared.AccessCertificate
    )
    Resources.FrozenPhysicalComponentGlobalGuidePlan = Prepared.CoarsePlan
    return Prepared


def SolvePreparedPhysicalComponentEligibility(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    *,
    Resources: Any,
    Deadline: RoutingDeadline,
    DeferLocalCompositeSelection: bool = True,
    RequiredBoundaryPorts: tuple[Any, ...] | None = None,
) -> PreparedPhysicalComponentAssembly:
    """Solve one identity-validated eligibility domain without rebuilding it."""
    from .Authoritative.PortSolving import (
        SolvePreparedPhysicalComponentPortFactorDomain,
    )

    try:
        Assembly = SolvePreparedPhysicalComponentPortFactorDomain(
            Preparation,
            Resources,
            Deadline=Deadline,
            DeferLocalCompositeSelection=DeferLocalCompositeSelection,
            RequiredBoundaryPorts=RequiredBoundaryPorts,
            WorkCheck=lambda Diagnostics: Deadline.RaiseIfExpired(
                "PhysicalComponentAssembly",
                Diagnostics,
            ),
        )
    except RoutingStageError as Error:
        Diagnostics = {
            **dict(Error.Failure.Diagnostics or {}),
            "DomainFingerprint": Preparation.DomainFingerprint,
            "PreparedFactorDomainReused": True,
        }
        Classified = ClassifyPhysicalComponentAssemblyFailure(
            RoutingStageError(replace(
                Error.Failure,
                Diagnostics=Diagnostics,
            )),
            Operation="solve-prepared-eligibility",
            Resources=Resources,
        )
        raise Classified from Error
    Resources.PreparedComponentRoutingProblem = Assembly.Problem
    Resources.PreparedPhysicalComponentAssembly = Assembly
    Resources.FrozenPhysicalComponentAssemblyPlan = Assembly.Plan
    Resources.FrozenPhysicalComponentGlobalGuidePlan = (
        Assembly.GlobalGuidePlan
    )
    return Assembly


def PreparePhysicalComponentAssembly(
    Placement: PcbPlacement,
    *,
    Resources: Any,
    Policy: PhysicalDesignPolicy,
    Deadline: RoutingDeadline,
    StateFingerprint: str = "",
    LocalRouteFingerprint: str = "",
    ProgressCallback: Callable[[int, int], None] | None = None,
    StatusCallback: Callable[[str], None] | None = None,
) -> PreparedPhysicalComponentAssembly:
    """Prepare authoritative global ports and corridors before local solve."""
    Resources.PreparedPhysicalComponentAssembly = None
    Resources.PreparedPhysicalComponentUnboundProblem = None
    Resources.PreparedComponentRoutingProblem = None
    Resources.PreparedClusterInterfaceAssignment = None
    Resources.FrozenClusterInterfaceAssignment = None
    Resources.FrozenPhysicalComponentAssemblyPlan = None
    Resources.FrozenPhysicalComponentGlobalGuidePlan = None
    # The prior cluster-interface cache intentionally contains only the
    # component slice and has no whole-design guide. Port-first preparation
    # must rebuild the complete authoritative domain.
    Resources.FrozenPreparedPortalDomainCache = None
    Resources.PreparedPortalDomainCaches = ()
    Configuration = BuildPcbRoutingConfigurations(Placement)[0]
    try:
        RoutePcbAttempt(
            replace(Placement, ClusterBoundaryLeaseVariant=0),
            Configuration,
            Resources=Resources,
            ProgressCallback=ProgressCallback,
            StatusCallback=StatusCallback,
            Policy=Policy,
            Deadline=Deadline,
            PreparePhysicalComponentAssemblyOnly=True,
            RequireCompleteClusterInterfaceDomain=True,
            ClusterInterfaceStateFingerprint=StateFingerprint,
            ClusterInterfaceLocalRouteFingerprint=(
                LocalRouteFingerprint
            ),
        )
    except PhysicalComponentAssemblyPrepared as Prepared:
        if (
            Resources.PreparedPhysicalComponentAssembly
            is not Prepared.Assembly
        ):
            raise RuntimeError(
                "physical component assembly lost its typed result"
            )
        Resources.FrozenPhysicalComponentAssemblyPlan = (
            Prepared.Assembly.Plan
        )
        return Prepared.Assembly
    except RoutingStageError as Error:
        Classified = ClassifyPhysicalComponentAssemblyFailure(
            Error,
            Operation="prepare",
            Resources=Resources,
        )
        if Classified is Error:
            raise
        raise Classified from Error
    raise RuntimeError(
        "physical component assembly preparation returned a routed design"
    )


def ClassifyPhysicalComponentAssemblyFailure(
    Error: RoutingStageError,
    *,
    Operation: str,
    Resources: Any | None = None,
) -> RoutingStageError:
    """Preserve physical-plan incompleteness across the router adapter."""
    if Error.Failure.Reason not in {
        RoutingFailureReason.RuntimeBudgetExceeded,
        RoutingFailureReason.ClusterInterfaceSolveIncomplete,
        RoutingFailureReason.PhysicalComponentAssemblyIncomplete,
    }:
        return Error
    Diagnostics = dict(Error.Failure.Diagnostics or {})
    FactorStage = str(
        Diagnostics.get("Stage", Error.Failure.Stage)
    )
    FactorDiagnostics = {
        Key: Diagnostics[Key]
        for Key in (
            "AssignedPortCount",
            "PortCount",
            "AssignedTerminalCount",
            "TerminalCount",
            "ExpansionCount",
            "FactorExpansionCount",
            "PortAssignmentExpansionCount",
            "DomainFingerprint",
            "PreparedFactorDomainReused",
        )
        if Key in Diagnostics
    }
    RejectedSignalReservations = {
        str(Signal): sorted(map(str, Fingerprints))
        for Signal, Fingerprints in sorted(
            getattr(
                Resources,
                "RejectedPhysicalComponentPortReservationsBySignal",
                {},
            ).items()
        )
        if Fingerprints
    }
    FactorDiagnostics.update({
        "RejectedSignalReservationFingerprintsBySignal": (
            RejectedSignalReservations
        ),
        "RejectedSignalReservationCount": sum(
            len(Fingerprints)
            for Fingerprints in RejectedSignalReservations.values()
        ),
        "RejectedPortAssignmentFingerprints": sorted(map(
            str,
            getattr(
                Resources,
                "RejectedPhysicalComponentPortAssignmentFingerprints",
                (),
            ),
        )),
    })
    return RoutingStageError(replace(
        Error.Failure,
        Reason=(
            RoutingFailureReason
            .PhysicalComponentAssemblyIncomplete
        ),
        Stage="PhysicalComponentAssemblyIncomplete",
        RepairActions=(),
        Diagnostics={
            **Diagnostics,
            "PhysicalComponentAssemblyClassification": {
                "Operation": Operation,
                "ActiveFactorStage": FactorStage,
                "Complete": False,
                "FactorDiagnostics": FactorDiagnostics,
                "ExecutableRetryAllowed": False,
                "FlatFallbackAllowed": False,
                "SignalLevelFallbackAllowed": False,
            },
        },
    ))


def ReplanPhysicalComponentAssembly(
    Placement: PcbPlacement,
    *,
    Resources: Any,
    Deadline: RoutingDeadline,
    RequiredGlobalBoundaryPorts: tuple[Any, ...] | None = None,
) -> PreparedPhysicalComponentAssembly:
    """Select the next physical plan from the frozen authoritative domain."""
    Preparation = Resources.PreparedPhysicalComponentPortFactorDomain
    if Preparation is None:
        raise RuntimeError(
            "physical component replanning requires a frozen complete "
            "port factor domain"
        )
    try:
        Assembly = SolvePreparedPhysicalComponentEligibility(
            Preparation,
            Resources=Resources,
            Deadline=Deadline,
            DeferLocalCompositeSelection=(
                RequiredGlobalBoundaryPorts is None
            ),
            RequiredBoundaryPorts=RequiredGlobalBoundaryPorts,
        )
    except RoutingStageError as Error:
        Classified = ClassifyPhysicalComponentAssemblyFailure(
            Error,
            Operation="replan",
            Resources=Resources,
        )
        if Classified is Error:
            raise
        raise Classified from Error
    Resources.PreparedComponentRoutingProblem = Assembly.Problem
    Resources.PreparedPhysicalComponentAssembly = Assembly
    Resources.FrozenPhysicalComponentAssemblyPlan = Assembly.Plan
    return Assembly


def ValidateClusterInterfaceForeignAccess(
    Placement: PcbPlacement,
    *,
    Resources: Any,
    Assignment: ClusterInterfaceAssignment,
    Policy: PhysicalDesignPolicy,
    Deadline: RoutingDeadline,
) -> dict[str, object]:
    """Verify the frozen component leaves every global terminal escapable."""
    if Resources.FrozenPreparedPortalDomainCache is None:
        raise RuntimeError(
            "foreign-access validation requires a frozen portal-domain cache"
        )
    Resources.FrozenClusterInterfaceAssignment = Assignment
    Configuration = BuildPcbRoutingConfigurations(Placement)[0]
    try:
        RoutePcbAttempt(
            replace(Placement, ClusterBoundaryLeaseVariant=0),
            Configuration,
            Resources=Resources,
            Policy=Policy,
            Deadline=Deadline,
            ValidateClusterInterfaceForeignAccessOnly=True,
        )
    except RoutingStageError as Error:
        if (
            Error.Failure.Stage
            == "ClusterInterfaceForeignAccessValidated"
        ):
            return dict(Error.Failure.Diagnostics or {})
        raise
    raise RuntimeError(
        "foreign-access validation returned without a typed result"
    )


def ValidatePhysicalComponentForeignPortalSupport(
    Placement: PcbPlacement,
    *,
    Resources: Any,
    Policy: PhysicalDesignPolicy,
    Deadline: RoutingDeadline,
) -> dict[str, object]:
    """Prove the frozen exterior leaves an access option per foreign pin."""
    Configuration = BuildPcbRoutingConfigurations(Placement)[0]
    try:
        RoutePcbAttempt(
            Placement,
            Configuration,
            Resources=Resources,
            Policy=Policy,
            Deadline=Deadline,
            ValidatePhysicalComponentForeignPortalSupportOnly=True,
            RequireCompleteClusterInterfaceDomain=True,
        )
    except RoutingStageError as Error:
        if Error.Failure.Stage == (
            "PhysicalComponentForeignPortalSupportValidated"
        ):
            return dict(Error.Failure.Diagnostics or {})
        raise
    raise RuntimeError(
        "physical foreign-portal validation returned without a typed result"
    )


def RoutePcbDesign(
    Placement: PcbPlacement,
    ProgressCallback: Callable[
        [int, int, int, int, int, RoutedDesign | None, str],
        None,
    ] | None = None,
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy,
    Deadline: RoutingDeadline | None = None,
    Resources: Any | None = None,
    RequireCompleteClusterInterfaceDomain: bool = False,
    FrozenTrackAssignmentPreparation: TrackAssignmentPreparation | None = None,
) -> RoutedDesign:
    """Run one strict guided route and fail immediately if it is illegal."""
    Configuration = BuildPcbRoutingConfigurations(Placement)[0]
    NativeThreadCount = GetRoutingThreadCount()
    Completed = 0
    Total = Configuration.MaximumIterations
    Stage = "building authoritative route assignment"

    def ReportProgress(
        Valid: int = 0,
        Failed: int = 0,
        Routed: RoutedDesign | None = None,
    ) -> None:
        if ProgressCallback is not None:
            ProgressCallback(
                Completed,
                Total,
                NativeThreadCount,
                Valid,
                Failed,
                Routed,
                Stage,
            )

    def RecordProgress(Current: int, Maximum: int) -> None:
        nonlocal Completed, Total
        Completed = Current
        Total = Maximum
        ReportProgress()

    def RecordStatus(CurrentStage: str) -> None:
        nonlocal Stage
        Stage = CurrentStage
        ReportProgress()

    ReportProgress()
    if Deadline is not None:
        Deadline.RaiseIfExpired(
            "RoutingResourceConstruction",
            {"Phase": "before"},
        )
    if Resources is None:
        Resources = BuildRoutingResources(
            Placement.Placed,
            WorkCheck=(
                (
                    lambda Diagnostics: Deadline.RaiseIfExpired(
                        "RoutingResourceConstruction",
                        Diagnostics,
                    )
                )
                if Deadline is not None
                else None
            ),
        )
    if Deadline is not None:
        Deadline.RaiseIfExpired(
            "RoutingResourceConstruction",
            {"Phase": "after"},
        )
    # Dense interfaces receive a bounded whole-state portfolio. The first
    # state pays immutable portal/guide setup; later states reuse it through
    # RoutingResources.RawPortalGeometryCaches and split the remainder.
    LeaseStateCount = (
        1
        if Resources.FrozenClusterInterfaceAssignment is not None
        else ClusterBoundaryLeaseStateCount(
            Placement,
            HasFrozenTrackAssignment=(
                FrozenTrackAssignmentPreparation is not None
            ),
        )
    )
    LeaseFailures: list[RoutingStageError] = []
    LeaseAttemptDiagnostics: list[dict[str, object]] = []
    Routed = None
    try:
        for LeaseVariant in range(LeaseStateCount):
            VariantPlacement = replace(
                Placement,
                ClusterBoundaryLeaseVariant=LeaseVariant,
            )
            VariantDeadline = Deadline
            if Deadline is not None and LeaseStateCount > 1:
                RemainingStates = LeaseStateCount - LeaseVariant
                SliceFraction = (
                    0.55
                    if LeaseVariant == 0
                    # The first cached follow-up proves one ownership state
                    # quickly. Keep the larger remainder for the final
                    # access-distinct state, which must still materialize its
                    # own exact lease and candidate assignment.
                    else 0.35
                    if RemainingStates > 1
                    else 1.0
                )
                VariantDeadline = RoutingDeadline(
                    StartedAt=Deadline.StartedAt,
                    ExpiresAt=min(
                        Deadline.ExpiresAt,
                        monotonic() + min(
                            max(
                                0.001,
                                Deadline.RemainingSeconds()
                                - ClusterBoundaryLeaseEndgameReserveSeconds(
                                    LeaseStateCount,
                                ),
                            ) * SliceFraction,
                            ClusterBoundaryLeaseStateSliceSeconds(
                                LeaseStateCount,
                                LeaseVariant,
                            ) or Deadline.RemainingSeconds(),
                        ),
                    ),
                )
            try:
                VariantPlacement.Placed.LocalRouteDiagnostics = {
                    **(VariantPlacement.Placed.LocalRouteDiagnostics or {}),
                    "__ClusterBoundaryLeaseScheduler__": {
                        "Variant": LeaseVariant,
                        "StateCount": LeaseStateCount,
                        "EndgameReserveSeconds": (
                            ClusterBoundaryLeaseEndgameReserveSeconds(
                                LeaseStateCount,
                            )
                        ),
                    },
                }
                VariantPolicy = Policy
                if VariantDeadline is not None and LeaseStateCount > 1:
                    VariantSeconds = VariantDeadline.RemainingSeconds()
                    VariantPolicy = replace(
                        Policy,
                        RuntimeBudgetSeconds=min(
                            Policy.RuntimeBudgetSeconds,
                            VariantSeconds,
                        ),
                        AdaptiveRouting=replace(
                            Policy.AdaptiveRouting,
                            MaximumRuntimeSeconds=min(
                                Policy.AdaptiveRouting.MaximumRuntimeSeconds,
                                VariantSeconds,
                            ),
                        ),
                    )
                Routed = RoutePcbAttempt(
                    VariantPlacement,
                    Configuration,
                    Resources=Resources,
                    ProgressCallback=RecordProgress,
                    StatusCallback=RecordStatus,
                    Policy=VariantPolicy,
                    Deadline=VariantDeadline,
                    RequireCompleteClusterInterfaceDomain=(
                        RequireCompleteClusterInterfaceDomain
                    ),
                    FrozenTrackAssignmentPreparation=(
                        FrozenTrackAssignmentPreparation
                    ),
                    PreparePortalGeometryOnly=(
                        LeaseStateCount > 1 and LeaseVariant == 0
                    ),
                )
                break
            except RoutingStageError as Error:
                if Error.Failure.Stage == "PortalGeometryPreparation":
                    LeaseAttemptDiagnostics.append({
                        "Variant": LeaseVariant,
                        "StateCount": LeaseStateCount,
                        "Status": "portal-geometry-prepared",
                        **dict(Error.Failure.Diagnostics or {}),
                    })
                    continue
                LeaseFailures.append(Error)
                FailureDiagnostics = dict(Error.Failure.Diagnostics or {})
                PatternSearchDiagnostics = dict(
                    FailureDiagnostics.get(
                        "ClusterInterfacePatternSearch",
                        {},
                    )
                )
                FailedAccessDomainFingerprint = str(
                    FailureDiagnostics.get(
                        "AuthoritativeCutAccessDomainFingerprint",
                        PatternSearchDiagnostics.get(
                            "AuthoritativeCutAccessDomainFingerprint",
                            "",
                        ),
                    )
                )
                LeaseAttemptDiagnostics.append({
                    "Variant": LeaseVariant,
                    "StateCount": LeaseStateCount,
                    "FailureReason": Error.Failure.Reason.value,
                    "FailureStage": Error.Failure.Stage,
                    "AffectedNets": list(Error.Failure.AffectedNets),
                    "RawPortalCacheHit": bool(
                        FailureDiagnostics.get("PortalCacheHit", False)
                    ),
                    "GlobalGuidePlanCacheHit": bool(
                        FailureDiagnostics.get(
                            "GlobalGuidePlanCacheHit",
                            False,
                        )
                    ),
                    "OwnershipFingerprint": str(
                        Resources.ClusterBoundaryLeaseOwnershipFingerprints.get(
                            LeaseVariant,
                            dict(
                                FailureDiagnostics.get(
                                    "ClusterBoundaryLeases",
                                    {},
                                )
                            ).get(
                                "OwnershipFingerprint",
                                FailedAccessDomainFingerprint,
                            ),
                        )
                    ),
                    "AuthoritativeAccessDomainFingerprint": str(
                        FailureDiagnostics.get(
                            "AuthoritativeAccessDomainFingerprint",
                            PatternSearchDiagnostics.get(
                                "AuthoritativeAccessDomainFingerprint",
                                "",
                            ),
                        )
                    ),
                    "AuthoritativeCutAccessDomainFingerprint": (
                        FailedAccessDomainFingerprint
                    ),
                    "Deadline": (
                        VariantDeadline.ToDictionary()
                        if VariantDeadline is not None
                        else None
                    ),
                })
                if Deadline is None or Deadline.IsExpired():
                    break
        if Routed is None:
            LastFailure = LeaseFailures[-1].Failure
            raise RoutingStageError(replace(
                LastFailure,
                Diagnostics={
                    **dict(LastFailure.Diagnostics or {}),
                    "ClusterBoundaryLeaseScheduler": {
                        "StateCount": LeaseStateCount,
                        "Attempts": LeaseAttemptDiagnostics,
                    },
                },
            ))
        if Deadline is not None:
            Deadline.RaiseIfExpired("Routing")
    except RoutingStageError as Error:
        Stage = "failed | " + str(Error)
        ReportProgress(Failed=1)
        raise
    except Exception as Error:
        Stage = "failed | " + str(Error).split("; cells:", 1)[0]
        ReportProgress(Failed=1)
        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
            traceback.print_exc()
        raise ValueError(f"PCB authoritative router failed: {Error}") from Error

    Completed = Total
    Stage = "complete"
    ReportProgress(Valid=1, Routed=Routed)
    return Routed

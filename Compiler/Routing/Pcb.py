"""Authoritative multilayer routing for PCB-style NAND placement."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import replace
from typing import Any, Callable

from ..Placement.Pcb import PcbPlacement
from ..Placement.Geometry import GetGateInputAccess
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
from .Models import RoutedDesign
from .Failures import RoutingStageError
from .Failures import RoutingFailure, RoutingFailureReason
from .ResourceGraph import (
    FindClaimConflicts,
    NormalizeRoutingEdge,
    RoutingResourceKind,
)
from .TrackAssignment import TrackAssignment
from .Policy import (
    BuildRoutingAttemptPolicies,
    DefaultPhysicalDesignPolicy,
    PhysicalDesignPolicy,
    RoutingAttemptPolicy,
)
from .Workers.DetailedRouting import RoutePcbNets

def CompactRoutedTrees(
    Placement: PcbPlacement,
    Routed: RoutedDesign,
    AccessLength: int = 3,
    Resources: Any | None = None,
) -> RoutedDesign:
    """Remove routed loops and branches that reach no required terminal."""
    Placed = Placement.Placed
    if Resources is None:
        Resources = BuildRoutingResources(Placed)
    ActualBlocks = set(Resources.StaticGeometry.ActualBlocks)
    ElectricalBlocks = set(Resources.StaticGeometry.ElectricalBlocks)
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
    Graphs = BuildPhysicalGraphs(
        NetWires,
        ActualBlocks,
        set(Routed.Supports),
        SolidBlocks,
    )
    for Signal, Graph in Graphs.items():
        Root = Producers[Signal].OutputPin
        Parents = {Root: None}
        Pending = deque([Root])
        while Pending:
            Current = Pending.popleft()
            for Neighbor in Graph[Current]:
                if Neighbor in Parents:
                    continue
                Parents[Neighbor] = Current
                Pending.append(Neighbor)
        Required = {Root}
        ReservedPositions = (
            tuple(
                Reservation.Position
                for Reservation in Routed.TrackAssignment.Tracks.get(
                    Signal,
                    type("EmptyTrack", (), {"RepeaterReservations": ()})(),
                ).RepeaterReservations
            )
            if Routed.TrackAssignment is not None
            else ()
        )
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
        for Target in (
            *Targets.get(Signal, ()),
            *ReservedPositions,
            *PinAccessPositions,
        ):
            if Target not in Parents:
                raise ValueError(f"PCB route compaction disconnected net {Signal}")
            Current = Target
            while Current is not None:
                Required.add(Current)
                Current = Parents[Current]
        NetWires[Signal].intersection_update(Required)

    Supports = {
        (X, Y - 1, Z)
        for Positions in NetWires.values()
        for X, Y, Z in Positions
    } - ActualBlocks
    PhysicalGraphs = BuildPhysicalGraphs(
        NetWires,
        ActualBlocks,
        Supports,
        SolidBlocks,
    )
    ValidatePhysicalRoutes(PhysicalGraphs, Producers, Targets)
    ValidateTemplateIsolation(
        NetWires,
        ActualBlocks,
        ElectricalBlocks,
        SolidBlocks,
        Producers,
        Targets,
        AccessBySignal,
    )
    TrackAssignmentValue = Routed.TrackAssignment
    if TrackAssignmentValue is None:
        raise ValueError("Routed design is missing authoritative track assignment")
    FinalClaims = {
        Signal: Resources.ResourceGraph.BuildRouteClaims(Positions)
        for Signal, Positions in NetWires.items()
    }
    FinalConflicts = FindClaimConflicts(FinalClaims)
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
    for Signal, Track in TrackAssignmentValue.Tracks.items():
        ReservedResources = frozenset(
            Resource
            for Resource in FinalClaims[Signal].ResourceIds
            if Resource.Kind != RoutingResourceKind.Electrical
        )
        for Resource in ReservedResources:
            FinalOwners[Resource].append(Signal)
        Graph = PhysicalGraphs[Signal]
        FinalTracks[Signal] = replace(
            Track,
            ReservedResources=ReservedResources,
            OwnedNodes=frozenset(NetWires[Signal]),
            OwnedEdges=frozenset(
                NormalizeRoutingEdge(Position, Neighbor)
                for Position, Neighbors in Graph.items()
                for Neighbor in Neighbors
                if Position < Neighbor
            ),
        )
    TrackAssignmentValue = TrackAssignment(
        Tracks=FinalTracks,
        ResourceOwners={
            Resource: tuple(Signals)
            for Resource, Signals in FinalOwners.items()
        },
    )
    Repeaters = MaterializeReservedRepeaters(
        NetWires,
        Producers,
        Targets,
        PhysicalGraphs,
        TrackAssignmentValue.Tracks,
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
        RepeaterReservationCount=Routed.RepeaterReservationCount,
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
    )


def RoutePcbAttempt(
    Placement: PcbPlacement,
    Configuration: RoutingAttemptPolicy,
    Resources: Any | None = None,
    ProgressCallback: Callable[[int, int], None] | None = None,
    StatusCallback: Callable[[str], None] | None = None,
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy,
) -> RoutedDesign:
    """Run the single authoritative routing configuration."""
    SearchMargin = Configuration.SearchMargin
    GuidePenalty = Configuration.GuidePenalty
    DetourRatio = Configuration.MaximumDetourRatio
    DetourAllowance = Configuration.MaximumDetourAllowance
    Iterations = Configuration.MaximumIterations
    OrderMode = Configuration.OrderMode
    if Resources is None:
        Resources = BuildRoutingResources(Placement.Placed)
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
            LastCompleted = max(LastCompleted, Completed)
            StageName = {
                0: "authoritative resource graph",
                1: "capacity-aware global guide planning",
                2: "authoritative portal generation",
                3: "guide-constrained route candidate generation",
                4: "authoritative capacity-one assignment",
                5: "authoritative validation and materialization",
            }.get(Completed, "authoritative routing")
            ReportStatus(StageName)
            if ProgressCallback is not None:
                ProgressCallback(
                    CompletedRoutingPasses + Completed,
                    CompletedRoutingPasses + Total,
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
    LocalNetBranches = Placement.Placed.LocalNetBranches or {}
    CompactionAccessLength = (
        max(AccessLength, 3)
        if (Placement.Placed.LocalRouteClaims or ())
        else AccessLength
    )
    ReportStatus(f"compacting access length {CompactionAccessLength}")
    Routed = CompactRoutedTrees(
        Placement,
        Routed,
        AccessLength=CompactionAccessLength,
        Resources=Resources,
    )
    if FrozenNetWires:
        NetWires = {
            Signal: set(Positions)
            for Signal, Positions in Routed.NetWires.items()
        }
        NetWires.update(
            {
                Signal: set(Positions)
                for Signal, Positions in FrozenNetWires.items()
            }
        )
        Conflicts, _ConflictCounts = FindFlatRouteConflicts(NetWires)
        if Conflicts:
            raise ValueError(
                "Frozen local NAND routes conflict with detailed routes: "
                + ", ".join(map(str, sorted(Conflicts)))
            )
        Routed.NetWires = {
            Signal: sorted(Positions)
            for Signal, Positions in sorted(NetWires.items())
        }
        Routed.Wires = sorted(set().union(*NetWires.values()))
        Routed.Supports = sorted(
            {
                (X, Y - 1, Z)
                for Positions in NetWires.values()
                for X, Y, Z in Positions
            }
        )
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
        FinalClaims = {
            Signal: Resources.ResourceGraph.BuildRouteClaims(Positions)
            for Signal, Positions in NetWires.items()
        }
        ClaimConflicts = FindClaimConflicts(FinalClaims)
        if ClaimConflicts:
            Resource = min(ClaimConflicts, key=str)
            raise ValueError(
                f"Merged local ownership conflicts at {Resource}: "
                + ",".join(ClaimConflicts[Resource])
            )
        Owners = defaultdict(list)
        for Signal, Claims in FinalClaims.items():
            for Resource in Claims.ResourceIds:
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
    return Routed

def BuildPcbRoutingConfigurations(
    Placement: PcbPlacement,
) -> tuple[RoutingAttemptPolicy, ...]:
    """Return the single authoritative routing attempt."""
    del Placement
    return BuildRoutingAttemptPolicies()


def RoutePcbDesign(
    Placement: PcbPlacement,
    ProgressCallback: Callable[
        [int, int, int, int, int, RoutedDesign | None, str],
        None,
    ] | None = None,
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy,
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
    Resources = BuildRoutingResources(Placement.Placed)
    try:
        Routed = RoutePcbAttempt(
            Placement,
            Configuration,
            Resources=Resources,
            ProgressCallback=RecordProgress,
            StatusCallback=RecordStatus,
            Policy=Policy,
        )
    except RoutingStageError as Error:
        Stage = "failed | " + str(Error)
        ReportProgress(Failed=1)
        raise
    except Exception as Error:
        Stage = "failed | " + str(Error).split("; cells:", 1)[0]
        ReportProgress(Failed=1)
        raise ValueError(f"PCB authoritative router failed: {Error}") from Error

    Completed = Total
    Stage = "complete"
    ReportProgress(Valid=1, Routed=Routed)
    return Routed

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
from .Reliability import RoutingDeadline
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
        Pending = deque([Root])
        ExpandedNodes = 0
        while Pending:
            Current = Pending.popleft()
            ExpandedNodes += 1
            if ExpandedNodes % 256 == 0:
                CheckDeadline(
                    "prune-expansion",
                    Signal=Signal,
                    ExpandedNodes=ExpandedNodes,
                )
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
            CheckDeadline("retain-required-path", Signal=Signal)
            if Target not in Parents:
                raise ValueError(f"PCB route compaction disconnected net {Signal}")
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
    Repeaters = MaterializeReservedRepeaters(
        NetWires,
        Producers,
        Targets,
        PhysicalGraphs,
        TrackAssignmentValue.Tracks,
        WorkCheck=CheckWork("repeater-materialization"),
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
    Deadline: RoutingDeadline | None = None,
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
    GuidePenalty = Configuration.GuidePenalty
    DetourRatio = Configuration.MaximumDetourRatio
    DetourAllowance = Configuration.MaximumDetourAllowance
    Iterations = Configuration.MaximumIterations
    OrderMode = Configuration.OrderMode
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
                3: "guide-constrained route candidate generation",
                4: "authoritative capacity-one assignment",
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


def RoutePcbDesign(
    Placement: PcbPlacement,
    ProgressCallback: Callable[
        [int, int, int, int, int, RoutedDesign | None, str],
        None,
    ] | None = None,
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy,
    Deadline: RoutingDeadline | None = None,
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
    try:
        Routed = RoutePcbAttempt(
            Placement,
            Configuration,
            Resources=Resources,
            ProgressCallback=RecordProgress,
            StatusCallback=RecordStatus,
            Policy=Policy,
            Deadline=Deadline,
        )
        if Deadline is not None:
            Deadline.RaiseIfExpired("Routing")
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

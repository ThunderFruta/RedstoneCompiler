"""Topology demand analysis and placement generation planning."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    replace,
)
from typing import (
    Any,
    Callable,
)
from Compiler.Routing.Failures import (
    RoutingFailure,
)
from Compiler.Routing.Reliability import (
    BuildStableFingerprint,
)
from Compiler.Routing.Policy import (
    PhysicalDesignPolicy,
)
from Compiler.Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)
from Compiler.Placement.Rotation import (
    RotatedCellSize,
)
from Compiler.Placement.Core.Clusters import (
    PcbPlacement,
)
from Compiler.Placement.Core.Compactness import (
    BuildPinAlignedPackedClusterPortfolio,
)
from Compiler.Placement.Core.MandatoryAccess import (
    MandatoryAccessConflictProfile,
)
from .Portfolios import (
    PlacementGenerationRequest,
)


@dataclass(frozen=True)
class PlacementGenerationPlan:
    """Bounded primary recipes plus spacing recipes deferred until useful."""

    PrimaryRequests: tuple[PlacementGenerationRequest, ...]
    DeferredRequests: tuple[PlacementGenerationRequest, ...]
    MaximumAttempts: int

PlacementFailureAggregateDiagnosticKeys = frozenset({
    "PlacementCandidates",
    "PlacementGenerationFailures",
    "PlacementGenerationDecisions",
    "PlacementAttempts",
    "JointPlacementStateEvents",
    "AssignmentCutHistory",
    "CurrentAssignmentCut",
    "ActivePlacementConstraints",
    "CoordinatedCandidateDiversificationSignals",
})

def BuildPlacementFailureHistorySnapshot(
    Failure: RoutingFailure,
) -> dict[str, object]:
    """Snapshot one failure without recursively embedding outer histories."""
    Snapshot = Failure.ToDictionary()
    Diagnostics = Snapshot.get("Diagnostics")
    if isinstance(Diagnostics, dict):
        Snapshot["Diagnostics"] = {
            Key: Value
            for Key, Value in Diagnostics.items()
            if Key not in PlacementFailureAggregateDiagnosticKeys
        }
    return Snapshot

@dataclass(frozen=True)
class TopologyDemandProfile:
    """Name-independent logical and exact-placement routing pressure."""

    MaximumFanout: int
    ReconvergentCutCount: int
    QualifyingReconvergentCutCount: int
    MaximumReconvergentFanout: int
    PeakBoundaryDemand: int
    InputTerminalCount: int = 0
    OutputTerminalCount: int = 0
    MaximumTerminalBankDemand: int = 0
    SingleCandidateBoundarySignals: tuple[tuple[int, str], ...] = ()
    AccessCandidateScarcity: int = 0
    MandatoryAccessConflictResources: int = 0
    MandatoryAccessConflictSignals: tuple[str, ...] = ()
    MandatoryAccessOwnershipFingerprint: str = ""
    MandatoryAccessConflictFingerprint: str = ""
    GateFootprint: int = 0
    Hpwl: int = 0

    @property
    def HasReconvergentFanoutPressure(self) -> bool:
        return self.QualifyingReconvergentCutCount > 0

    @property
    def RequiresJointPortfolio(self) -> bool:
        return (
            self.HasReconvergentFanoutPressure
            or self.MandatoryAccessConflictResources > 0
        )

    @property
    def EnableInitialJointOrientation(self) -> bool:
        return self.RequiresJointPortfolio

    @property
    def JointOrderKey(self) -> tuple[object, ...]:
        """Rank exact candidates by legality, scarcity, demand, then size."""
        return (
            0 if self.MandatoryAccessConflictResources == 0 else 1,
            len(self.SingleCandidateBoundarySignals),
            self.PeakBoundaryDemand,
            self.GateFootprint,
            self.Hpwl,
            self.MandatoryAccessOwnershipFingerprint,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "MaximumFanout": self.MaximumFanout,
            "ReconvergentCutCount": self.ReconvergentCutCount,
            "QualifyingReconvergentCutCount": (
                self.QualifyingReconvergentCutCount
            ),
            "MaximumReconvergentFanout": (
                self.MaximumReconvergentFanout
            ),
            "PeakBoundaryDemand": self.PeakBoundaryDemand,
            "InputTerminalCount": self.InputTerminalCount,
            "OutputTerminalCount": self.OutputTerminalCount,
            "MaximumTerminalBankDemand": (
                self.MaximumTerminalBankDemand
            ),
            "SingleCandidateBoundarySignals": [
                {
                    "ClusterId": ClusterId,
                    "Signal": Signal,
                }
                for ClusterId, Signal
                in self.SingleCandidateBoundarySignals
            ],
            "AccessCandidateScarcity": self.AccessCandidateScarcity,
            "MandatoryAccessConflictResources": (
                self.MandatoryAccessConflictResources
            ),
            "MandatoryAccessConflictSignals": list(
                self.MandatoryAccessConflictSignals
            ),
            "MandatoryAccessOwnershipFingerprint": (
                self.MandatoryAccessOwnershipFingerprint
            ),
            "MandatoryAccessConflictFingerprint": (
                self.MandatoryAccessConflictFingerprint
            ),
            "GateFootprint": self.GateFootprint,
            "Hpwl": self.Hpwl,
            "HasReconvergentFanoutPressure": (
                self.HasReconvergentFanoutPressure
            ),
            "RequiresJointPortfolio": self.RequiresJointPortfolio,
            "EnableInitialJointOrientation": (
                self.EnableInitialJointOrientation
            ),
            "JointOrderKey": list(self.JointOrderKey),
        }

def ComputeInterfaceStateCountBound(
    SignalCount: int,
    TopologyDemand: TopologyDemandProfile,
    GateCount: int,
) -> int:
    """Derive a bounded interface candidate cap from deterministic metrics."""
    if SignalCount < 0:
        raise ValueError("SignalCount must be non-negative")
    if GateCount < 0:
        raise ValueError("GateCount must be non-negative")
    if TopologyDemand.MaximumFanout < 0:
        raise ValueError("MaximumFanout must be non-negative")
    SignalPressure = max(0, min(2, (SignalCount - 20) // 20))
    DemandPressure = 0
    if TopologyDemand.ReconvergentCutCount >= 2:
        DemandPressure += 1
    if TopologyDemand.MaximumFanout >= 4:
        DemandPressure += 1
    if TopologyDemand.MaximumReconvergentFanout >= 4:
        DemandPressure += 1
    if TopologyDemand.MandatoryAccessConflictResources > 0:
        DemandPressure += 1
    if TopologyDemand.PeakBoundaryDemand >= 20:
        DemandPressure += 1
    if GateCount >= 72:
        DemandPressure += 1
    if TopologyDemand.GateFootprint >= 60:
        DemandPressure += 1
    return max(6, min(12, 6 + SignalPressure + DemandPressure))

def ResolveJointPlacementPortfolioTrigger(
    ExistingTrigger: bool,
    Demand: TopologyDemandProfile,
    MandatoryAccessConflictObserved: bool = False,
) -> bool:
    """Latch topology or exact mandatory-access pressure for this flow."""
    return bool(
        ExistingTrigger
        or Demand.RequiresJointPortfolio
        or MandatoryAccessConflictObserved
    )

def ApplyJointPlacementPortfolioTrigger(
    Request: PlacementGenerationRequest,
    Triggered: bool,
) -> PlacementGenerationRequest:
    """Enable the bounded joint portfolio on every later packed request."""
    PackingPolicy = Request.PackingPolicy
    if (
        not Triggered
        or not bool(getattr(PackingPolicy, "Enabled", False))
        or bool(
            getattr(
                PackingPolicy,
                "EnableJointClusterOrientation",
                False,
            )
        )
    ):
        return Request
    return replace(
        Request,
        PackingPolicy=replace(
            PackingPolicy,
            EnableJointClusterOrientation=True,
        ),
    )

@dataclass(frozen=True)
class TopologyDemandPressureProfile:
    """Policy-independent topology evidence classified against one capacity."""

    BoundaryCapacity: int
    ReconvergentAccessPressure: bool
    TerminalBankPressure: bool
    DistributedBoundaryPressure: bool
    ScaleGeometryPressure: bool

    def ToDictionary(self) -> dict[str, object]:
        return {
            "BoundaryCapacity": self.BoundaryCapacity,
            "ReconvergentAccessPressure": (
                self.ReconvergentAccessPressure
            ),
            "TerminalBankPressure": self.TerminalBankPressure,
            "DistributedBoundaryPressure": (
                self.DistributedBoundaryPressure
            ),
            "ScaleGeometryPressure": self.ScaleGeometryPressure,
        }

def BuildTopologyDemandPressureProfile(
    Demand: TopologyDemandProfile,
    BoundaryCapacity: int,
) -> TopologyDemandPressureProfile:
    """Classify reconvergent and broad scale pressure without identities."""
    if BoundaryCapacity < 1:
        raise ValueError("BoundaryCapacity must be positive")
    ReconvergentAccessPressure = Demand.HasReconvergentFanoutPressure
    TerminalBankPressure = (
        Demand.MaximumTerminalBankDemand > BoundaryCapacity
    )
    DistributedBoundaryPressure = (
        Demand.PeakBoundaryDemand > BoundaryCapacity
        and not ReconvergentAccessPressure
    )
    return TopologyDemandPressureProfile(
        BoundaryCapacity=BoundaryCapacity,
        ReconvergentAccessPressure=ReconvergentAccessPressure,
        TerminalBankPressure=TerminalBankPressure,
        DistributedBoundaryPressure=DistributedBoundaryPressure,
        ScaleGeometryPressure=(
            TerminalBankPressure or DistributedBoundaryPressure
        ),
    )

@dataclass(frozen=True)
class ExactStatePlacementEvaluation:
    """Immutable post-placement checks shared only by one exact-state key."""

    MandatoryAccessProfile: MandatoryAccessConflictProfile | None
    TopologyDemand: TopologyDemandProfile

def BuildTopologyDemandProfile(Module: Any) -> TopologyDemandProfile:
    """Measure reconvergent fanout and logical-cut demand without names."""
    Gates = tuple(Module.Gates)
    InputTerminalCount = len(tuple(getattr(Module, "Inputs", ())))
    OutputTerminalCount = len(tuple(getattr(Module, "Outputs", ())))
    ConsumersBySignal: dict[str, set[int]] = {}
    ProducerBySignal: dict[str, int] = {}
    for GateIndex, Gate in enumerate(Gates):
        for Signal in getattr(Gate, "Outputs", ()):
            ProducerBySignal[str(Signal)] = GateIndex
        for Signal in getattr(Gate, "Inputs", ()):
            ConsumersBySignal.setdefault(str(Signal), set()).add(
                GateIndex
            )

    Successors: dict[int, set[int]] = {
        GateIndex: set()
        for GateIndex in range(len(Gates))
    }
    for Signal, ProducerIndex in ProducerBySignal.items():
        Successors[ProducerIndex].update(
            ConsumersBySignal.get(Signal, ())
        )

    DescendantsByGate: dict[int, frozenset[int]] = {}

    def Descendants(GateIndex: int) -> frozenset[int]:
        Cached = DescendantsByGate.get(GateIndex)
        if Cached is not None:
            return Cached
        Seen = {GateIndex}
        Pending = [GateIndex]
        while Pending:
            Current = Pending.pop()
            for Successor in Successors.get(Current, ()):
                if Successor in Seen:
                    continue
                Seen.add(Successor)
                Pending.append(Successor)
        Result = frozenset(Seen)
        DescendantsByGate[GateIndex] = Result
        return Result

    FanoutBySignal = {
        Signal: len(Consumers)
        for Signal, Consumers in ConsumersBySignal.items()
    }
    ReconvergentFanouts: list[int] = []
    for Signal, Consumers in ConsumersBySignal.items():
        Fanout = len(Consumers)
        if Fanout < 2:
            continue
        Branches = tuple(sorted(Consumers))
        IsReconvergent = any(
            Descendants(Branches[FirstIndex])
            & Descendants(Branches[SecondIndex])
            for FirstIndex in range(len(Branches))
            for SecondIndex in range(FirstIndex + 1, len(Branches))
        )
        if IsReconvergent:
            ReconvergentFanouts.append(Fanout)

    # A live-signal level cut is a placement-independent lower bound on
    # cluster-boundary demand. Gate order and generated identifiers do not
    # participate in this scalar profile.
    Levels: dict[int, int] = {}
    PendingIndexes = set(range(len(Gates)))
    while PendingIndexes:
        Advanced = False
        for GateIndex in tuple(PendingIndexes):
            Predecessors = {
                ProducerBySignal[Signal]
                for Signal in map(
                    str,
                    getattr(Gates[GateIndex], "Inputs", ()),
                )
                if Signal in ProducerBySignal
                and ProducerBySignal[Signal] != GateIndex
            }
            if not Predecessors.issubset(Levels):
                continue
            Levels[GateIndex] = (
                1 + max((Levels[Index] for Index in Predecessors), default=-1)
            )
            PendingIndexes.remove(GateIndex)
            Advanced = True
        if not Advanced:
            # Validation reports combinational cycles elsewhere. Preserve a
            # deterministic diagnostic profile without hanging here.
            for GateIndex in sorted(PendingIndexes):
                Levels[GateIndex] = 0
            break

    MaximumLevel = max(Levels.values(), default=0)
    PeakBoundaryDemand = 0
    for CutLevel in range(MaximumLevel):
        Demand = sum(
            ProducerSignal in ConsumersBySignal
            and Levels.get(ProducerIndex, 0) <= CutLevel
            and any(
                Levels.get(ConsumerIndex, 0) > CutLevel
                for ConsumerIndex
                in ConsumersBySignal.get(ProducerSignal, ())
            )
            for ProducerSignal, ProducerIndex in ProducerBySignal.items()
        )
        PeakBoundaryDemand = max(PeakBoundaryDemand, Demand)

    return TopologyDemandProfile(
        MaximumFanout=max(FanoutBySignal.values(), default=0),
        ReconvergentCutCount=len(ReconvergentFanouts),
        QualifyingReconvergentCutCount=sum(
            Fanout >= 4 for Fanout in ReconvergentFanouts
        ),
        MaximumReconvergentFanout=max(
            ReconvergentFanouts,
            default=0,
        ),
        PeakBoundaryDemand=PeakBoundaryDemand,
        InputTerminalCount=InputTerminalCount,
        OutputTerminalCount=OutputTerminalCount,
        MaximumTerminalBankDemand=max(
            InputTerminalCount,
            OutputTerminalCount,
        ),
    )

def MeasurePlacementTopologyDemand(
    BaseProfile: TopologyDemandProfile,
    Candidate: PcbPlacement,
    MandatoryConflicts: dict[object, object] | None = None,
    MandatoryProfile: Any | None = None,
) -> TopologyDemandProfile:
    """Enrich logical pressure with exact boundary/access geometry."""
    Diagnostics = dict(
        Candidate.Placed.LocalRouteDiagnostics or {}
    )
    GapDiagnostics = Diagnostics.get("__InterClusterGaps__", {})
    BoundaryDemand = (
        GapDiagnostics.get("BoundaryDemand", ())
        if isinstance(GapDiagnostics, dict)
        else ()
    )
    PeakBoundaryDemand = max(
        (
            int(Record.get("RequiredCorridorLanes", 0))
            for Record in BoundaryDemand
            if isinstance(Record, dict)
        ),
        default=BaseProfile.PeakBoundaryDemand,
    )
    SingleCandidateBoundarySignals = tuple(sorted(
        (
            Cluster.ClusterId,
            str(Signal),
        )
        for Cluster in Candidate.PackedClusters
        for Signal, CandidateCount in getattr(
            Cluster,
            "LegalEscapeCandidateCounts",
            (),
        )
        if int(CandidateCount) == 1
    ))
    AccessCandidateScarcity = (
        len(SingleCandidateBoundarySignals)
        + sum(
            Cluster.PinScarcityCount
            for Cluster in Candidate.PackedClusters
        )
    )

    PlacedGates = Candidate.Placed.PlacedGates
    if PlacedGates:
        MinimumX = min(Gate.X for Gate in PlacedGates)
        MinimumZ = min(Gate.Z for Gate in PlacedGates)
        MaximumX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
            for Gate in PlacedGates
        )
        MaximumZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
            for Gate in PlacedGates
        )
        GateFootprint = (
            (MaximumX - MinimumX + 1)
            * (MaximumZ - MinimumZ + 1)
        )
    else:
        GateFootprint = 0

    PointsBySignal: dict[str, list[tuple[int, int]]] = {}
    for Gate in PlacedGates:
        for Signal in getattr(Gate, "Outputs", ()):
            OutputPin = getattr(Gate, "OutputPin", None)
            X, _Y, Z = (
                OutputPin
                if OutputPin is not None
                else (Gate.X, Gate.Y, Gate.Z)
            )
            PointsBySignal.setdefault(str(Signal), []).append((X, Z))
        for InputIndex, Signal in enumerate(
            getattr(Gate, "Inputs", ())
        ):
            InputPins = getattr(Gate, "InputPins", ())
            X, _Y, Z = (
                InputPins[InputIndex]
                if InputIndex < len(InputPins)
                else (Gate.X, Gate.Y, Gate.Z)
            )
            PointsBySignal.setdefault(str(Signal), []).append((X, Z))
    Hpwl = sum(
        (max(X for X, _Z in Points) - min(X for X, _Z in Points))
        + (max(Z for _X, Z in Points) - min(Z for _X, Z in Points))
        for Points in PointsBySignal.values()
        if len(Points) > 1
    )

    ConflictMap = MandatoryConflicts or {}
    ConflictSignals = tuple(sorted({
        str(Signal)
        for Owners in ConflictMap.values()
        for Signal in (
            Owners
            if isinstance(Owners, tuple | list | set | frozenset)
            else (Owners,)
        )
    }))
    ConflictResourceCount = len(ConflictMap)
    OwnershipFingerprint = ""
    ConflictFingerprint = ""
    if MandatoryProfile is not None:
        ConflictResourceCount = int(getattr(
            MandatoryProfile,
            "ConflictResourceCount",
            getattr(
                MandatoryProfile,
                "MandatoryAccessConflictResources",
                ConflictResourceCount,
            ),
        ))
        ConflictSignals = tuple(getattr(
            MandatoryProfile,
            "ConflictSignals",
            getattr(
                MandatoryProfile,
                "MandatoryAccessConflictSignals",
                ConflictSignals,
            ),
        ))
        OwnershipFingerprint = str(getattr(
            MandatoryProfile,
            "OwnershipFingerprint",
            getattr(
                MandatoryProfile,
                "MandatoryAccessOwnershipFingerprint",
                "",
            ),
        ))
        ConflictFingerprint = str(getattr(
            MandatoryProfile,
            "ConflictFingerprint",
            getattr(
                MandatoryProfile,
                "MandatoryAccessConflictFingerprint",
                "",
            ),
        ))
    if not OwnershipFingerprint:
        ClaimNodes = tuple(
            tuple(
                (int(Node[0]), int(Node[1]), int(Node[2]))
                for Node in Claim.Nodes
            )
            for Claim in Candidate.Placed.LocalRouteClaims or ()
        )
        AllClaimNodes = tuple(
            Node for Nodes in ClaimNodes for Node in Nodes
        )
        MinimumClaimX = min(
            (Node[0] for Node in AllClaimNodes),
            default=0,
        )
        MinimumClaimY = min(
            (Node[1] for Node in AllClaimNodes),
            default=0,
        )
        MinimumClaimZ = min(
            (Node[2] for Node in AllClaimNodes),
            default=0,
        )
        OwnershipFingerprint = BuildStableFingerprint({
            "AnonymousRelativeLocalClaims": sorted(
                tuple(sorted(
                    (
                        Node[0] - MinimumClaimX,
                        Node[1] - MinimumClaimY,
                        Node[2] - MinimumClaimZ,
                    )
                    for Node in Nodes
                ))
                for Nodes in ClaimNodes
            ),
        })
    if not ConflictFingerprint:
        ConflictFingerprint = BuildStableFingerprint({
            "Conflicts": [
                (
                    repr(Resource),
                    tuple(sorted(map(str, (
                        Owners
                        if isinstance(
                            Owners,
                            tuple | list | set | frozenset,
                        )
                        else (Owners,)
                    )))),
                )
                for Resource, Owners in sorted(
                    ConflictMap.items(),
                    key=lambda Item: repr(Item[0]),
                )
            ],
        })

    return replace(
        BaseProfile,
        PeakBoundaryDemand=PeakBoundaryDemand,
        SingleCandidateBoundarySignals=(
            SingleCandidateBoundarySignals
        ),
        AccessCandidateScarcity=AccessCandidateScarcity,
        MandatoryAccessConflictResources=ConflictResourceCount,
        MandatoryAccessConflictSignals=tuple(sorted(map(
            str,
            ConflictSignals,
        ))),
        MandatoryAccessOwnershipFingerprint=OwnershipFingerprint,
        MandatoryAccessConflictFingerprint=ConflictFingerprint,
        GateFootprint=GateFootprint,
        Hpwl=Hpwl,
    )

def BuildPlacementGenerationPlan(
    Policy: PhysicalDesignPolicy,
    PreferPackedPlacements: bool = False,
    PrioritizeSeparatedPacking: bool = False,
    EnableInitialJointOrientation: bool = True,
    EnableCompactDirectOnlyOrientation: bool = False,
    PreserveDirectOnlyJointPortfolio: bool = False,
) -> PlacementGenerationPlan:
    """Build a deterministic, recipe-deduplicated placement generation plan."""
    RoutingSpacing = Policy.Placement.RoutingSpacing
    InitialPackedSpacing = RoutingSpacing
    TriggeredPackingPolicy = replace(
        Policy.NandPacking,
        EnableJointClusterOrientation=EnableInitialJointOrientation,
    )
    PrimaryRequests: list[PlacementGenerationRequest] = []
    DeferredRequests: list[PlacementGenerationRequest] = []
    RecipeKeys: set[tuple[int, Any]] = set()

    def AddRequest(
        Target: list[PlacementGenerationRequest],
        SourceGenerator: str,
        CandidateSpacing: int,
        CandidatePacking: Any,
    ) -> None:
        RecipeKey = (CandidateSpacing, CandidatePacking)
        if RecipeKey in RecipeKeys:
            return
        RecipeKeys.add(RecipeKey)
        Target.append(
            PlacementGenerationRequest(
                SourceGenerator=SourceGenerator,
                RoutingSpacing=CandidateSpacing,
                PackingPolicy=CandidatePacking,
            )
        )

    if Policy.NandPacking.Enabled:
        # Start with the bounded row construction and the unpacked oracle.
        # Structure-aware alternatives remain available after both primaries
        # fail, under the same absolute deadline.
        UnpackedSpacing = (
            max(0, RoutingSpacing - 1)
            if Policy.Placement.EnableRoutingFeedback
            else RoutingSpacing
        )
        DeferUnpackedOracle = PreferPackedPlacements
        AddRequest(
            PrimaryRequests,
            "row-beam",
            InitialPackedSpacing,
            replace(
                TriggeredPackingPolicy,
                GraphBeamEnabled=False,
            ),
        )
        if not DeferUnpackedOracle:
            AddRequest(
                PrimaryRequests,
                "unpacked",
                UnpackedSpacing,
                replace(Policy.NandPacking, Enabled=False),
            )
        # This intentionally repeats the primary row recipe after routing
        # feedback exists. RelocationSignals makes it new physical geometry,
        # so recipe-level deduplication must not discard it.
        DeferredRequests.append(
            PlacementGenerationRequest(
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=InitialPackedSpacing,
                PackingPolicy=replace(
                    TriggeredPackingPolicy,
                    GraphBeamEnabled=False,
                ),
            )
        )
        DirectOnlyJointPlacementRetention = (
            Policy.NandPacking.RetainedJointPlacementCandidates
            if PreserveDirectOnlyJointPortfolio
            else 1
        )
        AddRequest(
            DeferredRequests,
            "row-beam-direct-only",
            InitialPackedSpacing,
            replace(
                TriggeredPackingPolicy,
                GraphBeamEnabled=False,
                # Restore the established single compact slot/orientation
                # selection for the direct-only fallback.  This is not the
                # topology-triggered six-candidate portfolio used for
                # reconvergent designs.
                EnableJointClusterOrientation=(
                    EnableInitialJointOrientation
                    or EnableCompactDirectOnlyOrientation
                ),
                RetainedJointPlacementCandidates=DirectOnlyJointPlacementRetention,
                MaximumLocalRouteLength=(
                    Policy.NandPacking.DirectConnectMaximumLength
                ),
            ),
        )
        if DeferUnpackedOracle:
            AddRequest(
                DeferredRequests,
                "unpacked",
                UnpackedSpacing,
                replace(Policy.NandPacking, Enabled=False),
            )
        if (
            Policy.Placement.EnableRoutingFeedback
            and Policy.Placement.RoutingSpacingAlternatives > 0
        ):
            for Delta in range(
                1,
                min(
                    Policy.Placement.RoutingFeedbackIterations,
                    Policy.Placement.RoutingSpacingAlternatives,
                ) + 1,
            ):
                WiderSpacing = RoutingSpacing + Delta
                AddRequest(
                    DeferredRequests,
                    f"unpacked-spacing-{WiderSpacing}",
                    WiderSpacing,
                    replace(Policy.NandPacking, Enabled=False),
                )
        if UnpackedSpacing != RoutingSpacing:
            AddRequest(
                DeferredRequests,
                "unpacked-configured-spacing",
                RoutingSpacing,
                replace(Policy.NandPacking, Enabled=False),
            )
        AddRequest(
            DeferredRequests,
            "configured-packing",
            RoutingSpacing,
            TriggeredPackingPolicy,
        )
        AddRequest(
            DeferredRequests,
            "graph-beam-direct-only",
            RoutingSpacing,
            replace(
                TriggeredPackingPolicy,
                MaximumLocalRouteLength=(
                    Policy.NandPacking.DirectConnectMaximumLength
                ),
            ),
        )
    else:
        AddRequest(
            PrimaryRequests,
            "configured-packing",
            RoutingSpacing,
            Policy.NandPacking,
        )

    if (
        Policy.Placement.EnableRoutingFeedback
        and Policy.Placement.RoutingFeedbackIterations > 0
    ):
        AlternativeCount = min(
            Policy.Placement.RoutingFeedbackIterations,
            Policy.Placement.RoutingSpacingAlternatives,
        )
        for Delta in range(1, AlternativeCount + 1):
            for AlternativeSpacing in (
                max(0, RoutingSpacing - Delta),
                RoutingSpacing + Delta,
            ):
                if AlternativeSpacing == RoutingSpacing:
                    continue
                AddRequest(
                    DeferredRequests,
                    f"spacing-{AlternativeSpacing}",
                    AlternativeSpacing,
                    TriggeredPackingPolicy,
                )

    if Policy.NandPacking.Enabled and PreferPackedPlacements:
        def DeferredGeneratorPriority(
            Request: PlacementGenerationRequest,
        ) -> int:
            """Try one packed repair, then preserve time for the unpacked oracle."""
            if Request.SourceGenerator == "row-beam-conflict-relocation":
                return 0
            if Request.SourceGenerator.startswith("unpacked"):
                return 1 if PrioritizeSeparatedPacking else 2
            if Request.SourceGenerator == "configured-packing":
                return 2 if PrioritizeSeparatedPacking else 3
            if Request.SourceGenerator == "row-beam-direct-only":
                return 3 if PrioritizeSeparatedPacking else 1
            return 4

        DeferredRequests.sort(
            key=lambda Request: (
                DeferredGeneratorPriority(Request),
                Request.SourceGenerator,
            )
        )

    MaximumAttempts = len(PrimaryRequests) + len(DeferredRequests)
    return PlacementGenerationPlan(
        PrimaryRequests=tuple(PrimaryRequests),
        DeferredRequests=tuple(DeferredRequests),
        MaximumAttempts=max(1, MaximumAttempts),
    )

def BuildDerivedPinAlignedEnvelopeLowerBoundObjective(
    State: Any,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> tuple[int, int, int, str]:
    """Rank one fixed graph-core state by its minimum physical ring shell.

    Terminal slots are materialized only after this bounded portfolio has
    been retained, so this function deliberately uses an admissible physical
    lower bound rather than guessing a terminal bank.  The first routing
    resource outside a macro's electrical keep-out is separated by two
    technology-defined neighbor steps: one for the electrical exclusion and
    one for the legal exterior routing node.  The exact terminal/ring bounds
    replace this value in the later pre-route interface objective.
    """
    Positions = {
        str(Name): (int(Position[0]), int(Position[1]))
        for Name, Position in dict(getattr(State, "Positions", ())).items()
    }
    Rotations = {
        str(Name): int(Rotation)
        for Name, Rotation in dict(getattr(State, "Rotations", ())).items()
    }
    if not Positions or frozenset(Positions) != frozenset(Rotations):
        raise ValueError(
            "derived pin-aligned envelope requires complete transforms"
        )
    MinimumX = min(Position[0] for Position in Positions.values())
    MinimumZ = min(Position[1] for Position in Positions.values())
    MaximumX = max(
        Positions[Name][0] + RotatedCellSize("NAND", Rotations[Name])[0] - 1
        for Name in Positions
    )
    MaximumZ = max(
        Positions[Name][1] + RotatedCellSize("NAND", Rotations[Name])[1] - 1
        for Name in Positions
    )
    # The technology, rather than a placement-policy halo, owns the minimum
    # planar electrical adjacency.  A ring needs the next legal node beyond
    # that exclusion on every physical face.
    ElectricalNeighborStep = max(
        (
            max(abs(Position[0]), abs(Position[2]))
            for Position in Technology.NeighborPositions((0, 0, 0))
            if Position[1] == 0
        ),
        default=0,
    )
    if ElectricalNeighborStep < 1:
        raise ValueError(
            "routing technology has no planar electrical neighbor step"
        )
    PerimeterClearance = ElectricalNeighborStep + ElectricalNeighborStep
    Width = MaximumX - MinimumX + 1 + 2 * PerimeterClearance
    Depth = MaximumZ - MinimumZ + 1 + 2 * PerimeterClearance
    Objective = tuple(getattr(State, "Objective", ()))
    Hpwl = int(Objective[4]) if len(Objective) > 4 else 0
    return (
        Width * Depth,
        max(Width, Depth),
        Hpwl,
        str(getattr(State, "Fingerprint", "")),
    )

def SelectDerivedPrimaryPlacementRequests(
    GenerationPlan: PlacementGenerationPlan,
    SinglePackedComponent: bool,
    *,
    Incumbent: PcbPlacement | None = None,
    Module: Any | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[PlacementGenerationRequest, ...]:
    """Build the finite single-component geometry domain before routing.

    The row-beam incumbent and every retained non-dominated pin-aligned state
    are fixed members of one placement problem.  The graph beam derives
    origins from transformed connected pins and exact macro legality before
    any capacity selection or routing begins; it never schedules a later
    geometry attempt after a failed candidate.
    """
    Primary = tuple(GenerationPlan.PrimaryRequests[:1])
    if SinglePackedComponent and Primary:
        if Incumbent is None or Module is None:
            raise ValueError(
                "single-component primary selection requires its row-beam "
                "incumbent and module to materialize the fixed graph domain"
            )
        if len(Incumbent.Clusters) != 1:
            raise ValueError(
                "single-component primary selection received a placement "
                "with multiple clusters"
            )
        Names = tuple(sorted(Incumbent.Clusters[0]))
        InternalByName = {
            Gate.Name: Gate
            for Gate in Module.Gates
            if Gate.Kind.value == "NAND"
            and Gate.Name in Names
        }
        if frozenset(InternalByName) != frozenset(Names):
            raise ValueError(
                "single-component graph domain is missing a NAND member"
            )
        CoreGates = tuple(
            Gate
            for Gate in Incumbent.Placed.PlacedGates
            if Gate.Name in InternalByName
        )
        if not CoreGates:
            raise ValueError(
                "single-component row-beam incumbent has no NAND core"
            )
        GraphRequest = replace(
            Primary[0],
            SourceGenerator="derived-pin-aligned-core",
            PackingPolicy=replace(
                Primary[0].PackingPolicy,
                GraphBeamEnabled=True,
                # The graph beam owns this component's transforms.  Do not
                # route it through the legacy joint-state queue, whose later
                # states are retry machinery rather than fixed domain members.
                EnableJointClusterOrientation=False,
            ),
        )
        Portfolio = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            GraphRequest.PackingPolicy.BeamWidth,
            WorkCheck=WorkCheck,
        )
        # The graph portfolio is already a finite, non-dominated core
        # frontier.  Do not collapse that frontier to its smallest NAND-only
        # rectangle: a slightly wider/shorter core can have the smaller
        # physical access ring once terminal slots and real keep-outs are
        # materialized.  Retain a deterministic prefix ranked by the minimum
        # four-sided physical envelope which every member must support.  The
        # later pre-route selector replaces this lower bound with the exact
        # fabric outer bounds before choosing one member to route.
        MaximumDerivedMemberCount = max(
            0,
            int(GraphRequest.PackingPolicy.RetainedPlacementCandidates)
            - len(Primary),
        )
        MaximumGraphCoreMemberCount = max(
            0,
            MaximumDerivedMemberCount - 1,
        )
        States = tuple(
            sorted(
                Portfolio.States,
                key=BuildDerivedPinAlignedEnvelopeLowerBoundObjective,
            )[:MaximumGraphCoreMemberCount]
        )
        # The incumbent is a certified comparison point.  Every derived
        # geometry below is a pre-route member, including the row-beam core
        # with perimeter-facing terminals and pin-aligned graph cores.  Each
        # member owns one finite, physically-derived terminal-slot domain;
        # its assignment is selected internally while materializing that
        # member.  Do not multiply geometry recipes by arbitrary terminal
        # layout indexes here: that would turn a fixed slot problem into an
        # implicit portfolio of duplicate placement attempts.
        DerivedGeometryRequests = (
            replace(
                Primary[0],
                SourceGenerator="derived-perimeter-row-beam",
            ),
            *(
                replace(
                    GraphRequest,
                    GraphCoreCandidateIndex=State.CandidateIndex,
                )
                for State in States
            ),
        )
        DerivedRequests = tuple(
            replace(Request, TerminalLayoutVariantIndex=0)
            for Request in DerivedGeometryRequests
        )
        return (
            *Primary,
            *DerivedRequests,
        )
    AccessSeparated = tuple(
        Request
        for Request in GenerationPlan.DeferredRequests
        if Request.SourceGenerator == "row-beam-direct-only"
    )[:1]
    return (*Primary, *AccessSeparated)

"""PCB-only physical placement and routing orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import combinations, islice
from math import isfinite
import os
from time import monotonic
import traceback
from typing import Any, Callable, Iterable, Mapping

from ..Cells.Library import GetCellMacro
from ..Routing.Pcb import (
    PrepareClusterInterfaceAssignment,
    PreparePhysicalComponentEligibility,
    ReplanPhysicalComponentAssembly,
    RoutePcbDesign,
    SolvePreparedPhysicalComponentEligibility,
    ValidateClusterInterfaceForeignAccess,
)
from ..Routing.ComponentPipeline import (
    AssembleClosedComponentForGlobalRouting,
    CompileClosedComponent,
)
from ..Routing.Models import (
    ClusterInterfaceAssignment,
    ClusterInterfacePortfolioAssignment,
    ClusterInterfacePortfolioProblem,
    ClusterInterfacePortfolioStateAudit,
    ClusterInterfacePlacementState,
    ClusterInterfaceRealizabilityNogood,
    ClusterInterfaceStateProof,
    RoutedComponentTemplate,
    RoutedDesign,
)
from ..Routing.Failures import (
    RoutingAssignmentCut,
    RoutingAssignmentCutClassification,
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from ..Routing.LocalFirst import (
    BuildLocalFirstSnapshot,
    MeasurePlacementRoutingFeedback,
)
from ..Routing.Reliability import BuildStableFingerprint, RoutingDeadline
from ..Routing.Actions import ValidatePlacedCellElectricalIsolation
from ..Routing.Actions.Geometry import BuildRoutingResources
from ..Routing.Policy import DefaultPhysicalDesignPolicy, PhysicalDesignPolicy
from ..Routing.Policy import (
    ExecutionStrategyForRequest,
    PolicyForRoutingStrategy,
    RoutingStrategy,
)
from ..Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)
from .Pcb import (
    BuildBoundedInterClusterRoutingChannel,
    BuildBoundedInterClusterRoutingDeck,
    BuildTransactionalClusterEndpointRepair,
    BuildAssignmentCutHigherOrderSignalSet,
    MandatoryAccessConflictProfile,
    MeasureMandatoryAccessConflictProfile,
    PlacementAssignmentConstraintSet,
    PcbPlacement,
    PlacePcbGraph,
)
from .Rotation import RotatedCellSize
from .Geometry import PlacedDesign
from ..Synthesis.Validation import ValidateNandOnlyDesign


@dataclass(frozen=True)
class PcbProgress:
    Completed: int
    Total: int
    Workers: int
    Valid: int
    BestBlocks: int | None
    BestWidth: int | None
    BestDepth: int | None
    BestFootprint: int | None
    Failed: int
    Stage: str = "preparing routing"
    Unit: str = "routing passes"


@dataclass
class PcbResult:
    Placed: PlacedDesign
    Routed: RoutedDesign
    Footprint: int
    EstimatedBlocks: int
    Width: int
    Depth: int
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology
    RequestedStrategy: str = RoutingStrategy.Default.value
    UsedStrategy: str = RoutingStrategy.Default.value
    FallbackUsed: bool = False
    FallbackReason: str | None = None
    PlanningContracts: dict[str, object] | None = None
    RejectedRewriteDiagnostics: dict[str, object] | None = None


def IsComponentKeepoutGlobalFailure(
    Failure: RoutingFailure,
    PhysicalAssemblyPlan: Any,
) -> bool:
    """Return whether a global net disproves the component envelope itself.

    Port reassignment can change the seam and the local template, but it
    cannot authorize an ordinary global signal to enter the plan's keepout.
    A bounded candidate proof that explicitly attributes starvation to the
    immutable routed component is therefore placement-level feedback, not a
    request to enumerate more port plans for the same envelope.
    """
    AffectedSignals = frozenset(map(str, Failure.AffectedNets))
    PortSignals = frozenset(
        str(Port.Signal)
        for Port in getattr(PhysicalAssemblyPlan, "Ports", ())
    )
    Diagnostics = dict(Failure.Diagnostics or {})
    return bool(
        AffectedSignals
        and AffectedSignals.isdisjoint(PortSignals)
        and Failure.Stage == "Candidate"
        and Diagnostics.get("Action")
        == "advance-routed-component-global-starvation"
        and "immutable routed-component state blocked"
        in Failure.Detail
    )


@dataclass(frozen=True)
class PcbPlacementCandidate:
    """One deterministic legal placement retained for authoritative routing."""

    CandidateId: str
    SourceGenerator: str
    RoutingSpacing: int
    PlacementFingerprint: str
    FeedbackScore: tuple[int, ...]
    BoundaryOverflow: int
    PinScarcityCount: int
    GuideOverflowPeak: int
    GuideOverflowCells: int
    PinEscapeConflictCount: int
    EstimatedGlobalExtensionNodes: int
    EstimatedGlobalExtensionNets: int
    PreOwnedNodeCount: int
    Placement: PcbPlacement
    JointExactScore: tuple[int, ...] = ()
    TopologyDemand: TopologyDemandProfile | None = None
    JointPortfolioCandidate: bool = False
    Feedback: Any | None = None
    AssignmentCutFingerprint: str = ""
    AssignmentConstraintFingerprint: str = ""
    JointPortfolioIdentityFingerprint: str = ""
    PlacementRetentionFingerprint: str = ""
    CutInterfaceDifference: int = 0
    AccessDistinctCandidateCount: int = 0
    InterfaceTopologyFingerprint: str = ""
    JointPlacementState: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CandidateId": self.CandidateId,
            "SourceGenerator": self.SourceGenerator,
            "RoutingSpacing": self.RoutingSpacing,
            "PlacementFingerprint": self.PlacementFingerprint,
            "PlacementRetentionFingerprint": (
                self.PlacementRetentionFingerprint
            ),
            "AssignmentCutFingerprint": self.AssignmentCutFingerprint,
            "AssignmentConstraintFingerprint": (
                self.AssignmentConstraintFingerprint
            ),
            "JointPortfolioIdentityFingerprint": (
                self.JointPortfolioIdentityFingerprint
            ),
            "CutInterfaceDifference": self.CutInterfaceDifference,
            "AccessDistinctCandidateCount": (
                self.AccessDistinctCandidateCount
            ),
            "InterfaceTopologyFingerprint": (
                self.InterfaceTopologyFingerprint
            ),
            "FeedbackScore": list(self.FeedbackScore),
            "BoundaryOverflow": self.BoundaryOverflow,
            "PinScarcityCount": self.PinScarcityCount,
            "GuideOverflowPeak": self.GuideOverflowPeak,
            "GuideOverflowCells": self.GuideOverflowCells,
            "PinEscapeConflictCount": self.PinEscapeConflictCount,
            "EstimatedGlobalExtensionNodes": (
                self.EstimatedGlobalExtensionNodes
            ),
            "EstimatedGlobalExtensionNets": self.EstimatedGlobalExtensionNets,
            "PreOwnedNodeCount": self.PreOwnedNodeCount,
            "JointExactScore": list(self.JointExactScore),
            "TopologyDemand": (
                self.TopologyDemand.ToDictionary()
                if self.TopologyDemand is not None
                else None
            ),
            "JointPortfolioCandidate": self.JointPortfolioCandidate,
            "RoutePressure": (
                self.PreOwnedNodeCount + self.EstimatedGlobalExtensionNodes
            ),
            "PackedNandPlacement": bool(self.Placement.PackedClusters),
            "LocalClaimCount": len(
                self.Placement.Placed.LocalRouteClaims or ()
            ),
        }


@dataclass(frozen=True)
class ClusterInterfaceStageSchedule:
    """Shared deadline and immutable state order for one component solve."""

    StartedAt: float
    ExpiresAt: float
    GlobalRoutingReserveSeconds: float
    PublicationReserveSeconds: float
    StateFingerprints: tuple[str, ...]

    @property
    def AvailableSeconds(self) -> float:
        return max(0.0, self.ExpiresAt - self.StartedAt)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "StartedAt": self.StartedAt,
            "ExpiresAt": self.ExpiresAt,
            "AvailableSeconds": round(self.AvailableSeconds, 6),
            "GlobalRoutingReserveSeconds": (
                self.GlobalRoutingReserveSeconds
            ),
            "PublicationReserveSeconds": (
                self.PublicationReserveSeconds
            ),
            "StateFingerprints": list(self.StateFingerprints),
            "StateCount": len(self.StateFingerprints),
            "Scheduling": "sequential-shared-budget",
        }


def BuildRetainedComponentPlacementSearchDomain(
    PlacementFingerprints: Iterable[str],
    *,
    MaximumComponentSelections: int = 6,
) -> tuple[tuple[int, int, str], ...]:
    """Advance placement before selecting another component partition."""
    if MaximumComponentSelections < 1:
        raise ValueError(
            "MaximumComponentSelections must be positive"
        )
    Placements = tuple(map(str, PlacementFingerprints))
    return tuple(
        (ComponentVariant, PlacementIndex, PlacementFingerprint)
        for ComponentVariant in range(MaximumComponentSelections)
        for PlacementIndex, PlacementFingerprint
        in enumerate(Placements)
    )


def BuildComponentAccessFeedbackPlacementScore(
    Candidate: PcbPlacementCandidate,
    Signals: Iterable[str],
) -> tuple[int, int, int, int, int]:
    """Rank retained placements by learned component-port escape geometry."""
    SignalSet = frozenset(map(str, Signals))
    Placement = Candidate.Placement
    GateByName = {
        Gate.Name: Gate for Gate in Placement.Placed.PlacedGates
    }
    ClusterBounds = {}
    for ClusterIndex, GateNames in enumerate(Placement.Clusters):
        Gates = tuple(
            GateByName[Name]
            for Name in GateNames
            if Name in GateByName
        )
        if not Gates:
            continue
        ClusterBounds[ClusterIndex] = (
            min(Gate.X for Gate in Gates),
            max(Gate.X for Gate in Gates),
            min(Gate.Z for Gate in Gates),
            max(Gate.Z for Gate in Gates),
        )
    SeenSignals = set()
    PerimeterDepths = []
    DirectionPenalties = []

    def Record(
        ClusterIndex: int,
        Terminal: tuple[int, int, int] | None,
        Side: str,
    ) -> None:
        if Terminal is None or ClusterIndex not in ClusterBounds:
            return
        MinimumX, MaximumX, MinimumZ, MaximumZ = ClusterBounds[
            ClusterIndex
        ]
        X, _Y, Z = Terminal
        SideDepths = {
            "west": abs(X - MinimumX),
            "east": abs(MaximumX - X),
            "north": abs(Z - MinimumZ),
            "south": abs(MaximumZ - Z),
        }
        MinimumDepth = min(SideDepths.values())
        DirectedDepth = SideDepths.get(
            str(Side).lower(),
            MinimumDepth,
        )
        PerimeterDepths.append(MinimumDepth)
        DirectionPenalties.append(max(0, DirectedDepth - MinimumDepth))

    Requests = (
        Placement.ClusterBoundaryLeaseRequests
        or Placement.Placed.ClusterBoundaryLeaseRequests
        or ()
    )
    for Request in Requests:
        Signal = str(Request.Signal)
        if Signal not in SignalSet:
            continue
        SeenSignals.add(Signal)
        Record(
            int(Request.SourceCluster),
            Request.SourceTerminal,
            Request.SourceBoundarySide,
        )
        for Terminal in Request.TargetTerminals:
            Record(
                int(Request.TargetCluster),
                Terminal,
                Request.TargetBoundarySide,
            )
    return (
        len(SignalSet - SeenSignals),
        max(DirectionPenalties, default=0),
        sum(DirectionPenalties),
        max(PerimeterDepths, default=0),
        sum(PerimeterDepths),
    )


def ReuseRetainedPlacementRoutingResources(
    Cache: dict[str, Any],
    PlacementFingerprint: str,
    Build: Callable[[], Any],
) -> tuple[Any, bool]:
    """Reuse immutable whole-design routing geometry across components."""
    Existing = Cache.get(PlacementFingerprint)
    if Existing is not None:
        return Existing, True
    Created = Build()
    Cache[PlacementFingerprint] = Created
    return Created, False


def BuildClusterInterfaceStageSchedule(
    Deadline: RoutingDeadline,
    StateFingerprints: Iterable[str],
    *,
    GlobalRoutingReserveSeconds: float,
    PublicationReserveSeconds: float = 2.0,
) -> ClusterInterfaceStageSchedule:
    """Reserve global routing while funding complete interface states."""
    if GlobalRoutingReserveSeconds < 0:
        raise ValueError("global routing reserve cannot be negative")
    if PublicationReserveSeconds < 0:
        raise ValueError("publication reserve cannot be negative")
    StartedAt = monotonic()
    ExpiresAt = max(
        StartedAt,
        Deadline.ExpiresAt
        - GlobalRoutingReserveSeconds
        - PublicationReserveSeconds,
    )
    return ClusterInterfaceStageSchedule(
        StartedAt=StartedAt,
        ExpiresAt=ExpiresAt,
        GlobalRoutingReserveSeconds=GlobalRoutingReserveSeconds,
        PublicationReserveSeconds=PublicationReserveSeconds,
        StateFingerprints=tuple(StateFingerprints),
    )


def ApplyRoutingRuntimeBudget(
    Policy: PhysicalDesignPolicy,
    RoutingDeadlineSeconds: float | None,
) -> PhysicalDesignPolicy:
    """Return the immutable policy carrying the effective absolute budget."""
    if RoutingDeadlineSeconds is None:
        return Policy
    if (
        isinstance(RoutingDeadlineSeconds, bool)
        or not isfinite(RoutingDeadlineSeconds)
        or RoutingDeadlineSeconds <= 0
    ):
        raise ValueError("RoutingDeadlineSeconds must be finite and positive")
    EffectiveSeconds = float(RoutingDeadlineSeconds)
    return replace(
        Policy,
        RuntimeBudgetSeconds=EffectiveSeconds,
        AdaptiveRouting=replace(
            Policy.AdaptiveRouting,
            MaximumRuntimeSeconds=min(
                Policy.AdaptiveRouting.MaximumRuntimeSeconds,
                EffectiveSeconds,
            ),
        ),
    )


@dataclass(frozen=True)
class PlacementGenerationRequest:
    """One deterministic placement recipe, before its expensive construction."""

    SourceGenerator: str
    RoutingSpacing: int
    PackingPolicy: Any
    UseCurrentAssignmentCutRelocationSignals: bool = False


@dataclass(frozen=True)
class PendingJointPlacementState:
    """Immutable recipe state for one retained joint placement candidate."""

    Request: PlacementGenerationRequest
    CandidateIndex: int
    RelocationVariant: int
    RoutingSpacing: int
    RelocationSignals: frozenset[str]
    RelocationPrioritySignals: frozenset[str]
    RequiredRelocationSignals: frozenset[str]
    AssignmentCut: RoutingAssignmentCut | None = None
    AssignmentConstraints: PlacementAssignmentConstraintSet = (
        PlacementAssignmentConstraintSet()
    )
    CoordinatedCandidateDiversificationSignals: frozenset[str] = frozenset()
    EnableClusterLocalRouteReuse: bool = False
    IsPostPinBankRepairEpoch: bool = False
    EnableInternalPinBankGeometryRepair: bool = False
    InternalPinBankGeometryRepairSignals: frozenset[str] = frozenset()
    RequiredDistinctPinBankOwnershipFingerprint: str = ""
    TopologyCutFrontier: tuple[RoutingAssignmentCut, ...] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SourceGenerator": self.Request.SourceGenerator,
            "CandidateIndex": self.CandidateIndex,
            "RelocationVariant": self.RelocationVariant,
            "RoutingSpacing": self.RoutingSpacing,
            "RelocationSignals": sorted(self.RelocationSignals),
            "RelocationPrioritySignals": sorted(
                self.RelocationPrioritySignals
            ),
            "RequiredRelocationSignals": sorted(
                self.RequiredRelocationSignals
            ),
            "AssignmentCut": (
                self.AssignmentCut.ToDictionary()
                if self.AssignmentCut is not None
                else None
            ),
            "AssignmentConstraints": (
                self.AssignmentConstraints.ToDictionary()
            ),
            "CoordinatedCandidateDiversificationSignals": sorted(
                self.CoordinatedCandidateDiversificationSignals
            ),
            "EnableClusterLocalRouteReuse": (
                self.EnableClusterLocalRouteReuse
            ),
            "IsPostPinBankRepairEpoch": self.IsPostPinBankRepairEpoch,
            "EnableInternalPinBankGeometryRepair": (
                self.EnableInternalPinBankGeometryRepair
            ),
            **(
                {
                    "InternalPinBankGeometryRepairSignals": sorted(
                        self.InternalPinBankGeometryRepairSignals
                    ),
                }
                if self.InternalPinBankGeometryRepairSignals
                else {}
            ),
            **(
                {
                    "RequiredDistinctPinBankOwnershipFingerprint": (
                        self.RequiredDistinctPinBankOwnershipFingerprint
                    ),
                }
                if self.RequiredDistinctPinBankOwnershipFingerprint
                else {}
            ),
            **(
                {
                    "TopologyCutFrontier": [
                        Cut.ToDictionary()
                        for Cut in self.TopologyCutFrontier
                    ],
                }
                if self.TopologyCutFrontier
                else {}
            ),
        }


@dataclass(frozen=True)
class PendingJointPlacementPortfolioIdentity:
    """One immutable retained-state cut/constraint placement epoch."""

    SourceGenerator: str
    RequestRoutingSpacing: int
    PackingPolicyFingerprint: str
    UseCurrentAssignmentCutRelocationSignals: bool
    RoutingSpacing: int
    RelocationVariant: int
    RelocationSignals: tuple[str, ...]
    RelocationPrioritySignals: tuple[str, ...]
    RequiredRelocationSignals: tuple[str, ...]
    AssignmentCutFingerprint: str
    AssignmentCutWorkFingerprint: str
    AssignmentConstraintFingerprint: str
    CoordinatedSignals: tuple[str, ...]
    InternalPinBankGeometryRepairSignals: tuple[str, ...] = ()
    RequiredDistinctPinBankOwnershipFingerprint: str = ""
    TopologyCutFrontierFingerprints: tuple[
        tuple[str, str], ...
    ] = ()


@dataclass(frozen=True)
class TopologyCutEpochIdentity:
    """One ownership-aware topology repair epoch for an exact cut."""

    AssignmentCutFingerprint: str
    AssignmentConstraintFingerprint: str
    MandatoryAccessOwnershipFingerprint: str


@dataclass(frozen=True)
class DeferredActivePortfolioAssignmentCut:
    """One authoritative cut retained until screened siblings are exhausted."""

    AssignmentCut: RoutingAssignmentCut
    SourceCandidateId: str
    FailureStage: str
    Error: RoutingStageError
    Candidate: PcbPlacementCandidate

    def ToDictionary(self) -> dict[str, object]:
        return {
            "AssignmentCut": self.AssignmentCut.ToDictionary(),
            "SourceCandidateId": self.SourceCandidateId,
            "FailureStage": self.FailureStage,
        }


def ShouldDeferTopologyCutForRetainedPortfolioSibling(
    *,
    TopologyRequiresJointPortfolio: bool,
    ActiveRelocatedPortfolioCandidate: bool,
    RemainingRetainedActiveCandidates: int,
    Failure: RoutingFailure,
    ActiveTransactionalEndpointPortfolioCandidate: bool = False,
    TransactionalCutStrictlyNarrowsParentInterface: bool = False,
    TransactionalCutRepeatedAcrossAccessDistinctPlacements: bool = False,
    TransactionalCutRevisitsAncestorInterface: bool = False,
    TransactionalExactPairAfterCoordinatedRepair: bool = False,
) -> bool:
    """Finish bounded access-distinct siblings before relocating geometry."""
    if (
        TopologyRequiresJointPortfolio
        and ActiveTransactionalEndpointPortfolioCandidate
        and TransactionalExactPairAfterCoordinatedRepair
        and RemainingRetainedActiveCandidates > 1
    ):
        # An exhaustive pair proof applies to the current portal domains, not
        # to access-distinct geometry already materialized in the same
        # bounded transactional portfolio. Exhaust those immutable siblings
        # before opening a broad relocation epoch; this cannot loop because
        # the retained portfolio is finite and attempted identities are
        # removed after every route trial.
        return True
    if (
        ActiveTransactionalEndpointPortfolioCandidate
        and TransactionalCutRevisitsAncestorInterface
    ):
        # The local pin-bank branch resolved its parent cut but returned to a
        # previously observed interface. This is structural geometry evidence,
        # not a reason to spend the remaining slice on sibling pin-bank
        # permutations; commit the complete cut and open the bounded joint
        # relocation epoch.
        return False
    if (
        ActiveTransactionalEndpointPortfolioCandidate
        and not TransactionalCutRevisitsAncestorInterface
        and not TransactionalExactPairAfterCoordinatedRepair
        and (
            TransactionalCutRepeatedAcrossAccessDistinctPlacements
            or (
                TransactionalCutStrictlyNarrowsParentInterface
                and AssignmentCutHasBoundedExactCore(
                    RoutingAssignmentCut.FromFailure(Failure)
                )
            )
        )
    ):
        # A local ECO that exposes a smaller exact cut is best-first repair
        # progress.  Advance that frontier immediately instead of spending
        # the remaining wall time on siblings of the superseded interface.
        return False
    return (
        TopologyRequiresJointPortfolio
        and RemainingRetainedActiveCandidates > 1
        and (
            (
                ActiveRelocatedPortfolioCandidate
                and Failure.Stage == "ClusterBoundaryLease"
                and Failure.Reason
                == RoutingFailureReason.BoundaryEscapeInfeasible
                and not FailureHasExhaustiveExactPairPinBankProof(Failure)
            )
            or (
                ActiveTransactionalEndpointPortfolioCandidate
                and not FailureHasExhaustiveExactPairPinBankProof(
                    Failure
                )
            )
        )
    )


def BuildTopologyCutEpochIdentity(
    AssignmentCut: RoutingAssignmentCut,
    Constraints: PlacementAssignmentConstraintSet,
) -> TopologyCutEpochIdentity:
    """Bind a bounded repair epoch to its cut, cumulative legality and owner."""
    return TopologyCutEpochIdentity(
        AssignmentCutFingerprint=AssignmentCut.ConflictFingerprint,
        AssignmentConstraintFingerprint=Constraints.Fingerprint,
        MandatoryAccessOwnershipFingerprint=(
            AssignmentCut.AccessTopologyFingerprint
        ),
    )


def PlacementMatchesTopologyCutEpoch(
    Placement: PcbPlacement,
    Epoch: TopologyCutEpochIdentity,
) -> bool:
    """Return whether materialized geometry belongs to one exact cut epoch."""
    Diagnostics = dict(Placement.Placed.LocalRouteDiagnostics or {})
    if not bool(Diagnostics.get("__JointClusterPlacement__", {})):
        return False
    Recipe = dict(Diagnostics.get("__PlacementRecipe__", {}))
    return (
        str(Recipe.get("AssignmentCutFingerprint", ""))
        == Epoch.AssignmentCutFingerprint
        and str(Recipe.get("AssignmentConstraintFingerprint", ""))
        == Epoch.AssignmentConstraintFingerprint
    )


def ShouldOpenTopologyCutEpoch(
    *,
    TopologyRequiresJointPortfolio: bool,
    AssignmentCut: RoutingAssignmentCut | None,
    Epoch: TopologyCutEpochIdentity | None,
    OpenedEpochs: Iterable[TopologyCutEpochIdentity],
) -> bool:
    """Open one fresh joint epoch only for a new mandatory capacity cut."""
    return (
        TopologyRequiresJointPortfolio
        and AssignmentCut is not None
        and (
            AssignmentCut.CompleteAssignmentCutProof
            or AssignmentCut.Classification
            in {
                RoutingAssignmentCutClassification.SaturatedBoundaryCut,
                RoutingAssignmentCutClassification.MandatoryAccessSelfConflict,
                RoutingAssignmentCutClassification.MandatoryBoundaryCapacityCut,
                RoutingAssignmentCutClassification.PortalCoveragePairConflict,
                RoutingAssignmentCutClassification.SparseRegionRouteCut,
            }
        )
        and bool(AssignmentCut.ConflictFingerprint)
        and (
            AssignmentCut.CompleteAssignmentCutProof
            or
            bool(AssignmentCut.AccessTopologyFingerprint)
            or AssignmentCut.Classification
            in {
                RoutingAssignmentCutClassification.SaturatedBoundaryCut,
                RoutingAssignmentCutClassification.MandatoryAccessSelfConflict,
                RoutingAssignmentCutClassification.SparseRegionRouteCut,
            }
        )
        and Epoch is not None
        and Epoch not in frozenset(OpenedEpochs)
    )


def AssignmentCutHasBoundedExactCore(
    AssignmentCut: RoutingAssignmentCut | None,
) -> bool:
    """Return whether one cut is small enough for a local repair probe.

    Pairwise edges are already minimal conflict cores.  A saturated
    authoritative interface cut with at most four signals is the equivalent
    bounded higher-order core.  A larger cut is also bounded when the exact
    pattern solver publishes observed pair interactions: the cluster
    refinement selector caps that neighborhood at four signals.
    """
    return bool(
        AssignmentCut is not None
        and (
            AssignmentCut.CompleteAssignmentCutProof
            or AssignmentCut.PairwiseConflictEdges
            or (
                AssignmentCut.Classification
                == RoutingAssignmentCutClassification.SaturatedBoundaryCut
                and 2 <= len(AssignmentCut.ConflictSignals) <= 4
            )
            or (
                AssignmentCut.Classification
                == RoutingAssignmentCutClassification.SaturatedBoundaryCut
                and any(
                    isinstance(Edge, tuple | list) and len(Edge) == 2
                    for Edge in AssignmentCut.ConflictGraph.get(
                        "ObservedPatternConflictEdges",
                        (),
                    )
                )
            )
            or (
                AssignmentCut.Classification
                == (
                    RoutingAssignmentCutClassification
                    .CandidateStarvationPlacementConflict
                )
                and 1
                <= len(
                    AssignmentCut.NoCandidateSignals
                    or AssignmentCut.ConflictSignals
                )
                <= 4
            )
            or (
                AssignmentCut.Classification
                == RoutingAssignmentCutClassification.SparseRegionRouteCut
                and 1 <= len(AssignmentCut.ConflictSignals) <= 4
            )
            or (
                AssignmentCut.Classification
                == (
                    RoutingAssignmentCutClassification
                    .MandatoryAccessSelfConflict
                )
                and 1 <= len(AssignmentCut.ConflictSignals) <= 4
            )
        )
    )


def BoundedAssignmentCutRepeatsAcrossDistinctOwnership(
    History: Iterable[RoutingAssignmentCut],
    Current: RoutingAssignmentCut | None,
) -> bool:
    """Detect a bounded exact cut repeated under different access ownership."""
    return bool(
        AssignmentCutHasBoundedExactCore(Current)
        and Current is not None
        and Current.ConflictFingerprint
        and Current.AccessTopologyFingerprint
        and any(
            AssignmentCutHasBoundedExactCore(Previous)
            and Previous.ConflictFingerprint
            == Current.ConflictFingerprint
            and bool(Previous.AccessTopologyFingerprint)
            and Previous.AccessTopologyFingerprint
            != Current.AccessTopologyFingerprint
            for Previous in History
        )
    )


def AssignmentCutRepeatsAcrossDistinctPlacementOwnership(
    History: Iterable[RoutingAssignmentCut],
    Current: RoutingAssignmentCut | None,
) -> bool:
    """Detect one exact cut preserved by access-distinct placed geometry."""
    return bool(
        Current is not None
        and Current.ConflictFingerprint
        and Current.MandatoryAccessOwnershipFingerprint
        and any(
            Previous.ConflictFingerprint
            == Current.ConflictFingerprint
            and bool(Previous.MandatoryAccessOwnershipFingerprint)
            and Previous.MandatoryAccessOwnershipFingerprint
            != Current.MandatoryAccessOwnershipFingerprint
            for Previous in History
        )
    )


def BoundedAssignmentSignalCutRepeatsAcrossDistinctOwnership(
    History: Iterable[RoutingAssignmentCut],
    Current: RoutingAssignmentCut | None,
) -> bool:
    """Detect a stable bounded signal cut despite changed exact patterns."""
    if (
        not AssignmentCutHasBoundedExactCore(Current)
        or Current is None
        or not Current.MandatoryAccessOwnershipFingerprint
    ):
        return False
    CurrentSignals = frozenset(
        Current.PriorityRelocationSignals
        or Current.NoCandidateSignals
        or Current.ConflictSignals
    )
    if not CurrentSignals:
        return False
    return any(
        AssignmentCutHasBoundedExactCore(Previous)
        and Previous.Classification == Current.Classification
        and bool(Previous.MandatoryAccessOwnershipFingerprint)
        and Previous.MandatoryAccessOwnershipFingerprint
        != Current.MandatoryAccessOwnershipFingerprint
        and frozenset(
            Previous.PriorityRelocationSignals
            or Previous.NoCandidateSignals
            or Previous.ConflictSignals
        )
        == CurrentSignals
        for Previous in History
    )


def CompleteAssignmentCutSupersedesLeasePairRetry(
    AssignmentCut: RoutingAssignmentCut | None,
) -> bool:
    """Prefer proved geometry feedback over a speculative same-slot retry."""
    return bool(
        AssignmentCut is not None
        and AssignmentCut.CompleteAssignmentCutProof
    )


def SelectTransactionalEndpointRepairSignals(
    AssignmentCut: RoutingAssignmentCut | None,
    *,
    InternalPinBankGeometryRepairActive: bool,
    PinBankRepairSignals: frozenset[str],
    CandidateIsTransactionalEndpointRepair: bool = False,
    ParentTransactionalRepairSignals: frozenset[str] = frozenset(),
    RepeatedAccessDistinctTransactionalCut: bool = False,
    ProvenSiblingStarvationSignals: frozenset[str] = frozenset(),
    AncestorTransactionalRepairSignalSets: tuple[
        frozenset[str], ...
    ] = (),
    ParentTransactionalRepairClusterCount: int = 1,
    AllowAncestorCutLocalRepair: bool = False,
    AllowPostDiversificationOwnershipRepair: bool = False,
) -> frozenset[str]:
    """Prefer the exact complete-proof frontier, then the existing pin bank."""
    if CandidateIsTransactionalEndpointRepair:
        ChildSignals = TransactionalCutRepairSignals(AssignmentCut)
        ProvenChildSignals = ChildSignals.intersection(
            ProvenSiblingStarvationSignals
        )
        # A witnessed rigid macro transform can legitimately expose a cut that
        # preceded this local-ECO branch.  Give that *one* cut a local repair
        # attempt instead of returning to a global row beam.  The caller only
        # enables this for the first such recurrence; normal ancestor cycles
        # remain rejected below.
        if AllowAncestorCutLocalRepair and ChildSignals:
            return ChildSignals
        # A coordinated local ECO may expose a structurally new exact
        # ownership cut after its one bounded candidate-domain
        # diversification.  Admit that new frontier once so ownership
        # coverage can select the minimum two-to-three-cluster repair.
        # The caller proves both the prior coordinated repair and that this
        # signal set is absent from the transactional ancestry.
        if AllowPostDiversificationOwnershipRepair and ChildSignals:
            return ChildSignals
        if TransactionalCutRevisitsAncestorInterface(
            AncestorTransactionalRepairSignalSets,
            ChildSignals or ProvenChildSignals,
        ) and not TransactionalCutMayEscalateRepairClusterCount(
            ParentTransactionalRepairSignals,
            ChildSignals or ProvenChildSignals,
            ParentTransactionalRepairClusterCount,
        ):
            return frozenset()
        return (
            ChildSignals
            if TransactionalCutStrictlyNarrowsParentInterface(
                ParentTransactionalRepairSignals,
                ChildSignals,
            )
            or (
                RepeatedAccessDistinctTransactionalCut
                and ChildSignals
            )
            else ProvenChildSignals
        )
    if (
        AssignmentCut is not None
        and AssignmentCut.CompleteAssignmentCutProof
        and AssignmentCut.PriorityRelocationSignals
    ):
        return frozenset(AssignmentCut.PriorityRelocationSignals)
    return (
        PinBankRepairSignals
        if InternalPinBankGeometryRepairActive
        else frozenset()
    )


def TransactionalCutRepairSignals(
    AssignmentCut: RoutingAssignmentCut | None,
) -> frozenset[str]:
    """Return the smallest authoritative interface published by one cut."""
    if (
        AssignmentCut is None
        or not AssignmentCutHasBoundedExactCore(AssignmentCut)
    ):
        return frozenset()
    return frozenset(
        AssignmentCut.PriorityRelocationSignals
        or AssignmentCut.NoCandidateSignals
        or AssignmentCut.ConflictSignals
    )


def TransactionalCutStrictlyNarrowsParentInterface(
    ParentSignals: frozenset[str],
    ChildSignals: frozenset[str],
) -> bool:
    """Admit only monotonic local-ECO descendants.

    A disjoint or equal cut is evidence that this local repair branch did not
    resolve its parent interface.  Returning to a retained access-distinct
    sibling is both more general and more useful than recursively moving an
    unrelated endpoint.
    """
    return bool(
        ParentSignals
        and ChildSignals
        and ChildSignals < ParentSignals
    )


def TransactionalCutRevisitsAncestorInterface(
    AncestorSignalSets: Iterable[frozenset[str]],
    ChildSignals: frozenset[str],
) -> bool:
    """Reject a local-ECO cycle even when placement ownership changes."""
    return bool(
        ChildSignals
        and any(
            ChildSignals == frozenset(map(str, AncestorSignals))
            for AncestorSignals in AncestorSignalSets
        )
    )


def TransactionalCutMayEscalateRepairClusterCount(
    ParentSignals: frozenset[str],
    ChildSignals: frozenset[str],
    ParentRepairClusterCount: int,
) -> bool:
    """Allow one same-interface step from one-cluster to coordinated repair."""
    return bool(
        ParentSignals
        and ChildSignals == ParentSignals
        and ParentRepairClusterCount <= 1
    )


def ShouldAdmitPostDiversificationOwnershipRepair(
    AssignmentCut: RoutingAssignmentCut | None,
    *,
    TopologyRequiresJointPortfolio: bool,
    CandidateIsTransactionalEndpointRepair: bool,
    ParentTransactionalRepairClusterCount: int,
    CandidateDiversificationFixedLevel: int,
    ParentTransactionalRepairSignals: frozenset[str],
    TransactionalRepairSignalHistory: Iterable[frozenset[str]],
) -> bool:
    """Admit one new exact ownership frontier after coordinated repair."""
    Signals = TransactionalCutRepairSignals(AssignmentCut)
    History = tuple(
        frozenset(map(str, PriorSignals))
        for PriorSignals in TransactionalRepairSignalHistory
    )
    return bool(
        TopologyRequiresJointPortfolio
        and CandidateIsTransactionalEndpointRepair
        and ParentTransactionalRepairClusterCount >= 2
        and CandidateDiversificationFixedLevel == 1
        and TransactionalCutRequiresCoordinatedClusterRepair(AssignmentCut)
        and Signals
        and not TransactionalCutStrictlyNarrowsParentInterface(
            ParentTransactionalRepairSignals,
            Signals,
        )
        and Signals not in History
    )


def SelectTransactionalRepairClusterCount(
    *,
    CandidateIsTransactionalEndpointRepair: bool,
    RepeatedAccessDistinctTransactionalCut: bool,
    CutStrictlyNarrowsParentInterface: bool = False,
    ExactBoundaryPairCut: bool = False,
    AllowInitialExactBoundaryCutRepair: bool = False,
) -> int:
    """Coordinate repeated repairs and exact two-sided boundary pairs."""
    return (
        2
        if (
            (
                CandidateIsTransactionalEndpointRepair
                or AllowInitialExactBoundaryCutRepair
            )
            and (
                ExactBoundaryPairCut
                or (
                    RepeatedAccessDistinctTransactionalCut
                    and not CutStrictlyNarrowsParentInterface
                )
            )
        )
        else 1
    )


def TransactionalCutRequiresCoordinatedClusterRepair(
    AssignmentCut: RoutingAssignmentCut | None,
) -> bool:
    """Return whether a small exact boundary cut needs joint endpoints."""
    return bool(
        AssignmentCutHasBoundedExactCore(AssignmentCut)
        and AssignmentCut is not None
        and 2 <= len(TransactionalCutRepairSignals(AssignmentCut)) <= 4
        and AssignmentCut.Classification
        in {
            RoutingAssignmentCutClassification.SaturatedBoundaryCut,
            RoutingAssignmentCutClassification
            .MandatoryBoundaryCapacityCut,
            RoutingAssignmentCutClassification
            .PortalCoveragePairConflict,
            RoutingAssignmentCutClassification.PairwiseIncompatibility,
            RoutingAssignmentCutClassification
            .RelocatedPairwiseIncompatibility,
        }
    )


def ShouldStopTransactionalRepairVariantGeneration(
    *,
    CandidateIsTransactionalEndpointRepair: bool,
    RepairClusterCount: int,
    VariantPublished: bool,
) -> bool:
    """Keep a proved coordinated repair's bounded access-distinct siblings."""
    return bool(
        VariantPublished
        and CandidateIsTransactionalEndpointRepair
        and RepairClusterCount <= 1
    )


def ShouldBoundClusterPinBankRepairProbe(
    HasClusterPinBankRepair: bool,
    IsTransactionalEndpointRepair: bool,
) -> bool:
    """Bound diagnostic pin-bank probes, not committed access-distinct ECOs."""
    return (
        HasClusterPinBankRepair
        and not IsTransactionalEndpointRepair
    )


def ShouldWidenTopologyCutTerminalShell(
    *,
    TopologyRequiresJointPortfolio: bool,
    AssignmentCut: RoutingAssignmentCut | None,
    ExternalSignals: Iterable[str],
) -> bool:
    """Open lateral terminal access only for a reported external cut signal."""
    if not TopologyRequiresJointPortfolio or AssignmentCut is None:
        return False
    CutSignals = frozenset((
        *AssignmentCut.ConflictSignals,
        *AssignmentCut.RelocationSignals,
        *AssignmentCut.PriorityRelocationSignals,
        *AssignmentCut.NoCandidateSignals,
    ))
    return bool(CutSignals & frozenset(map(str, ExternalSignals)))


def BuildPendingJointPlacementPortfolioIdentity(
    State: PendingJointPlacementState,
) -> PendingJointPlacementPortfolioIdentity:
    """Return the exact recipe epoch shared by retained candidate indexes."""
    return PendingJointPlacementPortfolioIdentity(
        SourceGenerator=State.Request.SourceGenerator,
        RequestRoutingSpacing=State.Request.RoutingSpacing,
        PackingPolicyFingerprint=BuildStableFingerprint(
            repr(State.Request.PackingPolicy)
        ),
        UseCurrentAssignmentCutRelocationSignals=(
            State.Request.UseCurrentAssignmentCutRelocationSignals
        ),
        RoutingSpacing=State.RoutingSpacing,
        RelocationVariant=State.RelocationVariant,
        RelocationSignals=tuple(sorted(State.RelocationSignals)),
        RelocationPrioritySignals=tuple(sorted(
            State.RelocationPrioritySignals
        )),
        RequiredRelocationSignals=tuple(sorted(
            State.RequiredRelocationSignals
        )),
        AssignmentCutFingerprint=(
            State.AssignmentCut.ConflictFingerprint
            if State.AssignmentCut is not None
            else ""
        ),
        AssignmentCutWorkFingerprint=(
            State.AssignmentCut.EffectiveWorkFingerprint
            if State.AssignmentCut is not None
            else ""
        ),
        AssignmentConstraintFingerprint=(
            State.AssignmentConstraints.Fingerprint
        ),
        CoordinatedSignals=tuple(sorted(
            State.CoordinatedCandidateDiversificationSignals
        )),
        InternalPinBankGeometryRepairSignals=tuple(sorted(
            State.InternalPinBankGeometryRepairSignals
        )),
        RequiredDistinctPinBankOwnershipFingerprint=(
            State.RequiredDistinctPinBankOwnershipFingerprint
        ),
        TopologyCutFrontierFingerprints=tuple(
            (
                Cut.ConflictFingerprint,
                Cut.EffectiveWorkFingerprint,
            )
            for Cut in State.TopologyCutFrontier
        ),
    )


def BuildPendingJointPlacementStateKey(
    State: PendingJointPlacementState,
) -> tuple[PendingJointPlacementPortfolioIdentity, int]:
    """Return the complete identity of one retained candidate state."""
    return (
        BuildPendingJointPlacementPortfolioIdentity(State),
        State.CandidateIndex,
    )


def BuildPendingJointPlacementPortfolioFingerprint(
    State: PendingJointPlacementState,
) -> str:
    """Return a compact exact identity for one retained joint portfolio."""
    return BuildStableFingerprint(
        repr(BuildPendingJointPlacementPortfolioIdentity(State))
    )


def RetainUnmaterializedJointPlacementStates(
    ExistingStates: Iterable[PendingJointPlacementState],
    DeferredStates: Iterable[PendingJointPlacementState],
    MaterializedStateKeys: Iterable[
        tuple[PendingJointPlacementPortfolioIdentity, int]
    ] = (),
) -> list[PendingJointPlacementState]:
    """Prepend untouched exact states without reviving consumed duplicates."""
    MaterializedKeys = frozenset(MaterializedStateKeys)
    Retained: list[PendingJointPlacementState] = []
    SeenKeys: set[
        tuple[PendingJointPlacementPortfolioIdentity, int]
    ] = set()
    for State in (*tuple(DeferredStates), *tuple(ExistingStates)):
        StateKey = BuildPendingJointPlacementStateKey(State)
        if StateKey in MaterializedKeys or StateKey in SeenKeys:
            continue
        SeenKeys.add(StateKey)
        Retained.append(State)
    return Retained


@dataclass(frozen=True)
class MandatoryAccessPortfolioIdentity:
    """Immutable identity for one exact joint/access candidate portfolio."""

    ExactScreenFingerprint: str
    SourceGenerator: str
    RoutingSpacing: int
    RelocationVariant: int
    AssignmentCutFingerprint: str
    AssignmentConstraintFingerprint: str
    CoordinatedSignals: tuple[str, ...] = ()


@dataclass(frozen=True)
class MandatoryAccessPortfolioRejection:
    """One exact mandatory-access rejection within a retained portfolio."""

    CandidateIndex: int
    OwnershipFingerprint: str
    ConflictFingerprint: str
    PairwiseConflictEdges: tuple[tuple[str, str], ...] = ()


@dataclass
class MandatoryAccessPortfolioEvidence:
    """Run-local rejection evidence for one complete retained portfolio."""

    ExpectedCandidateIndices: tuple[int, ...]
    RejectionsByCandidateIndex: dict[
        int,
        MandatoryAccessPortfolioRejection,
    ]
    Finalized: bool = False


@dataclass(frozen=True)
class MandatoryAccessPortfolioEvaluation:
    """Pure completeness/access-distinctness verdict for one portfolio."""

    Verdict: str
    PairwiseConflictEdges: tuple[tuple[str, str], ...] = ()
    NewPairwiseConflictEdges: tuple[tuple[str, str], ...] = ()
    MissingCandidateIndices: tuple[int, ...] = ()
    UnexpectedCandidateIndices: tuple[int, ...] = ()
    MissingOwnershipCandidateIndices: tuple[int, ...] = ()
    DuplicateOwnershipFingerprints: tuple[str, ...] = ()

    @property
    def ShouldPromote(self) -> bool:
        return self.Verdict == "promote"


def BuildMandatoryAccessPortfolioRecipeIdentity(
    Identity: MandatoryAccessPortfolioIdentity,
    AssignmentConstraintFingerprint: str | None = None,
) -> MandatoryAccessPortfolioIdentity:
    """Normalize retained siblings to their shared recipe/cut epoch."""
    return replace(
        Identity,
        ExactScreenFingerprint="",
        AssignmentConstraintFingerprint=(
            Identity.AssignmentConstraintFingerprint
            if AssignmentConstraintFingerprint is None
            else AssignmentConstraintFingerprint
        ),
    )


def ShouldOpenStrongMandatoryAccessRepair(
    Evaluation: MandatoryAccessPortfolioEvaluation,
    *,
    IdentityStillCurrent: bool,
    AlreadyConsumed: bool,
) -> bool:
    """Admit one internal pin-bank ECO after rigid repair is fully disproved."""
    return (
        IdentityStillCurrent
        and not AlreadyConsumed
        and (
            Evaluation.ShouldPromote
            or Evaluation.Verdict == "already-represented"
        )
    )


def BuildMandatoryAccessPairwiseEdges(
    Profile: MandatoryAccessConflictProfile,
) -> tuple[tuple[str, str], ...]:
    """Project exact cross-owner resource conflicts into canonical pair edges."""
    return tuple(sorted({
        tuple(sorted((First, Second)))
        for _Resource, Owners in Profile.CrossConflicts
        for First, Second in combinations(
            tuple(sorted(set(map(str, Owners)))),
            2,
        )
        if First != Second
    }))


def BuildMandatoryAccessPortfolioExpectedCandidateIndices(
    JointDiagnostics: dict[str, object],
    SelectedCandidateIndex: int,
    RetainedCandidateLimit: int,
) -> tuple[int, ...]:
    """Return the exact bounded state set that the flow can materialize."""
    if RetainedCandidateLimit < 1:
        raise ValueError("Retained candidate limit must be positive")
    ExactLegalIndices = tuple(sorted({
        int(State["CandidateIndex"])
        for State in JointDiagnostics.get(
            "ExactLegalRetainedStates",
            (),
        )
        if isinstance(State, dict)
        and "CandidateIndex" in State
    }))
    if SelectedCandidateIndex not in ExactLegalIndices:
        return ()
    Alternatives = tuple(
        CandidateIndex
        for CandidateIndex in ExactLegalIndices
        if CandidateIndex != SelectedCandidateIndex
    )
    return (
        SelectedCandidateIndex,
        *Alternatives[:max(0, RetainedCandidateLimit - 1)],
    )


def EvaluateCompleteMandatoryAccessPortfolio(
    Evidence: MandatoryAccessPortfolioEvidence,
    Constraints: PlacementAssignmentConstraintSet,
) -> MandatoryAccessPortfolioEvaluation:
    """Require complete access-distinct rejection proof before adding edges."""
    Expected = frozenset(Evidence.ExpectedCandidateIndices)
    Observed = frozenset(Evidence.RejectionsByCandidateIndex)
    Missing = tuple(sorted(Expected - Observed))
    Unexpected = tuple(sorted(Observed - Expected))
    if Missing or Unexpected:
        return MandatoryAccessPortfolioEvaluation(
            Verdict="incomplete",
            MissingCandidateIndices=Missing,
            UnexpectedCandidateIndices=Unexpected,
        )
    MissingOwnership = tuple(sorted(
        CandidateIndex
        for CandidateIndex in Evidence.ExpectedCandidateIndices
        if not Evidence.RejectionsByCandidateIndex[
            CandidateIndex
        ].OwnershipFingerprint
    ))
    OwnershipFingerprints = tuple(
        Evidence.RejectionsByCandidateIndex[
            CandidateIndex
        ].OwnershipFingerprint
        for CandidateIndex in Evidence.ExpectedCandidateIndices
        if Evidence.RejectionsByCandidateIndex[
            CandidateIndex
        ].OwnershipFingerprint
    )
    DuplicateOwnership = tuple(sorted({
        Fingerprint
        for Fingerprint in OwnershipFingerprints
        if OwnershipFingerprints.count(Fingerprint) > 1
    }))
    if (
        MissingOwnership
        or DuplicateOwnership
        or len(OwnershipFingerprints) < 2
    ):
        return MandatoryAccessPortfolioEvaluation(
            Verdict="access-not-distinct",
            MissingOwnershipCandidateIndices=MissingOwnership,
            DuplicateOwnershipFingerprints=DuplicateOwnership,
        )
    PairwiseEdges = tuple(sorted({
        Edge
        for Rejection in Evidence.RejectionsByCandidateIndex.values()
        for Edge in Rejection.PairwiseConflictEdges
    }))
    if not PairwiseEdges:
        return MandatoryAccessPortfolioEvaluation(
            Verdict="no-cross-owner-pair-evidence",
        )
    ExistingEdges = frozenset(Constraints.PairwiseConflictEdges)
    NewEdges = tuple(
        Edge for Edge in PairwiseEdges if Edge not in ExistingEdges
    )
    if not NewEdges:
        return MandatoryAccessPortfolioEvaluation(
            Verdict="already-represented",
            PairwiseConflictEdges=PairwiseEdges,
        )
    return MandatoryAccessPortfolioEvaluation(
        Verdict="promote",
        PairwiseConflictEdges=PairwiseEdges,
        NewPairwiseConflictEdges=NewEdges,
    )


def AddMandatoryAccessPortfolioPairwiseConstraints(
    Constraints: PlacementAssignmentConstraintSet,
    Evaluation: MandatoryAccessPortfolioEvaluation,
) -> PlacementAssignmentConstraintSet:
    """Add only newly proven pair edges while preserving every prior cut."""
    if not Evaluation.ShouldPromote:
        return Constraints
    return PlacementAssignmentConstraintSet(
        PairwiseConflictEdges=(
            *Constraints.PairwiseConflictEdges,
            *Evaluation.NewPairwiseConflictEdges,
        ),
        HigherOrderSignalSets=Constraints.HigherOrderSignalSets,
        ObservedInterfaceConflictEdges=(
            Constraints.ObservedInterfaceConflictEdges
        ),
        HigherOrderSignalEvidence=(
            Constraints.HigherOrderSignalEvidence
        ),
        ObservedInterfaceConflictEvidence=(
            Constraints.ObservedInterfaceConflictEvidence
        ),
        ActiveHigherOrderSignalSets=(
            Constraints.ActiveHigherOrderSignalSets
        ),
        ActiveObservedInterfaceConflictEdges=(
            Constraints.ActiveObservedInterfaceConflictEdges
        ),
    )


def MandatoryAccessPortfolioIdentityMatchesCurrent(
    Identity: MandatoryAccessPortfolioIdentity,
    CurrentCut: RoutingAssignmentCut | None,
    CurrentConstraints: PlacementAssignmentConstraintSet,
) -> bool:
    """Reject evidence from a superseded cut or constraint epoch."""
    return (
        CurrentCut is not None
        and CurrentCut.ConflictFingerprint
        == Identity.AssignmentCutFingerprint
        and CurrentConstraints.Fingerprint
        == Identity.AssignmentConstraintFingerprint
    )


def ShouldPrioritizePlacementConflictRelocation(
    *,
    PreferRelocation: bool,
    RelocationSignals: frozenset[str],
    TotalRelocationGenerationCount: int,
    MaximumFeedbackRounds: int,
    RelocationPrioritySignals: frozenset[str],
    LastRelocationPrioritySignalsUsed: frozenset[str],
    RequiredRelocationSignals: frozenset[str],
    LastRequiredRelocationSignalsUsed: frozenset[str],
    CurrentAssignmentCutFingerprint: str,
    LastAssignmentCutFingerprintUsed: str,
    CurrentAssignmentConstraintFingerprint: str,
    LastAssignmentConstraintFingerprintUsed: str,
) -> bool:
    """Prioritize exact packed feedback when any typed input epoch changed."""
    return (
        PreferRelocation
        and bool(RelocationSignals)
        and TotalRelocationGenerationCount < MaximumFeedbackRounds
        and (
            TotalRelocationGenerationCount == 0
            or RelocationPrioritySignals
            != LastRelocationPrioritySignalsUsed
            or RequiredRelocationSignals
            != LastRequiredRelocationSignalsUsed
            or (
                bool(CurrentAssignmentCutFingerprint)
                and CurrentAssignmentCutFingerprint
                != LastAssignmentCutFingerprintUsed
            )
            or CurrentAssignmentConstraintFingerprint
            != LastAssignmentConstraintFingerprintUsed
        )
    )


def ShouldPrioritizeTopologyCutEpochRelocation(
    *,
    TopologyRequiresJointPortfolio: bool,
    HasRelocationSignals: bool,
    TotalRelocationGenerationCount: int,
    MaximumFeedbackRounds: int,
    CurrentAssignmentCutFingerprint: str,
    LastAssignmentCutFingerprintUsed: str,
) -> bool:
    """Replace one broad fallback with a new, typed topology cut epoch."""
    return (
        TopologyRequiresJointPortfolio
        and HasRelocationSignals
        and MaximumFeedbackRounds > 0
        and TotalRelocationGenerationCount == MaximumFeedbackRounds
        and bool(CurrentAssignmentCutFingerprint)
        and CurrentAssignmentCutFingerprint
        != LastAssignmentCutFingerprintUsed
    )


def ShouldPrioritizeCurrentExactCutBeforeBroad(
    *,
    Required: bool,
    PreferRelocation: bool,
    HasCurrentAssignmentCut: bool,
    HasRelocationSignals: bool,
    TotalRelocationGenerationCount: int,
    MaximumFeedbackRounds: int,
) -> bool:
    """Keep a newly authoritative exact epoch ahead of broad generators."""
    return (
        Required
        and PreferRelocation
        and HasCurrentAssignmentCut
        and HasRelocationSignals
        and TotalRelocationGenerationCount < MaximumFeedbackRounds
    )


def PendingJointPlacementStateMatchesIdentity(
    State: PendingJointPlacementState,
    CurrentCutFingerprint: str,
    CurrentConstraintFingerprint: str,
) -> bool:
    """Match retained work to both its current cut and learned constraints."""
    return (
        State.AssignmentCut is not None
        and State.AssignmentCut.ConflictFingerprint
        == CurrentCutFingerprint
        and State.AssignmentConstraints.Fingerprint
        == CurrentConstraintFingerprint
    )


def HasCurrentPendingJointPlacementState(
    States: Iterable[PendingJointPlacementState],
    CurrentCutFingerprint: str,
    CurrentConstraintFingerprint: str,
) -> bool:
    """Return whether an untried retained sibling belongs to this cut epoch."""
    return bool(CurrentCutFingerprint) and any(
        PendingJointPlacementStateMatchesIdentity(
            State,
            CurrentCutFingerprint,
            CurrentConstraintFingerprint,
        )
        for State in States
    )


def HasCurrentMaterializedJointPlacementCandidate(
    Candidates: Iterable[PcbPlacementCandidate],
    AttemptedFingerprints: frozenset[str] | set[str],
    CurrentCutFingerprint: str,
    CurrentConstraintFingerprint: str,
) -> bool:
    """Return whether a routed-next sibling belongs to this exact cut epoch."""
    return bool(CurrentCutFingerprint) and any(
        Candidate.JointPortfolioCandidate
        and Candidate.PlacementFingerprint not in AttemptedFingerprints
        and Candidate.AssignmentCutFingerprint == CurrentCutFingerprint
        and Candidate.AssignmentConstraintFingerprint
        == CurrentConstraintFingerprint
        for Candidate in Candidates
    )


def PlacementCandidateMatchesActiveJointPortfolio(
    Candidate: PcbPlacementCandidate,
    ActivePortfolioIdentityFingerprint: str,
) -> bool:
    """Keep only exact siblings of the active relocated portfolio."""
    return (
        bool(ActivePortfolioIdentityFingerprint)
        and Candidate.JointPortfolioCandidate
        and Candidate.SourceGenerator in {
            "row-beam-conflict-relocation",
            "transactional-cluster-endpoint-repair",
        }
        and Candidate.JointPortfolioIdentityFingerprint
        == ActivePortfolioIdentityFingerprint
    )


def HasActiveMaterializedJointPlacementCandidate(
    Candidates: Iterable[PcbPlacementCandidate],
    AttemptedFingerprints: frozenset[str] | set[str],
    ActivePortfolioIdentityFingerprint: str,
) -> bool:
    """Return whether the active relocated portfolio still has a route trial."""
    return any(
        Candidate.PlacementFingerprint not in AttemptedFingerprints
        and PlacementCandidateMatchesActiveJointPortfolio(
            Candidate,
            ActivePortfolioIdentityFingerprint,
        )
        for Candidate in Candidates
    )


def HasTopologyCutEpochRoutingReserve(
    *,
    RemainingSeconds: float,
    Policy: PhysicalDesignPolicy,
    RequiresDenseBoundaryRouting: bool,
    HasBoundedExactCutEvidence: bool = False,
) -> bool:
    """Admit a fresh cut epoch only with a complete exact-routing reserve.

    A cut epoch cancels its pending siblings.  Opening one after the shared
    deadline can no longer fund an exact route replaces an already legal,
    access-distinct candidate with placement churn.  Reuse the existing
    immutable routing reserve instead of expanding any budget.
    """
    return RemainingSeconds >= TopologyCutEpochAdmissionReserveSeconds(
        Policy,
        RequiresDenseBoundaryRouting,
        HasBoundedExactCutEvidence=HasBoundedExactCutEvidence,
    )


def TopologyCutEpochRoutingReserveSeconds(
    Policy: PhysicalDesignPolicy,
    RequiresDenseBoundaryRouting: bool,
    *,
    HasBoundedExactCutEvidence: bool = False,
) -> float:
    """Return the minimum viable exact route slice for a new cut epoch.

    A topology epoch replaces already-screened access-distinct siblings.  Its
    admission floor must therefore fund a real exact route attempt, not only
    the small generic placement-generation reserve.  This is a scheduler
    reallocation within the existing deadline: dense designs retain their
    larger established reserve, while other topology-triggered designs keep
    one fifth of the fixed wall-clock budget for the replacement state.
    """
    BoundedCoreReserve = (
        HasBoundedExactCutEvidence and not RequiresDenseBoundaryRouting
    )
    BoundedCoreProbeSeconds = min(
        5.0,
        max(0.01, Policy.RuntimeBudgetSeconds * 0.15),
    )
    GenericReserve = (
        min(
            Policy.AdaptiveRouting.MaximumRuntimeSeconds,
            BoundedCoreProbeSeconds,
        )
        if BoundedCoreReserve
        else PlacementGenerationRoutingReserveSeconds(
            Policy,
            RequiresDenseBoundaryRouting,
        )
    )
    ExactRouteSlice = min(
        Policy.AdaptiveRouting.MaximumRuntimeSeconds,
        max(
            0.01,
            (
                BoundedCoreProbeSeconds
                if BoundedCoreReserve
                else Policy.RuntimeBudgetSeconds * 0.20
            ),
        ),
    )
    return max(GenericReserve, ExactRouteSlice)


def TopologyCutEpochAdmissionReserveSeconds(
    Policy: PhysicalDesignPolicy,
    RequiresDenseBoundaryRouting: bool,
    *,
    HasBoundedExactCutEvidence: bool = False,
) -> float:
    """Reserve bounded geometry materialization plus one exact route slice.

    Opening an epoch discards stale candidates and must construct a new joint
    geometry before it can spend its routing allocation.  Counting only the
    latter admitted late epochs that reached their first route attempt with a
    few seconds remaining.  This is an admission floor within the fixed
    deadline, not an increase to either placement or routing work.
    """
    RoutingSeconds = TopologyCutEpochRoutingReserveSeconds(
        Policy,
        RequiresDenseBoundaryRouting,
        HasBoundedExactCutEvidence=HasBoundedExactCutEvidence,
    )
    BoundedCoreReserve = (
        HasBoundedExactCutEvidence and not RequiresDenseBoundaryRouting
    )
    MaterializationSeconds = min(
        Policy.AdaptiveRouting.MaximumRuntimeSeconds,
        max(
            0.01,
            (
                min(7.0, Policy.RuntimeBudgetSeconds * 0.10)
                if BoundedCoreReserve
                else Policy.RuntimeBudgetSeconds * 0.15
            ),
        ),
    )
    return min(
        Policy.RuntimeBudgetSeconds,
        RoutingSeconds + MaterializationSeconds,
    )


def ShouldUseMandatoryAccessPreScreen(
    *,
    SourceGenerator: str,
    PackingEnabled: bool,
    JointOrientationEnabled: bool,
    HasRelocationSignals: bool,
    TopologyRequiresJointPortfolio: bool,
    HasAssignmentCut: bool,
    AssignmentConstraintsActive: bool,
) -> bool:
    """Select the monotone internal-access screen before expensive routing."""
    StructuredJointEpoch = (
        JointOrientationEnabled
        and (HasAssignmentCut or AssignmentConstraintsActive)
    )
    return (
        SourceGenerator.startswith("row-beam")
        and PackingEnabled
        and TopologyRequiresJointPortfolio
        and (
            StructuredJointEpoch
            or (
                not HasRelocationSignals
                and not JointOrientationEnabled
            )
        )
    )


def ShouldRejectCutBoundaryEscapePlacement(
    *,
    TopologyRequiresJointPortfolio: bool,
    Diagnostics: object,
) -> bool:
    """Reject only an exhaustive topology-cut first-escape proof.

    Missing diagnostics and bounded-search exhaustion remain authoritative
    routing work. This keeps non-triggered placement behavior unchanged.
    """
    return bool(
        TopologyRequiresJointPortfolio
        and isinstance(Diagnostics, dict)
        and Diagnostics.get("Verdict") == "infeasible"
    )


@dataclass(frozen=True)
class CoordinatedCandidateDiversificationProfile:
    """Canonical routing-only controls for one reported assignment cut."""

    Signals: tuple[str, ...]
    DiversityLevel: int
    Fingerprint: str


def BuildCoordinatedCandidateDiversificationProfile(
    Signals: Iterable[str],
) -> CoordinatedCandidateDiversificationProfile:
    """Return the name-preserving but order-independent profile identity."""
    OrderedSignals = tuple(sorted(set(map(str, Signals))))
    DiversityLevel = 1 if OrderedSignals else 0
    return CoordinatedCandidateDiversificationProfile(
        Signals=OrderedSignals,
        DiversityLevel=DiversityLevel,
        Fingerprint=BuildStableFingerprint({
            "CoordinatedCandidateDiversificationSignals": list(
                OrderedSignals
            ),
            "CoordinatedCandidateDiversityLevel": DiversityLevel,
        }),
    )


@dataclass(frozen=True)
class AccessDistinctAssignmentCutDiversificationEvidence:
    """Typed proof that a routing-only retry is warranted for this cut."""

    RepeatedExactCut: bool = False
    RefinedExactCut: bool = False
    RepeatedExactSubcut: bool = False
    ExhaustedRepeaterAccessCut: bool = False

    @property
    def IsProven(self) -> bool:
        """Require an access-distinct exact cut or repeated structural pair."""
        return (
            self.RepeatedExactCut
            or self.RepeatedExactSubcut
            or self.ExhaustedRepeaterAccessCut
        )

    @property
    def Kinds(self) -> tuple[str, ...]:
        return tuple(
            Kind
            for Enabled, Kind in (
                (self.RepeatedExactCut, "repeated-exact-cut"),
                (self.RefinedExactCut, "refined-exact-cut"),
                (self.RepeatedExactSubcut, "repeated-exact-subcut"),
                (
                    self.ExhaustedRepeaterAccessCut,
                    "exhausted-repeater-access-cut",
                ),
            )
            if Enabled
        )


def SelectExhaustedRepeaterAccessCutSignals(
    Failure: RoutingFailure,
) -> frozenset[str]:
    """Select a proved local power-access cut for one route-only retry."""
    if (
        Failure.Reason != RoutingFailureReason.RepeaterAccessInfeasible
        or Failure.Stage != "NegotiatedDetailedRouting"
        or not Failure.AffectedNets
    ):
        return frozenset()
    Diagnostics = Failure.Diagnostics or {}
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    Region = Diagnostics.get("Region", {})
    SearchExpansionEscalations = Diagnostics.get(
        "SearchExpansionEscalations",
        {},
    )
    if (
        not isinstance(ConflictGraph, dict)
        or ConflictGraph.get("Classification") != "sparse-region-route-cut"
        or not isinstance(Region, dict)
        or not Region.get("ExpandedSides")
        or not isinstance(SearchExpansionEscalations, dict)
    ):
        return frozenset()
    Signals = frozenset(map(str, Failure.AffectedNets))
    if not Signals <= frozenset(map(str, SearchExpansionEscalations)):
        return frozenset()
    NativeSearch = Region.get("NativeSearch", ())
    if not isinstance(NativeSearch, tuple | list) or not NativeSearch:
        return frozenset()
    RelevantSearch = tuple(
        Entry for Entry in NativeSearch if isinstance(Entry, dict)
    )
    if (
        not RelevantSearch
        or any(
            Entry.get("Status") != "NoPath"
            or Entry.get("NoPathReason") != "SearchLimitReached"
            or bool(Entry.get("BoundaryFrontierNodes"))
            for Entry in RelevantSearch
        )
        or sum(
            max(0, int(Entry.get("RepeaterRejectedCount", 0)))
            for Entry in RelevantSearch
        )
        <= 0
        or sum(
            max(0, int(Entry.get("RepeaterReservationCount", 0)))
            for Entry in RelevantSearch
        )
        != 0
    ):
        return frozenset()
    return Signals


def SelectTopologyCoordinatedCandidateDiversificationSignals(
    *,
    TopologyRequiresJointPortfolio: bool,
    RepeatedExactCut: bool,
    CompleteCutSignals: Iterable[str],
    RepeatedSubcutSignals: Iterable[str],
) -> frozenset[str]:
    """Authorize the smallest proven routing-domain repair for topology flow.

    A complete repeated structural cut can diversify every reported endpoint.
    When only a capacity-one pair repeats through a distinct mandatory-access
    ownership topology, retain only that pair.  The compact non-topology flow
    deliberately never receives this CLA-oriented routing control.
    """
    if not TopologyRequiresJointPortfolio:
        return frozenset()
    if RepeatedExactCut:
        return frozenset(map(str, CompleteCutSignals))
    return frozenset(map(str, RepeatedSubcutSignals))


def SelectRepeatedHigherOrderPinBankRepairSignals(
    *,
    TopologyAccessRepairEligible: bool,
    RepeatedAcrossAccessDistinctPlacements: bool,
    CandidatePostPinBankRepairEpoch: bool,
    AssignmentCut: RoutingAssignmentCut | None,
) -> frozenset[str]:
    """Select one cut-local internal-interface repair after rigid repeats."""
    if (
        not TopologyAccessRepairEligible
        or not RepeatedAcrossAccessDistinctPlacements
        or CandidatePostPinBankRepairEpoch
    ):
        return frozenset()
    return frozenset(
        BuildAssignmentCutHigherOrderSignalSet(AssignmentCut)
    )


def SelectExhaustiveExactPairPinBankRepairSignals(
    *,
    TopologyRequiresJointPortfolio: bool,
    CandidatePostPinBankRepairEpoch: bool,
    AssignmentCut: RoutingAssignmentCut | None,
    Failure: RoutingFailure,
) -> frozenset[str]:
    """Select one exact pair whose complete local domain has no solution.

    A cut-local joint search is stronger evidence than observing the same
    pair after another global placement: it has already enumerated the
    bounded portal-pattern variants for those two endpoints.  When that
    exhaustive search reports no solution, another rigid cluster move is
    lower-value than changing the endpoint clusters' internal pin banks.
    This repair is deliberately limited to the reconvergent topology trigger;
    dense ripple interfaces retain their established lease scheduler.
    """
    if (
        not TopologyRequiresJointPortfolio
        or CandidatePostPinBankRepairEpoch
        or AssignmentCut is None
    ):
        return frozenset()
    PairEdges = {
        tuple(sorted((str(First), str(Second))))
        for First, Second in AssignmentCut.PairwiseConflictEdges
        if str(First) != str(Second)
    }
    if len(PairEdges) != 1:
        return frozenset()
    Pair = next(iter(PairEdges))
    PairSignals = frozenset(Pair)
    if len(PairSignals) != 2:
        return frozenset()
    Diagnostics = (
        Failure.Diagnostics
        if isinstance(Failure.Diagnostics, Mapping)
        else {}
    )
    PatternSearch = Diagnostics.get("ClusterInterfacePatternSearch", {})
    if (
        not isinstance(PatternSearch, Mapping)
        or not PatternSearch.get("Applied")
        or not PatternSearch.get("CoreShrinkComplete")
    ):
        return frozenset()
    UnavoidableEdges = {
        tuple(sorted((str(First), str(Second))))
        for First, Second in PatternSearch.get(
            "UnavoidablePairEdges",
            (),
        )
        if str(First) != str(Second)
    }
    if Pair not in UnavoidableEdges:
        return frozenset()
    for Search in PatternSearch.get("CutLocalJointSearches", ()):
        if not isinstance(Search, Mapping):
            continue
        SearchSignals = frozenset(map(str, Search.get("CutSignals", ())))
        SearchEdges = {
            tuple(sorted((str(First), str(Second))))
            for First, Second in Search.get("CutEdges", ())
            if str(First) != str(Second)
        }
        if (
            SearchSignals == PairSignals
            and Pair in SearchEdges
            and not Search.get("BudgetExhausted")
            and int(Search.get("SearchVariantCount", 0)) > 0
            and int(Search.get("ExpansionCount", 0)) > 0
            and int(Search.get("SolutionCount", 0)) == 0
            and int(Search.get("FailedStateCount", 0)) > 0
        ):
            return PairSignals
    return frozenset()


def FailureHasExhaustiveExactPairPinBankProof(
    Failure: RoutingFailure,
) -> bool:
    """Return whether retained rigid siblings are redundant for this pair."""
    AssignmentCut = RoutingAssignmentCut.FromFailure(Failure)
    return bool(SelectExhaustiveExactPairPinBankRepairSignals(
        TopologyRequiresJointPortfolio=True,
        CandidatePostPinBankRepairEpoch=False,
        AssignmentCut=AssignmentCut,
        Failure=Failure,
    ))


def PinBankRepairOwnershipIsDistinct(
    RequiredDistinctOwnershipFingerprint: str,
    ObservedOwnershipFingerprint: str,
) -> bool:
    """Require a targeted internal repair to change its scored ownership."""
    return (
        not RequiredDistinctOwnershipFingerprint
        or ObservedOwnershipFingerprint
        != RequiredDistinctOwnershipFingerprint
    )


def TransactionalEndpointRepairIdentityIsFresh(
    PlacementFingerprint: str,
    RetentionFingerprint: str,
    SeenPlacementFingerprints: Iterable[str],
    SeenRetentionFingerprints: Iterable[str],
) -> bool:
    """Prevent a bounded local ECO from oscillating between old geometries."""
    return (
        PlacementFingerprint not in set(SeenPlacementFingerprints)
        and RetentionFingerprint not in set(SeenRetentionFingerprints)
    )


def HasDenseBoundaryLeaseRepairEligibility(
    Candidate: PcbPlacementCandidate,
    Policy: PhysicalDesignPolicy,
) -> bool:
    """Recognize a topology-derived dense packed boundary interface."""
    Demand = Candidate.TopologyDemand
    if Demand is None:
        return False
    LeaseRequests = tuple(
        getattr(Candidate.Placement.Placed, "ClusterBoundaryLeaseRequests", ())
    )
    LeaseTerminalCount = sum(
        1 + len(tuple(getattr(Request, "TargetTerminals", ())))
        for Request in LeaseRequests
    )
    DenseDemand = (
        Demand.MaximumTerminalBankDemand
        >= Policy.Organization.MaximumClusterEntrances
    )
    return DenseDemand or (
        bool(LeaseRequests)
        and LeaseTerminalCount >= Policy.Organization.MaximumClusterEntrances
    )


def ExtractAccessDistinctLeaseOwnershipFingerprints(
    Failure: RoutingFailure,
) -> tuple[str, ...]:
    """Return stable ownership evidence retained by the lease scheduler."""
    Scheduler = (Failure.Diagnostics or {}).get(
        "ClusterBoundaryLeaseScheduler",
        {},
    )
    Attempts = Scheduler.get("Attempts", ()) if isinstance(Scheduler, dict) else ()
    return tuple(sorted({
        str(Attempt.get("OwnershipFingerprint", ""))
        for Attempt in Attempts
        if isinstance(Attempt, dict)
        and str(Attempt.get("OwnershipFingerprint", ""))
    }))


def ExtractAuthoritativeCutAccessDomainFingerprint(
    Failure: RoutingFailure,
) -> str:
    """Return the exact cut-domain identity emitted by lease assignment."""
    Diagnostics = (
        Failure.Diagnostics
        if isinstance(Failure.Diagnostics, Mapping)
        else {}
    )
    PatternSearch = Diagnostics.get(
        "ClusterInterfacePatternSearch",
        {},
    )
    if not isinstance(PatternSearch, Mapping):
        PatternSearch = {}
    Direct = str(Diagnostics.get(
        "AuthoritativeCutAccessDomainFingerprint",
        "",
    ))
    if Direct:
        return Direct
    Nested = str(PatternSearch.get(
        "AuthoritativeCutAccessDomainFingerprint",
        "",
    ))
    if Nested:
        return Nested
    Scheduler = Diagnostics.get("ClusterBoundaryLeaseScheduler", {})
    Attempts = (
        Scheduler.get("Attempts", ())
        if isinstance(Scheduler, Mapping)
        else ()
    )
    return next((
        str(Attempt.get(
            "AuthoritativeCutAccessDomainFingerprint",
            "",
        ))
        for Attempt in reversed(tuple(Attempts))
        if (
            isinstance(Attempt, Mapping)
            and str(Attempt.get(
                "AuthoritativeCutAccessDomainFingerprint",
                "",
            ))
        )
    ), "")


def IsExactPairedLeaseCut(AssignmentCut: RoutingAssignmentCut) -> bool:
    """Recognize the bounded two-pair repair shape without net identities."""
    PairwiseEdges = tuple(AssignmentCut.PairwiseConflictEdges)
    PairwiseSignals = frozenset(
        Signal
        for Edge in PairwiseEdges
        for Signal in Edge
    )
    return len(PairwiseEdges) == 2 and len(PairwiseSignals) == 4


def SelectRepeatedPairedLeaseSubcutSignals(
    History: list[RoutingAssignmentCut] | tuple[RoutingAssignmentCut, ...],
    Current: RoutingAssignmentCut | None,
    SignalTopologyFingerprints: Mapping[str, str],
) -> frozenset[str]:
    """Project a repeated broad cut to one disjoint two-pair repair.

    Dense interfaces commonly report a large portal-coverage cut even though
    only a small structural subcut needs a different pin-bank ownership.  The
    repair remains bounded: select exactly two disjoint edges that recur under
    a different mandatory-access ownership fingerprint.  Structural endpoint
    fingerprints, rather than endpoint identifiers, rank the projection.
    """
    PairClassifications = {
        RoutingAssignmentCutClassification.MandatoryBoundaryCapacityCut,
        RoutingAssignmentCutClassification.PortalCoveragePairConflict,
        RoutingAssignmentCutClassification.PairwiseIncompatibility,
        RoutingAssignmentCutClassification.RelocatedPairwiseIncompatibility,
        RoutingAssignmentCutClassification.MultiPairPlacementConflict,
        RoutingAssignmentCutClassification.RelocatedMultiPairConflict,
    }
    if (
        Current is None
        or Current.Classification not in PairClassifications
        or not Current.MandatoryAccessOwnershipFingerprint
    ):
        return frozenset()
    RepeatedEdges: set[tuple[str, str]] = set()
    CurrentEdges = frozenset(
        tuple(sorted((str(First), str(Second))))
        for First, Second in Current.PairwiseConflictEdges
        if str(First) != str(Second)
    )
    for Previous in History:
        if (
            not Previous.MandatoryAccessOwnershipFingerprint
            or Previous.MandatoryAccessOwnershipFingerprint
            == Current.MandatoryAccessOwnershipFingerprint
        ):
            continue
        RepeatedEdges.update(CurrentEdges.intersection(
            tuple(sorted((str(First), str(Second))))
            for First, Second in Previous.PairwiseConflictEdges
            if str(First) != str(Second)
        ))
    OrderedEdges = tuple(sorted(
        RepeatedEdges,
        key=lambda Edge: (
            tuple(sorted(
                SignalTopologyFingerprints.get(Signal, Signal)
                for Signal in Edge
            )),
            Edge,
        ),
    ))
    for FirstIndex, FirstEdge in enumerate(OrderedEdges):
        FirstSignals = frozenset(FirstEdge)
        for SecondEdge in OrderedEdges[FirstIndex + 1:]:
            if FirstSignals.isdisjoint(SecondEdge):
                return frozenset((*FirstEdge, *SecondEdge))
    return frozenset()


@dataclass(frozen=True)
class RoutingControlAttemptIdentity:
    """One bounded routing attempt against immutable placed geometry."""

    PlacementFingerprint: str
    RoutingControlProfileFingerprint: str

    def ToDictionary(self) -> dict[str, str]:
        return {
            "PlacementFingerprint": self.PlacementFingerprint,
            "RoutingControlProfileFingerprint": (
                self.RoutingControlProfileFingerprint
            ),
        }


@dataclass(frozen=True)
class SamePlacementRoutingControlRetryState:
    """One pending, evidence-backed coordinated retry of the source placement."""

    AttemptIdentity: RoutingControlAttemptIdentity
    Profile: CoordinatedCandidateDiversificationProfile
    AssignmentCutFingerprint: str
    Evidence: AccessDistinctAssignmentCutDiversificationEvidence

    def ToDictionary(self) -> dict[str, object]:
        return {
            **self.AttemptIdentity.ToDictionary(),
            "AssignmentCutFingerprint": self.AssignmentCutFingerprint,
            "CoordinatedCandidateDiversificationSignals": list(
                self.Profile.Signals
            ),
            "CoordinatedCandidateDiversityLevel": (
                self.Profile.DiversityLevel
            ),
            "EvidenceKinds": list(self.Evidence.Kinds),
        }


def BuildSamePlacementRoutingControlRetryState(
    *,
    PlacementFingerprint: str,
    AssignmentCutFingerprint: str,
    Signals: Iterable[str],
    Evidence: AccessDistinctAssignmentCutDiversificationEvidence,
) -> SamePlacementRoutingControlRetryState | None:
    """Build a retry only from nonempty access-distinct exact-cut evidence."""
    Profile = BuildCoordinatedCandidateDiversificationProfile(Signals)
    if (
        not PlacementFingerprint
        or not AssignmentCutFingerprint
        or not Profile.Signals
        or not Evidence.IsProven
    ):
        return None
    EffectiveProfileFingerprint = (
        BuildStableFingerprint({
            "BaseProfileFingerprint": Profile.Fingerprint,
            "EvidenceKinds": list(Evidence.Kinds),
        })
        if Evidence.ExhaustedRepeaterAccessCut
        else Profile.Fingerprint
    )
    return SamePlacementRoutingControlRetryState(
        AttemptIdentity=RoutingControlAttemptIdentity(
            PlacementFingerprint=PlacementFingerprint,
            RoutingControlProfileFingerprint=EffectiveProfileFingerprint,
        ),
        Profile=Profile,
        AssignmentCutFingerprint=AssignmentCutFingerprint,
        Evidence=Evidence,
    )


def ShouldRetrySamePlacementRoutingControl(
    State: SamePlacementRoutingControlRetryState | None,
    CandidatePlacementFingerprint: str,
    AttemptedIdentities: Iterable[RoutingControlAttemptIdentity],
) -> bool:
    """Select one new placement-plus-profile identity without reopening geometry."""
    return (
        State is not None
        and State.AttemptIdentity.PlacementFingerprint
        == CandidatePlacementFingerprint
        and State.AttemptIdentity not in frozenset(AttemptedIdentities)
    )


def ShouldDeferSamePlacementRoutingControlRetry(
    State: SamePlacementRoutingControlRetryState | None,
    *,
    HasRemainingActivePortfolioSibling: bool,
) -> bool:
    """Prefer an untried access topology over reopening placed geometry."""
    return (
        State is not None
        and HasRemainingActivePortfolioSibling
    )


def ApplyCoordinatedCandidateDiversificationProfile(
    Placement: PcbPlacement,
    Signals: frozenset[str],
    EnableClusterPinBankRepair: bool = False,
    EnableRepeaterReadyPortalRepair: bool = False,
) -> tuple[bool, str]:
    """Rebind routing controls on an unattempted immutable placement."""
    Diagnostics = dict(Placement.Placed.LocalRouteDiagnostics or {})
    RelocationDiagnostics = dict(
        Diagnostics.get("__PlacementRelocation__", {})
    )
    if not RelocationDiagnostics:
        return False, ""
    Profile = BuildCoordinatedCandidateDiversificationProfile(Signals)
    OrderedSignals = list(Profile.Signals)
    # This is intentionally a separate, opt-in physical control.  The
    # ordinary coordinated candidate profile is used by several recovery
    # paths; only a proven access-distinct paired lease cut may change the
    # ownership preference of a cluster's boundary pin banks.
    PinBankRepair = {
        "Signals": OrderedSignals,
        "CandidateDomainOffset": 1,
        "VariantCount": 3,
        "ProfileFingerprint": BuildStableFingerprint({
            "Signals": OrderedSignals,
            "CandidateDomainOffset": 1,
            "VariantCount": 3,
        }),
    } if EnableClusterPinBankRepair and OrderedSignals else {}
    RepeaterReadyPortalRepair = {
        "Signals": OrderedSignals,
        "ExtensionLength": 3,
        "MaximumExtensionsPerPortal": 2,
        "ProfileFingerprint": BuildStableFingerprint({
            "Signals": OrderedSignals,
            "ExtensionLength": 3,
            "MaximumExtensionsPerPortal": 2,
        }),
    } if EnableRepeaterReadyPortalRepair and OrderedSignals else {}
    EffectiveProfileFingerprint = (
        BuildStableFingerprint({
            "BaseProfileFingerprint": Profile.Fingerprint,
            "EvidenceKinds": ["exhausted-repeater-access-cut"],
        })
        if RepeaterReadyPortalRepair
        else Profile.Fingerprint
    )
    if (
        RelocationDiagnostics.get(
            "CoordinatedCandidateDiversificationSignals",
            (),
        )
        == OrderedSignals
        and int(
            RelocationDiagnostics.get(
                "CoordinatedCandidateDiversityLevel",
                0,
            )
        )
        == Profile.DiversityLevel
        and Diagnostics.get("__ClusterPinBankRepair__", {}) == PinBankRepair
        and Diagnostics.get("__RepeaterReadyPortalRepair__", {})
        == RepeaterReadyPortalRepair
    ):
        return False, EffectiveProfileFingerprint
    RelocationDiagnostics.update({
        "CoordinatedCandidateDiversificationSignals": OrderedSignals,
        "CoordinatedCandidateDiversityLevel": Profile.DiversityLevel,
        # A same-placement repair is a deliberately bounded, cut-scoped
        # probe.  It must not inherit an unrelated global retry generation
        # and turn into a broad candidate expansion.
        "CoordinatedCandidateDiversificationFixedLevel": (
            Profile.DiversityLevel
        ),
        "CoordinatedCandidateDiversificationProfileFingerprint": (
            Profile.Fingerprint
        ),
    })
    Diagnostics["__PlacementRelocation__"] = RelocationDiagnostics
    if PinBankRepair:
        Diagnostics["__ClusterPinBankRepair__"] = PinBankRepair
    else:
        Diagnostics.pop("__ClusterPinBankRepair__", None)
    if RepeaterReadyPortalRepair:
        Diagnostics["__RepeaterReadyPortalRepair__"] = (
            RepeaterReadyPortalRepair
        )
    else:
        Diagnostics.pop("__RepeaterReadyPortalRepair__", None)
    Placement.Placed.LocalRouteDiagnostics = Diagnostics
    return True, EffectiveProfileFingerprint


def ApplyActivePlacementAssignmentConstraints(
    Placement: PcbPlacement,
    Constraints: PlacementAssignmentConstraintSet,
) -> tuple[bool, str]:
    """Attach cumulative routed cuts without changing geometry provenance."""
    Diagnostics = dict(Placement.Placed.LocalRouteDiagnostics or {})
    JointDiagnostics = dict(
        Diagnostics.get("__JointClusterPlacement__", {})
    )
    if not JointDiagnostics:
        return False, ""
    ActiveConstraints = Constraints.ToDictionary()
    if (
        JointDiagnostics.get("ActiveAssignmentConstraints")
        == ActiveConstraints
        and JointDiagnostics.get(
            "ActiveAssignmentConstraintFingerprint",
            "",
        )
        == Constraints.Fingerprint
    ):
        return False, Constraints.Fingerprint
    JointDiagnostics.update({
        "ActiveAssignmentConstraints": ActiveConstraints,
        "ActiveAssignmentConstraintFingerprint": (
            Constraints.Fingerprint
        ),
    })
    Diagnostics["__JointClusterPlacement__"] = JointDiagnostics
    Placement.Placed.LocalRouteDiagnostics = Diagnostics
    return True, Constraints.Fingerprint


def ApplyRemainingExactLegalJointStateCount(
    Placement: PcbPlacement,
    RemainingStateCount: int,
) -> bool:
    """Publish the live portfolio count used by staged routing proof."""
    if RemainingStateCount < 1:
        raise ValueError("RemainingStateCount must be positive")
    Diagnostics = dict(Placement.Placed.LocalRouteDiagnostics or {})
    JointDiagnostics = dict(
        Diagnostics.get("__JointClusterPlacement__", {})
    )
    if not JointDiagnostics:
        return False
    if (
        JointDiagnostics.get("RemainingExactLegalRetainedStateCount")
        == RemainingStateCount
    ):
        return False
    JointDiagnostics["RemainingExactLegalRetainedStateCount"] = (
        RemainingStateCount
    )
    Diagnostics["__JointClusterPlacement__"] = JointDiagnostics
    Placement.Placed.LocalRouteDiagnostics = Diagnostics
    return True


def PlacementAssignmentConstraintsAreActive(
    Constraints: PlacementAssignmentConstraintSet,
) -> bool:
    """Return whether learned assignment geometry constrains candidates."""
    return Constraints.HasActivePlacementConstraints


def SerializedPlacementAssignmentConstraintsAreActive(
    Constraints: object,
) -> bool:
    """Recognize learned cuts without treating an empty manifest as active."""
    if not isinstance(Constraints, dict):
        return False
    if (
        "ActiveHigherOrderSignalSets" in Constraints
        or "ActiveObservedInterfaceConflictEdges" in Constraints
    ):
        return bool(
            Constraints.get("PairwiseConflictEdges")
            or Constraints.get("ActiveHigherOrderSignalSets")
            or Constraints.get("ActiveObservedInterfaceConflictEdges")
        )
    return bool(
        Constraints.get("PairwiseConflictEdges")
        or Constraints.get("HigherOrderSignalSets")
        or Constraints.get("ObservedInterfaceConflictEdges")
    )


def SelectImmediateTopologyPinBankRepairSignals(
    *,
    TopologyAccessRepairEligible: bool,
    TopologyRequiresJointPortfolio: bool = False,
    AssignmentCut: RoutingAssignmentCut | None,
    Constraints: PlacementAssignmentConstraintSet,
) -> frozenset[str]:
    """Admit one exact zero-domain repair only after structured topology work."""
    HasStructuredTopologyEvidence = bool(
        Constraints.PairwiseConflictEdges
        or Constraints.HigherOrderSignalSets
        or Constraints.ObservedInterfaceConflictEdges
        or Constraints.ActiveHigherOrderSignalSets
        or Constraints.ActiveObservedInterfaceConflictEdges
    )
    return (
        frozenset(AssignmentCut.NoCandidateSignals)
        if (
            TopologyAccessRepairEligible
            and AssignmentCut is not None
            and (
                AssignmentCut.Classification
                == RoutingAssignmentCutClassification
                .CandidateStarvationPlacementConflict
                or (
                    TopologyRequiresJointPortfolio
                    and AssignmentCut.Classification
                    == RoutingAssignmentCutClassification
                    .MandatoryAccessSelfConflict
                )
            )
            and bool(AssignmentCut.NoCandidateSignals)
            and HasStructuredTopologyEvidence
        )
        else frozenset()
    )


def ShouldContinuePostPinBankRepairEpoch(
    *,
    CandidatePostPinBankRepairEpoch: bool,
    InternalPinBankRetryPending: bool,
    ImmediateTopologyStarvationSignals: Iterable[str],
) -> bool:
    """Permit a new proved zero-domain profile after one local repair."""
    return (
        CandidatePostPinBankRepairEpoch
        and InternalPinBankRetryPending
        and bool(frozenset(map(str, ImmediateTopologyStarvationSignals)))
    )


def BuildTargetedPinBankPackingPolicy(PackingPolicy: Any) -> Any:
    """Fund one local ownership-changing state instead of a broad portfolio."""
    return replace(
        PackingPolicy,
        GraphBeamEnabled=False,
        EnableJointClusterOrientation=True,
        RetainedJointPlacementCandidates=1,
        JointPlacementBeamWidth=min(
            PackingPolicy.JointPlacementBeamWidth,
            max(16, PackingPolicy.JointPlacementBeamWidth // 2),
        ),
        JointPlacementPassLimit=min(
            PackingPolicy.JointPlacementPassLimit,
            max(4, PackingPolicy.JointPlacementPassLimit // 2),
        ),
    )


def PlacementCandidateMatchesConstraintIdentity(
    Candidate: PcbPlacementCandidate,
    CurrentConstraintFingerprint: str,
    ConstraintIdentityActive: bool,
) -> bool:
    """Apply learned epochs to exact joint/access portfolio candidates."""
    if not Candidate.JointPortfolioCandidate:
        return True
    return PlacementConstraintFingerprintMatchesIdentity(
        Candidate.AssignmentConstraintFingerprint,
        CurrentConstraintFingerprint,
        ConstraintIdentityActive,
    )


def PlacementConstraintFingerprintMatchesIdentity(
    CandidateConstraintFingerprint: str,
    CurrentConstraintFingerprint: str,
    ConstraintIdentityActive: bool,
) -> bool:
    """Match one materialized placement to the learned constraint epoch."""
    return (
        not ConstraintIdentityActive
        or CandidateConstraintFingerprint == CurrentConstraintFingerprint
    )


def ShouldRefreshTerminalActiveJointPlacementConstraintEpoch(
    *,
    ActivePendingCount: int,
    CandidateSourceGenerator: str,
    CandidateMatchesActivePortfolio: bool,
    CandidateConstraintFingerprint: str,
    CurrentConstraintFingerprint: str,
    RefreshAlreadyPerformed: bool,
) -> bool:
    """Select one bounded geometry refresh for the final stale sibling."""
    return (
        ActivePendingCount == 1
        and CandidateSourceGenerator == "row-beam-conflict-relocation"
        and CandidateMatchesActivePortfolio
        and bool(CurrentConstraintFingerprint)
        and CandidateConstraintFingerprint
        != CurrentConstraintFingerprint
        and not RefreshAlreadyPerformed
    )


def RebindTerminalJointPlacementConstraintEpoch(
    State: PendingJointPlacementState,
    AssignmentCut: RoutingAssignmentCut | None,
    AssignmentConstraints: PlacementAssignmentConstraintSet,
) -> PendingJointPlacementState:
    """Re-rank a stale terminal recipe from the current exact-cut anchor."""
    return replace(
        State,
        CandidateIndex=0,
        AssignmentCut=AssignmentCut,
        AssignmentConstraints=AssignmentConstraints,
    )


def SelectNewPendingJointPlacementPortfolioFingerprint(
    States: Iterable[PendingJointPlacementState],
    ExistingStateKeys: frozenset[
        tuple[PendingJointPlacementPortfolioIdentity, int]
    ],
    AssignmentConstraintFingerprint: str,
) -> str | None:
    """Select the one new current-epoch portfolio queued by a lead screen."""
    Fingerprints = {
        BuildPendingJointPlacementPortfolioFingerprint(State)
        for State in States
        if (
            BuildPendingJointPlacementStateKey(State)
            not in ExistingStateKeys
            and State.AssignmentConstraints.Fingerprint
            == AssignmentConstraintFingerprint
        )
    }
    return (
        next(iter(Fingerprints))
        if len(Fingerprints) == 1
        else None
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


def ApplyTopologyDemandPolicyWidening(
    Policy: PhysicalDesignPolicy,
    Technology: RedstoneRoutingTechnology,
    Pressure: TopologyDemandPressureProfile,
) -> PhysicalDesignPolicy:
    """Apply only the geometry controls justified by typed demand."""
    ScaleGeometryPressure = Pressure.ScaleGeometryPressure
    ReconvergentAccessPressure = Pressure.ReconvergentAccessPressure
    if not ScaleGeometryPressure and not ReconvergentAccessPressure:
        return Policy
    return replace(
        Policy,
        Placement=replace(
            Policy.Placement,
            MaximumRoutingLayers=(
                max(
                    Policy.Placement.MaximumRoutingLayers,
                    Technology.MaximumRoutableLayerCount,
                )
                if ScaleGeometryPressure
                else Policy.Placement.MaximumRoutingLayers
            ),
            PreferWideTerminalBanks=(
                Policy.Placement.PreferWideTerminalBanks
                or ScaleGeometryPressure
                or ReconvergentAccessPressure
            ),
        ),
        NandPacking=replace(
            Policy.NandPacking,
            MaximumClusterCells=(
                # A reconvergent fanout cut needs independently owned access
                # faces. Six-cell clusters keep the paired portal domains in
                # one compact shell; retain a bounded six-cell portfolio so
                # exact access scoring does not consume the routing budget.
                min(Policy.NandPacking.MaximumClusterCells, 6)
                if ReconvergentAccessPressure
                else Policy.NandPacking.MaximumClusterCells
            ),
            JointPlacementBeamWidth=(
                min(Policy.NandPacking.JointPlacementBeamWidth, 32)
                if ReconvergentAccessPressure
                else Policy.NandPacking.JointPlacementBeamWidth
            ),
            TerminalShellLateralSearch=(
                max(Policy.NandPacking.TerminalShellLateralSearch, 8)
                # A reconvergent cut can be electrically blocked at an I/O
                # shell even when its gate footprint is below the scale
                # threshold.  Keep the wider shell exclusively behind the
                # typed topology trigger; compact/ripple terminal geometry
                # must remain unchanged.
                if ScaleGeometryPressure or ReconvergentAccessPressure
                else Policy.NandPacking.TerminalShellLateralSearch
            ),
            MaximumTerminalAssignmentExpansions=(
                min(
                    Policy.NandPacking.MaximumTerminalAssignmentExpansions,
                    4_096,
                )
                if ReconvergentAccessPressure
                else Policy.NandPacking.MaximumTerminalAssignmentExpansions
            ),
            LocalGeometryRepairColumnGap=(
                max(Policy.NandPacking.LocalGeometryRepairColumnGap, 8)
                if ScaleGeometryPressure
                else Policy.NandPacking.LocalGeometryRepairColumnGap
            ),
        ),
        NegotiatedRouting=replace(
            Policy.NegotiatedRouting,
            MaximumPackedAreaGrowth=(
                max(
                    Policy.NegotiatedRouting.MaximumPackedAreaGrowth,
                    4.5,
                )
                if ScaleGeometryPressure
                else Policy.NegotiatedRouting.MaximumPackedAreaGrowth
            ),
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
                RetainedJointPlacementCandidates=1,
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


def PlacementCandidateOrder(
    Value: PcbPlacementCandidate,
    ConfiguredSpacing: int,
) -> tuple[object, ...]:
    """Return the stable demand-first order used for placement failover."""
    if (
        bool(getattr(Value, "JointPortfolioCandidate", False))
        and Value.TopologyDemand is not None
    ):
        RetentionFingerprint = (
            Value.PlacementRetentionFingerprint
            or BuildPlacementRetentionFingerprint(
                Value.Placement,
                Value.TopologyDemand
                .MandatoryAccessOwnershipFingerprint,
                IncludeLocalClaims=False,
            )
        )
        return (
            0,
            Value.TopologyDemand.JointOrderKey,
            abs(Value.RoutingSpacing - ConfiguredSpacing),
            RetentionFingerprint,
        )
    return (
        1,
        0 if Value.Placement.PackedClusters else 1,
        0 if (Value.Placement.Placed.LocalRouteClaims or ()) else 1,
        Value.JointExactScore,
        Value.FeedbackScore,
        abs(Value.RoutingSpacing - ConfiguredSpacing),
        Value.PlacementFingerprint,
    )


def PlacementCandidateIsExactAccessLegal(
    Value: PcbPlacementCandidate,
) -> bool:
    """Reject a proved mandatory-access conflict before routing."""
    return bool(
        Value.TopologyDemand is None
        or Value.TopologyDemand.MandatoryAccessConflictResources == 0
    )


def PlacementNeedsDemandDiversity(
    Candidates: list[PcbPlacementCandidate],
    ConfiguredSpacing: int,
) -> bool:
    """Return whether the best generated placement still needs more diversity."""
    if not Candidates:
        return True
    Best = min(
        Candidates,
        key=lambda Value: PlacementCandidateOrder(Value, ConfiguredSpacing),
    )
    return any((
        Best.BoundaryOverflow,
        Best.PinScarcityCount,
        Best.GuideOverflowPeak,
        Best.GuideOverflowCells,
        Best.PinEscapeConflictCount,
    ))


def PlacementGenerationRoutingReserveSeconds(
    Policy: PhysicalDesignPolicy,
    RequiresDenseBoundaryRouting: bool = False,
) -> float:
    """Reserve an explicit part of the one deadline for routing a legal candidate."""
    TotalSeconds = Policy.RuntimeBudgetSeconds
    RoutingFraction = 0.50 if RequiresDenseBoundaryRouting else 0.20
    return min(
        max(0.0, TotalSeconds - 0.001),
        Policy.AdaptiveRouting.MaximumRuntimeSeconds,
        max(0.01, TotalSeconds * RoutingFraction),
    )


def PlacementPortfolioGenerationNotAfter(
    Policy: PhysicalDesignPolicy,
    *,
    DeadlineExpiresAt: float,
    CurrentTime: float,
    RequiresDenseBoundaryRouting: bool = False,
) -> float:
    """Freeze one absolute routing floor for a retained placement portfolio."""
    RemainingSeconds = max(0.0, DeadlineExpiresAt - CurrentTime)
    RoutingReserveSeconds = min(
        PlacementGenerationRoutingReserveSeconds(
            Policy,
            RequiresDenseBoundaryRouting,
        ),
        max(0.01, RemainingSeconds * 0.5),
    )
    return DeadlineExpiresAt - RoutingReserveSeconds


def RequiresDenseBoundaryRoutingReserve(
    Demand: TopologyDemandProfile,
    Policy: PhysicalDesignPolicy,
) -> bool:
    """Reserve routing time for interfaces that need joint ownership proof."""
    return (
        Demand.RequiresJointPortfolio
        or Demand.MaximumTerminalBankDemand
        >= Policy.Organization.MaximumClusterEntrances
    )


def RequiresDenseBoundaryLeaseRouting(
    Placed: Any,
    Policy: PhysicalDesignPolicy,
) -> bool:
    """Return whether one placement owns a joint boundary-lease interface."""
    LeaseRequests = tuple(
        getattr(Placed, "ClusterBoundaryLeaseRequests", ())
    )
    LeaseTerminalCount = sum(
        1 + len(tuple(getattr(Request, "TargetTerminals", ())))
        for Request in LeaseRequests
    )
    return (
        bool(LeaseRequests)
        and LeaseTerminalCount >= Policy.Organization.MaximumClusterEntrances
    )


def RequiresExactClusterInterfaceSolve(
    Demand: TopologyDemandProfile | None,
    Placed: Any,
    Policy: PhysicalDesignPolicy,
) -> bool:
    """Gate the exact interface path using measured structure only."""
    return bool(
        Demand is not None
        and bool(getattr(Placed, "CompleteClusterInterfaceAccess", False))
        and (
            Demand.RequiresJointPortfolio
            or Demand.MandatoryAccessConflictResources > 0
            or RequiresDenseBoundaryLeaseRouting(Placed, Policy)
        )
    )


def BuildClusterInterfaceUnsatProof(
    StateProofs: Iterable[ClusterInterfaceStateProof],
) -> dict[str, object]:
    """Build one deterministic terminal proof for a retained state set."""
    Proofs = tuple(StateProofs)
    if not Proofs:
        raise ValueError("cluster interface proof requires retained states")
    StateFingerprints = tuple(
        Proof.PlacementStateFingerprint for Proof in Proofs
    )
    if len(set(StateFingerprints)) != len(StateFingerprints):
        raise ValueError(
            "cluster interface proof contains a repeated placement state"
        )
    ProofFingerprint = BuildStableFingerprint(tuple(
        Proof.StructuralIdentity() for Proof in Proofs
    ))
    return {
        "Complete": all(
            Proof.Exhaustive
            and Proof.DomainComplete
            and Proof.OwnershipComplete
            and Proof.RealizabilityComplete
            for Proof in Proofs
        ),
        "ExecutableRepairAllowed": False,
        "BroadFallbackAllowed": False,
        "AttemptedStateCount": len(Proofs),
        "StateProofs": [
            Proof.ToDictionary() for Proof in Proofs
        ],
        "ProofFingerprint": ProofFingerprint,
    }


def ShouldEnableClusterBoundaryLeaseInterface(
    *,
    ScaleGeometryPressure: bool,
    TopologyRequiresJointPortfolio: bool,
    IsPostPinBankRepairEpoch: bool = False,
) -> bool:
    """Materialize boundary contracts whenever compact clustered geometry can.

    Reconvergent placement previously disabled the lease interface until a
    post-pin-bank epoch.  That left the exact candidate screen blind to the
    simultaneous source/target portal ownership later proved impossible by
    the authoritative planner.  Scale-widened placements retain their proven
    route path; compact topology-triggered placements now carry the same typed
    boundary contract from placement through routing.
    """
    del TopologyRequiresJointPortfolio, IsPostPinBankRepairEpoch
    return not ScaleGeometryPressure


def PlacementFeedbackRoutingSlotCount(
    *,
    HasRemainingPlacementAlternative: bool,
    ReconvergentAccessPressure: bool,
    AttemptedCandidateCount: int,
) -> int:
    """Reserve one later route while establishing reconvergent-cut feedback."""
    return (
        2
        if (
            HasRemainingPlacementAlternative
            and ReconvergentAccessPressure
            and AttemptedCandidateCount < 2
        )
        else 1
    )


def RetainedPlacementRoutingSlotCount(
    *,
    RemainingRetainedCandidates: int,
    HighFanoutFeedbackRoutingSlots: int,
    HasRemainingPlacementAlternative: bool,
    TopologyPortfolioTriggered: bool,
    AttemptedCandidateCount: int,
) -> int:
    """Reserve an equal route slice for every live exact geometry."""
    return max(
        1,
        RemainingRetainedCandidates,
        HighFanoutFeedbackRoutingSlots,
        (
            2
            if (
                HasRemainingPlacementAlternative
                and TopologyPortfolioTriggered
                and AttemptedCandidateCount == 0
            )
            else 1
        ),
    )


def BuildPlacementRelocationVariant(
    *,
    RelocationGenerationCount: int,
    ReconvergentAccessPressure: bool,
) -> int:
    """Select repair strength from attempts against the current exact cut."""
    return (
        RelocationGenerationCount
        + 2
        + (
            9
            if (
                RelocationGenerationCount > 0
                and ReconvergentAccessPressure
            )
            else 0
        )
    )


def DenseRetainedLeaseProofSliceSeconds(
    *,
    RemainingSeconds: float,
    RemainingRetainedCandidates: int,
    MinimumProofSeconds: float = 10.0,
    PublicationReserveSeconds: float = 2.0,
    PrioritizeHigherOrderCutProof: bool = False,
) -> float:
    """Fund a useful exact lease proof while preserving the shared deadline.

    A fresh higher-order geometry epoch must reach boundary assignment once;
    equal six-way slicing repeatedly expired all states during immutable portal
    preparation. Give only its primary exact state a bounded lead share.
    """
    if RemainingSeconds <= 0:
        return 0.001
    if RemainingRetainedCandidates < 1:
        raise ValueError("RemainingRetainedCandidates must be positive")
    AvailableSeconds = max(
        0.001,
        RemainingSeconds - PublicationReserveSeconds,
    )
    FairShareSeconds = (
        RemainingSeconds / RemainingRetainedCandidates
    )
    PriorityProofSeconds = (
        min(
            40.0,
            max(
                20.0,
                RemainingSeconds * 0.45,
            ),
        )
        if PrioritizeHigherOrderCutProof
        else 0.0
    )
    return min(
        AvailableSeconds,
        max(
            FairShareSeconds,
            MinimumProofSeconds,
            PriorityProofSeconds,
        ),
    )


def TopologyPortfolioRoutingFraction(
    *,
    HasRemainingPlacementAlternative: bool,
    AttemptedCandidateCount: int,
    AuthoritativeMandatoryAccessConflictObserved: bool = False,
) -> float:
    """Give the ranked lead state enough time to expose an exact cut."""
    if AuthoritativeMandatoryAccessConflictObserved:
        # The routed portal-domain prescreen is more authoritative than the
        # placement-only pin-access score.  Once it disqualifies the ranked
        # lead, the next zero-conflict sibling becomes the effective lead and
        # owns the remaining bounded portfolio slice.
        return 1.0
    if (
        HasRemainingPlacementAlternative
        and AttemptedCandidateCount == 0
    ):
        return 0.75
    return 1.0


def ShouldGiveRankedJointPortfolioLeadSlice(
    *,
    ActiveRelocatedPortfolioCandidate: bool,
    CandidateId: str,
    PrimaryCandidateId: str | None,
) -> bool:
    """Reserve the established lead slice for the ranked active candidate."""
    return (
        ActiveRelocatedPortfolioCandidate
        and PrimaryCandidateId is not None
        and CandidateId == PrimaryCandidateId
    )


def IsAuthoritativeMandatoryAccessConflict(
    Failure: RoutingFailure,
) -> bool:
    """Recognize a complete retained generated-portal-domain cut proof."""
    Diagnostics = Failure.Diagnostics or {}
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    Proof = Diagnostics.get("MandatoryAccessProof", {})
    return (
        Failure.Reason == RoutingFailureReason.TrackAssignmentConflict
        and Failure.Stage == "InitialCandidateAssignment"
        and isinstance(ConflictGraph, dict)
        and ConflictGraph.get("Classification")
        == "mandatory-boundary-capacity-cut"
        and bool(Failure.AffectedNets)
        and isinstance(Proof, dict)
        and Proof.get("Kind")
        == "generated-fixed-portal-domain-exhausted"
        and Proof.get("Complete") is True
        and not bool(Proof.get("BudgetExhausted", False))
        and not bool(Proof.get("DeadlineExceeded", False))
        and bool(Proof.get("ConflictFingerprint"))
    )


def PromoteAuthoritativeMandatoryAccessConflict(
    Profile: TopologyDemandProfile,
    Failure: RoutingFailure,
) -> TopologyDemandProfile:
    """Merge a routed exact portal cut into one immutable topology profile.

    Placement can score fixed pin-access ownership before building routing
    layers, but only the authoritative portal domain can prove collisions
    across every retained generated fixed portal/access alternative. Normalize
    physical locations and owner cardinalities so the score and fingerprint
    remain rename-independent.
    """
    if not IsAuthoritativeMandatoryAccessConflict(Failure):
        return Profile
    Diagnostics = Failure.Diagnostics or {}
    Proof = Diagnostics["MandatoryAccessProof"]
    ConflictResourceCount = max(
        1,
        int(
            Proof.get(
                "ConflictPositionCount",
                Diagnostics.get("MandatoryConflictPositionCount", 0),
            )
        ),
    )
    return replace(
        Profile,
        MandatoryAccessConflictResources=ConflictResourceCount,
        MandatoryAccessConflictSignals=tuple(sorted(map(
            str,
            Failure.AffectedNets,
        ))),
        MandatoryAccessConflictFingerprint=str(
            Proof["ConflictFingerprint"]
        ),
    )


def FailureRequestsPlacementAdvance(Failure: RoutingFailure) -> bool:
    """Return whether a typed failure forbids same-candidate recovery work."""
    Diagnostics = Failure.Diagnostics or {}
    Action = str(Diagnostics.get("Action", ""))
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    return (
        Action.startswith("advance-placement")
        or Failure.Reason == RoutingFailureReason.NoPinAccessPattern
        or Failure.Reason
        == RoutingFailureReason.RepeaterAccessInfeasible
        or any(
            str(RepairAction).startswith("AdvancePlacement")
            for RepairAction in Failure.RepairActions
        )
        or (
            isinstance(ConflictGraph, dict)
            and ConflictGraph.get("Classification")
            == "mandatory-boundary-capacity-cut"
        )
    )


def FailurePrefersDirectOnlyPlacement(
    Failure: RoutingFailure,
    Candidate: PcbPlacementCandidate,
) -> bool:
    """Prefer fewer pre-owned local claims after one exact higher-order cut."""
    if (
        Candidate.SourceGenerator != "row-beam"
        or not Candidate.Placement.Placed.LocalRouteClaims
        or any((
            Candidate.BoundaryOverflow,
            Candidate.PinScarcityCount,
            Candidate.GuideOverflowPeak,
            Candidate.GuideOverflowCells,
            Candidate.PinEscapeConflictCount,
        ))
        or Failure.Reason != RoutingFailureReason.TrackAssignmentConflict
        or Failure.Stage != "TrackAssignment"
        or not FailureRequestsPlacementAdvance(Failure)
    ):
        return False
    ConflictGraph = (Failure.Diagnostics or {}).get("ConflictGraph", {})
    if not isinstance(ConflictGraph, dict):
        return False
    return (
        ConflictGraph.get("Classification")
        == "higher-order-placement-conflict"
        and not ConflictGraph.get("NoCandidateSignals")
        and not ConflictGraph.get("PairwiseIncompatibleEdges")
        and bool(ExtractCandidateStarvationSignals(Failure))
    )


def ExtractCandidateStarvationSignals(
    Failure: RoutingFailure,
) -> frozenset[str]:
    """Return signals repeatedly proved empty during exact candidate repair."""
    EscalationHistory = (Failure.Diagnostics or {}).get(
        "EscalationHistory",
        (),
    )
    if not isinstance(EscalationHistory, tuple | list):
        return frozenset()
    Signals: set[str] = set()
    for Entry in EscalationHistory:
        if (
            not isinstance(Entry, dict)
            or str(Entry.get("Stage", "")) != "CandidateGeneration"
        ):
            continue
        EntryDiagnostics = Entry.get("Diagnostics", {})
        if (
            not isinstance(EntryDiagnostics, dict)
            or int(EntryDiagnostics.get("Materialized", -1)) != 0
        ):
            continue
        AffectedSignals = Entry.get("AffectedSignals", ())
        if isinstance(AffectedSignals, tuple | list):
            Signals.update(str(Signal) for Signal in AffectedSignals)
    return frozenset(sorted(Signals))


def FailureRequiresPackedAccessRepair(Failure: RoutingFailure) -> bool:
    """Return whether a typed fixed-access cut requires local geometry repair."""
    ConflictGraph = (Failure.Diagnostics or {}).get("ConflictGraph", {})
    Classification = (
        str(ConflictGraph.get("Classification", ""))
        if isinstance(ConflictGraph, dict)
        else ""
    )
    return (
        Failure.Reason in {
            RoutingFailureReason.NoPinAccessPattern,
            RoutingFailureReason.RepeaterAccessInfeasible,
        }
        or Classification in {
            "mandatory-access-self-conflict",
            "mandatory-boundary-capacity-cut",
            "portal-coverage-pair-conflict",
            "relocated-higher-order-conflict",
            "relocated-larger-matching-failure",
            "relocated-multi-pair-conflict",
            "relocated-pairwise-incompatibility",
        }
    )


def ExpandAnalogousMandatoryRepairSignals(
    Module: Any,
    Signals: frozenset[str],
) -> frozenset[str]:
    """Expand one external fixed-access cut across equivalent gate motifs."""
    if len(Signals) < 2:
        return Signals
    ExternalInputs = frozenset(str(Signal) for Signal in Module.Inputs)
    Fanout = {
        Signal: sum(
            Gate.Inputs.count(Signal)
            for Gate in Module.Gates
        )
        for Signal in ExternalInputs
    }
    Patterns: set[tuple[object, int, tuple[int, ...], tuple[int, ...]]] = set()
    for Gate in Module.Gates:
        Positions = tuple(
            Index
            for Index, Signal in enumerate(Gate.Inputs)
            if Signal in Signals
        )
        if len(Positions) < 2:
            continue
        Patterns.add((
            getattr(Gate.Kind, "value", Gate.Kind),
            len(Gate.Inputs),
            Positions,
            tuple(Fanout.get(Gate.Inputs[Index], 0) for Index in Positions),
        ))
    if not Patterns:
        return Signals
    Expanded = set(Signals)
    for Gate in Module.Gates:
        Kind = getattr(Gate.Kind, "value", Gate.Kind)
        for PatternKind, Arity, Positions, PatternFanout in Patterns:
            if Kind != PatternKind or len(Gate.Inputs) != Arity:
                continue
            CandidateSignals = tuple(
                str(Gate.Inputs[Index]) for Index in Positions
            )
            if (
                all(Signal in ExternalInputs for Signal in CandidateSignals)
                and tuple(
                    Fanout.get(Signal, 0)
                    for Signal in CandidateSignals
                )
                == PatternFanout
            ):
                Expanded.update(CandidateSignals)
    return frozenset(Expanded)


def ExtractPlacementRelocationSignals(
    Failure: RoutingFailure,
) -> frozenset[str]:
    """Return typed routing offenders that should alter later placement."""
    # AffectedNets is allowed to describe the larger assignment frontier.  A
    # structured conflict graph is the more precise physical diagnosis, so do
    # not turn a three-net conflict back into a broad cluster move by unioning
    # the whole frontier into it.
    Signals: set[str] = set()
    Diagnostics = Failure.Diagnostics or {}
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    if isinstance(ConflictGraph, dict):
        RelocationValues = ConflictGraph.get("RelocationSignals", ())
        if isinstance(RelocationValues, tuple | list) and RelocationValues:
            return frozenset(str(Value) for Value in RelocationValues)
        for Key in (
            "ConflictSignals",
            "NativeConflictSignals",
            "NoCandidateSignals",
            "CumulativeConflictSignals",
            "CongestionCutSignals",
            "ConflictCutSignals",
        ):
            Values = ConflictGraph.get(Key, ())
            if isinstance(Values, tuple | list):
                Signals.update(str(Value) for Value in Values)
        Rebalancing = ConflictGraph.get("ConflictResources", ())
        if isinstance(Rebalancing, dict):
            Signals.update(
                str(Signal)
                for SignalsForResource in Rebalancing.values()
                if isinstance(SignalsForResource, tuple | list)
                for Signal in SignalsForResource
            )
        Pairwise = ConflictGraph.get("PairwiseIncompatibleEdges", ())
        if isinstance(Pairwise, tuple | list):
            Signals.update(
                str(Signal)
                for Pair in Pairwise
                if isinstance(Pair, tuple | list)
                for Signal in Pair
            )
    for Key in ("ConflictSignals", "NativeConflictSignals"):
        Values = Diagnostics.get(Key, ())
        if isinstance(Values, tuple | list):
            Signals.update(str(Value) for Value in Values)
    if not Signals:
        Signals.update(str(Value) for Value in Failure.AffectedNets)
    return frozenset(sorted(Signals))


def ExtractCompletedEscalationRelocationSignals(
    Failure: RoutingFailure,
) -> frozenset[str]:
    """Recover the latest exact cut completed before an interrupted escalation."""
    if Failure.Reason not in {
        RoutingFailureReason.RuntimeBudgetExceeded,
        RoutingFailureReason.Stagnated,
    }:
        return frozenset()
    Diagnostics = Failure.Diagnostics or {}
    EscalationHistory = Diagnostics.get("EscalationHistory", ())
    if not isinstance(EscalationHistory, tuple | list):
        return frozenset()
    RelocatedClassifications = {
        "portal-coverage-pair-conflict",
        "relocated-higher-order-conflict",
        "relocated-larger-matching-failure",
        "relocated-multi-pair-conflict",
        "relocated-pairwise-incompatibility",
    }
    for Entry in reversed(EscalationHistory):
        if not isinstance(Entry, dict):
            continue
        if (
            str(Entry.get("Stage", "")) != "TrackAssignment"
            or str(Entry.get("ConflictClassification", ""))
            not in RelocatedClassifications
            or (
                str(Entry.get("Decision", ""))
                != "RegenerateAffectedCandidates"
                and str(Entry.get("Action", ""))
                != "regenerate-affected-candidates"
            )
        ):
            continue
        for Key in (
            "PriorityRelocationSignals",
            "RelocationSignals",
            "AffectedSignals",
        ):
            Values = Entry.get(Key, ())
            if isinstance(Values, tuple | list) and Values:
                return frozenset(str(Signal) for Signal in Values)
    return frozenset()


_HigherOrderAssignmentCutClassifications = frozenset({
    RoutingAssignmentCutClassification.SaturatedBoundaryCut,
    RoutingAssignmentCutClassification.HigherOrderPlacementConflict,
    RoutingAssignmentCutClassification.LargerMatchingFailure,
    RoutingAssignmentCutClassification.MultiPairPlacementConflict,
    RoutingAssignmentCutClassification.RelocatedHigherOrderConflict,
    RoutingAssignmentCutClassification.RelocatedLargerMatchingFailure,
    RoutingAssignmentCutClassification.RelocatedMultiPairConflict,
    RoutingAssignmentCutClassification.RelocatedPairwiseIncompatibility,
})
_ImmediateAssignmentCutRelocationClassifications = frozenset({
    RoutingAssignmentCutClassification.SaturatedBoundaryCut,
    RoutingAssignmentCutClassification.MandatoryAccessSelfConflict,
    RoutingAssignmentCutClassification.MandatoryBoundaryCapacityCut,
    RoutingAssignmentCutClassification.PortalCoveragePairConflict,
    RoutingAssignmentCutClassification.RelocatedPairwiseIncompatibility,
})


def IsHigherOrderAssignmentCut(
    AssignmentCut: RoutingAssignmentCut | None,
) -> bool:
    """Return whether a complete assignment cut requires joint relocation."""
    return (
        AssignmentCut is not None
        and AssignmentCut.Classification
        in _HigherOrderAssignmentCutClassifications
    )


def ShouldUseCurrentAssignmentCutGeometry(
    Requested: bool,
    SourceGenerator: str,
    AssignmentCut: RoutingAssignmentCut | None,
) -> bool:
    """Keep higher-order relocation geometry scoped to its current cut."""
    return bool(
        Requested
        or (
            SourceGenerator == "row-beam-conflict-relocation"
            and IsHigherOrderAssignmentCut(AssignmentCut)
        )
    )


def SelectAssignmentCutGeometrySignals(
    *,
    TopologyRequiresJointPortfolio: bool,
    AssignmentCut: RoutingAssignmentCut | None,
    CompleteCutSignals: Iterable[str],
    PriorityCutSignals: Iterable[str],
) -> frozenset[str]:
    """Select relocation geometry without dropping a topology cut endpoint."""
    CompleteSignals = frozenset(map(str, CompleteCutSignals))
    PrioritySignals = frozenset(map(str, PriorityCutSignals))
    if (
        TopologyRequiresJointPortfolio
        and IsHigherOrderAssignmentCut(AssignmentCut)
    ):
        return CompleteSignals
    return (
        PrioritySignals
        if IsHigherOrderAssignmentCut(AssignmentCut) and PrioritySignals
        else CompleteSignals
    )


def BuildSignalTopologyFingerprints(
    Module: Any,
) -> dict[str, str]:
    """Color signals by anonymous directed topology, independent of names."""
    Gates = tuple(getattr(Module, "Gates", ()))
    InputSignals = frozenset(map(str, getattr(Module, "Inputs", ())))
    OutputSignals = frozenset(map(str, getattr(Module, "Outputs", ())))
    GateInputs = tuple(
        tuple(map(str, getattr(Gate, "Inputs", ())))
        for Gate in Gates
    )
    GateOutputs = tuple(
        tuple(map(str, getattr(Gate, "Outputs", ())))
        for Gate in Gates
    )
    Signals = frozenset((
        *InputSignals,
        *OutputSignals,
        *(
            Signal
            for Inputs in GateInputs
            for Signal in Inputs
        ),
        *(
            Signal
            for Outputs in GateOutputs
            for Signal in Outputs
        ),
    ))
    ProducersBySignal: dict[str, set[int]] = {
        Signal: set()
        for Signal in Signals
    }
    ConsumersBySignal: dict[str, set[int]] = {
        Signal: set()
        for Signal in Signals
    }
    for GateIndex, (Inputs, Outputs) in enumerate(
        zip(GateInputs, GateOutputs, strict=True)
    ):
        for Signal in Inputs:
            ConsumersBySignal[Signal].add(GateIndex)
        for Signal in Outputs:
            ProducersBySignal[Signal].add(GateIndex)

    GateKinds = tuple(
        str(getattr(getattr(Gate, "Kind", "NAND"), "value", getattr(
            Gate,
            "Kind",
            "NAND",
        )))
        for Gate in Gates
    )
    GateColors = {
        GateIndex: BuildStableFingerprint({
            "Kind": GateKinds[GateIndex],
            "InputCount": len(GateInputs[GateIndex]),
            "OutputCount": len(GateOutputs[GateIndex]),
        })
        for GateIndex in range(len(Gates))
    }
    SignalColors = {
        Signal: BuildStableFingerprint({
            "InputTerminal": Signal in InputSignals,
            "OutputTerminal": Signal in OutputSignals,
            "ProducerCount": len(ProducersBySignal[Signal]),
            "ConsumerCount": len(ConsumersBySignal[Signal]),
        })
        for Signal in Signals
    }
    # Fixed-depth color refinement avoids relying on declaration order or
    # generated identifiers, while still distinguishing directed cut roles.
    for _ in range(max(1, len(Gates) + len(Signals))):
        NextGateColors = {
            GateIndex: BuildStableFingerprint({
                "Kind": GateKinds[GateIndex],
                "Inputs": sorted(
                    SignalColors[Signal]
                    for Signal in GateInputs[GateIndex]
                ),
                "Outputs": sorted(
                    SignalColors[Signal]
                    for Signal in GateOutputs[GateIndex]
                ),
            })
            for GateIndex in range(len(Gates))
        }
        NextSignalColors = {
            Signal: BuildStableFingerprint({
                "InputTerminal": Signal in InputSignals,
                "OutputTerminal": Signal in OutputSignals,
                "Producers": sorted(
                    GateColors[GateIndex]
                    for GateIndex in ProducersBySignal[Signal]
                ),
                "Consumers": sorted(
                    GateColors[GateIndex]
                    for GateIndex in ConsumersBySignal[Signal]
                ),
            })
            for Signal in Signals
        }
        GateColors = NextGateColors
        SignalColors = NextSignalColors
    return SignalColors


def SelectCutDrivenClusterRefinementSignals(
    AssignmentCut: RoutingAssignmentCut | None,
    SignalTopologyFingerprints: Mapping[str, str],
    MaximumSignals: int = 4,
    Constraints: PlacementAssignmentConstraintSet = (
        PlacementAssignmentConstraintSet()
    ),
) -> frozenset[str]:
    """Select a bounded exact/observed interface neighborhood for reclustering."""
    if AssignmentCut is None or MaximumSignals < 2:
        return frozenset()
    RawEdges = AssignmentCut.PairwiseConflictEdges or tuple((
        *(
            tuple(map(str, Edge))
            for Edge in AssignmentCut.ConflictGraph.get(
                "ObservedPatternConflictEdges",
                (),
            )
            if isinstance(Edge, tuple | list) and len(Edge) == 2
        ),
        *Constraints.ActiveObservedInterfaceConflictEdges,
    ))
    Edges = tuple(sorted({
        tuple(sorted((str(First), str(Second))))
        for First, Second in RawEdges
        if str(First) and str(Second) and str(First) != str(Second)
    }))
    if not Edges:
        return frozenset()
    Degree = {
        Signal: sum(Signal in Edge for Edge in Edges)
        for Edge in Edges
        for Signal in Edge
    }

    def StructuralSignalKey(Signal: str) -> tuple[str, str]:
        return (
            SignalTopologyFingerprints.get(Signal, ""),
            Signal,
        )

    OrderedEdges = sorted(
        Edges,
        key=lambda Edge: (
            -max(Degree[Edge[0]], Degree[Edge[1]]),
            -(Degree[Edge[0]] + Degree[Edge[1]]),
            tuple(sorted(map(StructuralSignalKey, Edge))),
        ),
    )
    Selected: set[str] = set()
    for Edge in OrderedEdges:
        NewSignals = set(Edge) - Selected
        if len(Selected) + len(NewSignals) > MaximumSignals:
            continue
        Selected.update(Edge)
        if len(Selected) >= MaximumSignals:
            break
    return frozenset(Selected)


def BuildStructuralHigherOrderAssignmentCutFingerprint(
    AssignmentCut: RoutingAssignmentCut | None,
    SignalTopologyFingerprints: Mapping[str, str],
) -> str:
    """Fingerprint a higher-order cut without dynamic resources or names."""
    if not IsHigherOrderAssignmentCut(AssignmentCut):
        return ""
    assert AssignmentCut is not None
    ClassificationFamilies = {
        RoutingAssignmentCutClassification.SaturatedBoundaryCut: (
            "saturated-boundary-cut"
        ),
        RoutingAssignmentCutClassification.HigherOrderPlacementConflict: (
            "higher-order-placement-conflict"
        ),
        RoutingAssignmentCutClassification.RelocatedHigherOrderConflict: (
            "higher-order-placement-conflict"
        ),
        RoutingAssignmentCutClassification.LargerMatchingFailure: (
            "larger-matching-failure"
        ),
        RoutingAssignmentCutClassification.RelocatedLargerMatchingFailure: (
            "larger-matching-failure"
        ),
        RoutingAssignmentCutClassification.MultiPairPlacementConflict: (
            "multi-pair-placement-conflict"
        ),
        RoutingAssignmentCutClassification.RelocatedMultiPairConflict: (
            "multi-pair-placement-conflict"
        ),
        RoutingAssignmentCutClassification
        .RelocatedPairwiseIncompatibility: "pairwise-incompatibility",
    }
    UnknownSignalFingerprint = BuildStableFingerprint({
        "AnonymousSignalRole": "unknown",
    })

    def SignalFingerprint(Signal: str) -> str:
        return SignalTopologyFingerprints.get(
            str(Signal),
            UnknownSignalFingerprint,
        )

    ConflictSignals = (
        AssignmentCut.ConflictSignals
        or AssignmentCut.NoCandidateSignals
        or AssignmentCut.RelocationSignals
    )
    return BuildStableFingerprint({
        "Classification": ClassificationFamilies[
            AssignmentCut.Classification
        ],
        "ConflictSignalTopology": sorted(
            SignalFingerprint(Signal)
            for Signal in ConflictSignals
        ),
        "NoCandidateSignalTopology": sorted(
            SignalFingerprint(Signal)
            for Signal in AssignmentCut.NoCandidateSignals
        ),
        "PairwiseConflictTopology": sorted(
            tuple(sorted((
                SignalFingerprint(First),
                SignalFingerprint(Second),
            )))
            for First, Second in AssignmentCut.PairwiseConflictEdges
        ),
    })


def ShouldDiversifyRepeatedAssignmentCut(
    History: list[RoutingAssignmentCut] | tuple[RoutingAssignmentCut, ...],
    Current: RoutingAssignmentCut | None,
    SignalTopologyFingerprints: Mapping[str, str] | None = None,
) -> bool:
    """Detect one structural cut repeated by access-distinct placements."""
    if (
        not IsHigherOrderAssignmentCut(Current)
        or Current is None
        or not Current.AccessTopologyFingerprint
    ):
        return False
    CurrentFingerprint = (
        BuildStructuralHigherOrderAssignmentCutFingerprint(
            Current,
            SignalTopologyFingerprints,
        )
        if SignalTopologyFingerprints is not None
        else Current.ConflictFingerprint
    )
    if not CurrentFingerprint:
        return False
    return any(
        IsHigherOrderAssignmentCut(Previous)
        and (
            BuildStructuralHigherOrderAssignmentCutFingerprint(
                Previous,
                SignalTopologyFingerprints,
            )
            if SignalTopologyFingerprints is not None
            else Previous.ConflictFingerprint
        )
        == CurrentFingerprint
        and bool(Previous.AccessTopologyFingerprint)
        and Previous.AccessTopologyFingerprint
        != Current.AccessTopologyFingerprint
        for Previous in History
    )


def ShouldDeferTopologyCutForMaterializedSibling(
    *,
    Requested: bool,
    TopologyAccessRepairEligible: bool,
    CommittedHistory: Iterable[RoutingAssignmentCut],
    DeferredCuts: Iterable[RoutingAssignmentCut],
    Current: RoutingAssignmentCut,
    SignalTopologyFingerprints: Mapping[str, str],
    AllowRepeatedCutCommit: bool = True,
) -> bool:
    """Stop a retained portfolio once it proves one repeated exact cut.

    Access-distinct siblings are valuable until two of them return the same
    anonymous higher-order cut, two exact higher-order cuts share a stable
    two-signal core, or two candidates expose the same authoritative access
    domain for the same structural cut. At that point another access-equivalent
    sibling is lower-value than committing both cuts to a fresh geometry
    epoch. The overlap is scheduling evidence only; it is never promoted into
    a fabricated exact pair.
    """
    if Requested and not AllowRepeatedCutCommit:
        return True
    PriorCuts = (
        *tuple(CommittedHistory),
        *tuple(DeferredCuts),
    )
    RepeatedExactCut = ShouldDiversifyRepeatedAssignmentCut(
        PriorCuts,
        Current,
        SignalTopologyFingerprints,
    )
    CurrentStructuralFingerprint = (
        BuildStructuralHigherOrderAssignmentCutFingerprint(
            Current,
            SignalTopologyFingerprints,
        )
    )
    RepeatedEquivalentAccessDomainCut = bool(
        CurrentStructuralFingerprint
        and Current.AuthoritativeAccessDomainFingerprint
        and any(
            BuildStructuralHigherOrderAssignmentCutFingerprint(
                Prior,
                SignalTopologyFingerprints,
            )
            == CurrentStructuralFingerprint
            and Prior.AuthoritativeAccessDomainFingerprint
            == Current.AuthoritativeAccessDomainFingerprint
            for Prior in PriorCuts
        )
    )
    CurrentHigherOrderSignals = frozenset(
        BuildAssignmentCutHigherOrderSignalSet(Current)
    )
    OverlappingAccessDistinctCut = bool(
        len(CurrentHigherOrderSignals) >= 3
        and Current.AccessTopologyFingerprint
        and any(
            len(
                CurrentHigherOrderSignals.intersection(
                    BuildAssignmentCutHigherOrderSignalSet(Prior)
                )
            ) >= 2
            and bool(Prior.AccessTopologyFingerprint)
            and Prior.AccessTopologyFingerprint
            != Current.AccessTopologyFingerprint
            for Prior in PriorCuts
        )
    )
    return bool(
        Requested
        and not (
            TopologyAccessRepairEligible
            and (
                RepeatedExactCut
                or RepeatedEquivalentAccessDomainCut
                or OverlappingAccessDistinctCut
            )
        )
    )


def SelectRefinedAssignmentCutDiversificationSignals(
    History: list[RoutingAssignmentCut] | tuple[RoutingAssignmentCut, ...],
    Current: RoutingAssignmentCut | None,
) -> frozenset[str]:
    """Select one exact pair refined from an access-distinct higher-order cut."""
    if (
        Current is None
        or Current.Classification
        not in {
            RoutingAssignmentCutClassification.MandatoryBoundaryCapacityCut,
            RoutingAssignmentCutClassification.PortalCoveragePairConflict,
            RoutingAssignmentCutClassification.PairwiseIncompatibility,
            RoutingAssignmentCutClassification.RelocatedPairwiseIncompatibility,
        }
        or not Current.MandatoryAccessOwnershipFingerprint
    ):
        return frozenset()

    CurrentPair = frozenset()
    if len(Current.PairwiseConflictEdges) == 1:
        CurrentPair = frozenset(Current.PairwiseConflictEdges[0])
    if len(CurrentPair) != 2:
        for ReportedSignals in (
            Current.ConflictSignals,
            Current.RelocationSignals,
            Current.PriorityRelocationSignals,
        ):
            CandidatePair = frozenset(ReportedSignals)
            if len(CandidatePair) == 2:
                CurrentPair = CandidatePair
                break
    if len(CurrentPair) != 2:
        return frozenset()

    for Previous in History:
        if (
            not IsHigherOrderAssignmentCut(Previous)
            or not Previous.MandatoryAccessOwnershipFingerprint
            or Previous.MandatoryAccessOwnershipFingerprint
            == Current.MandatoryAccessOwnershipFingerprint
        ):
            continue
        PreviousSignals = frozenset((
            *Previous.ConflictSignals,
            *Previous.RelocationSignals,
            *Previous.PriorityRelocationSignals,
            *Previous.NoCandidateSignals,
            *(
                Signal
                for Edge in Previous.PairwiseConflictEdges
                for Signal in Edge
            ),
        ))
        if CurrentPair.issubset(PreviousSignals):
            return CurrentPair
    return frozenset()


def SelectRepeatedAssignmentSubcutDiversificationSignals(
    History: list[RoutingAssignmentCut] | tuple[RoutingAssignmentCut, ...],
    Current: RoutingAssignmentCut | None,
) -> frozenset[str]:
    """Select exact pair edges repeated by access-distinct placements.

    A repeated physical pair can be embedded in a larger multi-pair cut, so
    comparing only whole cut fingerprints or requiring exactly one current
    edge misses the actionable common subcut.  Preserve pair grouping while
    returning only endpoints whose exact incompatibility survived a changed
    mandatory-access ownership topology.
    """
    PairClassifications = {
        RoutingAssignmentCutClassification.MandatoryBoundaryCapacityCut,
        RoutingAssignmentCutClassification.PortalCoveragePairConflict,
        RoutingAssignmentCutClassification.PairwiseIncompatibility,
        RoutingAssignmentCutClassification.RelocatedPairwiseIncompatibility,
        RoutingAssignmentCutClassification.MultiPairPlacementConflict,
        RoutingAssignmentCutClassification.RelocatedMultiPairConflict,
    }
    if (
        Current is None
        or Current.Classification not in PairClassifications
        or not Current.MandatoryAccessOwnershipFingerprint
        or not Current.PairwiseConflictEdges
    ):
        return frozenset()
    CurrentEdges = frozenset(Current.PairwiseConflictEdges)
    RepeatedEdges: set[tuple[str, str]] = set()
    for Previous in History:
        if (
            Previous.Classification not in PairClassifications
            or not Previous.MandatoryAccessOwnershipFingerprint
            or Previous.MandatoryAccessOwnershipFingerprint
            == Current.MandatoryAccessOwnershipFingerprint
        ):
            continue
        RepeatedEdges.update(
            CurrentEdges.intersection(Previous.PairwiseConflictEdges)
        )
    return frozenset(
        Signal
        for Edge in RepeatedEdges
        for Signal in Edge
    )


def SelectCumulativeRepeatedAssignmentCutDiversificationSignals(
    History: list[RoutingAssignmentCut] | tuple[RoutingAssignmentCut, ...],
    ActiveConstraints: PlacementAssignmentConstraintSet,
) -> frozenset[str]:
    """Retain active pair endpoints repeated across access topologies.

    Placement repair remains focused on the newest exact cut.  Routing-domain
    diversification is cumulative because a later cut must not erase an
    earlier active pair whose incompatibility survived a different mandatory
    access ownership topology.
    """
    ActiveEdges = frozenset(
        tuple(sorted((str(First), str(Second))))
        for First, Second in ActiveConstraints.PairwiseConflictEdges
        if str(First) != str(Second)
    )
    if not ActiveEdges:
        return frozenset()

    OwnershipsByEdge: dict[tuple[str, str], set[str]] = {
        Edge: set() for Edge in ActiveEdges
    }
    for Cut in History:
        OwnershipFingerprint = (
            Cut.MandatoryAccessOwnershipFingerprint
        )
        if not OwnershipFingerprint:
            continue
        for First, Second in Cut.PairwiseConflictEdges:
            Edge = tuple(sorted((str(First), str(Second))))
            if Edge in OwnershipsByEdge:
                OwnershipsByEdge[Edge].add(OwnershipFingerprint)

    return frozenset(
        Signal
        for Edge, OwnershipFingerprints in OwnershipsByEdge.items()
        if len(OwnershipFingerprints) >= 2
        for Signal in Edge
    )


@dataclass(frozen=True)
class CandidateStarvationPlacementEvidence:
    """One completed empty-window observation within an immutable cut epoch."""

    AssignmentCutFingerprint: str
    AssignmentConstraintFingerprint: str
    AssignmentCut: RoutingAssignmentCut


def BuildCandidateStarvationPlacementEvidence(
    AssignmentCut: RoutingAssignmentCut | None,
    *,
    AssignmentCutFingerprint: str,
    AssignmentConstraintFingerprint: str,
) -> CandidateStarvationPlacementEvidence | None:
    """Bind one empty candidate domain to its originating geometry epoch.

    Retained access-distinct siblings may defer their cuts until the bounded
    portfolio finishes. Their starvation evidence is still authoritative
    within the immutable parent cut and constraint epoch.
    """
    if (
        AssignmentCut is None
        or AssignmentCut.Classification
        != RoutingAssignmentCutClassification
        .CandidateStarvationPlacementConflict
        or not AssignmentCutFingerprint
        or not AssignmentConstraintFingerprint
        or not AssignmentCut.MandatoryAccessOwnershipFingerprint
    ):
        return None
    return CandidateStarvationPlacementEvidence(
        AssignmentCutFingerprint=AssignmentCutFingerprint,
        AssignmentConstraintFingerprint=AssignmentConstraintFingerprint,
        AssignmentCut=AssignmentCut,
    )


def SelectRepeatedCandidateStarvationDiversificationSignals(
    History: (
        list[CandidateStarvationPlacementEvidence]
        | tuple[CandidateStarvationPlacementEvidence, ...]
    ),
    Current: RoutingAssignmentCut | None,
    *,
    AssignmentCutFingerprint: str,
    AssignmentConstraintFingerprint: str,
) -> frozenset[str]:
    """Select signals proven empty across access-distinct sibling placements."""
    if (
        Current is None
        or Current.Classification
        != RoutingAssignmentCutClassification
        .CandidateStarvationPlacementConflict
        or not AssignmentCutFingerprint
        or not AssignmentConstraintFingerprint
        or not Current.MandatoryAccessOwnershipFingerprint
    ):
        return frozenset()

    CurrentSignals = (
        frozenset(Current.NoCandidateSignals)
        or frozenset(Current.ConflictSignals)
    )
    if not CurrentSignals:
        return frozenset()

    RepeatedSignals: set[str] = set()
    for Evidence in History:
        Previous = Evidence.AssignmentCut
        if (
            Evidence.AssignmentCutFingerprint
            != AssignmentCutFingerprint
            or Evidence.AssignmentConstraintFingerprint
            != AssignmentConstraintFingerprint
            or Previous.Classification
            != RoutingAssignmentCutClassification
            .CandidateStarvationPlacementConflict
            or not Previous.MandatoryAccessOwnershipFingerprint
            or Previous.MandatoryAccessOwnershipFingerprint
            == Current.MandatoryAccessOwnershipFingerprint
        ):
            continue
        PreviousSignals = (
            frozenset(Previous.NoCandidateSignals)
            or frozenset(Previous.ConflictSignals)
        )
        RepeatedSignals.update(CurrentSignals.intersection(PreviousSignals))
    return frozenset(RepeatedSignals)


def RequiresImmediateAssignmentCutRelocation(
    AssignmentCut: RoutingAssignmentCut | None,
) -> bool:
    """Return whether exact cut feedback should preempt stale placements."""
    return (
        IsHigherOrderAssignmentCut(AssignmentCut)
        or (
            AssignmentCut is not None
            and AssignmentCut.Classification
            in _ImmediateAssignmentCutRelocationClassifications
        )
    )


def ShouldPreserveCurrentStructuredAssignmentCut(
    Current: RoutingAssignmentCut | None,
    Constraints: PlacementAssignmentConstraintSet,
    Reported: RoutingAssignmentCut | None,
) -> bool:
    """Keep a live exact cut when starvation adds no placement constraint."""
    return (
        Current is not None
        and Reported is not None
        and RequiresImmediateAssignmentCutRelocation(Current)
        and Reported.Classification
        == RoutingAssignmentCutClassification
        .CandidateStarvationPlacementConflict
        and Constraints.WithCut(Reported) == Constraints
    )


def BuildStructuredPlacementRelocationSignals(
    AssignmentCut: RoutingAssignmentCut | None,
    Constraints: PlacementAssignmentConstraintSet,
) -> frozenset[str]:
    """Project immutable cut evidence into the complete relocation cover."""
    CutSignals = (
        (
            *AssignmentCut.ConflictSignals,
            *AssignmentCut.RelocationSignals,
            *AssignmentCut.PriorityRelocationSignals,
            *AssignmentCut.NoCandidateSignals,
            *(
                Signal
                for Edge in AssignmentCut.PairwiseConflictEdges
                for Signal in Edge
            ),
        )
        if AssignmentCut is not None
        else ()
    )
    return frozenset((
        *CutSignals,
        *(
            Signal
            for Signals in Constraints.HigherOrderSignalSets
            for Signal in Signals
        ),
        *(
            Signal
            for Edge in Constraints.PairwiseConflictEdges
            for Signal in Edge
        ),
    ))


def BuildCurrentAssignmentCutRelocationSignals(
    AssignmentCut: RoutingAssignmentCut | None,
) -> frozenset[str]:
    """Return the complete signal cover reported by one exact cut."""
    if AssignmentCut is None:
        return frozenset()
    return frozenset((
        *AssignmentCut.ConflictSignals,
        *AssignmentCut.RelocationSignals,
        *AssignmentCut.PriorityRelocationSignals,
        *AssignmentCut.NoCandidateSignals,
        *(
            Signal
            for Edge in AssignmentCut.PairwiseConflictEdges
            for Signal in Edge
        ),
    ))


def BuildTopologyCutEpochGeometryRelocationSignals(
    AssignmentCut: RoutingAssignmentCut | None,
    ProvenLeaseGeometrySignals: Iterable[str] = (),
) -> frozenset[str]:
    """Combine the current cut with its proved unrealizable lease endpoints."""
    return frozenset((
        *BuildCurrentAssignmentCutRelocationSignals(AssignmentCut),
        *map(str, ProvenLeaseGeometrySignals),
    ))


def SelectRepeatedLeaseRealizabilityGeometrySignals(
    Failure: RoutingFailure,
    MinimumDistinctPatterns: int = 2,
    CompletePortfolioPatternCount: int = 2,
) -> frozenset[str]:
    """Promote the complete exhausted lease endpoint set to geometry repair."""
    if (
        MinimumDistinctPatterns < 1
        or CompletePortfolioPatternCount < 1
        or Failure.Reason != RoutingFailureReason.TrackAssignmentConflict
    ):
        return frozenset()
    Diagnostics = (
        Failure.Diagnostics
        if isinstance(Failure.Diagnostics, dict)
        else {}
    )
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    if (
        not isinstance(ConflictGraph, dict)
        or ConflictGraph.get("Classification")
        != "candidate-starvation-placement-conflict"
        or Diagnostics.get("Action")
        != "advance-placement-after-complete-cluster-lease-portfolio"
    ):
        return frozenset()
    CutSignals = frozenset(map(str, (
        *ConflictGraph.get("ConflictSignals", ()),
        *ConflictGraph.get("NoCandidateSignals", ()),
    )))
    PatternsBySignal: dict[str, set[str]] = {}
    DistinctPatternEntries: set[tuple[str, str]] = set()
    for RawNogood in Diagnostics.get(
        "CandidateRealizabilityNogoods",
        (),
    ):
        if not isinstance(RawNogood, dict):
            continue
        Signal = str(RawNogood.get("Signal", ""))
        Pattern = str(RawNogood.get("PatternFingerprint", ""))
        if Signal and Pattern:
            PatternsBySignal.setdefault(Signal, set()).add(Pattern)
            DistinctPatternEntries.add((Signal, Pattern))
    RepeatedCutSignals = frozenset(
        Signal
        for Signal, Patterns in PatternsBySignal.items()
        if Signal in CutSignals
        if len(Patterns) >= MinimumDistinctPatterns
    )
    CompletePortfolioExhausted = (
        len(DistinctPatternEntries) >= CompletePortfolioPatternCount
    )
    if not RepeatedCutSignals and not CompletePortfolioExhausted:
        return frozenset()
    return frozenset((
        *CutSignals,
        *PatternsBySignal,
    ))


def BuildTopologyCutEpochPinBankRelocationSignals(
    BaseSignals: Iterable[str],
    PinBankRepairSignals: Iterable[str],
    EnableInternalPinBankGeometryRepair: bool,
) -> frozenset[str]:
    """Add only the current pin-bank endpoints to a targeted cut epoch.

    The structured assignment cut remains the cumulative legality/scoring
    basis.  A proven internal pin-bank retry must nevertheless put its newly
    starved endpoints into the physical relocation identity; otherwise the
    epoch can reproduce the same placed geometry while changing only routing
    candidate domains.
    """
    Signals = frozenset(map(str, BaseSignals))
    if not EnableInternalPinBankGeometryRepair:
        return Signals
    return frozenset((
        *Signals,
        *map(str, PinBankRepairSignals),
    ))


def BuildTopologyCutEpochGeometryConstraints(
    AssignmentCut: RoutingAssignmentCut | None,
    CumulativeConstraints: PlacementAssignmentConstraintSet = (
        PlacementAssignmentConstraintSet()
    ),
) -> PlacementAssignmentConstraintSet:
    """Score the current cut while retaining recurrent placement evidence.

    Relocation remains scoped to the current cut's endpoints. The immutable
    cumulative constraints still govern legality and scoring so repairing one
    interface cannot recreate a repeatedly observed conflict elsewhere.
    """
    return CumulativeConstraints.WithCut(AssignmentCut)


def SelectTopologyCutFrontier(
    CurrentCut: RoutingAssignmentCut | None,
    CutHistory: Iterable[RoutingAssignmentCut],
    Enabled: bool,
    MaximumCuts: int = 2,
) -> tuple[RoutingAssignmentCut, ...]:
    """Select the current cut and one distinct recent bounded predecessor."""
    if not Enabled or CurrentCut is None or MaximumCuts <= 0:
        return ()

    def CutIdentity(Cut: RoutingAssignmentCut) -> tuple[str, str]:
        PublishedIdentity = (
            Cut.ConflictFingerprint or Cut.EffectiveWorkFingerprint
        )
        return (
            Cut.Classification.value,
            PublishedIdentity or BuildStableFingerprint(Cut.ToDictionary()),
        )

    Selected = [CurrentCut]
    Seen = {CutIdentity(CurrentCut)}
    for PriorCut in reversed(tuple(CutHistory)):
        Identity = CutIdentity(PriorCut)
        if Identity in Seen or not AssignmentCutHasBoundedExactCore(PriorCut):
            continue
        Selected.append(PriorCut)
        Seen.add(Identity)
        if len(Selected) >= MaximumCuts:
            break
    return tuple(Selected)


def BuildPlacementFingerprint(
    Placement: PcbPlacement,
    MandatoryAccessOwnershipFingerprint: str = "",
    IncludeLocalClaims: bool = True,
) -> str:
    """Fingerprint exact geometry and claims for provenance and artifacts."""
    return BuildStableFingerprint({
        "Gates": [
            (
                Gate.Name,
                Gate.Kind,
                Gate.X,
                Gate.Y,
                Gate.Z,
                Gate.Rotation,
                getattr(Gate, "MirrorX", False),
            )
            for Gate in sorted(
                Placement.Placed.PlacedGates,
                key=lambda Value: Value.Name,
            )
        ],
        "LocalClaims": [
            (
                Claim.Signal,
                Claim.ClusterId,
                tuple(sorted(Claim.Nodes)),
            )
            for Claim in sorted(
                (
                    Placement.Placed.LocalRouteClaims or ()
                    if IncludeLocalClaims
                    else ()
                ),
                key=lambda Value: (Value.Signal, Value.ClusterId),
            )
        ],
        "MandatoryAccessOwnershipFingerprint": (
            MandatoryAccessOwnershipFingerprint
        ),
        "InterClusterChannelFingerprint": (
            getattr(
                getattr(
                    Placement,
                    "InterClusterRoutingChannel",
                    None,
                ),
                "ChannelFingerprint",
                "",
            )
        ),
    })


def BuildPlacementRetentionFingerprint(
    Placement: PcbPlacement,
    MandatoryAccessOwnershipFingerprint: str = "",
    IncludeLocalClaims: bool = True,
) -> str:
    """Fingerprint anonymous relative geometry for candidate retention."""
    Gates = tuple(Placement.Placed.PlacedGates or ())
    Claims = tuple(
        Placement.Placed.LocalRouteClaims or ()
        if IncludeLocalClaims
        else ()
    )
    Positions = [
        (int(Gate.X), int(Gate.Y), int(Gate.Z))
        for Gate in Gates
    ]
    Positions.extend(
        (int(Node[0]), int(Node[1]), int(Node[2]))
        for Claim in Claims
        for Node in Claim.Nodes
    )
    MinimumX = min((Position[0] for Position in Positions), default=0)
    MinimumY = min((Position[1] for Position in Positions), default=0)
    MinimumZ = min((Position[2] for Position in Positions), default=0)

    def NormalizePosition(
        Position: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        return (
            int(Position[0]) - MinimumX,
            int(Position[1]) - MinimumY,
            int(Position[2]) - MinimumZ,
        )

    return BuildStableFingerprint({
        "AnonymousRelativeGates": sorted(
            (
                str(getattr(Gate.Kind, "value", Gate.Kind)),
                *NormalizePosition((Gate.X, Gate.Y, Gate.Z)),
                int(Gate.Rotation),
                bool(getattr(Gate, "MirrorX", False)),
            )
            for Gate in Gates
        ),
        "AnonymousRelativeLocalClaims": sorted(
            tuple(sorted(
                NormalizePosition(Node)
                for Node in Claim.Nodes
            ))
            for Claim in Claims
        ),
        "MandatoryAccessOwnershipFingerprint": (
            MandatoryAccessOwnershipFingerprint
        ),
        "InterClusterChannelFingerprint": (
            getattr(
                getattr(
                    Placement,
                    "InterClusterRoutingChannel",
                    None,
                ),
                "ChannelFingerprint",
                "",
            )
        ),
    })


def BuildClusterInterfacePlacementTopologyFingerprint(
    Placement: PcbPlacement,
    SignalTopologyFingerprints: Mapping[str, str],
) -> str:
    """Fingerprint the boundary ownership topology a placement exposes.

    Absolute translation, portal identifiers, and signal names are excluded.
    The identity changes only when a structural terminal moves relative to the
    component, changes boundary side, or changes its cluster ownership pair.
    """
    Requests = tuple(
        getattr(
            Placement.Placed,
            "ClusterBoundaryLeaseRequests",
            (),
        )
        or ()
    )
    Positions = [
        tuple(int(Coordinate) for Coordinate in Position)
        for Request in Requests
        for Position in (
            *((Request.SourceTerminal,) if Request.SourceTerminal else ()),
            *tuple(Request.TargetTerminals),
        )
    ]
    MinimumX = min((Position[0] for Position in Positions), default=0)
    MinimumY = min((Position[1] for Position in Positions), default=0)
    MinimumZ = min((Position[2] for Position in Positions), default=0)

    def Normalize(
        Position: tuple[int, int, int] | None,
    ) -> tuple[int, int, int] | None:
        if Position is None:
            return None
        return (
            int(Position[0]) - MinimumX,
            int(Position[1]) - MinimumY,
            int(Position[2]) - MinimumZ,
        )

    return BuildStableFingerprint(tuple(sorted(
        (
            int(Request.SourceCluster),
            int(Request.TargetCluster),
            str(Request.SourceBoundarySide),
            str(Request.TargetBoundarySide),
            Normalize(Request.SourceTerminal),
            tuple(sorted(Normalize(Value) for Value in Request.TargetTerminals)),
            bool(Request.CompletePinAccess),
            SignalTopologyFingerprints.get(Request.Signal, ""),
        )
        for Request in Requests
    )))


def SelectInterfaceDiversePlacementStates(
    Candidates: Iterable[PcbPlacementCandidate],
    MaximumStates: int = 6,
) -> tuple[
    tuple[PcbPlacementCandidate, ...],
    tuple[ClusterInterfacePortfolioStateAudit, ...],
]:
    """Retain legal states by actual boundary topology, then stable score."""
    if MaximumStates <= 0:
        raise ValueError("interface placement state bound must be positive")
    Ordered = tuple(Candidates)
    Selected: list[PcbPlacementCandidate] = []
    Audits: list[ClusterInterfacePortfolioStateAudit] = []
    SeenTopologies: dict[str, PcbPlacementCandidate] = {}
    LegalDistinct: list[
        tuple[int, int, PcbPlacementCandidate]
    ] = []
    for CandidateOrdinal, Candidate in enumerate(Ordered):
        CandidateIndex = int(getattr(
            Candidate.JointPlacementState,
            "CandidateIndex",
            CandidateOrdinal,
        ))
        TopologyFingerprint = Candidate.InterfaceTopologyFingerprint
        if (
            Candidate.TopologyDemand is not None
            and Candidate.TopologyDemand
            .MandatoryAccessConflictResources > 0
        ):
            Audits.append(ClusterInterfacePortfolioStateAudit(
                StateIndex=CandidateIndex,
                Classification="mandatory-access-unsat",
                PlacementStateFingerprint=(
                    Candidate.PlacementFingerprint
                ),
                InterfaceTopologyFingerprint=TopologyFingerprint,
                Detail="mandatory capacity-one prescreen rejected state",
            ))
            continue
        if TopologyFingerprint in SeenTopologies:
            Audits.append(ClusterInterfacePortfolioStateAudit(
                StateIndex=CandidateIndex,
                Classification="duplicate-access-topology",
                PlacementStateFingerprint=(
                    Candidate.PlacementFingerprint
                ),
                InterfaceTopologyFingerprint=TopologyFingerprint,
                Detail=(
                    "same structural boundary ownership topology as "
                    f"{SeenTopologies[TopologyFingerprint].CandidateId}"
                ),
            ))
            continue
        SeenTopologies[TopologyFingerprint] = Candidate
        LegalDistinct.append((
            CandidateOrdinal,
            CandidateIndex,
            Candidate,
        ))
    if len(LegalDistinct) <= MaximumStates:
        SelectedPositions = tuple(range(len(LegalDistinct)))
    elif MaximumStates == 1:
        SelectedPositions = (0,)
    else:
        # The input is already score-ranked. Span the complete bounded search
        # result instead of truncating to six near-identical leading states;
        # this preserves the best state while sampling progressively broader
        # slot/transform choices under the unchanged six-state solve bound.
        SelectedPositions = tuple(
            Index * (len(LegalDistinct) - 1) // (MaximumStates - 1)
            for Index in range(MaximumStates)
        )
    SelectedPositionSet = frozenset(SelectedPositions)
    for Position, (
        _CandidateOrdinal,
        CandidateIndex,
        Candidate,
    ) in enumerate(LegalDistinct):
        TopologyFingerprint = Candidate.InterfaceTopologyFingerprint
        if Position not in SelectedPositionSet:
            Audits.append(ClusterInterfacePortfolioStateAudit(
                StateIndex=CandidateIndex,
                Classification="pruned-by-scoring-budget",
                PlacementStateFingerprint=(
                    Candidate.PlacementFingerprint
                ),
                InterfaceTopologyFingerprint=TopologyFingerprint,
                Detail="legal interface-distinct state exceeded fixed bound",
            ))
            continue
        Selected.append(Candidate)
        Audits.append(ClusterInterfacePortfolioStateAudit(
            StateIndex=CandidateIndex,
            Classification="retained-interface-distinct",
            PlacementStateFingerprint=Candidate.PlacementFingerprint,
            InterfaceTopologyFingerprint=TopologyFingerprint,
            Detail="retained for joint placement/interface solve",
        ))
    Audits.sort(key=lambda Audit: Audit.StateIndex)
    ClassifiedIndexes = {Audit.StateIndex for Audit in Audits}
    for MissingIndex in range(MaximumStates):
        if MissingIndex in ClassifiedIndexes:
            continue
        Audits.append(ClusterInterfacePortfolioStateAudit(
            StateIndex=MissingIndex,
            Classification="pruned-by-scoring-budget",
            Detail=(
                "candidate generator exhausted or was deadline-pruned "
                "before producing this bounded state"
            ),
        ))
    Audits.sort(key=lambda Audit: Audit.StateIndex)
    return tuple(Selected), tuple(Audits)


def SelectReleasableLocalClaimSignals(
    AffectedSignals: frozenset[str],
    Claims: tuple[Any, ...],
) -> frozenset[str]:
    """Return only affected signals that actually own local claims."""
    AvailableSignals = frozenset(Claim.Signal for Claim in Claims)
    return AffectedSignals & AvailableSignals


def MeasurePcbDesign(
    Placed: PlacedDesign,
    Routed: RoutedDesign,
) -> tuple[int, int, int, int]:
    """Measure the final PCB footprint and emitted block estimate."""
    Positions = list(Routed.Wires) + list(Routed.Supports)
    for Gate in Placed.PlacedGates:
        Width, Depth = RotatedCellSize(Gate.Kind, Gate.Rotation)
        Positions.append((Gate.X, Gate.Y, Gate.Z))
        Positions.append((Gate.X + Width - 1, Gate.Y, Gate.Z + Depth - 1))
    if not Positions:
        return (0, 0, 0, 0)

    MinimumX = min(Position[0] for Position in Positions)
    MaximumX = max(Position[0] for Position in Positions)
    MinimumZ = min(Position[2] for Position in Positions)
    MaximumZ = max(Position[2] for Position in Positions)
    Width = MaximumX - MinimumX + 1
    Depth = MaximumZ - MinimumZ + 1
    Footprint = Width * Depth
    EstimatedBlocks = len(Routed.Wires) + sum(
        GetCellMacro(Gate.Kind).EstimatedBlocks
        for Gate in Placed.PlacedGates
    )
    return Footprint, EstimatedBlocks, Width, Depth


def PlaceAndRoutePcb(
    Netlist: Any,
    ProgressCallback: Callable[[PcbProgress], None] | None = None,
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
    Strategy: RoutingStrategy | str | None = None,
    RoutedValidationCallback: Callable[[RoutedDesign], None] | None = None,
    RoutingDeadlineSeconds: float | None = None,
) -> PcbResult:
    """Run the explicitly selected router without an automatic fallback."""
    RequestedStrategy = RoutingStrategy.Parse(Strategy or RoutingStrategy.Default)
    UsedStrategy = ExecutionStrategyForRequest(RequestedStrategy)
    ActivePolicy = (
        Policy
        if Policy != DefaultPhysicalDesignPolicy
        and UsedStrategy == RoutingStrategy.Default
        else PolicyForRoutingStrategy(UsedStrategy)
    )
    ActivePolicy = ApplyRoutingRuntimeBudget(
        ActivePolicy,
        RoutingDeadlineSeconds,
    )
    return _PlaceAndRoutePcbWithPolicy(
        Netlist,
        ProgressCallback=ProgressCallback,
        Policy=ActivePolicy,
        Technology=Technology,
        RequestedStrategy=RequestedStrategy,
        UsedStrategy=UsedStrategy,
        RoutedValidationCallback=RoutedValidationCallback,
    )


def _PlaceAndRoutePcbWithPolicy(
    Netlist: Any,
    ProgressCallback: Callable[[PcbProgress], None] | None,
    Policy: PhysicalDesignPolicy,
    Technology: RedstoneRoutingTechnology,
    RequestedStrategy: RoutingStrategy,
    UsedStrategy: RoutingStrategy,
    RoutedValidationCallback: Callable[[RoutedDesign], None] | None = None,
) -> PcbResult:
    """Execute one immutable policy through the template PCB backend."""
    Module = Netlist.Modules[Netlist.Top]
    NandGateCount = sum(
        (
            Kind.value
            if hasattr(Kind := getattr(Gate, "Kind", "NAND"), "value")
            else str(Kind)
        )
        == "NAND"
        for Gate in Module.Gates
    )
    TopologyDemand = BuildTopologyDemandProfile(Module)
    SignalTopologyFingerprints = BuildSignalTopologyFingerprints(Module)
    TopologyPressure = BuildTopologyDemandPressureProfile(
        TopologyDemand,
        Policy.Organization.MaximumClusterEntrances,
    )
    DenseBoundaryRoutingReserve = RequiresDenseBoundaryRoutingReserve(
        TopologyDemand,
        Policy,
    )
    if not TopologyDemand.RequiresJointPortfolio:
        # Preserve the proven v15 compact/ripple repair envelope.  The v16
        # reconvergent-access bounds apply only after topology proves that the
        # joint portfolio is needed, so CLA work cannot perturb established
        # adders by reducing their legal placement recovery space.
        Policy = replace(
            Policy,
            NegotiatedRouting=replace(
                Policy.NegotiatedRouting,
                MaximumPlacementFeedbackRounds=max(
                    5,
                    Policy.NegotiatedRouting.MaximumPlacementFeedbackRounds,
                ),
                MaximumPackedAreaGrowth=max(
                    4.5,
                    Policy.NegotiatedRouting.MaximumPackedAreaGrowth,
                ),
            ),
        )
    # Broad terminal/boundary scale and reconvergent access require different
    # controls. Keep compact geometry unless its typed demand exceeds the
    # configured capacity; never infer a circuit identity from its NAND count.
    Policy = ApplyTopologyDemandPolicyWidening(
        Policy,
        Technology,
        TopologyPressure,
    )
    Deadline = RoutingDeadline.Start(Policy.RuntimeBudgetSeconds)
    Started = Deadline.StartedAt
    ValidateNandOnlyDesign(Netlist)
    if not Module.Gates:
        EmptyPlaced = PlacedDesign(Module=Module, PlacedGates=[])
        EmptyRouted = RoutedDesign(
            Module=Module,
            PlacedGates=[],
            Wires=[],
            Supports=[],
            Repeaters={},
            NetWires={},
        )
        return PcbResult(
            Placed=EmptyPlaced,
            Routed=EmptyRouted,
            Footprint=0,
            EstimatedBlocks=0,
            Width=0,
            Depth=0,
            Policy=Policy,
            Technology=Technology,
            RequestedStrategy=RequestedStrategy.value,
            UsedStrategy=UsedStrategy.value,
        )

    RoutingSpacing = Policy.Placement.RoutingSpacing
    ConfiguredRoutingSpacing = RoutingSpacing
    PlacementGenerationFailures: list[dict[str, object]] = []
    PlacementGenerationDecisions: list[dict[str, object]] = [{
        "Result": "topology-demand-profile",
        "Trigger": (
            "reconvergent-access-pressure"
            if TopologyPressure.ReconvergentAccessPressure
            else (
                "scale-geometry-pressure"
                if TopologyPressure.ScaleGeometryPressure
                else "none"
            )
        ),
        "EnableInitialJointOrientation": (
            TopologyDemand.EnableInitialJointOrientation
        ),
        "Profile": TopologyDemand.ToDictionary(),
        "Pressure": TopologyPressure.ToDictionary(),
    }]
    LastStructuredPlacementFailure: RoutingFailure | None = None
    UniquePlacements: dict[str, tuple[str, int, PcbPlacement]] = {}
    FeedbackByFingerprint: dict[str, Any] = {}
    RoutingResourcesByFingerprint: dict[str, Any] = {}
    FrozenClusterInterfaceAssignmentsByPlacementFingerprint: dict[
        str, ClusterInterfaceAssignment
    ] = {}
    FrozenPreparedPortalDomainCachesByPlacementFingerprint: dict[
        str, Any
    ] = {}
    PreRoutedClusterInterfaceDesignsByPlacementFingerprint: dict[
        str, RoutedDesign
    ] = {}
    RoutedComponentTemplatesByPlacementFingerprint: dict[
        str, RoutedComponentTemplate
    ] = {}
    PortableRawPortalGeometryCaches: tuple[Any, ...] = ()
    MaximumPortableRawPortalGeometryCaches = 8

    def SeedPortableRawPortalGeometryCaches(Resources: Any) -> None:
        """Share only bounded immutable raw portal work across topology slots."""
        if not TopologyDemand.RequiresJointPortfolio:
            return
        Combined = (
            *PortableRawPortalGeometryCaches,
            *tuple(Resources.RawPortalGeometryCaches),
        )
        Seen: set[int] = set()
        Retained = []
        for Cache in reversed(Combined):
            if id(Cache) in Seen:
                continue
            Seen.add(id(Cache))
            Retained.append(Cache)
            if len(Retained) >= MaximumPortableRawPortalGeometryCaches:
                break
        Resources.RawPortalGeometryCaches = tuple(reversed(Retained))

    def CapturePortableRawPortalGeometryCaches(Resources: Any) -> None:
        """Retain newest cross-placement portal templates by object identity."""
        nonlocal PortableRawPortalGeometryCaches
        if not TopologyDemand.RequiresJointPortfolio:
            return
        Combined = (
            *PortableRawPortalGeometryCaches,
            *tuple(Resources.RawPortalGeometryCaches),
        )
        Seen: set[int] = set()
        Retained = []
        for Cache in reversed(Combined):
            if id(Cache) in Seen:
                continue
            Seen.add(id(Cache))
            Retained.append(Cache)
            if len(Retained) >= MaximumPortableRawPortalGeometryCaches:
                break
        PortableRawPortalGeometryCaches = tuple(reversed(Retained))

    MaterializedPlacementByFingerprint: dict[str, PcbPlacement] = {}
    TopologyDemandByFingerprint: dict[
        str,
        TopologyDemandProfile,
    ] = {}
    PlacementRetentionFingerprintByFingerprint: dict[str, str] = {}
    RetainedPlacementTopologyFingerprints: dict[
        str, tuple[str, str]
    ] = {}
    RejectedPlacementRetentionFingerprints: set[str] = set()
    SeenTransactionalEndpointRepairFingerprints: set[str] = set()
    SeenTransactionalEndpointRepairRetentionFingerprints: set[str] = set()
    ExactStatePlacementEvaluationCache: dict[
        str, ExactStatePlacementEvaluation
    ] = {}
    JointPlacementStateByPlacementFingerprint: dict[
        str, PendingJointPlacementState
    ] = {}
    MandatoryAccessPortfolioEvidenceByIdentity: dict[
        MandatoryAccessPortfolioIdentity,
        MandatoryAccessPortfolioEvidence,
    ] = {}
    MandatoryAccessPortfolioEvidenceByRecipeIdentity: dict[
        MandatoryAccessPortfolioIdentity,
        MandatoryAccessPortfolioEvidence,
    ] = {}
    ConsumedStrongMandatoryAccessRepairIdentities: set[
        MandatoryAccessPortfolioIdentity
    ] = set()
    PendingStrongMandatoryAccessRepair = False
    StrongMandatoryAccessRepairMaterializationPending = False
    PendingJointPlacementStates: list[PendingJointPlacementState] = []
    PendingTopologyCutEpoch: TopologyCutEpochIdentity | None = None
    OpenedTopologyCutEpochs: set[TopologyCutEpochIdentity] = set()
    MaterializedJointPlacementStateKeys: set[
        tuple[PendingJointPlacementPortfolioIdentity, int]
    ] = set()
    JointPortfolioGenerationNotAfterByIdentity: dict[
        PendingJointPlacementPortfolioIdentity, float
    ] = {}
    ActiveJointPortfolioIdentityFingerprint = ""
    JointPlacementStateEvents: list[dict[str, object]] = []
    JointPortfolioSliceSeconds: float | None = None
    JointPortfolioPrimaryCandidateId: str | None = None
    PlacementAttemptFailures: list[dict[str, object]] = []
    LastRoutingError: Exception | None = None
    LastStructuredRoutingError: RoutingStageError | None = None
    LastCompletedAssignmentCutError: RoutingStageError | None = None
    PlacementAssignmentCutHistory: list[RoutingAssignmentCut] = []
    DeferredActivePortfolioAssignmentCuts: list[
        DeferredActivePortfolioAssignmentCut
    ] = []
    CutSourcePlacementByFingerprint: dict[str, PcbPlacement] = {}
    CandidateStarvationPlacementHistory: list[
        CandidateStarvationPlacementEvidence
    ] = []
    PlacementRepeatedCandidateStarvationSignals: frozenset[str] = (
        frozenset()
    )
    CurrentPlacementAssignmentCut: RoutingAssignmentCut | None = None
    PlacementAssignmentConstraints = PlacementAssignmentConstraintSet()
    PlacementCoordinatedCandidateDiversificationSignals: frozenset[str] = (
        frozenset()
    )
    # Kept separately from generic candidate diversity.  It is populated
    # only by the measured, access-distinct two-pair lease proof below.
    PlacementClusterPinBankRepairSignals: frozenset[str] = frozenset()
    PlacementRepeatedLeaseGeometrySignals: frozenset[str] = frozenset()
    PostPinBankRepairEpochActive = False
    InternalPinBankGeometryRepairActive = False
    RotatedMacroAncestorTargetedEpochPending = False
    RequiredDistinctPinBankOwnershipFingerprint = ""
    PendingSamePlacementRoutingControlRetry: (
        SamePlacementRoutingControlRetryState | None
    ) = None
    ConsumedPairedLeaseRepairProfileFingerprints: set[str] = set()
    NeedsCurrentStructuredCutRegeneration = False
    NeedsFeedbackPlacementGeneration = False
    PreferPackedPlacements = (
        Policy.NegotiatedRouting.Enabled
        and Policy.NandPacking.Enabled
        and Policy.NandPacking.DeferUnpackedOracle
    )
    GenerationPlan = BuildPlacementGenerationPlan(
        Policy,
        PreferPackedPlacements=PreferPackedPlacements,
        PrioritizeSeparatedPacking=(
            TopologyPressure.ScaleGeometryPressure
        ),
        EnableInitialJointOrientation=(
            TopologyDemand.EnableInitialJointOrientation
        ),
        # The compact ripple recovery is intentionally a direct-only recipe,
        # not a second orientation portfolio.  Its historical geometry keeps
        # the seven-layer footprint; topology-triggered designs receive their
        # bounded joint portfolio through the separate demand predicate.
        EnableCompactDirectOnlyOrientation=False,
    )
    if GenerationPlan.PrimaryRequests:
        ConfiguredRoutingSpacing = (
            GenerationPlan.PrimaryRequests[0].RoutingSpacing
        )
    PlacementGenerationAttempts = 0
    DeferredRequestIndex = 0
    ConsumedDeferredRequestIndexes: set[int] = set()
    PlacementRelocationSignals: frozenset[str] = frozenset()
    PlacementRelocationPrioritySignals: frozenset[str] = frozenset()
    PlacementRequiredRelocationSignals: frozenset[str] = frozenset()
    LastRelocationSignalsUsed: frozenset[str] = frozenset()
    LastRelocationPrioritySignalsUsed: frozenset[str] = frozenset()
    LastRequiredRelocationSignalsUsed: frozenset[str] = frozenset()
    LastAssignmentCutFingerprintUsed = ""
    LastAssignmentConstraintFingerprintUsed = ""
    RelocationGenerationCount = 0
    TotalRelocationGenerationCount = 0
    BaselinePackedGateArea: int | None = None
    RejectedPlacementFingerprints: set[str] = set()
    ProactiveRelocationRequested = False
    BestMandatoryAccessConflictKey: tuple[object, ...] | None = None
    TerminalConstraintEpochRefreshPerformed = False
    TerminalConstraintEpochPortfolioNeedsMaterialization = False
    TerminalConstraintEpochPrimaryCandidateId: str | None = None
    TerminalConstraintEpochPortfolioIdentityFingerprint = ""
    TerminalConstraintEpochAuthoritativeAccessConflictObserved = False
    JointPortfolioTriggered = ResolveJointPlacementPortfolioTrigger(
        False,
        TopologyDemand,
    )

    def _PackedGateArea(Candidate: PcbPlacement) -> int:
        Gates = Candidate.Placed.PlacedGates
        if not Gates:
            return 0
        MinimumX = min(Gate.X for Gate in Gates)
        MinimumZ = min(Gate.Z for Gate in Gates)
        MaximumX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
            for Gate in Gates
        )
        MaximumZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
            for Gate in Gates
        )
        return (MaximumX - MinimumX + 1) * (MaximumZ - MinimumZ + 1)

    def _InterClusterSignals(Candidate: PcbPlacement) -> frozenset[str]:
        """Return signals whose endpoints span packed-cluster ownership."""
        ClusterByGate = {
            Name: ClusterIndex
            for ClusterIndex, Cluster in enumerate(Candidate.Clusters)
            for Name in Cluster
        }
        ProducerCluster = {
            Signal: ClusterByGate.get(Gate.Name)
            for Gate in Module.Gates
            for Signal in Gate.Outputs
        }
        Result: set[str] = set()
        for Gate in Module.Gates:
            TargetCluster = ClusterByGate.get(Gate.Name)
            if TargetCluster is None:
                continue
            for Signal in Gate.Inputs:
                SourceCluster = ProducerCluster.get(Signal)
                if SourceCluster is None or SourceCluster != TargetCluster:
                    Result.add(Signal)
        return frozenset(Result)

    def _RecordAssignmentCut(
        Error: RoutingStageError,
        Candidate: PcbPlacementCandidate,
        *,
        DeferTopologyEpochForMaterializedSibling: bool = False,
    ) -> RoutingAssignmentCut | None:
        """Preserve one complete cut and prepare its exact placement repair."""
        nonlocal LastCompletedAssignmentCutError
        nonlocal CurrentPlacementAssignmentCut
        nonlocal PlacementAssignmentConstraints
        nonlocal PlacementCoordinatedCandidateDiversificationSignals
        nonlocal PlacementClusterPinBankRepairSignals
        nonlocal PlacementRepeatedLeaseGeometrySignals
        nonlocal PostPinBankRepairEpochActive
        nonlocal InternalPinBankGeometryRepairActive
        nonlocal RotatedMacroAncestorTargetedEpochPending
        nonlocal RequiredDistinctPinBankOwnershipFingerprint
        nonlocal PendingSamePlacementRoutingControlRetry
        nonlocal ConsumedPairedLeaseRepairProfileFingerprints
        nonlocal NeedsCurrentStructuredCutRegeneration
        nonlocal PlacementRelocationSignals
        nonlocal PlacementRelocationPrioritySignals
        nonlocal PlacementRequiredRelocationSignals
        nonlocal NeedsFeedbackPlacementGeneration
        nonlocal PendingTopologyCutEpoch
        nonlocal JointPortfolioTriggered
        nonlocal DeferredActivePortfolioAssignmentCuts
        nonlocal PlacementRepeatedCandidateStarvationSignals
        OwnershipFingerprint = (
            Candidate.TopologyDemand.MandatoryAccessOwnershipFingerprint
            if Candidate.TopologyDemand is not None
            else ""
        )
        DenseLeaseRepairEligible = HasDenseBoundaryLeaseRepairEligibility(
            Candidate,
            Policy,
        )
        AccessDistinctLeaseOwnershipFingerprints = (
            ExtractAccessDistinctLeaseOwnershipFingerprints(Error.Failure)
        )
        TopologyAccessRepairEligible = bool(
            Candidate.TopologyDemand
            and Candidate.TopologyDemand.RequiresJointPortfolio
        ) or DenseLeaseRepairEligible
        RepeaterReadyPortalRepairSignals = (
            SelectExhaustedRepeaterAccessCutSignals(Error.Failure)
            if TopologyAccessRepairEligible
            else frozenset()
        )
        ExistingRepeaterReadyPortalRepair = dict(
            (
                Candidate.Placement.Placed.LocalRouteDiagnostics or {}
            ).get("__RepeaterReadyPortalRepair__", {})
        )
        if ExistingRepeaterReadyPortalRepair:
            # The repair is one-shot for a placement and signal profile. Its
            # next failure must become geometry evidence rather than reopen
            # the same portal extension epoch.
            RepeaterReadyPortalRepairSignals = frozenset()
        if TopologyAccessRepairEligible:
            # Dense measured lease pressure is a topology trigger in its own
            # right.  Activate the existing exact joint-state scheduler so a
            # dense cut is repaired through retained access-distinct geometry,
            # rather than falling through the ordinary deferred recipes.
            JointPortfolioTriggered = True
        AssignmentCut = RoutingAssignmentCut.FromFailure(
            Error.Failure,
            SourceCandidateId=Candidate.CandidateId,
            MandatoryAccessOwnershipFingerprint=OwnershipFingerprint,
        )
        if AssignmentCut is None:
            return None
        PlacementRepeatedCandidateStarvationSignals = frozenset()

        def RecordCandidateStarvationEvidence(
            Cut: RoutingAssignmentCut,
            *,
            CutFingerprint: str,
            ConstraintFingerprint: str,
        ) -> None:
            Evidence = BuildCandidateStarvationPlacementEvidence(
                Cut,
                AssignmentCutFingerprint=CutFingerprint,
                AssignmentConstraintFingerprint=ConstraintFingerprint,
            )
            if Evidence is None:
                return
            if any(
                Existing.AssignmentCutFingerprint
                == Evidence.AssignmentCutFingerprint
                and Existing.AssignmentConstraintFingerprint
                == Evidence.AssignmentConstraintFingerprint
                and Existing.AssignmentCut
                .MandatoryAccessOwnershipFingerprint
                == Evidence.AssignmentCut
                .MandatoryAccessOwnershipFingerprint
                and Existing.AssignmentCut.ConflictFingerprint
                == Evidence.AssignmentCut.ConflictFingerprint
                for Existing in CandidateStarvationPlacementHistory
            ):
                return
            CandidateStarvationPlacementHistory.append(Evidence)
        CutSourcePlacementByFingerprint[
            AssignmentCut.ConflictFingerprint
        ] = Candidate.Placement
        DeferCurrentTopologyCut = (
            ShouldDeferTopologyCutForMaterializedSibling(
                Requested=DeferTopologyEpochForMaterializedSibling,
                TopologyAccessRepairEligible=(
                    TopologyAccessRepairEligible
                ),
                CommittedHistory=PlacementAssignmentCutHistory,
                DeferredCuts=(
                    Evidence.AssignmentCut
                    for Evidence in DeferredActivePortfolioAssignmentCuts
                ),
                Current=AssignmentCut,
                SignalTopologyFingerprints=(
                    SignalTopologyFingerprints
                ),
                AllowRepeatedCutCommit=not (
                    Candidate.SourceGenerator
                    == "transactional-cluster-endpoint-repair"
                    and not TransactionalCutStrictlyNarrowsParentInterface(
                        frozenset(map(
                            str,
                            dict(
                                Candidate.Placement.Placed
                                .LocalRouteDiagnostics or {}
                            ).get(
                                "__PlacementRecipe__",
                                {},
                            ).get(
                                "InternalPinBankGeometryRepairSignals",
                                (),
                            ),
                        )),
                        TransactionalCutRepairSignals(AssignmentCut),
                    )
                ),
            )
        )
        if DeferCurrentTopologyCut:
            PlacementRepeatedCandidateStarvationSignals = (
                SelectRepeatedCandidateStarvationDiversificationSignals(
                    CandidateStarvationPlacementHistory,
                    AssignmentCut,
                    AssignmentCutFingerprint=(
                        Candidate.AssignmentCutFingerprint
                    ),
                    AssignmentConstraintFingerprint=(
                        Candidate.AssignmentConstraintFingerprint
                    ),
                )
            )
            RecordCandidateStarvationEvidence(
                AssignmentCut,
                CutFingerprint=Candidate.AssignmentCutFingerprint,
                ConstraintFingerprint=(
                    Candidate.AssignmentConstraintFingerprint
                ),
            )
            DeferredEvidence = DeferredActivePortfolioAssignmentCut(
                AssignmentCut=AssignmentCut,
                SourceCandidateId=Candidate.CandidateId,
                FailureStage=Error.Failure.Stage,
                Error=Error,
                Candidate=Candidate,
            )
            if all(
                Existing.AssignmentCut.ConflictFingerprint
                != AssignmentCut.ConflictFingerprint
                or Existing.AssignmentCut.MandatoryAccessOwnershipFingerprint
                != AssignmentCut.MandatoryAccessOwnershipFingerprint
                for Existing in DeferredActivePortfolioAssignmentCuts
            ):
                DeferredActivePortfolioAssignmentCuts.append(
                    DeferredEvidence
                )
            LastCompletedAssignmentCutError = Error
            JointPlacementStateEvents.append({
                "Status": "active-portfolio-assignment-cut-deferred",
                "CandidateId": Candidate.CandidateId,
                "AssignmentCutFingerprint": (
                    AssignmentCut.ConflictFingerprint
                ),
                "MandatoryAccessOwnershipFingerprint": (
                    AssignmentCut.MandatoryAccessOwnershipFingerprint
                ),
                "DeferredCutCount": len(
                    DeferredActivePortfolioAssignmentCuts
                ),
                "RepeatedCandidateStarvationSignals": sorted(
                    PlacementRepeatedCandidateStarvationSignals
                ),
                "CandidateStarvationEvidenceEpoch": {
                    "AssignmentCutFingerprint": (
                        Candidate.AssignmentCutFingerprint
                    ),
                    "AssignmentConstraintFingerprint": (
                        Candidate.AssignmentConstraintFingerprint
                    ),
                },
                "NextAction": (
                    "repair-repeated-sibling-starvation"
                    if PlacementRepeatedCandidateStarvationSignals
                    else "route-materialized-access-distinct-sibling"
                ),
            })
            return AssignmentCut
        if (
            DeferTopologyEpochForMaterializedSibling
            and not DeferCurrentTopologyCut
        ):
            JointPlacementStateEvents.append({
                "Status": (
                    "active-portfolio-repeated-cut-commit-requested"
                ),
                "CandidateId": Candidate.CandidateId,
                "AssignmentCutFingerprint": (
                    AssignmentCut.ConflictFingerprint
                ),
                "MandatoryAccessOwnershipFingerprint": (
                    AssignmentCut.MandatoryAccessOwnershipFingerprint
                ),
                "DeferredCutCount": len(
                    DeferredActivePortfolioAssignmentCuts
                ),
                "NextAction": (
                    "commit-repeated-cut-and-open-fresh-epoch"
                ),
            })
        if DeferredActivePortfolioAssignmentCuts:
            DeferredCuts = tuple(
                Evidence.AssignmentCut
                for Evidence in DeferredActivePortfolioAssignmentCuts
            )
            for DeferredCut in DeferredCuts:
                PlacementAssignmentCutHistory.append(DeferredCut)
                PlacementAssignmentConstraints = (
                    PlacementAssignmentConstraints.WithCut(DeferredCut)
                )
            JointPlacementStateEvents.append({
                "Status": "active-portfolio-assignment-cuts-committed",
                "CommittedCutCount": len(DeferredCuts),
                "AssignmentCutFingerprints": [
                    Cut.ConflictFingerprint for Cut in DeferredCuts
                ],
                "SourceCandidateIds": [
                    Evidence.SourceCandidateId
                    for Evidence in DeferredActivePortfolioAssignmentCuts
                ],
                "NextAction": "open-aggregate-geometry-epoch",
            })
            DeferredActivePortfolioAssignmentCuts.clear()
        RepeatedLeaseGeometrySignals = (
            SelectRepeatedLeaseRealizabilityGeometrySignals(
                Error.Failure
            )
            if TopologyAccessRepairEligible
            else frozenset()
        )
        PlacementRepeatedLeaseGeometrySignals = (
            RepeatedLeaseGeometrySignals
        )
        PreserveCurrentStructuredCut = (
            ShouldPreserveCurrentStructuredAssignmentCut(
                CurrentPlacementAssignmentCut,
                PlacementAssignmentConstraints,
                AssignmentCut,
            )
            and not RepeatedLeaseGeometrySignals
        )
        ActiveAssignmentCutFingerprint = (
            CurrentPlacementAssignmentCut.ConflictFingerprint
            if PreserveCurrentStructuredCut
            and CurrentPlacementAssignmentCut is not None
            else ""
        )
        ActiveAssignmentConstraintFingerprint = (
            PlacementAssignmentConstraints.Fingerprint
            if PreserveCurrentStructuredCut
            else ""
        )
        CandidateStarvationEpochCutFingerprint = (
            ActiveAssignmentCutFingerprint
            or Candidate.AssignmentCutFingerprint
        )
        CandidateStarvationEpochConstraintFingerprint = (
            ActiveAssignmentConstraintFingerprint
            or Candidate.AssignmentConstraintFingerprint
        )
        RepeatedAcrossAccessDistinctPlacements = (
            ShouldDiversifyRepeatedAssignmentCut(
                PlacementAssignmentCutHistory,
                AssignmentCut,
                SignalTopologyFingerprints,
            )
        )
        RefinedDiversificationSignals = (
            SelectRefinedAssignmentCutDiversificationSignals(
                PlacementAssignmentCutHistory,
                AssignmentCut,
            )
        )
        RepeatedSubcutDiversificationSignals = (
            SelectRepeatedAssignmentSubcutDiversificationSignals(
                PlacementAssignmentCutHistory,
                AssignmentCut,
            )
        )
        LeaseRepeatedPairSignals = (
            SelectRepeatedPairedLeaseSubcutSignals(
                PlacementAssignmentCutHistory,
                AssignmentCut,
                SignalTopologyFingerprints,
            )
            if DenseLeaseRepairEligible
            else frozenset()
        )
        ExistingRoutingControlDiagnostics = dict(
            (Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get(
                "__PlacementRelocation__",
                {},
            )
        )
        CandidatePostPinBankRepairEpoch = bool(
            dict(
                (Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get(
                    "__PlacementRecipe__",
                    {},
                )
            ).get("IsPostPinBankRepairEpoch", False)
        )
        CandidateTransactionalRepairDiagnostics = dict(
            (Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get(
                "__TransactionalClusterEndpointRepair__",
                {},
            )
        )
        CandidateUsedWitnessedMacroRotation = any(
            bool(ClusterDiagnostics.get(
                "PriorityEndpointRotationDelta"
            ))
            for ClusterDiagnostics in dict(
                CandidateTransactionalRepairDiagnostics.get(
                    "Clusters",
                    {},
                )
            ).values()
            if isinstance(ClusterDiagnostics, dict)
        )
        ExistingJointRepairDiagnostics = dict(
            (Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get(
                "__JointClusterPlacement__",
                {},
            )
        )
        CandidateHasActiveStructuredJointRepair = bool(
            SerializedPlacementAssignmentConstraintsAreActive(
                ExistingJointRepairDiagnostics.get(
                    "ActiveAssignmentConstraints"
                )
            )
        )
        LeasePairRetryAlreadyApplied = bool(
            (Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get(
                "__ClusterPinBankRepair__",
                {},
            )
        )
        LeasePairRetryPending = bool(
            LeaseRepeatedPairSignals
            and not LeasePairRetryAlreadyApplied
            and not CompleteAssignmentCutSupersedesLeasePairRetry(
                AssignmentCut
            )
        )
        LeasePairRetryProfileFingerprint = (
            BuildCoordinatedCandidateDiversificationProfile(
                LeaseRepeatedPairSignals
            ).Fingerprint
            if LeasePairRetryPending
            else ""
        )
        LeasePairRetryAlreadyConsumed = bool(
            LeasePairRetryProfileFingerprint
            and LeasePairRetryProfileFingerprint
            in ConsumedPairedLeaseRepairProfileFingerprints
        )
        if LeasePairRetryAlreadyConsumed:
            LeasePairRetryPending = False
        RepeatedCandidateStarvationSignals = (
            SelectRepeatedCandidateStarvationDiversificationSignals(
                CandidateStarvationPlacementHistory,
                AssignmentCut,
                AssignmentCutFingerprint=(
                    CandidateStarvationEpochCutFingerprint
                ),
                AssignmentConstraintFingerprint=(
                    CandidateStarvationEpochConstraintFingerprint
                ),
            )
        )
        PlacementRepeatedCandidateStarvationSignals = (
            RepeatedCandidateStarvationSignals
        )
        # A fresh zero-domain result on an ownership-distinct topology state
        # is already an exact local access proof when it follows a complete
        # structured cut.  Admit the existing one-shot pin-bank repair while
        # the successor epoch is still fundable; ripple/compact paths and
        # ordinary starvation keep their unchanged repeated-evidence rule.
        ImmediateTopologyStarvationSignals = (
            SelectImmediateTopologyPinBankRepairSignals(
                TopologyAccessRepairEligible=(
                    TopologyAccessRepairEligible
                ),
                TopologyRequiresJointPortfolio=bool(
                    Candidate.TopologyDemand
                    and Candidate.TopologyDemand.RequiresJointPortfolio
                ),
                AssignmentCut=AssignmentCut,
                Constraints=PlacementAssignmentConstraints,
            )
        )
        RepeatedHigherOrderPinBankRepairSignals = (
            SelectRepeatedHigherOrderPinBankRepairSignals(
                TopologyAccessRepairEligible=(
                    TopologyAccessRepairEligible
                ),
                RepeatedAcrossAccessDistinctPlacements=(
                    RepeatedAcrossAccessDistinctPlacements
                ),
                CandidatePostPinBankRepairEpoch=(
                    CandidatePostPinBankRepairEpoch
                ),
                AssignmentCut=AssignmentCut,
            )
        )
        ExhaustiveExactPairPinBankRepairSignals = (
            SelectExhaustiveExactPairPinBankRepairSignals(
                TopologyRequiresJointPortfolio=bool(
                    Candidate.TopologyDemand
                    and Candidate.TopologyDemand.RequiresJointPortfolio
                ),
                CandidatePostPinBankRepairEpoch=(
                    CandidatePostPinBankRepairEpoch
                ),
                AssignmentCut=AssignmentCut,
                Failure=Error.Failure,
            )
        )
        PinBankRepairSignals = frozenset((
            *RepeatedCandidateStarvationSignals,
            *ImmediateTopologyStarvationSignals,
            *RepeatedHigherOrderPinBankRepairSignals,
            *ExhaustiveExactPairPinBankRepairSignals,
        ))
        InternalPinBankRetryPending = bool(
            PinBankRepairSignals
            and not LeasePairRetryAlreadyApplied
            and not RepeatedLeaseGeometrySignals
        )
        InternalPinBankRetryProfileFingerprint = (
            BuildCoordinatedCandidateDiversificationProfile(
                PinBankRepairSignals
            ).Fingerprint
            if InternalPinBankRetryPending
            else ""
        )
        if (
            InternalPinBankRetryProfileFingerprint
            in ConsumedPairedLeaseRepairProfileFingerprints
        ):
            InternalPinBankRetryPending = False
        RepeatedHigherOrderPinBankRetryPending = bool(
            InternalPinBankRetryPending
            and RepeatedHigherOrderPinBankRepairSignals
        )
        ExhaustiveExactPairPinBankRetryPending = bool(
            InternalPinBankRetryPending
            and ExhaustiveExactPairPinBankRepairSignals
        )
        if (
            (
                RepeatedHigherOrderPinBankRetryPending
                or ExhaustiveExactPairPinBankRetryPending
            )
            and InternalPinBankRetryProfileFingerprint
        ):
            # Repeated rigid topologies and an exhaustive pair-local search
            # both prove that another routing-only probe cannot change the
            # relevant endpoint domains. Consume the one internal-layout
            # repair directly.
            ConsumedPairedLeaseRepairProfileFingerprints.add(
                InternalPinBankRetryProfileFingerprint
            )
        PlacementAssignmentCutHistory.append(AssignmentCut)
        RecordCandidateStarvationEvidence(
            AssignmentCut,
            CutFingerprint=CandidateStarvationEpochCutFingerprint,
            ConstraintFingerprint=(
                CandidateStarvationEpochConstraintFingerprint
            ),
        )
        PlacementAssignmentConstraints = (
            PlacementAssignmentConstraints.WithCut(AssignmentCut)
        )
        if LeasePairRetryAlreadyApplied and not PreserveCurrentStructuredCut:
            # The pin-bank probe produced a new authoritative cut. Queued
            # states are cancelled below, but previously materialized joint
            # siblings also live in UniquePlacements. Retaining those stale
            # constraint epochs would let them consume the first route slice
            # intended for the fresh cut-scoped portfolio.
            StaleJointFingerprints = tuple(
                Fingerprint
                for Fingerprint, (_Source, _Spacing, StalePlacement) in (
                    UniquePlacements.items()
                )
                if dict(
                    StalePlacement.Placed.LocalRouteDiagnostics or {}
                ).get("__JointClusterPlacement__", {})
                and str(dict(
                    dict(
                        StalePlacement.Placed.LocalRouteDiagnostics or {}
                    ).get("__PlacementRecipe__", {})
                ).get("AssignmentConstraintFingerprint", ""))
                != PlacementAssignmentConstraints.Fingerprint
            )
            for Fingerprint in StaleJointFingerprints:
                _DiscardPlacementFingerprint(Fingerprint)
            if StaleJointFingerprints:
                PlacementGenerationDecisions.append({
                    "Result": "pin-bank-cut-stale-geometry-evicted",
                    "EvictedPlacementFingerprints": list(
                        StaleJointFingerprints
                    ),
                    "AssignmentConstraintFingerprint": (
                        PlacementAssignmentConstraints.Fingerprint
                    ),
                })
        TopologyEpochAssignmentCut = (
            CurrentPlacementAssignmentCut
            if (
                PreserveCurrentStructuredCut
                and CurrentPlacementAssignmentCut is not None
            )
            else AssignmentCut
        )
        TopologyCutEpoch = BuildTopologyCutEpochIdentity(
            TopologyEpochAssignmentCut,
            PlacementAssignmentConstraints,
        )
        TopologyCutEpochRequested = (
            not LeasePairRetryPending
            and (
                bool(RepeatedLeaseGeometrySignals)
                or bool(ImmediateTopologyStarvationSignals)
                or ShouldOpenTopologyCutEpoch(
                    TopologyRequiresJointPortfolio=(
                        TopologyAccessRepairEligible
                    ),
                    AssignmentCut=AssignmentCut,
                    Epoch=TopologyCutEpoch,
                    OpenedEpochs=OpenedTopologyCutEpochs,
                )
            )
        )
        HasEpochRoutingReserve = HasTopologyCutEpochRoutingReserve(
            RemainingSeconds=Deadline.RemainingSeconds(),
            Policy=Policy,
            RequiresDenseBoundaryRouting=(
                TopologyPressure.ScaleGeometryPressure
                and not ImmediateTopologyStarvationSignals
            ),
            HasBoundedExactCutEvidence=(
                AssignmentCutHasBoundedExactCore(AssignmentCut)
            ),
        )
        if TopologyCutEpochRequested and HasEpochRoutingReserve:
            RequestedTopologyCutFrontier = SelectTopologyCutFrontier(
                TopologyEpochAssignmentCut,
                PlacementAssignmentCutHistory,
                Enabled=TopologyDemand.RequiresJointPortfolio,
            )
            StaleStateCount = len(PendingJointPlacementStates)
            PendingJointPlacementStates.clear()
            # A new authoritative cut supersedes every unattempted geometry
            # materialized for an older topology epoch. Retaining those
            # siblings allowed stale access states to consume routing slices
            # after their pending recipes had already been cancelled.
            StaleMaterializedFingerprints = tuple(
                Fingerprint
                for Fingerprint, (_Source, _Spacing, Placement) in (
                    UniquePlacements.items()
                )
                if (
                    Fingerprint != Candidate.PlacementFingerprint
                    and bool(dict(
                        Placement.Placed.LocalRouteDiagnostics or {}
                    ).get("__JointClusterPlacement__", {}))
                    and not PlacementMatchesTopologyCutEpoch(
                        Placement,
                        TopologyCutEpoch,
                    )
                )
            )
            for Fingerprint in StaleMaterializedFingerprints:
                _DiscardPlacementFingerprint(Fingerprint)
            PendingTopologyCutEpoch = TopologyCutEpoch
            NeedsFeedbackPlacementGeneration = True
            PlacementGenerationDecisions.append({
                "Result": "topology-cut-epoch-requested",
                "AssignmentCutFingerprint": (
                    TopologyCutEpoch.AssignmentCutFingerprint
                ),
                "AssignmentConstraintFingerprint": (
                    TopologyCutEpoch.AssignmentConstraintFingerprint
                ),
                "MandatoryAccessOwnershipFingerprint": (
                    TopologyCutEpoch.MandatoryAccessOwnershipFingerprint
                ),
                "TopologyCutFrontier": [
                    {
                        "AssignmentCutFingerprint": (
                            Cut.ConflictFingerprint
                        ),
                        "AssignmentCutWorkFingerprint": (
                            Cut.EffectiveWorkFingerprint
                        ),
                    }
                    for Cut in RequestedTopologyCutFrontier
                ],
                "TopologyCutFrontierCutCount": len(
                    RequestedTopologyCutFrontier
                ),
                "RepeatedLeaseRealizabilityGeometrySignals": sorted(
                    RepeatedLeaseGeometrySignals
                ),
                "CancelledStalePendingStateCount": StaleStateCount,
                "CancelledStaleMaterializedStateCount": len(
                    StaleMaterializedFingerprints
                ),
                "RemainingRoutingSeconds": round(
                    max(0.0, Deadline.RemainingSeconds()),
                    6,
                ),
            })
        elif TopologyCutEpochRequested:
            PlacementGenerationDecisions.append({
                "Result": "topology-cut-epoch-deferred-routing-reserve",
                "AssignmentCutFingerprint": (
                    TopologyCutEpoch.AssignmentCutFingerprint
                ),
                "MandatoryAccessOwnershipFingerprint": (
                    TopologyCutEpoch.MandatoryAccessOwnershipFingerprint
                ),
                "RemainingRoutingSeconds": round(
                    max(0.0, Deadline.RemainingSeconds()),
                    6,
                ),
                "RequiredRoutingReserveSeconds": round(
                    TopologyCutEpochAdmissionReserveSeconds(
                        Policy,
                        (
                            TopologyPressure.ScaleGeometryPressure
                            and not ImmediateTopologyStarvationSignals
                        ),
                        HasBoundedExactCutEvidence=(
                            AssignmentCutHasBoundedExactCore(AssignmentCut)
                        ),
                    ),
                    6,
                ),
                "Reason": (
                    "retain current exact-legal access-distinct portfolio "
                    "instead of cancelling it without a full routing slice"
                ),
            })
        if not PreserveCurrentStructuredCut:
            CurrentPlacementAssignmentCut = AssignmentCut
            NeedsCurrentStructuredCutRegeneration = False
        else:
            NeedsCurrentStructuredCutRegeneration = True
        LastCompletedAssignmentCutError = Error
        PairwiseSignals = frozenset(
            Signal
            for Edge in AssignmentCut.PairwiseConflictEdges
            for Signal in Edge
        )
        CompleteCutSignals = frozenset((
            *AssignmentCut.RelocationSignals,
            *AssignmentCut.ConflictSignals,
            *AssignmentCut.NoCandidateSignals,
            *PairwiseSignals,
            *RepeatedLeaseGeometrySignals,
        ))
        CutPrioritySignals = frozenset(
            (
                *AssignmentCut.PriorityRelocationSignals,
                *RepeatedLeaseGeometrySignals,
            )
        ) or CompleteCutSignals
        GeometryCutSignals = SelectAssignmentCutGeometrySignals(
            TopologyRequiresJointPortfolio=TopologyAccessRepairEligible,
            AssignmentCut=AssignmentCut,
            CompleteCutSignals=CompleteCutSignals,
            PriorityCutSignals=CutPrioritySignals,
        )
        # The compact default path never receives topology-specific routing
        # controls.  A repeated complete cut may diversify all endpoints; a
        # repeated capacity-one pair through a different ownership topology
        # may diversify only that reported structural subcut.
        CurrentCutCoordinatedCandidateDiversificationSignals = (
            SelectTopologyCoordinatedCandidateDiversificationSignals(
                TopologyRequiresJointPortfolio=TopologyAccessRepairEligible,
                RepeatedExactCut=RepeatedAcrossAccessDistinctPlacements,
                CompleteCutSignals=CompleteCutSignals,
                RepeatedSubcutSignals=(
                    frozenset((
                        *RepeatedSubcutDiversificationSignals,
                        *(
                            LeaseRepeatedPairSignals
                            if LeasePairRetryPending
                            else ()
                        ),
                    ))
                ),
            )
        )
        CumulativeRepeatedCutDiversificationSignals = (
            SelectCumulativeRepeatedAssignmentCutDiversificationSignals(
                PlacementAssignmentCutHistory,
                PlacementAssignmentConstraints,
            )
        )
        if (
            GeometryCutSignals
            and not PreserveCurrentStructuredCut
            and not LeasePairRetryPending
        ):
            PlacementRelocationSignals = (
                GeometryCutSignals
                if IsHigherOrderAssignmentCut(AssignmentCut)
                else frozenset((
                    *PlacementRelocationSignals,
                    *GeometryCutSignals,
                ))
            )
        if (
            CutPrioritySignals
            and not PreserveCurrentStructuredCut
            and not LeasePairRetryPending
        ):
            PlacementRelocationPrioritySignals = CutPrioritySignals
        if (
            RequiresImmediateAssignmentCutRelocation(AssignmentCut)
            and not PreserveCurrentStructuredCut
            and not LeasePairRetryPending
        ):
            PlacementRequiredRelocationSignals = (
                CutPrioritySignals or GeometryCutSignals
            )
            NeedsFeedbackPlacementGeneration = True
        if not PreserveCurrentStructuredCut:
            PlacementCoordinatedCandidateDiversificationSignals = (
                CurrentCutCoordinatedCandidateDiversificationSignals
            )
        elif CurrentCutCoordinatedCandidateDiversificationSignals:
            PlacementCoordinatedCandidateDiversificationSignals = frozenset((
                *PlacementCoordinatedCandidateDiversificationSignals,
                *CurrentCutCoordinatedCandidateDiversificationSignals,
            ))
        if LeasePairRetryPending or InternalPinBankRetryPending:
            PlacementClusterPinBankRepairSignals = frozenset((
                *LeaseRepeatedPairSignals,
                *PinBankRepairSignals,
            ))
            PlacementCoordinatedCandidateDiversificationSignals = frozenset((
                *PlacementCoordinatedCandidateDiversificationSignals,
                *PlacementClusterPinBankRepairSignals,
            ))
            PostPinBankRepairEpochActive = True
            InternalPinBankGeometryRepairActive = (
                InternalPinBankRetryPending
            )
            RequiredDistinctPinBankOwnershipFingerprint = (
                (
                    Candidate.TopologyDemand
                    .MandatoryAccessOwnershipFingerprint
                )
                if (
                    InternalPinBankRetryPending
                    and Candidate.TopologyDemand is not None
                )
                else ""
            )
        elif not PreserveCurrentStructuredCut:
            PlacementClusterPinBankRepairSignals = frozenset()
            InternalPinBankGeometryRepairActive = False
            RequiredDistinctPinBankOwnershipFingerprint = ""
            if RepeatedLeaseGeometrySignals:
                PostPinBankRepairEpochActive = False
        if (
            Candidate.SourceGenerator
            == "transactional-cluster-endpoint-repair"
            and CandidateUsedWitnessedMacroRotation
            and RepeatedAcrossAccessDistinctPlacements
            and not PreserveCurrentStructuredCut
        ):
            # A legal macro rotation that returns an earlier exact cut has
            # exhausted local pin permutations. Reuse the bounded targeted
            # materializer for its aggregate geometry epoch so the fixed
            # deadline funds a state rather than another broad beam.
            PostPinBankRepairEpochActive = True
            InternalPinBankGeometryRepairActive = True
            RotatedMacroAncestorTargetedEpochPending = True
            PlacementClusterPinBankRepairSignals = frozenset(
                AssignmentCut.PriorityRelocationSignals
                or AssignmentCut.RelocationSignals
            )
            PlacementGenerationDecisions.append({
                "Result": "rotated-macro-ancestor-cut-targeted-epoch",
                "CandidateId": Candidate.CandidateId,
                "AssignmentCutFingerprint": AssignmentCut.ConflictFingerprint,
                "RelocationSignals": sorted(
                    PlacementClusterPinBankRepairSignals
                ),
            })
        AccessDistinctDiversificationEvidence = (
            AccessDistinctAssignmentCutDiversificationEvidence(
                RepeatedExactCut=RepeatedAcrossAccessDistinctPlacements,
                RefinedExactCut=bool(RefinedDiversificationSignals),
                RepeatedExactSubcut=bool(
                    RepeatedSubcutDiversificationSignals
                    or LeasePairRetryPending
                    or InternalPinBankRetryPending
                ),
            )
        )
        PendingSamePlacementRoutingControlRetry = (
            BuildSamePlacementRoutingControlRetryState(
                PlacementFingerprint=Candidate.PlacementFingerprint,
                AssignmentCutFingerprint=(
                    AssignmentCut.ConflictFingerprint
                ),
                Signals=(
                    PinBankRepairSignals
                    if ImmediateTopologyStarvationSignals
                    else PlacementCoordinatedCandidateDiversificationSignals
                ),
                Evidence=AccessDistinctDiversificationEvidence,
            )
            if (
                (
                    not PreserveCurrentStructuredCut
                    or InternalPinBankRetryPending
                )
                and not CandidatePostPinBankRepairEpoch
                and not RepeatedHigherOrderPinBankRetryPending
                and not ExhaustiveExactPairPinBankRetryPending
                and (
                    # A repeated paired lease subcut is the one routing-only
                    # repair permitted to reopen an already-relocated exact
                    # geometry. It changes boundary ownership, not geometry,
                    # and is still consumed after this single attempt.
                    LeasePairRetryPending
                    or InternalPinBankRetryPending
                    or (
                        not LeasePairRetryAlreadyApplied
                        and not LeasePairRetryAlreadyConsumed
                        and not CandidateHasActiveStructuredJointRepair
                    )
                )
            )
            else None
        )
        if RepeaterReadyPortalRepairSignals:
            PendingSamePlacementRoutingControlRetry = (
                BuildSamePlacementRoutingControlRetryState(
                    PlacementFingerprint=Candidate.PlacementFingerprint,
                    AssignmentCutFingerprint=(
                        AssignmentCut.ConflictFingerprint
                    ),
                    Signals=RepeaterReadyPortalRepairSignals,
                    Evidence=(
                        AccessDistinctAssignmentCutDiversificationEvidence(
                            ExhaustedRepeaterAccessCut=True,
                        )
                    ),
                )
            )
            PlacementCoordinatedCandidateDiversificationSignals = (
                RepeaterReadyPortalRepairSignals
            )
            PlacementGenerationDecisions.append({
                "Result": "repeater-ready-portal-retry-requested",
                "CandidateId": Candidate.CandidateId,
                "PlacementFingerprint": Candidate.PlacementFingerprint,
                "AssignmentCutFingerprint": (
                    AssignmentCut.ConflictFingerprint
                ),
                "Signals": sorted(RepeaterReadyPortalRepairSignals),
                "ReusedPlacedGeometry": True,
                "NextAction": (
                    "route-same-placement-with-repeater-ready-portals"
                ),
            })
        if (
            PendingSamePlacementRoutingControlRetry is not None
            and (
                LeasePairRetryProfileFingerprint
                or InternalPinBankRetryProfileFingerprint
            )
        ):
            ConsumedPairedLeaseRepairProfileFingerprints.add(
                LeasePairRetryProfileFingerprint
                or InternalPinBankRetryProfileFingerprint
            )
        elif (
            ImmediateTopologyStarvationSignals
            and InternalPinBankRetryProfileFingerprint
        ):
            ConsumedPairedLeaseRepairProfileFingerprints.add(
                InternalPinBankRetryProfileFingerprint
            )
        ContinuePostPinBankRepairEpoch = (
            ShouldContinuePostPinBankRepairEpoch(
                CandidatePostPinBankRepairEpoch=(
                    CandidatePostPinBankRepairEpoch
                ),
                InternalPinBankRetryPending=(
                    InternalPinBankRetryPending
                ),
                ImmediateTopologyStarvationSignals=(
                    ImmediateTopologyStarvationSignals
                ),
            )
        )
        if (
            CandidatePostPinBankRepairEpoch
            and not ContinuePostPinBankRepairEpoch
        ):
            # The marker authorizes exactly one fresh exact geometry after
            # the pin-bank probe. Its next cut must advance through normal
            # complete-cut relocation, not inherit stale routing controls.
            PostPinBankRepairEpochActive = False
            InternalPinBankGeometryRepairActive = False
            RequiredDistinctPinBankOwnershipFingerprint = ""
        PlacementGenerationDecisions.append({
            "Result": "structured-assignment-cut-feedback",
            "CandidateId": Candidate.CandidateId,
            "AssignmentCut": AssignmentCut.ToDictionary(),
            "ActivePlacementConstraints": (
                PlacementAssignmentConstraints.ToDictionary()
            ),
            "RepeatedAcrossAccessDistinctPlacements": (
                RepeatedAcrossAccessDistinctPlacements
            ),
            "RepeatedLeaseRealizabilityGeometrySignals": sorted(
                RepeatedLeaseGeometrySignals
            ),
            "RefinedAcrossAccessDistinctPlacements": bool(
                RefinedDiversificationSignals
            ),
            "RepeatedAssignmentSubcutDiversificationSignals": sorted(
                RepeatedSubcutDiversificationSignals
            ),
            "AccessDistinctLeaseOwnershipFingerprints": list(
                AccessDistinctLeaseOwnershipFingerprints
            ),
            "LeaseRepeatedPairDiversificationSignals": sorted(
                LeaseRepeatedPairSignals
            ),
            "ClusterPinBankRepairSignals": sorted(
                PlacementClusterPinBankRepairSignals
            ),
            "LeasePairRetryAlreadyApplied": LeasePairRetryAlreadyApplied,
            "LeasePairRetryAlreadyConsumed": LeasePairRetryAlreadyConsumed,
            "DenseLeaseRepairEligible": DenseLeaseRepairEligible,
            "CumulativeRepeatedAssignmentCutDiversificationSignals": sorted(
                CumulativeRepeatedCutDiversificationSignals
            ),
            "RepeatedCandidateStarvationSignals": sorted(
                RepeatedCandidateStarvationSignals
            ),
            "RepeatedHigherOrderPinBankRepairSignals": sorted(
                RepeatedHigherOrderPinBankRepairSignals
            ),
            "RepeatedHigherOrderPinBankRetryPending": (
                RepeatedHigherOrderPinBankRetryPending
            ),
            "ExhaustiveExactPairPinBankRepairSignals": sorted(
                ExhaustiveExactPairPinBankRepairSignals
            ),
            "ExhaustiveExactPairPinBankRetryPending": (
                ExhaustiveExactPairPinBankRetryPending
            ),
            "ContinuedPostPinBankRepairEpoch": (
                ContinuePostPinBankRepairEpoch
            ),
            "CandidateStarvationEvidenceEpoch": (
                {
                    "AssignmentCutFingerprint": (
                        CandidateStarvationEpochCutFingerprint
                    ),
                    "AssignmentConstraintFingerprint": (
                        CandidateStarvationEpochConstraintFingerprint
                    ),
                }
                if (
                    CandidateStarvationEpochCutFingerprint
                    and CandidateStarvationEpochConstraintFingerprint
                )
                else None
            ),
            "PreservedCurrentStructuredCut": (
                PreserveCurrentStructuredCut
            ),
            "CoordinatedCandidateDiversificationSignals": sorted(
                PlacementCoordinatedCandidateDiversificationSignals
            ),
            "CurrentCutCoordinatedCandidateDiversificationSignals": sorted(
                CurrentCutCoordinatedCandidateDiversificationSignals
            ),
            "SamePlacementRoutingControlRetryEligible": (
                PendingSamePlacementRoutingControlRetry is not None
            ),
            "SamePlacementRoutingControlRetry": (
                PendingSamePlacementRoutingControlRetry.ToDictionary()
                if PendingSamePlacementRoutingControlRetry is not None
                else None
            ),
            "NextAction": (
                "advance-retained-portfolio-with-current-structured-cut"
                if PreserveCurrentStructuredCut
                else "joint-cut-relocation"
                if RequiresImmediateAssignmentCutRelocation(AssignmentCut)
                else "bounded-placement-feedback"
            ),
        })
        return AssignmentCut

    def _PlacementFailureWithHistory(
        Failure: RoutingFailure,
    ) -> RoutingFailure:
        Diagnostics = dict(Failure.Diagnostics or {})
        Diagnostics.update({
            "PlacementGenerationFailures": PlacementGenerationFailures,
            "PlacementGenerationDecisions": PlacementGenerationDecisions,
            "PlacementAttempts": PlacementAttemptFailures,
            "JointPlacementStateEvents": JointPlacementStateEvents,
            "AssignmentCutHistory": [
                AssignmentCut.ToDictionary()
                for AssignmentCut in PlacementAssignmentCutHistory
            ],
            "DeferredActivePortfolioAssignmentCuts": [
                Evidence.ToDictionary()
                for Evidence in DeferredActivePortfolioAssignmentCuts
            ],
            "CurrentAssignmentCut": (
                CurrentPlacementAssignmentCut.ToDictionary()
                if CurrentPlacementAssignmentCut is not None
                else None
            ),
            "ActivePlacementConstraints": (
                PlacementAssignmentConstraints.ToDictionary()
            ),
            "Deadline": Deadline.ToDictionary(),
        })
        return RoutingFailure(
            Reason=Failure.Reason,
            Stage=Failure.Stage,
            AffectedNets=Failure.AffectedNets,
            Resources=Failure.Resources,
            Locations=Failure.Locations,
            RepairActions=Failure.RepairActions,
            Detail=Failure.Detail,
            Diagnostics=Diagnostics,
        )

    AssignmentCutUnspecified = object()

    def _TryPlacement(
        Request: PlacementGenerationRequest,
        JointPlacementCandidateIndex: int = 0,
        FixedRelocationVariant: int | None = None,
        FixedCandidateSpacing: int | None = None,
        FixedRelocationSignals: frozenset[str] | None = None,
        FixedRelocationPrioritySignals: frozenset[str] | None = None,
        FixedRequiredRelocationSignals: frozenset[str] | None = None,
        FixedAssignmentCut: object = AssignmentCutUnspecified,
        FixedAssignmentConstraints: object = AssignmentCutUnspecified,
        FixedCoordinatedCandidateDiversificationSignals: object = (
            AssignmentCutUnspecified
        ),
        FixedTopologyCutFrontier: object = AssignmentCutUnspecified,
        MaterializeRoutingResources: bool = True,
        PlacementGenerationNotAfter: float | None = None,
        CountPlacementGenerationAttempt: bool = True,
        QueueRetainedJointPortfolioStates: bool = True,
    ) -> bool:
        nonlocal PlacementGenerationAttempts, LastStructuredPlacementFailure
        nonlocal LastRelocationSignalsUsed
        nonlocal LastRelocationPrioritySignalsUsed
        nonlocal LastRequiredRelocationSignalsUsed
        nonlocal LastAssignmentCutFingerprintUsed
        nonlocal LastAssignmentConstraintFingerprintUsed
        nonlocal RelocationGenerationCount
        nonlocal TotalRelocationGenerationCount
        nonlocal BaselinePackedGateArea
        nonlocal PlacementRelocationSignals
        nonlocal PlacementRelocationPrioritySignals
        nonlocal PlacementRequiredRelocationSignals
        nonlocal PlacementAssignmentConstraints
        nonlocal PlacementCoordinatedCandidateDiversificationSignals
        nonlocal PlacementClusterPinBankRepairSignals
        nonlocal PlacementRepeatedLeaseGeometrySignals
        nonlocal InternalPinBankGeometryRepairActive
        nonlocal NeedsCurrentStructuredCutRegeneration
        nonlocal NeedsFeedbackPlacementGeneration
        nonlocal ProactiveRelocationRequested
        nonlocal BestMandatoryAccessConflictKey
        nonlocal JointPortfolioTriggered
        nonlocal PostPinBankRepairEpochActive
        nonlocal PendingStrongMandatoryAccessRepair
        nonlocal StrongMandatoryAccessRepairMaterializationPending
        EffectiveAssignmentCut = (
            CurrentPlacementAssignmentCut
            if FixedAssignmentCut is AssignmentCutUnspecified
            else FixedAssignmentCut
        )
        EffectiveAssignmentConstraints = (
            PlacementAssignmentConstraints
            if FixedAssignmentConstraints is AssignmentCutUnspecified
            else FixedAssignmentConstraints
        )
        if (
            EffectiveAssignmentCut is not None
            and not isinstance(EffectiveAssignmentCut, RoutingAssignmentCut)
        ):
            raise TypeError(
                "FixedAssignmentCut must be RoutingAssignmentCut or None"
            )
        if not isinstance(
            EffectiveAssignmentConstraints,
            PlacementAssignmentConstraintSet,
        ):
            raise TypeError(
                "FixedAssignmentConstraints must be "
                "PlacementAssignmentConstraintSet"
            )
        EffectiveAssignmentCutFingerprint = (
            EffectiveAssignmentCut.ConflictFingerprint
            if EffectiveAssignmentCut is not None
            else ""
        )
        EffectiveAssignmentConstraintFingerprint = (
            EffectiveAssignmentConstraints.Fingerprint
        )
        EffectiveCoordinatedCandidateDiversificationSignals = (
            PlacementCoordinatedCandidateDiversificationSignals
            if (
                FixedCoordinatedCandidateDiversificationSignals
                is AssignmentCutUnspecified
            )
            else FixedCoordinatedCandidateDiversificationSignals
        )
        if not isinstance(
            EffectiveCoordinatedCandidateDiversificationSignals,
            frozenset,
        ):
            raise TypeError(
                "FixedCoordinatedCandidateDiversificationSignals must be "
                "a frozenset"
            )
        EnableCurrentClusterLocalRouteReuse = bool(
            PlacementClusterPinBankRepairSignals
        ) and PlacementClusterPinBankRepairSignals.issubset(
            EffectiveCoordinatedCandidateDiversificationSignals
        )
        Request = ApplyJointPlacementPortfolioTrigger(
            Request,
            JointPortfolioTriggered,
        )
        StrongMandatoryAccessRepair = bool(
            StrongMandatoryAccessRepairMaterializationPending
            and SourceGenerator == "row-beam-conflict-relocation"
            and JointPlacementCandidateIndex == 0
        )
        if (
            JointPlacementCandidateIndex == 0
            and CountPlacementGenerationAttempt
            and PlacementGenerationAttempts >= GenerationPlan.MaximumAttempts
            and not StrongMandatoryAccessRepair
        ):
            return False
        if (
            JointPlacementCandidateIndex == 0
            and CountPlacementGenerationAttempt
        ):
            PlacementGenerationAttempts += 1
        SourceGenerator = Request.SourceGenerator
        if FixedRelocationVariant is not None:
            RelocationVariant = FixedRelocationVariant
            RelocationSpacingLevel = 0
        elif StrongMandatoryAccessRepair:
            # One complete access-distinct portfolio has proved that rigid
            # slot/orientation changes cannot legalize the current cut. Reuse
            # the existing variant-12 near-portal geometry repair exactly
            # once for this typed cut/constraint epoch before any broad
            # unpacked generator consumes the remaining routing budget.
            StrongMandatoryAccessRepairMaterializationPending = False
            RelocationVariant = 12
            RelocationSpacingLevel = 0
            RelocationGenerationCount = max(
                RelocationGenerationCount,
                2,
            )
            TotalRelocationGenerationCount += 1
        elif SourceGenerator == "row-beam-conflict-relocation":
            RelocationInputsChanged = (
                PlacementRelocationSignals != LastRelocationSignalsUsed
                or PlacementRelocationPrioritySignals
                != LastRelocationPrioritySignalsUsed
                or PlacementRequiredRelocationSignals
                != LastRequiredRelocationSignalsUsed
                or EffectiveAssignmentCutFingerprint
                != LastAssignmentCutFingerprintUsed
                or EffectiveAssignmentConstraintFingerprint
                != LastAssignmentConstraintFingerprintUsed
            )
            if RelocationInputsChanged:
                RelocationGenerationCount = 0
            LastRelocationSignalsUsed = PlacementRelocationSignals
            LastRelocationPrioritySignalsUsed = (
                PlacementRelocationPrioritySignals
            )
            LastRequiredRelocationSignalsUsed = (
                PlacementRequiredRelocationSignals
            )
            LastAssignmentCutFingerprintUsed = (
                EffectiveAssignmentCutFingerprint
            )
            LastAssignmentConstraintFingerprintUsed = (
                EffectiveAssignmentConstraintFingerprint
            )
            RelocationVariant = BuildPlacementRelocationVariant(
                RelocationGenerationCount=RelocationGenerationCount,
                ReconvergentAccessPressure=(
                    TopologyPressure.ReconvergentAccessPressure
                ),
            )
            RelocationGenerationCount += 1
            RelocationSpacingLevel = min(
                TotalRelocationGenerationCount,
                Policy.Placement.RoutingSpacingAlternatives,
            )
            if Policy.NegotiatedRouting.Enabled:
                RelocationSpacingLevel = 0
            if (
                ConfiguredRoutingSpacing
                > Policy.Placement.RoutingSpacing
            ):
                RelocationSpacingLevel = 0
            TotalRelocationGenerationCount += 1
        else:
            RelocationVariant = 0
            RelocationSpacingLevel = 0
        # Ordinary deferred generators preserve cumulative congestion
        # feedback.  The one topology cut epoch instead relocates exactly
        # the complete current cut while retaining all learned constraints
        # for placement legality and scoring.
        UseCurrentCutGeometry = ShouldUseCurrentAssignmentCutGeometry(
            Request.UseCurrentAssignmentCutRelocationSignals,
            SourceGenerator,
            EffectiveAssignmentCut,
        )
        if UseCurrentCutGeometry:
            EffectiveRelocationSignals = (
                FixedRelocationSignals
                if FixedRelocationSignals is not None
                else BuildTopologyCutEpochPinBankRelocationSignals(
                    BuildTopologyCutEpochGeometryRelocationSignals(
                        EffectiveAssignmentCut,
                        PlacementRepeatedLeaseGeometrySignals,
                    ),
                    PlacementClusterPinBankRepairSignals,
                    InternalPinBankGeometryRepairActive,
                )
            )
            GeometryAssignmentConstraints = (
                BuildTopologyCutEpochGeometryConstraints(
                    EffectiveAssignmentCut,
                    EffectiveAssignmentConstraints,
                )
            )
        else:
            BaseRelocationSignals = (
                PlacementRelocationSignals
                if FixedRelocationSignals is None
                else FixedRelocationSignals
            )
            EffectiveRelocationSignals = frozenset((
                *BaseRelocationSignals,
                *BuildStructuredPlacementRelocationSignals(
                    EffectiveAssignmentCut,
                    EffectiveAssignmentConstraints,
                ),
            ))
            GeometryAssignmentConstraints = EffectiveAssignmentConstraints
        EffectiveTopologyCutFrontier = (
            SelectTopologyCutFrontier(
                EffectiveAssignmentCut,
                PlacementAssignmentCutHistory,
                Enabled=(
                    UseCurrentCutGeometry
                    and TopologyDemand.RequiresJointPortfolio
                ),
            )
            if FixedTopologyCutFrontier is AssignmentCutUnspecified
            else FixedTopologyCutFrontier
        )
        if not isinstance(EffectiveTopologyCutFrontier, tuple) or any(
            not isinstance(Cut, RoutingAssignmentCut)
            for Cut in EffectiveTopologyCutFrontier
        ):
            raise TypeError(
                "FixedTopologyCutFrontier must be a tuple of "
                "RoutingAssignmentCut values"
            )
        EffectiveRelocationPrioritySignals = (
            PlacementRelocationPrioritySignals
            if FixedRelocationPrioritySignals is None
            else FixedRelocationPrioritySignals
        )
        EffectiveRequiredRelocationSignals = (
            PlacementRequiredRelocationSignals
            if FixedRequiredRelocationSignals is None
            else FixedRequiredRelocationSignals
        )
        EffectiveRelocationPrioritySignals = (
            BuildTopologyCutEpochPinBankRelocationSignals(
                EffectiveRelocationPrioritySignals,
                PlacementClusterPinBankRepairSignals,
                InternalPinBankGeometryRepairActive,
            )
        )
        EffectiveRequiredRelocationSignals = (
            BuildTopologyCutEpochPinBankRelocationSignals(
                EffectiveRequiredRelocationSignals,
                PlacementClusterPinBankRepairSignals,
                InternalPinBankGeometryRepairActive,
            )
        )
        EffectiveInternalPinBankGeometryRepairSignals = (
            PlacementClusterPinBankRepairSignals
            if InternalPinBankGeometryRepairActive
            else frozenset()
        )
        GeometryAssignmentCut = EffectiveAssignmentCut
        if (
            SourceGenerator == "row-beam-direct-only"
            and not TopologyDemand.RequiresJointPortfolio
        ):
            GeometryAssignmentCut = None
        CandidateSpacing = (
            FixedCandidateSpacing
            if FixedCandidateSpacing is not None
            else Request.RoutingSpacing + RelocationSpacingLevel
        )
        CandidatePacking = Request.PackingPolicy
        JointPortfolioState = PendingJointPlacementState(
            Request=Request,
            CandidateIndex=JointPlacementCandidateIndex,
            RelocationVariant=RelocationVariant,
            RoutingSpacing=CandidateSpacing,
            RelocationSignals=EffectiveRelocationSignals,
            RelocationPrioritySignals=(
                EffectiveRelocationPrioritySignals
            ),
            RequiredRelocationSignals=(
                EffectiveRequiredRelocationSignals
            ),
            AssignmentCut=GeometryAssignmentCut,
            AssignmentConstraints=GeometryAssignmentConstraints,
            CoordinatedCandidateDiversificationSignals=(
                EffectiveCoordinatedCandidateDiversificationSignals
            ),
            EnableClusterLocalRouteReuse=(
                EnableCurrentClusterLocalRouteReuse
            ),
            IsPostPinBankRepairEpoch=PostPinBankRepairEpochActive,
            EnableInternalPinBankGeometryRepair=(
                InternalPinBankGeometryRepairActive
            ),
            InternalPinBankGeometryRepairSignals=(
                EffectiveInternalPinBankGeometryRepairSignals
            ),
            RequiredDistinctPinBankOwnershipFingerprint=(
                RequiredDistinctPinBankOwnershipFingerprint
            ),
            TopologyCutFrontier=EffectiveTopologyCutFrontier,
        )
        JointPortfolioIdentity = (
            BuildPendingJointPlacementPortfolioIdentity(
                JointPortfolioState
            )
        )
        JointPortfolioIdentityFingerprint = (
            BuildPendingJointPlacementPortfolioFingerprint(
                JointPortfolioState
            )
            if CandidatePacking.EnableJointClusterOrientation
            else ""
        )
        if (
            CandidatePacking.EnableJointClusterOrientation
            and PlacementGenerationNotAfter is not None
        ):
            JointPortfolioGenerationNotAfterByIdentity.setdefault(
                JointPortfolioIdentity,
                PlacementGenerationNotAfter,
            )
        PlacementStarted = monotonic()
        IsDeferredRequest = Request in GenerationPlan.DeferredRequests
        RemainingGenerationSlots = (
            max(
                1,
                GenerationPlan.MaximumAttempts
                - PlacementGenerationAttempts
                + 1,
            )
            if IsDeferredRequest
            else max(
                1,
                len(GenerationPlan.PrimaryRequests)
                - PlacementGenerationAttempts
                + 1,
            )
        )
        if PlacementGenerationNotAfter is None:
            RoutingReserveSeconds = min(
                PlacementGenerationRoutingReserveSeconds(
                    Policy,
                    DenseBoundaryRoutingReserve,
                ),
                max(0.01, Deadline.RemainingSeconds() * 0.5),
            )
            AvailableGenerationSeconds = max(
                0.0,
                Deadline.RemainingSeconds() - RoutingReserveSeconds,
            )
        else:
            RoutingReserveSeconds = max(
                0.0,
                Deadline.ExpiresAt - PlacementGenerationNotAfter,
            )
            AvailableGenerationSeconds = max(
                0.0,
                min(Deadline.ExpiresAt, PlacementGenerationNotAfter)
                - PlacementStarted,
            )
        if AvailableGenerationSeconds <= 0:
            PlacementGenerationDecisions.append({
                "SourceGenerator": SourceGenerator,
                "RoutingSpacing": CandidateSpacing,
                "Result": "skipped-routing-reserve",
                "RoutingReserveSeconds": round(RoutingReserveSeconds, 6),
                "RemainingSeconds": round(Deadline.RemainingSeconds(), 6),
                "PlacementAttempts": list(PlacementAttemptFailures),
            })
            if not UniquePlacements:
                LastStructuredPlacementFailure = RoutingFailure(
                    Reason=RoutingFailureReason.Stagnated,
                    Stage="PlacementGeneration",
                    Detail=(
                        "placement generation reached the routing reserve "
                        "before producing an exact-legal candidate"
                    ),
                    RepairActions=("AdvancePlacementGenerator",),
                    Diagnostics={
                        "SourceGenerator": SourceGenerator,
                        "RoutingReserveSeconds": RoutingReserveSeconds,
                        "PlacementAttempts": PlacementAttemptFailures,
                    },
                )
                FailureSnapshot = BuildPlacementFailureHistorySnapshot(
                    LastStructuredPlacementFailure
                )
                PlacementGenerationFailures.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "PackedNandPlacement": bool(CandidatePacking.Enabled),
                    "Failure": LastStructuredPlacementFailure.Detail,
                    "PlacementGenerationBudgetSeconds": 0.0,
                    "ElapsedSeconds": 0.0,
                    "Diagnostics": FailureSnapshot,
                })
            return False
        PlacementGenerationBudgetSeconds = max(
            0.001,
            AvailableGenerationSeconds / RemainingGenerationSlots,
        )
        if SourceGenerator == "row-beam-conflict-relocation":
            PlacementGenerationBudgetSeconds = max(
                PlacementGenerationBudgetSeconds,
                min(36.0, AvailableGenerationSeconds),
            )
        PlacementGenerationExpiresAt = min(
            Deadline.ExpiresAt,
            PlacementStarted + PlacementGenerationBudgetSeconds,
            (
                PlacementGenerationNotAfter
                if PlacementGenerationNotAfter is not None
                else Deadline.ExpiresAt
            ),
        )
        DebugPlacementPhase = [None]
        DebugPlacementPhaseStarted = [monotonic()]

        def CheckPlacementGeneration(
            Diagnostics: dict[str, object],
        ) -> None:
            Current = monotonic()
            Phase = Diagnostics.get("Phase")
            if (
                bool(os.environ.get("RCS_DEBUG_PLACEMENT_PHASES"))
                and Phase
                in {
                    "start",
                    "connectivity-clusters",
                    "cluster-slots",
                    "vertical-stacking-start",
                    "localized-terminal",
                    "localized-terminal-search-complete",
                    "terminal-placement-complete",
                    "local-access-geometry",
                    "complete",
                    "placement-construction-complete",
                    "exact-isolation-complete",
                }
                and Phase != DebugPlacementPhase[0]
            ):
                print(
                    "[debug] placement phase "
                    f"previous={DebugPlacementPhase[0]} "
                    f"elapsed={Current - DebugPlacementPhaseStarted[0]:.6f}s "
                    f"next={Phase} diagnostics={Diagnostics}",
                    flush=True,
                )
                DebugPlacementPhase[0] = Phase
                DebugPlacementPhaseStarted[0] = Current
            if (
                Current < Deadline.ExpiresAt
                and Current < PlacementGenerationExpiresAt
            ):
                return
            FailureDiagnostics = {
                "SourceGenerator": SourceGenerator,
                "RoutingSpacing": CandidateSpacing,
                "PlacementGenerationAttempt": PlacementGenerationAttempts,
                "MaximumPlacementGenerationAttempts": (
                    GenerationPlan.MaximumAttempts
                ),
                "PlacementGenerationFailures": PlacementGenerationFailures,
                "PlacementGenerationDecisions": PlacementGenerationDecisions,
                "PlacementAttempts": PlacementAttemptFailures,
                "PlacementGenerationDeadline": {
                    "RuntimeBudgetSeconds": round(
                        PlacementGenerationBudgetSeconds,
                        6,
                    ),
                    "ElapsedSeconds": round(
                        Current - PlacementStarted,
                        6,
                    ),
                    "Expired": Current >= PlacementGenerationExpiresAt,
                    "LimitedByGlobalDeadline": (
                        PlacementGenerationExpiresAt >= Deadline.ExpiresAt
                    ),
                    "RoutingReserveSeconds": round(
                        RoutingReserveSeconds,
                        6,
                    ),
                },
                **Diagnostics,
            }
            Deadline.RaiseIfExpired("Placement", FailureDiagnostics)
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.Stagnated,
                    Stage="PlacementGeneration",
                    Detail=(
                        "per-candidate placement generation slice expired; "
                        "advance to the next deterministic generator"
                    ),
                    RepairActions=("AdvancePlacementGenerator",),
                    Diagnostics=FailureDiagnostics,
                )
            )
        Fingerprint: str | None = None
        RetentionFingerprint: str | None = None

        def MandatoryConflictMap(
            Profile: Any,
        ) -> dict[object, set[str]]:
            Result: dict[object, set[str]] = {}
            for Resource, Owners in (
                *Profile.CrossConflicts,
                *Profile.SelfConflicts,
            ):
                Result.setdefault(Resource, set()).update(
                    map(str, Owners)
                )
            return Result

        try:
            CheckPlacementGeneration({"Phase": "placement-generation-start"})
            UseMandatoryAccessPreScreen = (
                ShouldUseMandatoryAccessPreScreen(
                    SourceGenerator=SourceGenerator,
                    PackingEnabled=CandidatePacking.Enabled,
                    JointOrientationEnabled=(
                        CandidatePacking.EnableJointClusterOrientation
                    ),
                    HasRelocationSignals=bool(
                        EffectiveRelocationSignals
                    ),
                    TopologyRequiresJointPortfolio=(
                        TopologyDemand.RequiresJointPortfolio
                    ),
                    HasAssignmentCut=EffectiveAssignmentCut is not None,
                    AssignmentConstraintsActive=(
                        PlacementAssignmentConstraintsAreActive(
                            GeometryAssignmentConstraints
                        )
                    ),
                )
            )
            Candidate = PlacePcbGraph(
                Netlist,
                RoutingSpacing=CandidateSpacing,
                PlacementPolicy=Policy.Placement,
                ClusterPolicy=Policy.Clustering,
                MaximumBoundaryTerminals=Policy.Organization.MaximumClusterEntrances,
                MaximumEntrancesPerSignal=Policy.Organization.MaximumClusterEntrancesPerSignal,
                PackingPolicy=CandidatePacking,
                RelocationSignals=EffectiveRelocationSignals,
                RelocationPrioritySignals=(
                    EffectiveRelocationPrioritySignals
                ),
                RequiredRelocationSignals=(
                    EffectiveRequiredRelocationSignals
                ),
                RelocationVariant=RelocationVariant,
                JointPlacementCandidateIndex=JointPlacementCandidateIndex,
                AssignmentCut=GeometryAssignmentCut,
                AssignmentConstraints=GeometryAssignmentConstraints,
                CoordinatedCandidateDiversificationSignals=(
                    EffectiveCoordinatedCandidateDiversificationSignals
                ),
                MandatoryAccessPreScreenOnly=UseMandatoryAccessPreScreen,
                PlacementScoringOnly=(
                    CandidatePacking.EnableJointClusterOrientation
                    and TopologyDemand.RequiresJointPortfolio
                ),
                EnableClusterBoundaryLeases=(
                    ShouldEnableClusterBoundaryLeaseInterface(
                        ScaleGeometryPressure=(
                            TopologyPressure.ScaleGeometryPressure
                        ),
                        TopologyRequiresJointPortfolio=(
                            TopologyDemand.RequiresJointPortfolio
                        ),
                    )
                ),
                EnableClusterInterfacePlacementFeasibility=(
                    TopologyDemand.RequiresJointPortfolio
                ),
                CutDrivenClusterRefinementSignals=(
                    SelectCutDrivenClusterRefinementSignals(
                        GeometryAssignmentCut,
                        SignalTopologyFingerprints,
                        Constraints=GeometryAssignmentConstraints,
                    )
                    if TopologyDemand.RequiresJointPortfolio
                    else None
                ),
                EnableInternalPinBankGeometryRepair=(
                    JointPortfolioState
                    .EnableInternalPinBankGeometryRepair
                ),
                InternalPinBankGeometryRepairSignals=(
                    JointPortfolioState
                    .InternalPinBankGeometryRepairSignals
                ),
                FocusedCutEpochPlacement=(
                    Request.UseCurrentAssignmentCutRelocationSignals
                ),
                TopologyCutFrontier=(
                    JointPortfolioState.TopologyCutFrontier
                ),
                WorkCheck=CheckPlacementGeneration,
            )
            PreScreenMandatoryProfile = (
                (
                    Candidate.MandatoryAccessPreScreenProfile
                    if (
                        Candidate.MandatoryAccessPreScreenProfile
                        is not None
                    )
                    else MeasureMandatoryAccessConflictProfile(
                        Candidate.Placed.PlacedGates,
                        Candidate.SignalOrder,
                        WorkCheck=CheckPlacementGeneration,
                    )
                )
                if UseMandatoryAccessPreScreen
                else None
            )
            PreScreenMandatoryConflicts = (
                MandatoryConflictMap(PreScreenMandatoryProfile)
                if PreScreenMandatoryProfile is not None
                else None
            )
            if UseMandatoryAccessPreScreen and not PreScreenMandatoryConflicts:
                Candidate = PlacePcbGraph(
                    Netlist,
                    RoutingSpacing=CandidateSpacing,
                    PlacementPolicy=Policy.Placement,
                    ClusterPolicy=Policy.Clustering,
                    MaximumBoundaryTerminals=Policy.Organization.MaximumClusterEntrances,
                    MaximumEntrancesPerSignal=Policy.Organization.MaximumClusterEntrancesPerSignal,
                    PackingPolicy=CandidatePacking,
                    RelocationSignals=EffectiveRelocationSignals,
                    RelocationPrioritySignals=(
                        EffectiveRelocationPrioritySignals
                    ),
                    RequiredRelocationSignals=(
                        EffectiveRequiredRelocationSignals
                    ),
                    RelocationVariant=RelocationVariant,
                    JointPlacementCandidateIndex=JointPlacementCandidateIndex,
                    AssignmentCut=GeometryAssignmentCut,
                    AssignmentConstraints=GeometryAssignmentConstraints,
                    CoordinatedCandidateDiversificationSignals=(
                        EffectiveCoordinatedCandidateDiversificationSignals
                    ),
                    PlacementScoringOnly=(
                        CandidatePacking.EnableJointClusterOrientation
                        and TopologyDemand.RequiresJointPortfolio
                    ),
                    EnableClusterBoundaryLeases=(
                        ShouldEnableClusterBoundaryLeaseInterface(
                            ScaleGeometryPressure=(
                                TopologyPressure.ScaleGeometryPressure
                            ),
                            TopologyRequiresJointPortfolio=(
                                TopologyDemand.RequiresJointPortfolio
                            ),
                        )
                    ),
                    EnableClusterInterfacePlacementFeasibility=(
                        TopologyDemand.RequiresJointPortfolio
                    ),
                    CutDrivenClusterRefinementSignals=(
                        SelectCutDrivenClusterRefinementSignals(
                            GeometryAssignmentCut,
                            SignalTopologyFingerprints,
                            Constraints=GeometryAssignmentConstraints,
                        )
                        if TopologyDemand.RequiresJointPortfolio
                        else None
                    ),
                    EnableInternalPinBankGeometryRepair=(
                        JointPortfolioState
                        .EnableInternalPinBankGeometryRepair
                    ),
                    InternalPinBankGeometryRepairSignals=(
                        JointPortfolioState
                        .InternalPinBankGeometryRepairSignals
                    ),
                    FocusedCutEpochPlacement=(
                        Request.UseCurrentAssignmentCutRelocationSignals
                    ),
                    TopologyCutFrontier=(
                        JointPortfolioState.TopologyCutFrontier
                    ),
                    WorkCheck=CheckPlacementGeneration,
                )
                # The fast screen intentionally omits terminal banks. Measure
                # the committed geometry once below for ownership ranking.
                PreScreenMandatoryProfile = None
                PreScreenMandatoryConflicts = None
            PackedGateArea = _PackedGateArea(Candidate)
            if (
                CandidatePacking.Enabled
                and SourceGenerator != "row-beam-conflict-relocation"
                and BaselinePackedGateArea is None
            ):
                BaselinePackedGateArea = PackedGateArea
            MaximumPackedGateArea = (
                int(
                    BaselinePackedGateArea
                    * Policy.NegotiatedRouting.MaximumPackedAreaGrowth
                    * (
                        1.1
                        if (
                            RelocationVariant >= 3
                            and (
                                TopologyPressure
                                .ReconvergentAccessPressure
                            )
                        )
                        else 1.0
                    )
                )
                if BaselinePackedGateArea is not None
                else None
            )
            if (
                CandidatePacking.Enabled
                and MaximumPackedGateArea is not None
                and PackedGateArea > MaximumPackedGateArea
            ):
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": "rejected-packed-area-growth",
                    "PackedGateArea": PackedGateArea,
                    "BaselinePackedGateArea": BaselinePackedGateArea,
                    "MaximumPackedGateArea": MaximumPackedGateArea,
                })
                return False
            RecipeDiagnostics = dict(
                Candidate.Placed.LocalRouteDiagnostics or {}
            )
            RecipeDiagnostics["__PlacementRecipe__"] = {
                "SourceGenerator": SourceGenerator,
                "RoutingSpacing": CandidateSpacing,
                "Packed": bool(CandidatePacking.Enabled),
                "AssignmentCutFingerprint": (
                    EffectiveAssignmentCutFingerprint
                ),
                "AssignmentConstraintFingerprint": (
                    EffectiveAssignmentConstraintFingerprint
                ),
                "JointPortfolioIdentityFingerprint": (
                    JointPortfolioIdentityFingerprint
                ),
                "IsPostPinBankRepairEpoch": (
                    PostPinBankRepairEpochActive
                ),
                "EnableInternalPinBankGeometryRepair": (
                    InternalPinBankGeometryRepairActive
                ),
                "RequiredDistinctPinBankOwnershipFingerprint": (
                    RequiredDistinctPinBankOwnershipFingerprint
                ),
            }
            Candidate.Placed.LocalRouteDiagnostics = RecipeDiagnostics
            CheckPlacementGeneration({
                "Phase": "placement-construction-complete",
            })
            JointDiagnostics = dict(
                RecipeDiagnostics.get("__JointClusterPlacement__", {})
            )
            ExactStatePlacementCacheDiagnostics = dict(
                JointDiagnostics.get(
                    "ExactStatePlacementCache",
                    {},
                )
            )
            ExactStatePlacementCacheKey = str(
                ExactStatePlacementCacheDiagnostics.get("Key", "")
            )
            CachedExactStateEvaluation = (
                ExactStatePlacementEvaluationCache.get(
                    ExactStatePlacementCacheKey
                )
                if ExactStatePlacementCacheKey
                else None
            )
            if CachedExactStateEvaluation is None:
                ValidatePlacedCellElectricalIsolation(
                    Candidate.Placed,
                    WorkCheck=CheckPlacementGeneration,
                )
                CheckPlacementGeneration({
                    "Phase": "exact-isolation-complete",
                })
            else:
                CheckPlacementGeneration({
                    "Phase": "exact-state-evaluation-cache-hit",
                    "ExactStatePlacementCacheKey": (
                        ExactStatePlacementCacheKey
                    ),
                })
            CandidateResources = None
            MandatoryProfile = (
                CachedExactStateEvaluation.MandatoryAccessProfile
                if CachedExactStateEvaluation is not None
                else (
                    (
                        PreScreenMandatoryProfile
                        if PreScreenMandatoryProfile is not None
                        else MeasureMandatoryAccessConflictProfile(
                            Candidate.Placed.PlacedGates,
                            Candidate.SignalOrder,
                            WorkCheck=CheckPlacementGeneration,
                        )
                    )
                    if (
                        CandidatePacking.Enabled
                        and CandidatePacking.EnableProactiveInterClusterRelocation
                    )
                    else None
                )
            )
            MandatoryConflicts = (
                MandatoryConflictMap(MandatoryProfile)
                if MandatoryProfile is not None
                else {}
            )
            if MandatoryConflicts:
                JointPortfolioTriggered = (
                    ResolveJointPlacementPortfolioTrigger(
                        JointPortfolioTriggered,
                        TopologyDemand,
                        MandatoryAccessConflictObserved=True,
                    )
                )
            CandidateTopologyDemand = (
                CachedExactStateEvaluation.TopologyDemand
                if CachedExactStateEvaluation is not None
                else MeasurePlacementTopologyDemand(
                    TopologyDemand,
                    Candidate,
                    MandatoryConflicts=MandatoryConflicts,
                    MandatoryProfile=MandatoryProfile,
                )
            )
            if (
                ExactStatePlacementCacheKey
                and CachedExactStateEvaluation is None
            ):
                ExactStatePlacementEvaluationCache[
                    ExactStatePlacementCacheKey
                ] = ExactStatePlacementEvaluation(
                    MandatoryAccessProfile=MandatoryProfile,
                    TopologyDemand=CandidateTopologyDemand,
                )
            if ExactStatePlacementCacheDiagnostics:
                ExactStatePlacementCacheDiagnostics[
                    "EvaluationHit"
                ] = CachedExactStateEvaluation is not None
                JointDiagnostics["ExactStatePlacementCache"] = (
                    ExactStatePlacementCacheDiagnostics
                )
                RecipeDiagnostics["__JointClusterPlacement__"] = (
                    JointDiagnostics
                )
            RecipeDiagnostics["__TopologyDemandProfile__"] = (
                CandidateTopologyDemand.ToDictionary()
            )
            Candidate.Placed.LocalRouteDiagnostics = RecipeDiagnostics

            def QueueRetainedJointStates() -> None:
                if not (
                    QueueRetainedJointPortfolioStates
                    and JointPlacementCandidateIndex == 0
                    and CandidatePacking.Enabled
                    and CandidatePacking.EnableJointClusterOrientation
                ):
                    return
                RetainedStates = JointDiagnostics.get(
                    "ExactLegalRetainedStates",
                    JointDiagnostics.get("RetainedStates", ()),
                )
                QueuedStates = [
                    State
                    for State in RetainedStates
                    if int(State["CandidateIndex"])
                    != JointPlacementCandidateIndex
                ][:max(
                    0,
                    (
                        CandidatePacking
                        .RetainedJointPlacementCandidates
                        * (
                            2
                            if TopologyDemand.RequiresJointPortfolio
                            else 1
                        )
                    )
                    - 1,
                )]
                CandidateStates = [
                    PendingJointPlacementState(
                        Request=Request,
                        CandidateIndex=int(State["CandidateIndex"]),
                        RelocationVariant=RelocationVariant,
                        RoutingSpacing=CandidateSpacing,
                        RelocationSignals=EffectiveRelocationSignals,
                        RelocationPrioritySignals=(
                            EffectiveRelocationPrioritySignals
                        ),
                        RequiredRelocationSignals=(
                            EffectiveRequiredRelocationSignals
                        ),
                        AssignmentCut=EffectiveAssignmentCut,
                        AssignmentConstraints=EffectiveAssignmentConstraints,
                        CoordinatedCandidateDiversificationSignals=(
                            EffectiveCoordinatedCandidateDiversificationSignals
                        ),
                        EnableClusterLocalRouteReuse=(
                            EnableCurrentClusterLocalRouteReuse
                        ),
                        IsPostPinBankRepairEpoch=(
                            PostPinBankRepairEpochActive
                        ),
                        EnableInternalPinBankGeometryRepair=(
                            InternalPinBankGeometryRepairActive
                        ),
                        InternalPinBankGeometryRepairSignals=(
                            EffectiveInternalPinBankGeometryRepairSignals
                        ),
                        RequiredDistinctPinBankOwnershipFingerprint=(
                            RequiredDistinctPinBankOwnershipFingerprint
                        ),
                        TopologyCutFrontier=(
                            EffectiveTopologyCutFrontier
                        ),
                    )
                    for State in QueuedStates
                ]
                ExistingStateKeys = {
                    *MaterializedJointPlacementStateKeys,
                    *(
                        BuildPendingJointPlacementStateKey(State)
                        for State in PendingJointPlacementStates
                    ),
                }
                NewCandidateStates = [
                    State
                    for State in CandidateStates
                    if BuildPendingJointPlacementStateKey(State)
                    not in ExistingStateKeys
                ]
                PendingJointPlacementStates.extend(NewCandidateStates)
                NewCandidateIndices = {
                    State.CandidateIndex for State in NewCandidateStates
                }
                QueuedStates = [
                    State
                    for State in QueuedStates
                    if int(State["CandidateIndex"]) in NewCandidateIndices
                ]
                JointPlacementStateEvents.extend({
                    "CandidateIndex": int(State["CandidateIndex"]),
                    "Status": "queued",
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Score": State.get("SearchScore"),
                    "Transforms": State.get("Transforms", {}),
                } for State in QueuedStates)
                PlacementGenerationDecisions.append({
                    "Result": "queued-joint-placement-states",
                    "SourceGenerator": SourceGenerator,
                    "JointPlacementCandidateIndex": (
                        JointPlacementCandidateIndex
                    ),
                    "QueuedCandidateIndices": [
                        int(State["CandidateIndex"])
                        for State in QueuedStates
                    ],
                })

            if JointDiagnostics:
                ExactPreScreen = {
                    "MandatoryAccessConflictResources": len(
                        MandatoryConflicts
                    ),
                    "MandatoryAccessConflictSignals": sorted({
                        Signal
                        for Owners in MandatoryConflicts.values()
                        for Signal in Owners
                    }),
                    "BoundaryOverflow": sum(
                        Cluster.BoundaryOverflow
                        for Cluster in Candidate.PackedClusters
                    ),
                    "PinScarcityCount": sum(
                        Cluster.PinScarcityCount
                        for Cluster in Candidate.PackedClusters
                    ),
                    "LocalClaimCount": len(
                        Candidate.Placed.LocalRouteClaims or ()
                    ),
                    "MandatoryAccessOwnershipFingerprint": (
                        CandidateTopologyDemand
                        .MandatoryAccessOwnershipFingerprint
                    ),
                    "MandatoryAccessConflictFingerprint": (
                        CandidateTopologyDemand
                        .MandatoryAccessConflictFingerprint
                    ),
                    "JointOrderKey": list(
                        CandidateTopologyDemand.JointOrderKey
                    ),
                    "TopologyDemandProfile": (
                        CandidateTopologyDemand.ToDictionary()
                    ),
                    "MandatoryAccessProfile": (
                        MandatoryProfile.ToDictionary()
                        if MandatoryProfile is not None
                        else None
                    ),
                }
                JointDiagnostics["ExactPreScreen"] = ExactPreScreen
                RecipeDiagnostics["__JointClusterPlacement__"] = (
                    JointDiagnostics
                )
                Candidate.Placed.LocalRouteDiagnostics = RecipeDiagnostics
                JointPlacementStateEvents.append({
                    "CandidateIndex": JointPlacementCandidateIndex,
                    "Status": (
                        "materialized-mandatory-access-conflict"
                        if MandatoryConflicts
                        else "materialized-exact-legal"
                    ),
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Score": JointDiagnostics.get("SelectedScore"),
                    "Transforms": JointDiagnostics.get("SelectedTransforms", {}),
                    "ExactPreScreen": ExactPreScreen,
                    "MandatoryAccessOwnershipFingerprint": (
                        CandidateTopologyDemand
                        .MandatoryAccessOwnershipFingerprint
                    ),
                    "MandatoryAccessConflictFingerprint": (
                        CandidateTopologyDemand
                        .MandatoryAccessConflictFingerprint
                    ),
                    "JointOrderKey": list(
                        CandidateTopologyDemand.JointOrderKey
                    ),
                })
            # Retain the complete bounded slot/orientation hypothesis set
            # before rejecting a conflicting primary state. Otherwise the
            # first exact mandatory cut suppresses every access-distinct
            # candidate that the joint search already found.
            QueueRetainedJointStates()
            RequiredDistinctOwnership = (
                JointPortfolioState
                .RequiredDistinctPinBankOwnershipFingerprint
            )
            if not PinBankRepairOwnershipIsDistinct(
                RequiredDistinctOwnership,
                CandidateTopologyDemand
                .MandatoryAccessOwnershipFingerprint,
            ):
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": (
                        "rejected-stagnant-pin-bank-ownership"
                    ),
                    "JointPlacementCandidateIndex": (
                        JointPlacementCandidateIndex
                    ),
                    "RequiredDistinctPinBankOwnershipFingerprint": (
                        RequiredDistinctOwnership
                    ),
                    "ObservedPinBankOwnershipFingerprint": (
                        CandidateTopologyDemand
                        .MandatoryAccessOwnershipFingerprint
                    ),
                    "InternalPinBankGeometryRepairSignals": sorted(
                        JointPortfolioState
                        .InternalPinBankGeometryRepairSignals
                    ),
                    "NextAction": (
                        "materialize-next-retained-exact-state"
                        if PendingJointPlacementStates
                        else "advance-bounded-placement-generator"
                    ),
                })
                return False
            CutBoundaryEscapeDiagnostics = RecipeDiagnostics.get(
                "__CutBoundaryEscapeFeasibility__",
            )
            if ShouldRejectCutBoundaryEscapePlacement(
                TopologyRequiresJointPortfolio=(
                    TopologyDemand.RequiresJointPortfolio
                ),
                Diagnostics=CutBoundaryEscapeDiagnostics,
            ):
                assert isinstance(
                    CutBoundaryEscapeDiagnostics,
                    dict,
                )
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": (
                        "rejected-exact-cut-boundary-escape-infeasible"
                    ),
                    "JointPlacementCandidateIndex": (
                        JointPlacementCandidateIndex
                    ),
                    "AssignmentCutFingerprint": (
                        EffectiveAssignmentCutFingerprint
                    ),
                    "AssignmentConstraintFingerprint": (
                        EffectiveAssignmentConstraintFingerprint
                    ),
                    "CutBoundaryEscapeFeasibility": dict(
                        CutBoundaryEscapeDiagnostics
                    ),
                    "NextAction": (
                        "materialize-next-retained-exact-state"
                        if PendingJointPlacementStates
                        else "advance-bounded-placement-generator"
                    ),
                })
                return False
            MandatoryAccessPortfolioTracking: dict[str, object] | None = None
            if (
                MandatoryConflicts
                and MandatoryProfile is not None
                and JointDiagnostics
                and EffectiveAssignmentCut is not None
                and PlacementAssignmentConstraintsAreActive(
                    EffectiveAssignmentConstraints
                )
            ):
                ExactScreenFingerprint = str(
                    JointDiagnostics.get("ExactScreenFingerprint", "")
                )
                PortfolioIdentity = MandatoryAccessPortfolioIdentity(
                    ExactScreenFingerprint=ExactScreenFingerprint,
                    SourceGenerator=SourceGenerator,
                    RoutingSpacing=CandidateSpacing,
                    RelocationVariant=RelocationVariant,
                    AssignmentCutFingerprint=(
                        EffectiveAssignmentCutFingerprint
                    ),
                    AssignmentConstraintFingerprint=(
                        EffectiveAssignmentConstraintFingerprint
                    ),
                    CoordinatedSignals=tuple(sorted(
                        EffectiveCoordinatedCandidateDiversificationSignals
                    )),
                )
                PortfolioRecipeIdentity = (
                    BuildMandatoryAccessPortfolioRecipeIdentity(
                        PortfolioIdentity
                    )
                )
                PortfolioEvidence = (
                    MandatoryAccessPortfolioEvidenceByIdentity.get(
                        PortfolioIdentity
                    )
                    or MandatoryAccessPortfolioEvidenceByRecipeIdentity.get(
                        PortfolioRecipeIdentity
                    )
                )
                if (
                    PortfolioEvidence is None
                    and JointPlacementCandidateIndex == 0
                    and ExactScreenFingerprint
                ):
                    ExpectedCandidateIndices = (
                        BuildMandatoryAccessPortfolioExpectedCandidateIndices(
                            JointDiagnostics,
                            JointPlacementCandidateIndex,
                            CandidatePacking
                            .RetainedJointPlacementCandidates,
                        )
                    )
                    if ExpectedCandidateIndices:
                        PortfolioEvidence = MandatoryAccessPortfolioEvidence(
                            ExpectedCandidateIndices=(
                                ExpectedCandidateIndices
                            ),
                            RejectionsByCandidateIndex={},
                        )
                        MandatoryAccessPortfolioEvidenceByIdentity[
                            PortfolioIdentity
                        ] = PortfolioEvidence
                        MandatoryAccessPortfolioEvidenceByRecipeIdentity[
                            PortfolioRecipeIdentity
                        ] = PortfolioEvidence
                if (
                    PortfolioEvidence is not None
                    and not PortfolioEvidence.Finalized
                    and JointPlacementCandidateIndex
                    in PortfolioEvidence.ExpectedCandidateIndices
                ):
                    PortfolioEvidence.RejectionsByCandidateIndex[
                        JointPlacementCandidateIndex
                    ] = MandatoryAccessPortfolioRejection(
                        CandidateIndex=JointPlacementCandidateIndex,
                        OwnershipFingerprint=(
                            CandidateTopologyDemand
                            .MandatoryAccessOwnershipFingerprint
                        ),
                        ConflictFingerprint=(
                            CandidateTopologyDemand
                            .MandatoryAccessConflictFingerprint
                        ),
                        PairwiseConflictEdges=(
                            BuildMandatoryAccessPairwiseEdges(
                                MandatoryProfile
                            )
                        ),
                    )
                    PortfolioEvaluation = (
                        EvaluateCompleteMandatoryAccessPortfolio(
                            PortfolioEvidence,
                            EffectiveAssignmentConstraints,
                        )
                    )
                    MandatoryAccessPortfolioTracking = {
                        "ExactScreenFingerprint": ExactScreenFingerprint,
                        "ExpectedCandidateIndices": list(
                            PortfolioEvidence.ExpectedCandidateIndices
                        ),
                        "ObservedCandidateIndices": sorted(
                            PortfolioEvidence.RejectionsByCandidateIndex
                        ),
                        "Verdict": PortfolioEvaluation.Verdict,
                        "MissingCandidateIndices": list(
                            PortfolioEvaluation.MissingCandidateIndices
                        ),
                        "UnexpectedCandidateIndices": list(
                            PortfolioEvaluation.UnexpectedCandidateIndices
                        ),
                    }
                    if PortfolioEvaluation.Verdict != "incomplete":
                        PortfolioEvidence.Finalized = True
                        CurrentCutFingerprint = (
                            CurrentPlacementAssignmentCut
                            .ConflictFingerprint
                            if CurrentPlacementAssignmentCut is not None
                            else ""
                        )
                        IdentityStillCurrent = (
                            MandatoryAccessPortfolioIdentityMatchesCurrent(
                                PortfolioIdentity,
                                CurrentPlacementAssignmentCut,
                                PlacementAssignmentConstraints,
                            )
                        )
                        PreviousConstraints = (
                            PlacementAssignmentConstraints
                        )
                        PromotedConstraints = PreviousConstraints
                        if (
                            PortfolioEvaluation.ShouldPromote
                            and IdentityStillCurrent
                        ):
                            PromotedConstraints = (
                                AddMandatoryAccessPortfolioPairwiseConstraints(
                                    PreviousConstraints,
                                    PortfolioEvaluation,
                                )
                            )
                            PlacementAssignmentConstraints = (
                                PromotedConstraints
                            )
                            PromotedSignals = frozenset(
                                Signal
                                for Edge in (
                                    PortfolioEvaluation
                                    .NewPairwiseConflictEdges
                                )
                                for Signal in Edge
                            )
                            PlacementRelocationSignals = frozenset((
                                *PlacementRelocationSignals,
                                *PromotedSignals,
                            ))
                            # The pair edges remain cumulative typed evidence,
                            # while their endpoints become the latest exact
                            # repair focus.  Reusing the previous cut's hard
                            # focus merely rotates unrelated clusters after
                            # all six access-distinct states proved this new
                            # mandatory resource collision.
                            PlacementRelocationPrioritySignals = (
                                PromotedSignals
                            )
                            PlacementRequiredRelocationSignals = (
                                PromotedSignals
                            )
                            NeedsFeedbackPlacementGeneration = True
                            NeedsCurrentStructuredCutRegeneration = False
                        elif (
                            IdentityStillCurrent
                            and PortfolioEvaluation.Verdict
                            == "already-represented"
                        ):
                            # A complete, access-distinct portfolio which
                            # repeats already-learned exact conflicts is a
                            # bounded-search fixed point.  Consume the next
                            # existing relocation variant before any broad
                            # unpacked recipe; the global round/deadline caps
                            # remain authoritative.
                            NeedsFeedbackPlacementGeneration = True
                            NeedsCurrentStructuredCutRegeneration = True
                        StrongRepairIdentity = (
                            BuildMandatoryAccessPortfolioRecipeIdentity(
                                PortfolioIdentity,
                                AssignmentConstraintFingerprint=(
                                    PromotedConstraints.Fingerprint
                                ),
                            )
                        )
                        if ShouldOpenStrongMandatoryAccessRepair(
                            PortfolioEvaluation,
                            IdentityStillCurrent=IdentityStillCurrent,
                            AlreadyConsumed=(
                                StrongRepairIdentity
                                in ConsumedStrongMandatoryAccessRepairIdentities
                            ),
                        ):
                            ConsumedStrongMandatoryAccessRepairIdentities.add(
                                StrongRepairIdentity
                            )
                            StrongRepairSignals = frozenset((
                                *(
                                    CurrentPlacementAssignmentCut
                                    .ConflictSignals
                                    if CurrentPlacementAssignmentCut
                                    is not None
                                    else ()
                                ),
                                *(
                                    Signal
                                    for Edge in (
                                        PortfolioEvaluation
                                        .PairwiseConflictEdges
                                    )
                                    for Signal in Edge
                                ),
                            ))
                            PlacementRelocationSignals = frozenset((
                                *PlacementRelocationSignals,
                                *StrongRepairSignals,
                            ))
                            PlacementRelocationPrioritySignals = (
                                StrongRepairSignals
                            )
                            PlacementRequiredRelocationSignals = (
                                StrongRepairSignals
                            )
                            PlacementClusterPinBankRepairSignals = (
                                StrongRepairSignals
                            )
                            PlacementCoordinatedCandidateDiversificationSignals = (
                                frozenset((
                                    *PlacementCoordinatedCandidateDiversificationSignals,
                                    *StrongRepairSignals,
                                ))
                            )
                            PostPinBankRepairEpochActive = True
                            InternalPinBankGeometryRepairActive = True
                            PendingStrongMandatoryAccessRepair = True
                            NeedsFeedbackPlacementGeneration = True
                            NeedsCurrentStructuredCutRegeneration = True
                        PlacementGenerationDecisions.append({
                            "Result": (
                                "complete-mandatory-access-portfolio-"
                                + (
                                    "promoted"
                                    if (
                                        PortfolioEvaluation.ShouldPromote
                                        and IdentityStillCurrent
                                    )
                                    else (
                                        PortfolioEvaluation.Verdict
                                        if IdentityStillCurrent
                                        else "stale-identity"
                                    )
                                )
                            ),
                            "SourceGenerator": SourceGenerator,
                            "RoutingSpacing": CandidateSpacing,
                            "RelocationVariant": RelocationVariant,
                            "ExactScreenFingerprint": (
                                ExactScreenFingerprint
                            ),
                            "AssignmentCutFingerprint": (
                                PortfolioIdentity
                                .AssignmentCutFingerprint
                            ),
                            "AssignmentCutPreserved": (
                                CurrentCutFingerprint
                                == PortfolioIdentity
                                .AssignmentCutFingerprint
                            ),
                            "AssignmentConstraintFingerprintBefore": (
                                PreviousConstraints.Fingerprint
                            ),
                            "AssignmentConstraintFingerprintAfter": (
                                PromotedConstraints.Fingerprint
                            ),
                            "IdentityStillCurrent": IdentityStillCurrent,
                            "ExpectedCandidateIndices": list(
                                PortfolioEvidence
                                .ExpectedCandidateIndices
                            ),
                            "ObservedCandidateIndices": sorted(
                                PortfolioEvidence
                                .RejectionsByCandidateIndex
                            ),
                            "OwnershipFingerprints": [
                                PortfolioEvidence
                                .RejectionsByCandidateIndex[Index]
                                .OwnershipFingerprint
                                for Index in (
                                    PortfolioEvidence
                                    .ExpectedCandidateIndices
                                )
                            ],
                            "ConflictFingerprints": [
                                PortfolioEvidence
                                .RejectionsByCandidateIndex[Index]
                                .ConflictFingerprint
                                for Index in (
                                    PortfolioEvidence
                                    .ExpectedCandidateIndices
                                )
                            ],
                            "PairwiseConflictEdges": [
                                list(Edge)
                                for Edge in (
                                    PortfolioEvaluation
                                    .PairwiseConflictEdges
                                )
                            ],
                            "NewPairwiseConflictEdges": [
                                list(Edge)
                                for Edge in (
                                    PortfolioEvaluation
                                    .NewPairwiseConflictEdges
                                )
                            ],
                            "HigherOrderSignalSetsPreserved": [
                                list(Signals)
                                for Signals in (
                                    PromotedConstraints
                                    .HigherOrderSignalSets
                                )
                            ],
                            "MissingCandidateIndices": list(
                                PortfolioEvaluation
                                .MissingCandidateIndices
                            ),
                            "UnexpectedCandidateIndices": list(
                                PortfolioEvaluation
                                .UnexpectedCandidateIndices
                            ),
                            "MissingOwnershipCandidateIndices": list(
                                PortfolioEvaluation
                                .MissingOwnershipCandidateIndices
                            ),
                            "DuplicateOwnershipFingerprints": list(
                                PortfolioEvaluation
                                .DuplicateOwnershipFingerprints
                            ),
                            "NextAction": (
                                "generate-exact-cut-relocation"
                                if (
                                    PortfolioEvaluation.ShouldPromote
                                    and IdentityStillCurrent
                                )
                                else (
                                    "generate-stronger-exact-cut-relocation"
                                    if (
                                        IdentityStillCurrent
                                        and PortfolioEvaluation.Verdict
                                        == "already-represented"
                                    )
                                    else "none"
                                )
                            ),
                        })
            if (
                MandatoryConflicts
                and JointPlacementCandidateIndex == 0
                and CandidatePacking.Enabled
                and not CandidatePacking.EnableJointClusterOrientation
            ):
                DynamicRequest = PlacementGenerationRequest(
                    SourceGenerator="row-beam-mandatory-joint",
                    RoutingSpacing=CandidateSpacing,
                    PackingPolicy=replace(
                        CandidatePacking,
                        EnableJointClusterOrientation=True,
                    ),
                )
                PendingJointPlacementStates.insert(
                    0,
                    PendingJointPlacementState(
                        Request=DynamicRequest,
                        CandidateIndex=0,
                        RelocationVariant=RelocationVariant,
                        RoutingSpacing=CandidateSpacing,
                        RelocationSignals=EffectiveRelocationSignals,
                        RelocationPrioritySignals=(
                            EffectiveRelocationPrioritySignals
                        ),
                        RequiredRelocationSignals=(
                            EffectiveRequiredRelocationSignals
                        ),
                        AssignmentCut=EffectiveAssignmentCut,
                        AssignmentConstraints=EffectiveAssignmentConstraints,
                        CoordinatedCandidateDiversificationSignals=(
                            EffectiveCoordinatedCandidateDiversificationSignals
                        ),
                        EnableClusterLocalRouteReuse=(
                            EnableCurrentClusterLocalRouteReuse
                        ),
                        IsPostPinBankRepairEpoch=(
                            PostPinBankRepairEpochActive
                        ),
                        EnableInternalPinBankGeometryRepair=(
                            InternalPinBankGeometryRepairActive
                        ),
                        InternalPinBankGeometryRepairSignals=(
                            EffectiveInternalPinBankGeometryRepairSignals
                        ),
                        RequiredDistinctPinBankOwnershipFingerprint=(
                            RequiredDistinctPinBankOwnershipFingerprint
                        ),
                        TopologyCutFrontier=(
                            EffectiveTopologyCutFrontier
                        ),
                    ),
                )
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": "mandatory-access-enabled-joint-portfolio",
                    "ConflictResourceCount": len(MandatoryConflicts),
                    "ConflictSignals": sorted(
                        CandidateTopologyDemand
                        .MandatoryAccessConflictSignals
                    ),
                    "MandatoryAccessOwnershipFingerprint": (
                        CandidateTopologyDemand
                        .MandatoryAccessOwnershipFingerprint
                    ),
                    "MandatoryAccessConflictFingerprint": (
                        CandidateTopologyDemand
                        .MandatoryAccessConflictFingerprint
                    ),
                    "JointOrderKey": list(
                        CandidateTopologyDemand.JointOrderKey
                    ),
                    "TopologyDemandProfile": (
                        CandidateTopologyDemand.ToDictionary()
                    ),
                    "MandatoryAccessProfile": (
                        MandatoryProfile.ToDictionary()
                        if MandatoryProfile is not None
                        else None
                    ),
                    "MandatoryAccessPortfolioTracking": (
                        MandatoryAccessPortfolioTracking
                    ),
                    "Trigger": "mandatory-access-conflict",
                })
                return False
            if MandatoryConflicts and (
                SourceGenerator != "row-beam-conflict-relocation"
                or CandidatePacking.EnableJointClusterOrientation
            ):
                ConflictSignals = frozenset(
                    Signal
                    for Owners in MandatoryConflicts.values()
                    for Signal in Owners
                )
                ConflictFingerprint = (
                    CandidateTopologyDemand
                    .MandatoryAccessConflictFingerprint
                )
                ConflictKey = (
                    len(MandatoryConflicts),
                    len(ConflictSignals),
                    int(JointDiagnostics.get("SelectedScore", 0)),
                    JointPlacementCandidateIndex,
                    ConflictFingerprint,
                )
                if (
                    BestMandatoryAccessConflictKey is None
                    or ConflictKey < BestMandatoryAccessConflictKey
                ):
                    BestMandatoryAccessConflictKey = ConflictKey
                    if not (
                        RequiresImmediateAssignmentCutRelocation(
                            EffectiveAssignmentCut
                        )
                        or PlacementAssignmentConstraintsAreActive(
                            EffectiveAssignmentConstraints
                        )
                    ):
                        # Feed the complete, exact resource-owner cut into one
                        # bounded relocation. A rejected sibling cannot replace
                        # an already-authoritative structured cut.
                        PlacementRelocationSignals = ConflictSignals
                        PlacementRelocationPrioritySignals = ConflictSignals
                        PlacementRequiredRelocationSignals = ConflictSignals
                CandidateSelectedForRelocation = (
                    ConflictKey == BestMandatoryAccessConflictKey
                    and not (
                        RequiresImmediateAssignmentCutRelocation(
                            EffectiveAssignmentCut
                        )
                        or PlacementAssignmentConstraintsAreActive(
                            EffectiveAssignmentConstraints
                        )
                    )
                )
                ProactiveRelocationRequested = (
                    ProactiveRelocationRequested
                    or CandidateSelectedForRelocation
                )
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": "rejected-mandatory-access-conflict",
                    "JointPlacementCandidateIndex": (
                        JointPlacementCandidateIndex
                    ),
                    "ConflictSignals": sorted(ConflictSignals),
                    "ConflictResourceCount": len(MandatoryConflicts),
                    "ConflictFingerprint": ConflictFingerprint,
                    "MandatoryAccessOwnershipFingerprint": (
                        CandidateTopologyDemand
                        .MandatoryAccessOwnershipFingerprint
                    ),
                    "JointOrderKey": list(
                        CandidateTopologyDemand.JointOrderKey
                    ),
                    "TopologyDemandProfile": (
                        CandidateTopologyDemand.ToDictionary()
                    ),
                    "MandatoryAccessProfile": (
                        MandatoryProfile.ToDictionary()
                        if MandatoryProfile is not None
                        else None
                    ),
                    "MandatoryAccessPortfolioTracking": (
                        MandatoryAccessPortfolioTracking
                    ),
                    "MandatoryAccessPortfolioIdentity": (
                        {
                            "ExactScreenFingerprint": (
                                PortfolioIdentity.ExactScreenFingerprint
                            ),
                            "SourceGenerator": (
                                PortfolioIdentity.SourceGenerator
                            ),
                            "RoutingSpacing": (
                                PortfolioIdentity.RoutingSpacing
                            ),
                            "RelocationVariant": (
                                PortfolioIdentity.RelocationVariant
                            ),
                            "AssignmentCutFingerprint": (
                                PortfolioIdentity
                                .AssignmentCutFingerprint
                            ),
                            "AssignmentConstraintFingerprint": (
                                PortfolioIdentity
                                .AssignmentConstraintFingerprint
                            ),
                            "CoordinatedSignals": list(
                                PortfolioIdentity.CoordinatedSignals
                            ),
                            "EvidenceFound": (
                                PortfolioEvidence is not None
                            ),
                        }
                        if (
                            MandatoryConflicts
                            and MandatoryProfile is not None
                            and JointDiagnostics
                            and EffectiveAssignmentCut is not None
                            and PlacementAssignmentConstraintsAreActive(
                                EffectiveAssignmentConstraints
                            )
                        )
                        else None
                    ),
                    "SelectedForRelocation": (
                        CandidateSelectedForRelocation
                    ),
                    "ElapsedSeconds": round(
                        monotonic() - PlacementStarted, 6
                    ),
                })
                return False
            Fingerprint = BuildPlacementFingerprint(
                Candidate,
                CandidateTopologyDemand
                .MandatoryAccessOwnershipFingerprint,
                IncludeLocalClaims=(
                    not CandidatePacking.EnableJointClusterOrientation
                ),
            )
            RetentionFingerprint = BuildPlacementRetentionFingerprint(
                Candidate,
                CandidateTopologyDemand
                .MandatoryAccessOwnershipFingerprint,
                IncludeLocalClaims=(
                    not CandidatePacking.EnableJointClusterOrientation
                ),
            )
            CheckPlacementGeneration({
                "Phase": "placement-fingerprint-complete",
            })
            if (
                Fingerprint in RejectedPlacementFingerprints
                or RetentionFingerprint
                in RejectedPlacementRetentionFingerprints
            ):
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": "rejected-placement-repeat",
                    "PlacementFingerprint": Fingerprint,
                    "PlacementRetentionFingerprint": (
                        RetentionFingerprint
                    ),
                    "ExactStatePlacementEvaluationCacheHit": (
                        CachedExactStateEvaluation is not None
                    ),
                    "ElapsedSeconds": round(
                        monotonic() - PlacementStarted,
                        6,
                    ),
                })
                return False
            ExistingRetention = RetainedPlacementTopologyFingerprints.get(
                RetentionFingerprint
            )
            if ExistingRetention is not None:
                ExistingFingerprint, ExistingSourceGenerator = (
                    ExistingRetention
                )
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": "duplicate-placement",
                    "PlacementFingerprint": Fingerprint,
                    "PlacementRetentionFingerprint": (
                        RetentionFingerprint
                    ),
                    "ExactStatePlacementEvaluationCacheHit": (
                        CachedExactStateEvaluation is not None
                    ),
                    "DuplicatePlacementFingerprint": (
                        ExistingFingerprint
                    ),
                    "DuplicateOf": ExistingSourceGenerator,
                    "ElapsedSeconds": round(
                        monotonic() - PlacementStarted,
                        6,
                    ),
                })
                if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                    print(
                        "[debug] authoritative: deduplicated placement "
                        f"source={SourceGenerator} spacing={CandidateSpacing} "
                        f"duplicate_of={ExistingSourceGenerator} "
                        f"elapsed={monotonic() - PlacementStarted:.3f}s",
                        flush=True,
                    )
                return False
            # Resource-graph construction repeats exact geometry validation and
            # expands every placed block.  Pay for it only after the cheap
            # mandatory-access and identity screens prove that this candidate
            # can enter the routing portfolio.
            if (
                MaterializeRoutingResources
                and not CandidatePacking.EnableJointClusterOrientation
            ):
                CandidateResources = BuildRoutingResources(
                    Candidate.Placed,
                    WorkCheck=CheckPlacementGeneration,
                )
                CheckPlacementGeneration({
                    "Phase": "routing-resource-construction-complete",
                })
            Feedback = None
            if (
                Policy.Placement.EnableRoutingFeedback
                and not bool(os.environ.get("RCS_SKIP_PLACEMENT_FEEDBACK"))
                and not JointDiagnostics
            ):
                Feedback = MeasurePlacementRoutingFeedback(
                    Candidate,
                    CandidateSpacing,
                    Policy,
                    Technology,
                    CheckPlacementGeneration,
                )
                CheckPlacementGeneration({
                    "Phase": "placement-feedback-complete",
                })
            # Publish only after construction, exact legality, resource
            # construction, and feedback all finish inside the same slice.
            UniquePlacements[Fingerprint] = (
                SourceGenerator,
                CandidateSpacing,
                Candidate,
            )
            PlacementRetentionFingerprintByFingerprint[Fingerprint] = (
                RetentionFingerprint
            )
            RetainedPlacementTopologyFingerprints[RetentionFingerprint] = (
                Fingerprint,
                SourceGenerator,
            )
            TopologyDemandByFingerprint[Fingerprint] = (
                CandidateTopologyDemand
            )
            if JointDiagnostics:
                JointPlacementStateByPlacementFingerprint[Fingerprint] = (
                    JointPortfolioState
                )
            if CandidateResources is not None:
                RoutingResourcesByFingerprint[Fingerprint] = CandidateResources
            if Feedback is not None:
                FeedbackByFingerprint[Fingerprint] = Feedback
            PlacementGenerationDecisions.append({
                "SourceGenerator": SourceGenerator,
                "RoutingSpacing": CandidateSpacing,
                "Result": "unique-placement",
                "JointPlacementCandidateIndex": JointPlacementCandidateIndex,
                "PlacementFingerprint": Fingerprint,
                "PlacementRetentionFingerprint": RetentionFingerprint,
                "ExactStatePlacementEvaluationCacheHit": (
                    CachedExactStateEvaluation is not None
                ),
                "MandatoryAccessOwnershipFingerprint": (
                    CandidateTopologyDemand
                    .MandatoryAccessOwnershipFingerprint
                ),
                "MandatoryAccessConflictFingerprint": (
                    CandidateTopologyDemand
                    .MandatoryAccessConflictFingerprint
                ),
                "TopologyDemandProfile": (
                    CandidateTopologyDemand.ToDictionary()
                ),
                "JointOrderKey": list(
                    CandidateTopologyDemand.JointOrderKey
                ),
                "RelocationSignals": sorted(EffectiveRelocationSignals),
                "PackedGateArea": PackedGateArea,
                "BaselinePackedGateArea": BaselinePackedGateArea,
                "MaximumPackedGateArea": MaximumPackedGateArea,
                "PackedClusters": [
                    {
                        "ClusterId": Cluster.ClusterId,
                        "Members": list(Cluster.MemberNands),
                        "StackId": Cluster.StackId,
                        "StackLevel": Cluster.StackLevel,
                        "BaseY": Cluster.BaseY,
                        "OrientationRotation": Cluster.OrientationRotation,
                        "OrientationMirrorX": Cluster.OrientationMirrorX,
                    }
                    for Cluster in Candidate.PackedClusters
                ],
                "JointClusterPlacement": RecipeDiagnostics.get(
                    "__JointClusterPlacement__", {}
                ),
                "PlacementGenerationBudgetSeconds": round(
                    PlacementGenerationBudgetSeconds,
                    6,
                ),
                "RoutingReserveSeconds": round(
                    RoutingReserveSeconds,
                    6,
                ),
                "ElapsedSeconds": round(
                    monotonic() - PlacementStarted,
                    6,
                ),
            })
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: generated placement "
                    f"source={SourceGenerator} spacing={CandidateSpacing} "
                    f"variant={RelocationVariant} "
                    f"fingerprint={Fingerprint[:12]} "
                    f"elapsed={monotonic() - PlacementStarted:.3f}s",
                    flush=True,
                )
                print(
                    "[debug] authoritative: terminal placements "
                    f"values={[(Gate.Name, str(getattr(Gate.Kind, 'value', Gate.Kind)), Gate.X, Gate.Z, Gate.Rotation, Gate.OutputPin, Gate.InputPins) for Gate in Candidate.Placed.PlacedGates if str(getattr(Gate.Kind, 'value', Gate.Kind)) in {'INPUT', 'OUTPUT'}]}",
                    flush=True,
                )
            return True
        except Exception as Error:
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                traceback.print_exc()
            if (
                isinstance(Error, ValueError)
                and JointPlacementCandidateIndex == 0
                and CandidatePacking.Enabled
                and CandidatePacking.EnableJointClusterOrientation
                and str(Error).startswith(
                    "Exact joint placement candidate rejected:"
                )
            ):
                # The exact screen runs after the bounded joint search has
                # retained its portfolio.  Candidate zero can be rejected by
                # the final placed-gate predicate even when later retained
                # states are legal.  Keep those state identities available to
                # the caller instead of abandoning the entire portfolio for a
                # broad unpacked generator.
                ExistingIndexes = {
                    State.CandidateIndex
                    for State in PendingJointPlacementStates
                    if (
                        State.Request == Request
                        and State.RelocationVariant == RelocationVariant
                        and State.RoutingSpacing == CandidateSpacing
                        and State.RelocationSignals
                        == EffectiveRelocationSignals
                        and State.RelocationPrioritySignals
                        == EffectiveRelocationPrioritySignals
                        and State.RequiredRelocationSignals
                        == EffectiveRequiredRelocationSignals
                        and State.AssignmentCut == EffectiveAssignmentCut
                        and State.AssignmentConstraints
                        == EffectiveAssignmentConstraints
                        and (
                            State.CoordinatedCandidateDiversificationSignals
                            == EffectiveCoordinatedCandidateDiversificationSignals
                        )
                    )
                }
                QueuedCandidateIndexes = [
                    CandidateIndex
                    for CandidateIndex in range(
                        1,
                        CandidatePacking.RetainedJointPlacementCandidates
                        * (
                            2
                            if TopologyDemand.RequiresJointPortfolio
                            else 1
                        ),
                    )
                    if CandidateIndex not in ExistingIndexes
                ]
                CandidateStates = [
                    PendingJointPlacementState(
                        Request=Request,
                        CandidateIndex=CandidateIndex,
                        RelocationVariant=RelocationVariant,
                        RoutingSpacing=CandidateSpacing,
                        RelocationSignals=EffectiveRelocationSignals,
                        RelocationPrioritySignals=(
                            EffectiveRelocationPrioritySignals
                        ),
                        RequiredRelocationSignals=(
                            EffectiveRequiredRelocationSignals
                        ),
                        AssignmentCut=EffectiveAssignmentCut,
                        AssignmentConstraints=EffectiveAssignmentConstraints,
                        CoordinatedCandidateDiversificationSignals=(
                            EffectiveCoordinatedCandidateDiversificationSignals
                        ),
                        EnableClusterLocalRouteReuse=(
                            EnableCurrentClusterLocalRouteReuse
                        ),
                        IsPostPinBankRepairEpoch=(
                            PostPinBankRepairEpochActive
                        ),
                        EnableInternalPinBankGeometryRepair=(
                            InternalPinBankGeometryRepairActive
                        ),
                        InternalPinBankGeometryRepairSignals=(
                            EffectiveInternalPinBankGeometryRepairSignals
                        ),
                        RequiredDistinctPinBankOwnershipFingerprint=(
                            RequiredDistinctPinBankOwnershipFingerprint
                        ),
                        TopologyCutFrontier=(
                            EffectiveTopologyCutFrontier
                        ),
                    )
                    for CandidateIndex in QueuedCandidateIndexes
                ]
                ExistingStateKeys = {
                    *MaterializedJointPlacementStateKeys,
                    *(
                        BuildPendingJointPlacementStateKey(State)
                        for State in PendingJointPlacementStates
                    ),
                }
                CandidateStates = [
                    State
                    for State in CandidateStates
                    if BuildPendingJointPlacementStateKey(State)
                    not in ExistingStateKeys
                ]
                PendingJointPlacementStates.extend(CandidateStates)
                QueuedCandidateIndexes = [
                    State.CandidateIndex for State in CandidateStates
                ]
                JointPlacementStateEvents.extend({
                    "CandidateIndex": CandidateIndex,
                    "Status": "queued-after-exact-overlap-rejection",
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "RejectedCandidateIndex": (
                        JointPlacementCandidateIndex
                    ),
                } for CandidateIndex in QueuedCandidateIndexes)
                PlacementGenerationDecisions.append({
                    "Result": (
                        "retained-joint-states-after-exact-overlap-rejection"
                    ),
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "RejectedCandidateIndex": JointPlacementCandidateIndex,
                    "QueuedCandidateIndices": QueuedCandidateIndexes,
                })
            # A candidate that reached a stable fingerprint but failed a later
            # transactional stage was never published.  Remember only its
            # identity so another recipe cannot repeat identical bounded work
            # and starve the next distinct retained placement.
            if Fingerprint is not None:
                RejectedPlacementFingerprints.add(Fingerprint)
            if RetentionFingerprint is not None:
                RejectedPlacementRetentionFingerprints.add(
                    RetentionFingerprint
                )
            if isinstance(Error, RoutingStageError):
                Failure = Error.Failure
            elif isinstance(Error, ValueError):
                Failure = RoutingFailure(
                    Reason=RoutingFailureReason.PlacementOverlap,
                    Stage="PlacementGeneration",
                    Detail=str(Error),
                    RepairActions=("AdvancePlacementGenerator",),
                    Diagnostics={"ErrorType": type(Error).__name__},
                )
            else:
                Failure = RoutingFailure(
                    Reason=RoutingFailureReason.DetailedSearchExhausted,
                    Stage="PlacementGeneration",
                    Detail=(
                        "unexpected bounded placement-generation failure: "
                        f"{type(Error).__name__}: {Error}"
                    ),
                    RepairActions=("AdvancePlacementGenerator",),
                    Diagnostics={"ErrorType": type(Error).__name__},
                )
            LastStructuredPlacementFailure = Failure
            FailureSnapshot = BuildPlacementFailureHistorySnapshot(Failure)
            PlacementGenerationFailures.append({
                "SourceGenerator": SourceGenerator,
                "RoutingSpacing": CandidateSpacing,
                "JointPlacementCandidateIndex": JointPlacementCandidateIndex,
                "PackedNandPlacement": bool(CandidatePacking.Enabled),
                "Failure": str(Error),
                "PlacementGenerationBudgetSeconds": round(
                    PlacementGenerationBudgetSeconds,
                    6,
                ),
                "ElapsedSeconds": round(
                    monotonic() - PlacementStarted,
                    6,
                ),
                "Diagnostics": FailureSnapshot,
            })
            if CandidatePacking.Enabled and CandidatePacking.EnableJointClusterOrientation:
                JointPlacementStateEvents.append({
                    "CandidateIndex": JointPlacementCandidateIndex,
                    "Status": "materialization-rejected",
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Reason": str(Error),
                    "Failure": FailureSnapshot,
                })
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: skipped placement candidate "
                    f"spacing={CandidateSpacing} packing={CandidatePacking.Enabled} "
                    f"reason={Error}",
                    f"elapsed={monotonic() - PlacementStarted:.3f}s",
                    flush=True,
                )
            if Failure.Reason == RoutingFailureReason.RuntimeBudgetExceeded:
                raise RoutingStageError(
                    _PlacementFailureWithHistory(Failure)
                ) from Error
            return False

    def _TakeNextDeferredRequest(
        PreferRelocation: bool = False,
        PreferDirectOnly: bool = False,
        RequireExactCutBeforeBroad: bool = False,
    ) -> PlacementGenerationRequest | None:
        nonlocal DeferredRequestIndex
        nonlocal NeedsCurrentStructuredCutRegeneration
        nonlocal PendingTopologyCutEpoch
        nonlocal RotatedMacroAncestorTargetedEpochPending
        nonlocal PendingStrongMandatoryAccessRepair
        nonlocal StrongMandatoryAccessRepairMaterializationPending

        def ConsumeDeferredRequest(
            RequestIndex: int,
        ) -> PlacementGenerationRequest:
            nonlocal DeferredRequestIndex
            ConsumedDeferredRequestIndexes.add(RequestIndex)
            while (
                DeferredRequestIndex < len(GenerationPlan.DeferredRequests)
                and DeferredRequestIndex in ConsumedDeferredRequestIndexes
            ):
                DeferredRequestIndex += 1
            return GenerationPlan.DeferredRequests[RequestIndex]

        MaximumFeedbackRounds = (
            Policy.NegotiatedRouting.MaximumPlacementFeedbackRounds
            if Policy.NegotiatedRouting.Enabled
            else max(1, Policy.NandPacking.PlacementFeedbackIterations + 1)
        )
        if TopologyDemand.RequiresJointPortfolio:
            # CLA-style reconvergent access repairs use the bounded single
            # topology epoch.  Do not reduce the established feedback budget
            # of ordinary ripple/compact designs to mask this harder case.
            MaximumFeedbackRounds = min(MaximumFeedbackRounds, 1)
        elif NandGateCount < 32:
            # A tiny graph has too little independent geometry for a second
            # relocation to be useful; preserve the direct-only fallback.
            MaximumFeedbackRounds = min(MaximumFeedbackRounds, 1)
        if PendingTopologyCutEpoch is not None:
            CurrentEpoch = (
                BuildTopologyCutEpochIdentity(
                    CurrentPlacementAssignmentCut,
                    PlacementAssignmentConstraints,
                )
                if CurrentPlacementAssignmentCut is not None
                else None
            )
            if CurrentEpoch == PendingTopologyCutEpoch:
                OpenedTopologyCutEpochs.add(PendingTopologyCutEpoch)
                PlacementGenerationDecisions.append({
                    "Result": "topology-cut-epoch-materializing",
                    "AssignmentCutFingerprint": (
                        PendingTopologyCutEpoch.AssignmentCutFingerprint
                    ),
                    "AssignmentConstraintFingerprint": (
                        PendingTopologyCutEpoch
                        .AssignmentConstraintFingerprint
                    ),
                    "MandatoryAccessOwnershipFingerprint": (
                        PendingTopologyCutEpoch
                        .MandatoryAccessOwnershipFingerprint
                    ),
                    "RemainingRoutingSeconds": round(
                        max(0.0, Deadline.RemainingSeconds()),
                        6,
                    ),
                    "BroadGenerationDeferred": True,
                    "TargetedPinBankEpoch": (
                        PostPinBankRepairEpochActive
                        and InternalPinBankGeometryRepairActive
                    ),
                })
                PendingTopologyCutEpoch = None
                TargetedPinBankEpoch = (
                    PostPinBankRepairEpochActive
                    and InternalPinBankGeometryRepairActive
                ) or RotatedMacroAncestorTargetedEpochPending
                RotatedMacroAncestorTargetedEpochPending = False
                EpochPackingPolicy = (
                    BuildTargetedPinBankPackingPolicy(
                        Policy.NandPacking
                    )
                    if TargetedPinBankEpoch
                    else replace(
                        Policy.NandPacking,
                        GraphBeamEnabled=False,
                        EnableJointClusterOrientation=True,
                    )
                )
                return PlacementGenerationRequest(
                    SourceGenerator="row-beam-conflict-relocation",
                    RoutingSpacing=ConfiguredRoutingSpacing,
                    PackingPolicy=replace(
                        EpochPackingPolicy,
                        TerminalShellLateralSearch=(
                            max(
                                Policy.NandPacking.TerminalShellLateralSearch,
                                4,
                            )
                            if (
                                not TargetedPinBankEpoch
                                and ShouldWidenTopologyCutTerminalShell(
                                TopologyRequiresJointPortfolio=True,
                                AssignmentCut=CurrentPlacementAssignmentCut,
                                ExternalSignals=(*Module.Inputs, *Module.Outputs),
                                )
                            )
                            else Policy.NandPacking.TerminalShellLateralSearch
                        ),
                    ),
                    UseCurrentAssignmentCutRelocationSignals=True,
                )
            PlacementGenerationDecisions.append({
                "Result": "topology-cut-epoch-superseded",
                "RequestedAssignmentCutFingerprint": (
                    PendingTopologyCutEpoch.AssignmentCutFingerprint
                ),
                "CurrentAssignmentCutFingerprint": (
                    CurrentEpoch.AssignmentCutFingerprint
                    if CurrentEpoch is not None
                    else ""
                ),
            })
            PendingTopologyCutEpoch = None
        if (
            PreferRelocation
            and PendingStrongMandatoryAccessRepair
            and CurrentPlacementAssignmentCut is not None
            and PlacementRelocationSignals
        ):
            PendingStrongMandatoryAccessRepair = False
            StrongMandatoryAccessRepairMaterializationPending = True
            NeedsCurrentStructuredCutRegeneration = False
            PlacementGenerationDecisions.append({
                "Result": "strong-mandatory-access-pin-bank-epoch",
                "Reason": (
                    "complete access-distinct compact portfolio exhausted "
                    "rigid slot/orientation repair"
                ),
                "RelocationVariant": 12,
                "ConflictSignals": sorted(
                    PlacementClusterPinBankRepairSignals
                ),
                "CurrentAssignmentCut": (
                    CurrentPlacementAssignmentCut.ToDictionary()
                ),
                "ActivePlacementConstraints": (
                    PlacementAssignmentConstraints.ToDictionary()
                ),
                "BroadGenerationDeferred": True,
            })
            return PlacementGenerationRequest(
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=ConfiguredRoutingSpacing,
                PackingPolicy=replace(
                    Policy.NandPacking,
                    GraphBeamEnabled=False,
                    EnableJointClusterOrientation=True,
                    RetainedJointPlacementCandidates=1,
                ),
                UseCurrentAssignmentCutRelocationSignals=True,
            )
        if PlacementGenerationAttempts >= GenerationPlan.MaximumAttempts:
            return None
        if (
            TopologyDemand.RequiresJointPortfolio
            and
            PreferRelocation
            and NeedsCurrentStructuredCutRegeneration
            and PlacementRelocationSignals
            and TotalRelocationGenerationCount < MaximumFeedbackRounds
        ):
            NeedsCurrentStructuredCutRegeneration = False
            PlacementGenerationDecisions.append({
                "Result": "regenerate-current-structured-cut-portfolio",
                "Reason": (
                    "the retained access-distinct portfolio exhausted after "
                    "a non-promoting candidate-starvation report"
                ),
                "CurrentAssignmentCut": (
                    CurrentPlacementAssignmentCut.ToDictionary()
                    if CurrentPlacementAssignmentCut is not None
                    else None
                ),
                "ActivePlacementConstraints": (
                    PlacementAssignmentConstraints.ToDictionary()
                ),
            })
            return PlacementGenerationRequest(
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=ConfiguredRoutingSpacing,
                PackingPolicy=replace(
                    Policy.NandPacking,
                    GraphBeamEnabled=False,
                    EnableJointClusterOrientation=(
                        TopologyDemand.RequiresJointPortfolio
                    ),
                ),
            )
        if PreferDirectOnly:
            for RequestIndex, Request in enumerate(
                GenerationPlan.DeferredRequests
            ):
                if (
                    RequestIndex not in ConsumedDeferredRequestIndexes
                    and Request.SourceGenerator == "row-beam-direct-only"
                ):
                    PlacementGenerationDecisions.append({
                        "Result": "prioritize-direct-only-after-exact-cut",
                        "Reason": (
                            "the primary row placement reached an exact "
                            "higher-order assignment cut without boundary, "
                            "guide, or pin-access pressure"
                        ),
                    })
                    return ConsumeDeferredRequest(RequestIndex)
        if (
            TopologyDemand.RequiresJointPortfolio
            and
            ShouldPrioritizeCurrentExactCutBeforeBroad(
            Required=RequireExactCutBeforeBroad,
            PreferRelocation=PreferRelocation,
            HasCurrentAssignmentCut=(
                CurrentPlacementAssignmentCut is not None
            ),
            HasRelocationSignals=bool(PlacementRelocationSignals),
            TotalRelocationGenerationCount=(
                TotalRelocationGenerationCount
            ),
            MaximumFeedbackRounds=MaximumFeedbackRounds,
            )
            and (
                not TopologyDemand.RequiresJointPortfolio
                or HasTopologyCutEpochRoutingReserve(
                    RemainingSeconds=Deadline.RemainingSeconds(),
                    Policy=Policy,
                    RequiresDenseBoundaryRouting=(
                        TopologyPressure.ScaleGeometryPressure
                    ),
                    HasBoundedExactCutEvidence=(
                        AssignmentCutHasBoundedExactCore(
                            CurrentPlacementAssignmentCut
                        )
                    ),
                )
            )
        ):
            # A newly authoritative cut/constraint epoch must materialize its
            # bounded packed repair before any broad spacing or unpacked
            # recipe. The existing feedback-round and shared-deadline bounds
            # remain the termination controls.
            PlacementGenerationDecisions.append({
                "Result": (
                    "prioritize-current-exact-cut-before-broad"
                ),
                "CurrentAssignmentCut": (
                    CurrentPlacementAssignmentCut.ToDictionary()
                ),
                "ActivePlacementConstraints": (
                    PlacementAssignmentConstraints.ToDictionary()
                ),
                "TotalRelocationGenerationCount": (
                    TotalRelocationGenerationCount
                ),
                "MaximumFeedbackRounds": MaximumFeedbackRounds,
            })
            return PlacementGenerationRequest(
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=ConfiguredRoutingSpacing,
                PackingPolicy=replace(
                    Policy.NandPacking,
                    GraphBeamEnabled=False,
                    EnableJointClusterOrientation=(
                        TopologyDemand.RequiresJointPortfolio
                    ),
                ),
            )
        if (
            ShouldPrioritizePlacementConflictRelocation(
            PreferRelocation=PreferRelocation,
            RelocationSignals=PlacementRelocationSignals,
            TotalRelocationGenerationCount=(
                TotalRelocationGenerationCount
            ),
            MaximumFeedbackRounds=MaximumFeedbackRounds,
            RelocationPrioritySignals=(
                PlacementRelocationPrioritySignals
            ),
            LastRelocationPrioritySignalsUsed=(
                LastRelocationPrioritySignalsUsed
            ),
            RequiredRelocationSignals=(
                PlacementRequiredRelocationSignals
            ),
            LastRequiredRelocationSignalsUsed=(
                LastRequiredRelocationSignalsUsed
            ),
            CurrentAssignmentCutFingerprint=(
                CurrentPlacementAssignmentCut.ConflictFingerprint
                if CurrentPlacementAssignmentCut is not None
                else ""
            ),
            LastAssignmentCutFingerprintUsed=(
                LastAssignmentCutFingerprintUsed
            ),
            CurrentAssignmentConstraintFingerprint=(
                PlacementAssignmentConstraints.Fingerprint
            ),
            LastAssignmentConstraintFingerprintUsed=(
                LastAssignmentConstraintFingerprintUsed
            ),
            )
            and (
                not TopologyDemand.RequiresJointPortfolio
                or HasTopologyCutEpochRoutingReserve(
                    RemainingSeconds=Deadline.RemainingSeconds(),
                    Policy=Policy,
                    RequiresDenseBoundaryRouting=(
                        TopologyPressure.ScaleGeometryPressure
                    ),
                    HasBoundedExactCutEvidence=(
                        AssignmentCutHasBoundedExactCore(
                            CurrentPlacementAssignmentCut
                        )
                    ),
                )
            )
        ):
            # Route the bounded packed-feedback portfolio first when each
            # routed state proves a different typed exact cut.  This lets the
            # next precise access conflict alter geometry without postponing
            # that repair behind unrelated unpacked recipes.
            return PlacementGenerationRequest(
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=ConfiguredRoutingSpacing,
                PackingPolicy=replace(
                    Policy.NandPacking,
                    GraphBeamEnabled=False,
                    EnableJointClusterOrientation=(
                        TopologyDemand.RequiresJointPortfolio
                    ),
                ),
            )
        PrioritizeTopologyCutEpochRelocation = (
            ShouldPrioritizeTopologyCutEpochRelocation(
            TopologyRequiresJointPortfolio=(
                TopologyDemand.RequiresJointPortfolio
            ),
            HasRelocationSignals=bool(PlacementRelocationSignals),
            TotalRelocationGenerationCount=(
                TotalRelocationGenerationCount
            ),
            MaximumFeedbackRounds=MaximumFeedbackRounds,
            CurrentAssignmentCutFingerprint=(
                CurrentPlacementAssignmentCut.ConflictFingerprint
                if CurrentPlacementAssignmentCut is not None
                else ""
            ),
            LastAssignmentCutFingerprintUsed=(
                LastAssignmentCutFingerprintUsed
            ),
            )
        )
        TopologyEpochRoutingReserveAvailable = (
            HasTopologyCutEpochRoutingReserve(
                RemainingSeconds=Deadline.RemainingSeconds(),
                Policy=Policy,
                RequiresDenseBoundaryRouting=(
                    TopologyPressure.ScaleGeometryPressure
                ),
                HasBoundedExactCutEvidence=(
                    AssignmentCutHasBoundedExactCore(
                        CurrentPlacementAssignmentCut
                    )
                ),
            )
        )
        if (
            PrioritizeTopologyCutEpochRelocation
            and TopologyEpochRoutingReserveAvailable
        ):
            PlacementGenerationDecisions.append({
                "Result": "prioritize-topology-cut-epoch-relocation",
                "CurrentAssignmentCut": (
                    CurrentPlacementAssignmentCut.ToDictionary()
                    if CurrentPlacementAssignmentCut is not None
                    else None
                ),
                "TotalRelocationGenerationCount": (
                    TotalRelocationGenerationCount
                ),
            })
            return PlacementGenerationRequest(
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=ConfiguredRoutingSpacing,
                PackingPolicy=replace(
                    Policy.NandPacking,
                    GraphBeamEnabled=False,
                    EnableJointClusterOrientation=True,
                ),
                UseCurrentAssignmentCutRelocationSignals=True,
            )
        if PrioritizeTopologyCutEpochRelocation:
            PlacementGenerationDecisions.append({
                "Result": "topology-cut-epoch-relocation-deferred-routing-reserve",
                "RemainingRoutingSeconds": round(
                    max(0.0, Deadline.RemainingSeconds()),
                    6,
                ),
                "RequiredRoutingReserveSeconds": round(
                    TopologyCutEpochAdmissionReserveSeconds(
                        Policy,
                        TopologyPressure.ScaleGeometryPressure,
                        HasBoundedExactCutEvidence=(
                            AssignmentCutHasBoundedExactCore(
                                CurrentPlacementAssignmentCut
                            )
                        ),
                    ),
                    6,
                ),
                "Reason": (
                    "preserve the current exact access-distinct state instead "
                    "of materializing an unfunded replacement relocation"
                ),
            })
        if DeferredRequestIndex < len(GenerationPlan.DeferredRequests):
            Request = GenerationPlan.DeferredRequests[DeferredRequestIndex]
            if Request.SourceGenerator == "row-beam-conflict-relocation":
                Request = ConsumeDeferredRequest(DeferredRequestIndex)
                if (
                    PlacementRelocationSignals
                    and TotalRelocationGenerationCount == 0
                ):
                    return Request
        if DeferredRequestIndex < len(GenerationPlan.DeferredRequests):
            Request = GenerationPlan.DeferredRequests[DeferredRequestIndex]
            if (
                Request.SourceGenerator == "row-beam-direct-only"
                and TotalRelocationGenerationCount >= 2
            ):
                return ConsumeDeferredRequest(DeferredRequestIndex)
        if DeferredRequestIndex < len(GenerationPlan.DeferredRequests):
            return ConsumeDeferredRequest(DeferredRequestIndex)
        if (
            PlacementRelocationSignals
            and TotalRelocationGenerationCount
            < MaximumFeedbackRounds
            and (
                PlacementRelocationSignals != LastRelocationSignalsUsed
                or PlacementRelocationPrioritySignals
                != LastRelocationPrioritySignalsUsed
                or PlacementRequiredRelocationSignals
                != LastRequiredRelocationSignalsUsed
                or (
                    CurrentPlacementAssignmentCut is not None
                    and CurrentPlacementAssignmentCut.ConflictFingerprint
                    != LastAssignmentCutFingerprintUsed
                )
                or PlacementAssignmentConstraints.Fingerprint
                != LastAssignmentConstraintFingerprintUsed
                or RelocationGenerationCount
                < Policy.NegotiatedRouting.MaximumPlacementFeedbackRounds
            )
        ):
            return PlacementGenerationRequest(
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=ConfiguredRoutingSpacing,
                PackingPolicy=replace(
                    Policy.NandPacking,
                    GraphBeamEnabled=False,
                    EnableJointClusterOrientation=(
                        TopologyDemand.RequiresJointPortfolio
                    ),
                ),
            )
        return None

    if ProgressCallback is not None:
        ProgressCallback(
            PcbProgress(
                Completed=0,
                Total=1,
                Workers=0,
                Valid=0,
                BestBlocks=None,
                BestWidth=None,
                BestDepth=None,
                BestFootprint=None,
                Failed=0,
                Stage=f"spacing {RoutingSpacing} | placing clustered NAND graph",
                    )
                )
    for Request in GenerationPlan.PrimaryRequests:
        if PlacementGenerationAttempts >= GenerationPlan.MaximumAttempts:
            break
        _TryPlacement(Request)
        while PendingJointPlacementStates and not UniquePlacements:
            JointState = PendingJointPlacementStates.pop(0)
            _TryPlacement(
                JointState.Request,
                JointPlacementCandidateIndex=JointState.CandidateIndex,
                FixedRelocationVariant=JointState.RelocationVariant,
                FixedCandidateSpacing=JointState.RoutingSpacing,
                FixedRelocationSignals=JointState.RelocationSignals,
                FixedRelocationPrioritySignals=(
                    JointState.RelocationPrioritySignals
                ),
                FixedRequiredRelocationSignals=(
                    JointState.RequiredRelocationSignals
                ),
                FixedAssignmentCut=JointState.AssignmentCut,
                FixedAssignmentConstraints=JointState.AssignmentConstraints,
                FixedCoordinatedCandidateDiversificationSignals=(
                    JointState.CoordinatedCandidateDiversificationSignals
                ),
                FixedTopologyCutFrontier=(
                    JointState.TopologyCutFrontier
                ),
                MaterializeRoutingResources=False,
            )

    if ProactiveRelocationRequested and not UniquePlacements:
        for RequestIndex, Request in enumerate(GenerationPlan.DeferredRequests):
            if Request.SourceGenerator != "row-beam-conflict-relocation":
                continue
            _TryPlacement(Request)
            ConsumedDeferredRequestIndexes.add(RequestIndex)
            while (
                DeferredRequestIndex < len(GenerationPlan.DeferredRequests)
                and DeferredRequestIndex in ConsumedDeferredRequestIndexes
            ):
                DeferredRequestIndex += 1
            break

    while not UniquePlacements:
        if PendingJointPlacementStates:
            JointState = PendingJointPlacementStates.pop(0)
            _TryPlacement(
                JointState.Request,
                JointPlacementCandidateIndex=JointState.CandidateIndex,
                FixedRelocationVariant=JointState.RelocationVariant,
                FixedCandidateSpacing=JointState.RoutingSpacing,
                FixedRelocationSignals=JointState.RelocationSignals,
                FixedRelocationPrioritySignals=(
                    JointState.RelocationPrioritySignals
                ),
                FixedRequiredRelocationSignals=(
                    JointState.RequiredRelocationSignals
                ),
                FixedAssignmentCut=JointState.AssignmentCut,
                FixedAssignmentConstraints=JointState.AssignmentConstraints,
                FixedCoordinatedCandidateDiversificationSignals=(
                    JointState.CoordinatedCandidateDiversificationSignals
                ),
                FixedTopologyCutFrontier=(
                    JointState.TopologyCutFrontier
                ),
                MaterializeRoutingResources=False,
            )
            continue
        Request = _TakeNextDeferredRequest()
        if Request is None:
            break
        _TryPlacement(Request)

    if not UniquePlacements:
        BaseFailure = LastStructuredPlacementFailure or RoutingFailure(
            Reason=RoutingFailureReason.PlacementOverlap,
            Stage="Placement",
            Detail="no exact-legal placement candidate was generated",
        )
        FailureDiagnostics = dict(BaseFailure.Diagnostics or {})
        FailureDiagnostics.update({
            "PlacementGenerationFailures": PlacementGenerationFailures,
            "PlacementGenerationDecisions": PlacementGenerationDecisions,
            "PlacementAttempts": PlacementAttemptFailures,
            "Deadline": Deadline.ToDictionary(),
        })
        raise RoutingStageError(
            RoutingFailure(
                Reason=BaseFailure.Reason,
                Stage=BaseFailure.Stage,
                AffectedNets=BaseFailure.AffectedNets,
                Resources=BaseFailure.Resources,
                Locations=BaseFailure.Locations,
                RepairActions=BaseFailure.RepairActions,
                Detail=BaseFailure.Detail,
                Diagnostics=FailureDiagnostics,
            )
        )

    def _BuildCandidateRecords() -> list[PcbPlacementCandidate]:
        def JointExactScore(Candidate: PcbPlacement) -> tuple[int, ...]:
            """Order retained joint states by materialized access pressure."""
            Diagnostics = dict(
                Candidate.Placed.LocalRouteDiagnostics or {}
            )
            Joint = dict(Diagnostics.get("__JointClusterPlacement__", {}))
            Exact = dict(Joint.get("ExactPreScreen", {}))
            if not Joint:
                return ()
            return (
                int(Exact.get("MandatoryAccessConflictResources", 0)),
                int(Exact.get("BoundaryOverflow", 0)),
                int(Exact.get("PinScarcityCount", 0)),
                int(Exact.get("LocalClaimCount", 0)),
                int(Joint.get("SelectedScore", 0)),
            )

        CandidateRecords: list[PcbPlacementCandidate] = []
        for CandidateIndex, (
            Fingerprint,
            (SourceGenerator, CandidateSpacing, Candidate),
        ) in enumerate(sorted(UniquePlacements.items())):
            CandidateTopologyDemand = (
                TopologyDemandByFingerprint.get(
                    Fingerprint,
                    TopologyDemand,
                )
            )
            CandidateDiagnostics = dict(
                Candidate.Placed.LocalRouteDiagnostics or {}
            )
            CandidateRecipe = dict(
                CandidateDiagnostics.get("__PlacementRecipe__", {})
            )
            JointPortfolioCandidate = bool(
                CandidateDiagnostics.get(
                    "__JointClusterPlacement__",
                    {},
                )
            )
            Feedback = None
            if (
                Policy.Placement.EnableRoutingFeedback
                and not bool(os.environ.get("RCS_SKIP_PLACEMENT_FEEDBACK"))
                and not JointPortfolioCandidate
            ):
                Feedback = FeedbackByFingerprint.get(Fingerprint)
                if Feedback is None:
                    raise RoutingStageError(
                        _PlacementFailureWithHistory(
                            RoutingFailure(
                                Reason=RoutingFailureReason.Stagnated,
                                Stage="PlacementFeedback",
                                Detail=(
                                    "retained placement was missing its bounded "
                                    "routing-feedback record"
                                ),
                                RepairActions=("AdvancePlacementGenerator",),
                                Diagnostics={
                                    "PlacementFingerprint": Fingerprint,
                                    "SourceGenerator": SourceGenerator,
                                },
                            )
                        )
                    )
            FeedbackScore = (
                Feedback.Score if Feedback is not None else (CandidateIndex,)
            )
            CandidateRecords.append(
                PcbPlacementCandidate(
                    CandidateId=f"Placement-{Fingerprint[:12]}",
                    SourceGenerator=SourceGenerator,
                    RoutingSpacing=CandidateSpacing,
                    PlacementFingerprint=Fingerprint,
                    FeedbackScore=tuple(FeedbackScore),
                    BoundaryOverflow=(
                        Feedback.BoundaryOverflow if Feedback is not None else 0
                    ),
                    PinScarcityCount=(
                        Feedback.PinScarcityCount if Feedback is not None else 0
                    ),
                    GuideOverflowPeak=(
                        Feedback.GuideOverflowPeak if Feedback is not None else 0
                    ),
                    GuideOverflowCells=(
                        Feedback.GuideOverflowCells if Feedback is not None else 0
                    ),
                    PinEscapeConflictCount=(
                        Feedback.PinEscapeConflictCount
                        if Feedback is not None
                        else 0
                    ),
                    EstimatedGlobalExtensionNodes=(
                        Feedback.EstimatedGlobalExtensionNodes
                        if Feedback is not None
                        else 0
                    ),
                    EstimatedGlobalExtensionNets=(
                        Feedback.EstimatedGlobalExtensionNets
                        if Feedback is not None
                        else 0
                    ),
                    PreOwnedNodeCount=(
                        Feedback.PreOwnedNodeCount
                        if Feedback is not None
                        else 0
                    ),
                    Placement=Candidate,
                    PlacementRetentionFingerprint=(
                        PlacementRetentionFingerprintByFingerprint.get(
                            Fingerprint,
                            "",
                        )
                    ),
                    InterfaceTopologyFingerprint=(
                        BuildClusterInterfacePlacementTopologyFingerprint(
                            Candidate,
                            SignalTopologyFingerprints,
                        )
                    ),
                    JointPlacementState=(
                        JointPlacementStateByPlacementFingerprint.get(
                            Fingerprint
                        )
                    ),
                    AssignmentCutFingerprint=str(
                        CandidateRecipe.get(
                            "AssignmentCutFingerprint",
                            "",
                        )
                    ),
                    AssignmentConstraintFingerprint=str(
                        CandidateRecipe.get(
                            "AssignmentConstraintFingerprint",
                            "",
                        )
                    ),
                    JointPortfolioIdentityFingerprint=str(
                        CandidateRecipe.get(
                            "JointPortfolioIdentityFingerprint",
                            "",
                        )
                    ),
                    JointExactScore=JointExactScore(Candidate),
                    TopologyDemand=CandidateTopologyDemand,
                    JointPortfolioCandidate=JointPortfolioCandidate,
                    Feedback=Feedback,
                )
            )
        CandidateRecords.sort(
            key=lambda Value: PlacementCandidateOrder(
                Value,
                ConfiguredRoutingSpacing,
            )
        )
        # After a topology cut, lead with the access-distinct retained state
        # that changes the proven cut interface most from the geometry that
        # produced it.  This is based solely on placed endpoint geometry, not
        # signal identifiers or benchmark identity; the stable normal order
        # remains the tie-breaker and is unchanged off the topology trigger.
        ActiveCut = CurrentPlacementAssignmentCut
        ReferencePlacement = (
            CutSourcePlacementByFingerprint.get(
                ActiveCut.ConflictFingerprint,
            )
            if ActiveCut is not None
            else None
        )
        if (
            TopologyDemand.RequiresJointPortfolio
            and ActiveCut is not None
            and ReferencePlacement is not None
        ):
            CutSignals = frozenset(ActiveCut.RelocationSignals)
            CutGateNames = frozenset(
                Gate.Name
                for Gate in Module.Gates
                if (
                    any(Signal in CutSignals for Signal in Gate.Outputs)
                    or any(Signal in CutSignals for Signal in Gate.Inputs)
                )
            )
            ReferenceGeometry = {
                Gate.Name: (Gate.X, Gate.Y, Gate.Z, Gate.Rotation, Gate.MirrorX)
                for Gate in ReferencePlacement.Placed.PlacedGates
                if Gate.Name in CutGateNames
            }

            def CutInterfaceDifference(
                Candidate: PcbPlacementCandidate,
            ) -> int:
                if (
                    Candidate.AssignmentCutFingerprint
                    != ActiveCut.ConflictFingerprint
                    or Candidate.TopologyDemand is None
                    or Candidate.TopologyDemand
                    .MandatoryAccessOwnershipFingerprint
                    == ActiveCut.MandatoryAccessOwnershipFingerprint
                ):
                    return 0
                return sum(
                    abs(Gate.X - Reference[0])
                    + abs(Gate.Y - Reference[1])
                    + abs(Gate.Z - Reference[2])
                    + (Gate.Rotation != Reference[3])
                    + (Gate.MirrorX != Reference[4])
                    for Gate in Candidate.Placement.Placed.PlacedGates
                    if Gate.Name in CutGateNames
                    for Reference in (ReferenceGeometry.get(Gate.Name),)
                    if Reference is not None
                )

            CandidateRecords.sort(
                key=lambda Candidate: (
                    CutInterfaceDifference(Candidate) == 0,
                    -CutInterfaceDifference(Candidate),
                )
            )
            InterfaceDifferences = {
                Candidate.PlacementFingerprint: CutInterfaceDifference(
                    Candidate
                )
                for Candidate in CandidateRecords
            }
            AccessDistinctCandidateCount = sum(
                Difference > 0
                for Difference in InterfaceDifferences.values()
            )
            CandidateRecords = [
                replace(
                    Candidate,
                    CutInterfaceDifference=InterfaceDifferences[
                        Candidate.PlacementFingerprint
                    ],
                    AccessDistinctCandidateCount=(
                        AccessDistinctCandidateCount
                    ),
                )
                for Candidate in CandidateRecords
            ]
            PlacementGenerationDecisions.append({
                "Result": "topology-cut-interface-diversity-order",
                "AssignmentCutFingerprint": ActiveCut.ConflictFingerprint,
                "CandidateInterfaceDifferences": [
                    {
                        "CandidateId": Candidate.CandidateId,
                        "Difference": CutInterfaceDifference(Candidate),
                    }
                    for Candidate in CandidateRecords
                ],
            })
        return CandidateRecords

    def RetainedRoutingCandidateLimit(
        Candidates: list[PcbPlacementCandidate],
    ) -> int:
        """Keep the complete joint portfolio only when it is actually active."""
        JointPortfolioActive = any(
            Candidate.JointPortfolioCandidate
            for Candidate in Candidates
        )
        return max(
            1,
            Policy.NandPacking.RetainedPlacementCandidates,
            (
                Policy.NandPacking.RetainedJointPlacementCandidates
                if JointPortfolioActive
                else 1
            ),
        )

    def _DiscardPlacementFingerprint(Fingerprint: str) -> None:
        """Remove one materialized placement from every identity/cache index."""
        RetentionFingerprint = (
            PlacementRetentionFingerprintByFingerprint.pop(
                Fingerprint,
                None,
            )
        )
        if RetentionFingerprint is not None:
            ExistingRetention = RetainedPlacementTopologyFingerprints.get(
                RetentionFingerprint
            )
            if (
                ExistingRetention is not None
                and ExistingRetention[0] == Fingerprint
            ):
                RetainedPlacementTopologyFingerprints.pop(
                    RetentionFingerprint,
                    None,
                )
        UniquePlacements.pop(Fingerprint, None)
        FeedbackByFingerprint.pop(Fingerprint, None)
        RoutingResourcesByFingerprint.pop(Fingerprint, None)
        MaterializedPlacementByFingerprint.pop(Fingerprint, None)
        TopologyDemandByFingerprint.pop(Fingerprint, None)
        JointPlacementStateByPlacementFingerprint.pop(Fingerprint, None)

    def _TransactionalEndpointRepairPortfolioFingerprint(
        SourceCandidate: PcbPlacementCandidate,
        RepairSignals: frozenset[str],
        RepairClusterCount: int = 1,
    ) -> str:
        """Identify the bounded local ECO siblings of one exact cut."""
        return BuildStableFingerprint((
            "transactional-cluster-endpoint-repair",
            SourceCandidate.PlacementFingerprint,
            (
                CurrentPlacementAssignmentCut.ConflictFingerprint
                if CurrentPlacementAssignmentCut is not None
                else ""
            ),
            PlacementAssignmentConstraints.Fingerprint,
            tuple(sorted(map(str, RepairSignals))),
            max(1, RepairClusterCount),
        ))

    def _PublishTransactionalClusterEndpointRepair(
        SourceCandidate: PcbPlacementCandidate,
        RepairSignals: frozenset[str],
        RepairVariant: int = 0,
        RepairClusterCount: int = 1,
        RepairTerminalPositions: frozenset[
            tuple[int, int, int]
        ] = frozenset(),
    ) -> bool:
        """Publish one access-distinct local ECO without global replacement."""
        nonlocal PendingTopologyCutEpoch
        nonlocal NeedsFeedbackPlacementGeneration
        nonlocal InternalPinBankGeometryRepairActive
        nonlocal RequiredDistinctPinBankOwnershipFingerprint
        if (
            not RepairSignals
            or SourceCandidate.TopologyDemand is None
            or not SourceCandidate.TopologyDemand.RequiresJointPortfolio
        ):
            return False
        StartedAt = monotonic()
        try:
            Result = BuildTransactionalClusterEndpointRepair(
                SourceCandidate.Placement,
                RepairSignals,
                BeamWidth=min(16, Policy.NandPacking.BeamWidth),
                RepairVariant=RepairVariant,
                RepairClusterCount=RepairClusterCount,
                RepairTerminalPositions=RepairTerminalPositions,
                WorkCheck=lambda Diagnostics: Deadline.RaiseIfExpired(
                    "TransactionalClusterEndpointRepair",
                    {
                        "CandidateId": SourceCandidate.CandidateId,
                        **Diagnostics,
                    },
                ),
            )
        except RoutingStageError as Error:
            PlacementGenerationDecisions.append({
                "Result": "transactional-cluster-endpoint-repair-expired",
                "CandidateId": SourceCandidate.CandidateId,
                "Signals": sorted(RepairSignals),
                "Failure": Error.Failure.ToDictionary(),
                "ElapsedSeconds": round(monotonic() - StartedAt, 6),
            })
            return False
        if not Result.Accepted or Result.Placement is None:
            PlacementGenerationDecisions.append({
                "Result": "transactional-cluster-endpoint-repair-rejected",
                "CandidateId": SourceCandidate.CandidateId,
                "Signals": sorted(RepairSignals),
                "Diagnostics": Result.Diagnostics,
                "ElapsedSeconds": round(monotonic() - StartedAt, 6),
            })
            return False

        Candidate = Result.Placement
        CandidateProfile = Candidate.MandatoryAccessPreScreenProfile
        if CandidateProfile is None:
            PlacementGenerationDecisions.append({
                "Result": "transactional-cluster-endpoint-repair-rejected",
                "CandidateId": SourceCandidate.CandidateId,
                "Signals": sorted(RepairSignals),
                "Diagnostics": {
                    **Result.Diagnostics,
                    "Reason": "missing-mandatory-access-profile",
                },
                "ElapsedSeconds": round(monotonic() - StartedAt, 6),
            })
            return False
        MandatoryConflicts: dict[object, set[str]] = {}
        for Resource, Owners in (
            *CandidateProfile.CrossConflicts,
            *CandidateProfile.SelfConflicts,
        ):
            MandatoryConflicts.setdefault(Resource, set()).update(
                map(str, Owners)
            )
        CandidateTopologyDemand = MeasurePlacementTopologyDemand(
            TopologyDemand,
            Candidate,
            MandatoryConflicts=MandatoryConflicts,
            MandatoryProfile=CandidateProfile,
        )
        if (
            MandatoryConflicts
            or CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint
            == SourceCandidate.TopologyDemand
            .MandatoryAccessOwnershipFingerprint
        ):
            PlacementGenerationDecisions.append({
                "Result": "transactional-cluster-endpoint-repair-rejected",
                "CandidateId": SourceCandidate.CandidateId,
                "Signals": sorted(RepairSignals),
                "Diagnostics": {
                    **Result.Diagnostics,
                    "Reason": (
                        "mandatory-conflict-or-stagnant-ownership"
                    ),
                    "MandatoryConflictResourceCount": len(
                        MandatoryConflicts
                    ),
                },
                "ElapsedSeconds": round(monotonic() - StartedAt, 6),
            })
            return False

        CandidateDiagnostics = dict(
            Candidate.Placed.LocalRouteDiagnostics or {}
        )
        PortfolioIdentityFingerprint = (
            _TransactionalEndpointRepairPortfolioFingerprint(
                SourceCandidate,
                RepairSignals,
                RepairClusterCount,
            )
        )
        SourceRecipe = dict(
            CandidateDiagnostics.get("__PlacementRecipe__", {})
        )
        TransactionalRepairSignalHistory = [
            sorted(frozenset(map(str, Signals)))
            for Signals in SourceRecipe.get(
                "TransactionalRepairSignalHistory",
                (),
            )
            if isinstance(Signals, tuple | list | set | frozenset)
            and Signals
        ]
        CurrentRepairSignalSet = sorted(
            frozenset(map(str, RepairSignals))
        )
        if CurrentRepairSignalSet not in TransactionalRepairSignalHistory:
            TransactionalRepairSignalHistory.append(
                CurrentRepairSignalSet
            )
        EffectiveRepairClusterCount = int(
            Result.Diagnostics.get(
                "RepairClusterCount",
                RepairClusterCount,
            )
        )
        CandidateDiagnostics["__PlacementRecipe__"] = {
            **SourceRecipe,
            "SourceGenerator": "transactional-cluster-endpoint-repair",
            "AssignmentCutFingerprint": (
                CurrentPlacementAssignmentCut.ConflictFingerprint
                if CurrentPlacementAssignmentCut is not None
                else ""
            ),
            "AssignmentConstraintFingerprint": (
                PlacementAssignmentConstraints.Fingerprint
            ),
            "JointPortfolioIdentityFingerprint": (
                PortfolioIdentityFingerprint
            ),
            "IsPostPinBankRepairEpoch": True,
            "EnableInternalPinBankGeometryRepair": True,
            "InternalPinBankGeometryRepairSignals": sorted(
                RepairSignals
            ),
            "TransactionalRepairSignalHistory": (
                TransactionalRepairSignalHistory
            ),
            "RequiredDistinctPinBankOwnershipFingerprint": (
                SourceCandidate.TopologyDemand
                .MandatoryAccessOwnershipFingerprint
            ),
            "ReusedPlacedGeometry": True,
            "TransactionalClusterEndpointRepair": True,
            "TransactionalRepairClusterCount": (
                EffectiveRepairClusterCount
            ),
        }
        Candidate.Placed.LocalRouteDiagnostics = CandidateDiagnostics
        ApplyActivePlacementAssignmentConstraints(
            Candidate,
            PlacementAssignmentConstraints,
        )
        (
            _AppliedProfile,
            CandidateProfileFingerprint,
        ) = ApplyCoordinatedCandidateDiversificationProfile(
            Candidate,
            RepairSignals,
        )
        Fingerprint = BuildPlacementFingerprint(
            Candidate,
            CandidateTopologyDemand
            .MandatoryAccessOwnershipFingerprint,
            IncludeLocalClaims=False,
        )
        RetentionFingerprint = BuildPlacementRetentionFingerprint(
            Candidate,
            CandidateTopologyDemand
            .MandatoryAccessOwnershipFingerprint,
            IncludeLocalClaims=False,
        )
        if (
            Fingerprint in UniquePlacements
            or RetentionFingerprint in RetainedPlacementTopologyFingerprints
            or not TransactionalEndpointRepairIdentityIsFresh(
                Fingerprint,
                RetentionFingerprint,
                SeenTransactionalEndpointRepairFingerprints,
                SeenTransactionalEndpointRepairRetentionFingerprints,
            )
            or Fingerprint in RejectedPlacementFingerprints
            or RetentionFingerprint in RejectedPlacementRetentionFingerprints
        ):
            PlacementGenerationDecisions.append({
                "Result": "transactional-cluster-endpoint-repair-rejected",
                "CandidateId": SourceCandidate.CandidateId,
                "Signals": sorted(RepairSignals),
                "Diagnostics": {
                    **Result.Diagnostics,
                    "Reason": "duplicate-or-rejected-identity",
                    "PlacementFingerprint": Fingerprint,
                    "PlacementRetentionFingerprint": RetentionFingerprint,
                },
                "ElapsedSeconds": round(monotonic() - StartedAt, 6),
            })
            return False
        try:
            CandidateResources = BuildRoutingResources(
                Candidate.Placed,
                WorkCheck=lambda Diagnostics: Deadline.RaiseIfExpired(
                    "TransactionalClusterEndpointResourceMaterialization",
                    {
                        "CandidateId": SourceCandidate.CandidateId,
                        **Diagnostics,
                    },
                ),
            )
        except (RoutingStageError, ValueError) as Error:
            PlacementGenerationDecisions.append({
                "Result": "transactional-cluster-endpoint-repair-rejected",
                "CandidateId": SourceCandidate.CandidateId,
                "Signals": sorted(RepairSignals),
                "Diagnostics": {
                    **Result.Diagnostics,
                    "Reason": "resource-materialization-rejected",
                    "Validation": str(Error),
                },
                "ElapsedSeconds": round(monotonic() - StartedAt, 6),
            })
            return False

        UniquePlacements[Fingerprint] = (
            "transactional-cluster-endpoint-repair",
            SourceCandidate.RoutingSpacing,
            Candidate,
        )
        PlacementRetentionFingerprintByFingerprint[Fingerprint] = (
            RetentionFingerprint
        )
        RetainedPlacementTopologyFingerprints[RetentionFingerprint] = (
            Fingerprint,
            "transactional-cluster-endpoint-repair",
        )
        TopologyDemandByFingerprint[Fingerprint] = CandidateTopologyDemand
        RoutingResourcesByFingerprint[Fingerprint] = CandidateResources
        SeenTransactionalEndpointRepairFingerprints.add(Fingerprint)
        SeenTransactionalEndpointRepairRetentionFingerprints.add(
            RetentionFingerprint
        )
        PendingJointPlacementStates.clear()
        PendingTopologyCutEpoch = None
        NeedsFeedbackPlacementGeneration = False
        InternalPinBankGeometryRepairActive = False
        RequiredDistinctPinBankOwnershipFingerprint = ""
        PlacementGenerationDecisions.append({
            "Result": "transactional-cluster-endpoint-repair-published",
            "CandidateId": f"Placement-{Fingerprint[:12]}",
            "SourceCandidateId": SourceCandidate.CandidateId,
            "Signals": sorted(RepairSignals),
            "RepairVariant": RepairVariant,
            "RequestedRepairClusterCount": RepairClusterCount,
            "RepairClusterCount": EffectiveRepairClusterCount,
            "PlacementFingerprint": Fingerprint,
            "PlacementRetentionFingerprint": RetentionFingerprint,
            "CandidateProfileFingerprint": CandidateProfileFingerprint,
            "JointPortfolioIdentityFingerprint": (
                PortfolioIdentityFingerprint
            ),
            "MandatoryAccessOwnershipFingerprint": (
                CandidateTopologyDemand
                .MandatoryAccessOwnershipFingerprint
            ),
            "Diagnostics": Result.Diagnostics,
            "ElapsedSeconds": round(monotonic() - StartedAt, 6),
            "NextAction": "route-access-distinct-local-eco",
        })
        JointPlacementStateEvents.append({
            "Status": "transactional-cluster-endpoint-repair-published",
            "CandidateId": f"Placement-{Fingerprint[:12]}",
            "SourceCandidateId": SourceCandidate.CandidateId,
            "RepairVariant": RepairVariant,
            "ChangedGateCount": Result.Diagnostics.get(
                "ChangedGateCount", 0
            ),
            "InvalidatedSignals": Result.Diagnostics.get(
                "InvalidatedSignals", ()
            ),
            "PreservedLocalClaimCount": Result.Diagnostics.get(
                "PreservedLocalClaimCount", 0
            ),
            "GlobalEnvelopePreserved": True,
        })
        return True

    CandidateRecords = _BuildCandidateRecords()
    ExactClusterInterfaceSolveEnabled = any(
            RequiresExactClusterInterfaceSolve(
                Candidate.TopologyDemand,
                Candidate.Placement.Placed,
                Policy,
            )
            for Candidate in CandidateRecords
    )
    if ExactClusterInterfaceSolveEnabled:
        PlacementGenerationDecisions.append({
            "Result": "exact-cluster-interface-gate-enabled",
            "ExecutableLegacyRepairCascade": False,
            "MaximumPlacementStateCount": min(
                6,
                Policy.NandPacking.RetainedJointPlacementCandidates,
            ),
            "Trigger": "measured-interface-pressure",
        })
    PlacementGenerationDecisions.append({
        "Result": "deferred-placement-alternatives",
        "Reason": (
            "route exact-legal primary candidates before paying for "
            "structure-aware or spacing recovery"
        ),
        "DemandPressurePresent": PlacementNeedsDemandDiversity(
            CandidateRecords,
            ConfiguredRoutingSpacing,
        ),
        "DeferredCount": (
            len(GenerationPlan.DeferredRequests)
            - len(ConsumedDeferredRequestIndexes)
        ),
    })

    OrderedPlacements = CandidateRecords[
        : RetainedRoutingCandidateLimit(CandidateRecords)
    ]
    PlacementFeedback = [
        Candidate.ToDictionary() for Candidate in CandidateRecords
    ]
    Placement = OrderedPlacements[0].Placement
    RoutingSpacing = OrderedPlacements[0].RoutingSpacing
    if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
        for CandidateRecord in OrderedPlacements:
            print(
                "[debug] authoritative: retained placement "
                f"id={CandidateRecord.CandidateId} "
                f"source={CandidateRecord.SourceGenerator} "
                f"score={CandidateRecord.FeedbackScore} "
                f"boundary_overflow={CandidateRecord.BoundaryOverflow} "
                f"pin_scarcity={CandidateRecord.PinScarcityCount} "
                f"packed={bool(CandidateRecord.Placement.PackedClusters)}",
                flush=True,
            )

    def ReportRoutingProgress(
        Completed: int,
        Total: int,
        Workers: int,
        Valid: int,
        Failed: int,
        BestRouted: RoutedDesign | None,
        Stage: str,
    ) -> None:
        if ProgressCallback is None:
            return
        # RoutePcbDesign owns one candidate, not the complete placement flow.
        # Its completion must remain visibly pending until the shared deadline,
        # authoritative validation, and final result construction all pass.
        EffectiveTotal = max(1, Total)
        CandidateComplete = Completed >= EffectiveTotal or Valid > 0
        EffectiveCompleted = (
            min(Completed, EffectiveTotal - 1)
            if CandidateComplete
            else Completed
        )
        EffectiveValid = 0 if CandidateComplete else Valid
        EffectiveBestRouted = None if CandidateComplete else BestRouted
        EffectiveStage = (
            "routed candidate awaiting validation"
            if CandidateComplete and Failed == 0
            else Stage
        )
        BestFootprint = None
        BestBlocks = None
        BestWidth = None
        BestDepth = None
        if EffectiveBestRouted is not None:
            (
                BestFootprint,
                BestBlocks,
                BestWidth,
                BestDepth,
            ) = MeasurePcbDesign(Placement.Placed, EffectiveBestRouted)
        ProgressCallback(
            PcbProgress(
                Completed=EffectiveCompleted,
                Total=EffectiveTotal,
                Workers=Workers,
                Valid=EffectiveValid,
                BestBlocks=BestBlocks,
                BestWidth=BestWidth,
                BestDepth=BestDepth,
                BestFootprint=BestFootprint,
                Failed=Failed,
                Stage=f"spacing {RoutingSpacing} | {EffectiveStage}",
            )
        )

    def _RouteWithFailedLocalClaimsReleased(
        CandidatePlacement: PcbPlacement,
        AttemptPolicy: PhysicalDesignPolicy,
        AttemptDeadline: RoutingDeadline,
        Failure: RoutingFailure,
        AdaptiveStartedAt: float,
        AdaptiveExpiresAt: float,
    ) -> tuple[PcbPlacement, RoutedDesign] | None:
        """Release only an unextendable local tree and retain every clean tree.

        A packed local claim is an optimization, not a correctness dependency.
        When its boundary cannot be extended, the affected signal is returned
        to normal global routing while claims owned by unrelated signals remain
        authoritative base ownership.
        """
        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
            print(
                "[debug] authoritative: evaluating local-claim release "
                f"reason={Failure.Reason} stage={Failure.Stage}",
                flush=True,
            )
        # Mandatory capacity cuts are screened before detailed routing.  A
        # same-candidate retry here merely repeats a fully completed doomed
        # pass and consumes the shared routing deadline.
        if FailureRequestsPlacementAdvance(Failure):
            return None
        ReleasableReasons = {
            RoutingFailureReason.NoBoundaryEscape,
            RoutingFailureReason.PartialTreeExtensionFailed,
            RoutingFailureReason.MultiSourceStagnated,
            RoutingFailureReason.TrackAssignmentConflict,
            RoutingFailureReason.DetailedSearchExhausted,
        }
        Signals = ExtractPlacementRelocationSignals(Failure)
        if not Signals:
            Signals = frozenset(Failure.AffectedNets)
        if Failure.Reason not in ReleasableReasons or not Signals:
            return None
        Original = CandidatePlacement.Placed
        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
            print(
                "[debug] authoritative: candidate local claim signals="
                f"{sorted(Signals)} available="
                f"{sorted({Claim.Signal for Claim in (Original.LocalRouteClaims or ())})}",
                flush=True,
            )
        ExistingClaims = tuple(Original.LocalRouteClaims or ())
        AllSignals = {Claim.Signal for Claim in ExistingClaims}
        if not AllSignals:
            return None
        Signals = SelectReleasableLocalClaimSignals(Signals, ExistingClaims)
        if not Signals:
            return None
        RetainedClaims = tuple(
            Claim for Claim in (Original.LocalRouteClaims or ())
            if Claim.Signal not in Signals
        )
        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
            print(
                "[debug] authoritative: releasing local claims "
                f"signals={sorted(Signals)} original={len(Original.LocalRouteClaims or ())} "
                f"retained={len(RetainedClaims)}",
                flush=True,
            )
        if len(RetainedClaims) == len(Original.LocalRouteClaims or ()):
            return None
        Deadline.RaiseIfExpired(
            "LocalClaimRelease",
            {
                "Phase": "before-reroute",
                "AffectedSignals": sorted(Signals),
            },
        )
        ReleasedDiagnostics = dict(Original.LocalRouteDiagnostics or {})
        ReleasedDiagnostics["ReleasedLocalClaims"] = {
            "Signals": sorted(Signals),
            "Reason": Failure.Reason.value,
            "Stage": Failure.Stage,
        }
        ReleasedPlaced = replace(
            Original,
            LocalRouteClaims=RetainedClaims,
            FrozenNetWires={
                Signal: Nodes
                for Signal, Nodes in (Original.FrozenNetWires or {}).items()
                if Signal not in Signals
            },
            LocalNetBranches={
                Signal: Nodes
                for Signal, Nodes in (Original.LocalNetBranches or {}).items()
                if Signal not in Signals
            },
            LocalNetTargets={
                Signal: Nodes
                for Signal, Nodes in (Original.LocalNetTargets or {}).items()
                if Signal not in Signals
            },
            LocalRouteDiagnostics=ReleasedDiagnostics,
        )
        ReleasedPlacement = replace(CandidatePlacement, Placed=ReleasedPlaced)
        RecoveryStartedAt = monotonic()
        RemainingAdaptiveSeconds = min(
            Deadline.ExpiresAt,
            AdaptiveExpiresAt,
        ) - RecoveryStartedAt
        if RemainingAdaptiveSeconds <= 0:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="LocalClaimRelease",
                    Detail=(
                        "original placement adaptive slice expired before "
                        "same-candidate local-claim recovery"
                    ),
                    RepairActions=("AdvancePlacementCandidate",),
                    Diagnostics={
                        "Action": "advance-placement-adaptive-slice-expired",
                        "AdaptiveStartedAt": AdaptiveStartedAt,
                        "AdaptiveExpiresAt": AdaptiveExpiresAt,
                        "Deadline": Deadline.ToDictionary(),
                    },
                )
            )
        RecoveryPolicy = replace(
            AttemptPolicy,
            RuntimeBudgetSeconds=min(
                AttemptPolicy.RuntimeBudgetSeconds,
                Deadline.RemainingSeconds(),
                RemainingAdaptiveSeconds,
            ),
            AdaptiveRouting=replace(
                AttemptPolicy.AdaptiveRouting,
                MaximumRuntimeSeconds=min(
                    AttemptPolicy.AdaptiveRouting.MaximumRuntimeSeconds,
                    RemainingAdaptiveSeconds,
                ),
            ),
        )
        ReleasedRouted = RoutePcbDesign(
            ReleasedPlacement,
            ProgressCallback=ReportRoutingProgress,
            Policy=RecoveryPolicy,
            Deadline=AttemptDeadline,
        )
        if monotonic() >= AdaptiveExpiresAt:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="LocalClaimRelease",
                    Detail=(
                        "same-candidate local-claim recovery exceeded the "
                        "original placement adaptive slice"
                    ),
                    RepairActions=("AdvancePlacementCandidate",),
                    Diagnostics={
                        "Action": "advance-placement-adaptive-slice-expired",
                        "AdaptiveStartedAt": AdaptiveStartedAt,
                        "AdaptiveExpiresAt": AdaptiveExpiresAt,
                        "RecoveryStartedAt": RecoveryStartedAt,
                        "Deadline": Deadline.ToDictionary(),
                    },
                )
            )
        Deadline.RaiseIfExpired(
            "Routing",
            {
                "Recovery": "released-affected-local-claims",
                "AffectedSignals": sorted(Signals),
            },
        )
        Deadline.RaiseIfExpired(
            "RoutedValidation",
            {
                "Phase": "before",
                "Recovery": "released-affected-local-claims",
                "AffectedSignals": sorted(Signals),
            },
        )
        if RoutedValidationCallback is not None:
            RoutedValidationCallback(ReleasedRouted)
        Deadline.RaiseIfExpired(
            "RoutedValidation",
            {
                "Phase": "after",
                "Recovery": "released-affected-local-claims",
                "AffectedSignals": sorted(Signals),
            },
        )
        return ReleasedPlacement, ReleasedRouted

    Routed = None
    SelectedCandidate: PcbPlacementCandidate | None = None
    LastAttemptedCandidate: PcbPlacementCandidate | None = None
    RoutingPercentageSelectionEnabled = (
        Policy.MaterialObjective.OptimizeRoutingPercentage
        and NandGateCount
        >= Policy.MaterialObjective.MinimumRoutingPercentageSelectionNandCount
    )
    RoutedCandidates: list[tuple[
        tuple[float, int, int, int, int, int, int, str],
        PcbPlacementCandidate,
        PcbPlacement,
        RoutedDesign,
        dict[str, object],
    ]] = []

    def RecordRoutedCandidate(
        Candidate: PcbPlacementCandidate,
        CandidatePlacement: PcbPlacement,
        CandidateRouted: RoutedDesign,
    ) -> None:
        """Score legal routed placements by final volume, then route share."""
        from SchemEncoder.Writer262 import BuildLitematicBlockMap

        Composition = BuildLitematicBlockMap(CandidateRouted).Composition
        Score = (
            Composition.FullFootprint,
            Composition.RoutingFunctionalShare,
            Composition.RoutingOwnedFunctionalBlocks,
            Composition.Footprint,
            Composition.NonAirBlocks,
            Composition.Width,
            Composition.Depth,
            Candidate.CandidateId,
        )
        Diagnostics: dict[str, object] = {
            "CandidateId": Candidate.CandidateId,
            "RoutingFunctionalShare": Composition.RoutingFunctionalShare,
            "RoutingOwnedFunctionalBlocks": (
                Composition.RoutingOwnedFunctionalBlocks
            ),
            "NonAirBlocks": Composition.NonAirBlocks,
            "Footprint": Composition.Footprint,
            "XYFootprint": Composition.XYFootprint,
            "FullFootprint": Composition.FullFootprint,
            "Width": Composition.Width,
            "Height": Composition.Height,
            "Depth": Composition.Depth,
            "Score": list(Score[:-1]),
        }
        RoutedCandidates.append((
            Score,
            Candidate,
            CandidatePlacement,
            CandidateRouted,
            Diagnostics,
        ))

    def MaterializeSelectedJointPlacementLocalRouting(
        Candidate: PcbPlacementCandidate,
        WorkCheck: Callable[[dict[str, object]], None],
    ) -> PcbPlacement:
        """Materialize local routes only for a ranked joint candidate."""
        ScoringPlacement = Candidate.Placement
        ScoringDiagnostics = dict(
            ScoringPlacement.Placed.LocalRouteDiagnostics or {}
        )
        DeferredDiagnostics = ScoringDiagnostics.get(
            "__DeferredLocalRouting__",
            {},
        )
        if not (
            isinstance(DeferredDiagnostics, dict)
            and bool(DeferredDiagnostics.get("ScoringOnly"))
        ):
            return ScoringPlacement

        Cached = MaterializedPlacementByFingerprint.get(
            Candidate.PlacementFingerprint
        )
        if Cached is not None:
            ScoringRelocation = ScoringDiagnostics.get(
                "__PlacementRelocation__",
                {},
            )
            if isinstance(ScoringRelocation, dict):
                ApplyCoordinatedCandidateDiversificationProfile(
                    Cached,
                    frozenset(map(
                        str,
                        ScoringRelocation.get(
                            "CoordinatedCandidateDiversificationSignals",
                            (),
                        ),
                    )),
                )
            ApplyActivePlacementAssignmentConstraints(
                Cached,
                PlacementAssignmentConstraints,
            )
            JointPlacementStateEvents.append({
                "Status": "local-routing-materialization-cache-hit",
                "CandidateId": Candidate.CandidateId,
                "PlacementFingerprint": (
                    Candidate.PlacementFingerprint
                ),
            })
            return Cached

        State = (
            Candidate.JointPlacementState
            or JointPlacementStateByPlacementFingerprint.get(
                Candidate.PlacementFingerprint
            )
        )
        if State is None:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.PlacementOverlap,
                    Stage="PlacementLocalRoutingMaterialization",
                    Detail=(
                        "a scoring-only retained placement was missing its "
                        "immutable joint recipe state"
                    ),
                    RepairActions=(
                        "InspectJointPlacementStateRetention",
                    ),
                    Diagnostics={
                        "CandidateId": Candidate.CandidateId,
                        "PlacementFingerprint": (
                            Candidate.PlacementFingerprint
                        ),
                    },
                )
            )

        MaterializationStarted = monotonic()
        WorkCheck({
            "Phase": "local-routing-materialization-start",
            "CandidateId": Candidate.CandidateId,
        })
        PackingPolicy = State.Request.PackingPolicy
        Materialized = PlacePcbGraph(
            Netlist,
            RoutingSpacing=State.RoutingSpacing,
            PlacementPolicy=Policy.Placement,
            ClusterPolicy=Policy.Clustering,
            MaximumBoundaryTerminals=(
                Policy.Organization.MaximumClusterEntrances
            ),
            MaximumEntrancesPerSignal=(
                Policy.Organization.MaximumClusterEntrancesPerSignal
            ),
            PackingPolicy=PackingPolicy,
            RelocationSignals=State.RelocationSignals,
            RelocationPrioritySignals=(
                State.RelocationPrioritySignals
            ),
            RequiredRelocationSignals=(
                State.RequiredRelocationSignals
            ),
            RelocationVariant=State.RelocationVariant,
            JointPlacementCandidateIndex=State.CandidateIndex,
            AssignmentCut=State.AssignmentCut,
            AssignmentConstraints=State.AssignmentConstraints,
            CoordinatedCandidateDiversificationSignals=(
                State.CoordinatedCandidateDiversificationSignals
            ),
            EnableClusterLocalRouteReuse=(
                State.EnableClusterLocalRouteReuse
                or bool(
                    ScoringDiagnostics.get(
                        "__ClusterPinBankRepair__",
                        {},
                    )
                )
                or bool(
                    State.AssignmentCut is not None
                    and len(State.AssignmentCut.PairwiseConflictEdges) >= 2
                    and Candidate.TopologyDemand is not None
                    and RequiresDenseBoundaryRoutingReserve(
                        Candidate.TopologyDemand,
                        Policy,
                    )
                )
                or bool(
                    Candidate.TopologyDemand is not None
                    and RequiresDenseBoundaryRoutingReserve(
                        Candidate.TopologyDemand,
                        Policy,
                    )
                )
            ),
            EnableClusterBoundaryLeases=(
                ShouldEnableClusterBoundaryLeaseInterface(
                    ScaleGeometryPressure=(
                        TopologyPressure.ScaleGeometryPressure
                    ),
                    TopologyRequiresJointPortfolio=(
                        TopologyDemand.RequiresJointPortfolio
                    ),
                    IsPostPinBankRepairEpoch=(
                        State.IsPostPinBankRepairEpoch
                    ),
                )
            ),
            EnableClusterInterfacePlacementFeasibility=(
                TopologyDemand.RequiresJointPortfolio
            ),
            CutDrivenClusterRefinementSignals=(
                SelectCutDrivenClusterRefinementSignals(
                    State.AssignmentCut,
                    SignalTopologyFingerprints,
                    Constraints=State.AssignmentConstraints,
                )
                if TopologyDemand.RequiresJointPortfolio
                else None
            ),
            EnableInternalPinBankGeometryRepair=(
                State.EnableInternalPinBankGeometryRepair
            ),
            InternalPinBankGeometryRepairSignals=(
                State.InternalPinBankGeometryRepairSignals
            ),
            FocusedCutEpochPlacement=(
                State.Request.UseCurrentAssignmentCutRelocationSignals
            ),
            TopologyCutFrontier=State.TopologyCutFrontier,
            PlacementScoringOnly=False,
            WorkCheck=WorkCheck,
        )
        ExpectedTopologyDemand = Candidate.TopologyDemand
        if ExpectedTopologyDemand is None:
            raise RoutingStageError(RoutingFailure(
                Reason=RoutingFailureReason.PlacementOverlap,
                Stage="PlacementLocalRoutingMaterialization",
                Detail=(
                    "selected local-route materialization lacked its "
                    "retained topology proof"
                ),
                Diagnostics={
                    "CandidateId": Candidate.CandidateId,
                    "PlacementFingerprint": (
                        Candidate.PlacementFingerprint
                    ),
                },
            ))
        MaterializedFingerprint = BuildPlacementFingerprint(
            Materialized,
            ExpectedTopologyDemand
            .MandatoryAccessOwnershipFingerprint,
            IncludeLocalClaims=False,
        )
        MaterializedRetentionFingerprint = (
            BuildPlacementRetentionFingerprint(
                Materialized,
                ExpectedTopologyDemand
                .MandatoryAccessOwnershipFingerprint,
                IncludeLocalClaims=False,
            )
        )
        IdentityMatches = (
            MaterializedFingerprint == Candidate.PlacementFingerprint
            and MaterializedRetentionFingerprint
            == Candidate.PlacementRetentionFingerprint
        )
        MandatoryConflicts: dict[object, set[str]] = {}
        if IdentityMatches:
            # Placement scoring already performed exact template isolation
            # and mandatory-access measurement. Those properties depend only
            # on gate geometry, which this identity excludes local claims
            # specifically so immutable route materialization can reuse the
            # proof.
            MaterializedTopologyDemand = ExpectedTopologyDemand
            RankingMatches = True
        else:
            ValidatePlacedCellElectricalIsolation(
                Materialized.Placed,
                WorkCheck=WorkCheck,
            )
            MandatoryProfile = MeasureMandatoryAccessConflictProfile(
                Materialized.Placed.PlacedGates,
                Materialized.SignalOrder,
                WorkCheck=WorkCheck,
            )
            for Resource, Owners in (
                *MandatoryProfile.CrossConflicts,
                *MandatoryProfile.SelfConflicts,
            ):
                MandatoryConflicts.setdefault(
                    Resource,
                    set(),
                ).update(map(str, Owners))
            MaterializedTopologyDemand = MeasurePlacementTopologyDemand(
                TopologyDemand,
                Materialized,
                MandatoryConflicts=MandatoryConflicts,
                MandatoryProfile=MandatoryProfile,
            )
            RankingMatches = (
                MaterializedTopologyDemand.JointOrderKey
                == ExpectedTopologyDemand.JointOrderKey
            )
        if (
            MandatoryConflicts
            or not RankingMatches
            or not IdentityMatches
        ):
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.PlacementOverlap,
                    Stage="PlacementLocalRoutingMaterialization",
                    Detail=(
                        "selected local-route materialization changed exact "
                        "placement legality, ranking, or geometry identity"
                    ),
                    RepairActions=(
                        "InspectDeferredLocalRoutingIdentity",
                    ),
                    Diagnostics={
                        "CandidateId": Candidate.CandidateId,
                        "PlacementFingerprintExpected": (
                            Candidate.PlacementFingerprint
                        ),
                        "PlacementFingerprintMaterialized": (
                            MaterializedFingerprint
                        ),
                        "PlacementRetentionFingerprintExpected": (
                            Candidate.PlacementRetentionFingerprint
                        ),
                        "PlacementRetentionFingerprintMaterialized": (
                            MaterializedRetentionFingerprint
                        ),
                        "MandatoryAccessConflictResourceCount": len(
                            MandatoryConflicts
                        ),
                        "RankingMatches": RankingMatches,
                        "IdentityMatches": IdentityMatches,
                        "ExpectedTopologyDemand": (
                            ExpectedTopologyDemand.ToDictionary()
                            if ExpectedTopologyDemand is not None
                            else None
                        ),
                        "MaterializedTopologyDemand": (
                            MaterializedTopologyDemand.ToDictionary()
                        ),
                    },
                )
            )

        MaterializedDiagnostics = dict(
            Materialized.Placed.LocalRouteDiagnostics or {}
        )
        for DiagnosticKey in (
            "__PlacementRecipe__",
            "__TopologyDemandProfile__",
        ):
            if DiagnosticKey in ScoringDiagnostics:
                MaterializedDiagnostics[DiagnosticKey] = (
                    ScoringDiagnostics[DiagnosticKey]
                )
        Materialized.Placed.LocalRouteDiagnostics = (
            MaterializedDiagnostics
        )
        ApplyActivePlacementAssignmentConstraints(
            Materialized,
            PlacementAssignmentConstraints,
        )
        ScoringRelocation = ScoringDiagnostics.get(
            "__PlacementRelocation__",
            {},
        )
        if isinstance(ScoringRelocation, dict):
            ApplyCoordinatedCandidateDiversificationProfile(
                Materialized,
                frozenset(map(
                    str,
                    ScoringRelocation.get(
                        "CoordinatedCandidateDiversificationSignals",
                        (),
                    ),
                )),
            )
        if (
            Candidate.TopologyDemand is not None
            and RequiresDenseBoundaryRoutingReserve(
                Candidate.TopologyDemand,
                Policy,
            )
            and ConsumedPairedLeaseRepairProfileFingerprints
            and PlacementCoordinatedCandidateDiversificationSignals
        ):
            # A paired-cut retry has already earned this bounded candidate
            # profile.  Carry it onto the next exact geometry before its
            # first route attempt; otherwise that geometry merely spends a
            # second slice rediscovering the same ownership diversification.
            (
                PreappliedDenseProfile,
                PreappliedDenseProfileFingerprint,
            ) = ApplyCoordinatedCandidateDiversificationProfile(
                Materialized,
                PlacementCoordinatedCandidateDiversificationSignals,
            )
            if PreappliedDenseProfile:
                Materialized.Placed.LocalRouteDiagnostics.setdefault(
                    "__ClusterLocalRouteTemplates__", {}
                )["PreappliedDenseRoutingProfile"] = {
                    "Signals": sorted(
                        PlacementCoordinatedCandidateDiversificationSignals
                    ),
                    "ProfileFingerprint": PreappliedDenseProfileFingerprint,
                }
        WorkCheck({
            "Phase": "local-routing-materialization-complete",
            "CandidateId": Candidate.CandidateId,
        })
        MaterializedPlacementByFingerprint[
            Candidate.PlacementFingerprint
        ] = Materialized
        JointPlacementStateEvents.append({
            "Status": "local-routing-materialized",
            "CandidateId": Candidate.CandidateId,
            "PlacementFingerprint": Candidate.PlacementFingerprint,
            "LocalClaimCount": len(
                Materialized.Placed.LocalRouteClaims or ()
            ),
            "ElapsedSeconds": round(
                monotonic() - MaterializationStarted,
                6,
            ),
            "IdentityVerified": True,
            "RankingVerified": True,
            "ScoringIsolationProofReused": True,
            "ScoringMandatoryAccessProofReused": True,
        })
        return Materialized

    def _PlacementCandidatesForRouting():
        nonlocal CandidateRecords, OrderedPlacements
        nonlocal LastRoutingError, LastStructuredRoutingError
        nonlocal NeedsFeedbackPlacementGeneration
        nonlocal PendingJointPlacementStates
        nonlocal JointPortfolioSliceSeconds
        nonlocal JointPortfolioPrimaryCandidateId
        nonlocal LastAttemptedCandidate
        nonlocal PendingSamePlacementRoutingControlRetry
        nonlocal ActiveJointPortfolioIdentityFingerprint
        nonlocal LastCompletedAssignmentCutError
        nonlocal TerminalConstraintEpochRefreshPerformed
        nonlocal TerminalConstraintEpochPortfolioNeedsMaterialization
        nonlocal TerminalConstraintEpochPrimaryCandidateId
        nonlocal TerminalConstraintEpochPortfolioIdentityFingerprint
        AttemptedFingerprints: set[str] = set()
        AttemptedRoutingControlIdentities: set[
            RoutingControlAttemptIdentity
        ] = set()
        while True:
            # An authoritative topology cut is a scheduler boundary, not
            # ordinary feedback.  Once routing reports one, the candidate
            # that produced it has already consumed its slice; route the
            # fresh exact-cut portfolio before an older sibling can consume
            # the remaining shared deadline and replace that cut again.
            # PendingTopologyCutEpoch is only set for the typed topology
            # trigger, so compact/ripple designs retain their existing
            # candidate order byte-for-byte.
            if PendingTopologyCutEpoch is not None:
                if Deadline.IsExpired():
                    return
                Request = _TakeNextDeferredRequest()
                if Request is None:
                    return
                try:
                    _TryPlacement(
                        Request,
                        # A fresh topology epoch already consumed its unique
                        # cut/constraint/ownership admission and cancelled the
                        # stale portfolio.  It replaces late broad work even
                        # when the ordinary generator counter is exhausted;
                        # the epoch set, retained-state bound, and shared
                        # deadline remain its termination controls.
                        CountPlacementGenerationAttempt=False,
                    )
                except RoutingStageError as Error:
                    LastRoutingError = Error
                    LastStructuredRoutingError = Error
                    return
                CandidateRecords = _BuildCandidateRecords()
                OrderedPlacements = CandidateRecords[
                    : RetainedRoutingCandidateLimit(CandidateRecords)
                ]
                PlacementFeedback[:] = [
                    Candidate.ToDictionary() for Candidate in CandidateRecords
                ]
                continue
            # Retain only the recipe state for untried joint orientations.
            # A dense RCA8 placement owns large exact-route/resource graphs;
            # keeping six materialized copies alive can exhaust the process
            # before the first bounded routing slice begins.
            CurrentConstraintFingerprint = (
                PlacementAssignmentConstraints.Fingerprint
            )
            ConstraintIdentityActive = (
                PlacementAssignmentConstraintsAreActive(
                    PlacementAssignmentConstraints
                )
            )
            # A candidate-starvation cut can replace the live cut while the
            # remaining siblings still belong to the valid cumulative
            # constraint epoch. Filter exact joint/access candidates by that
            # epoch, not by the transient live cut, so current siblings
            # advance and pre-cut geometries cannot outrank them. Non-joint
            # unpacked/broad candidates remain available only after exact-cut
            # feedback generation has had its normal priority.
            StaleMaterializedFingerprints = {
                Fingerprint
                for Fingerprint, (
                    SourceGenerator,
                    _CandidateSpacing,
                    Candidate,
                ) in UniquePlacements.items()
                if Fingerprint not in AttemptedFingerprints
                if bool(
                    dict(
                        Candidate.Placed.LocalRouteDiagnostics or {}
                    ).get("__JointClusterPlacement__", {})
                )
                if not (
                    SourceGenerator == "row-beam-conflict-relocation"
                    and ActiveJointPortfolioIdentityFingerprint
                    and str(
                        dict(
                            dict(
                                Candidate.Placed.LocalRouteDiagnostics or {}
                            ).get("__PlacementRecipe__", {})
                        ).get(
                            "JointPortfolioIdentityFingerprint",
                            "",
                        )
                    )
                    == ActiveJointPortfolioIdentityFingerprint
                )
                if not PlacementConstraintFingerprintMatchesIdentity(
                    str(
                        dict(
                            dict(
                                Candidate.Placed.LocalRouteDiagnostics or {}
                            ).get("__PlacementRecipe__", {})
                        ).get(
                            "AssignmentConstraintFingerprint",
                            "",
                        )
                    ),
                    CurrentConstraintFingerprint,
                    ConstraintIdentityActive,
                )
            }
            if StaleMaterializedFingerprints:
                JointPlacementStateEvents.append({
                    "Status": "stale-materialized-candidates-discarded",
                    "DiscardedCandidateCount": len(
                        StaleMaterializedFingerprints
                    ),
                    "DiscardedCandidateIds": [
                        f"Placement-{Fingerprint[:12]}"
                        for Fingerprint in sorted(
                            StaleMaterializedFingerprints
                        )
                    ],
                    "CurrentConstraintFingerprint": (
                        CurrentConstraintFingerprint
                    ),
                })
            RemovedFingerprints = (
                AttemptedFingerprints
                | StaleMaterializedFingerprints
            )
            if RemovedFingerprints:
                for Fingerprint in RemovedFingerprints:
                    _DiscardPlacementFingerprint(Fingerprint)
                CandidateRecords = _BuildCandidateRecords()
                OrderedPlacements = CandidateRecords[
                    : RetainedRoutingCandidateLimit(CandidateRecords)
                ]
            ImmediateStructuredCutRelocation = (
                RequiresImmediateAssignmentCutRelocation(
                    CurrentPlacementAssignmentCut
                )
            )
            CurrentCutFingerprint = (
                CurrentPlacementAssignmentCut.ConflictFingerprint
                if CurrentPlacementAssignmentCut is not None
                else ""
            )
            HasCurrentPendingJointState = (
                HasCurrentPendingJointPlacementState(
                    PendingJointPlacementStates,
                    CurrentCutFingerprint,
                    CurrentConstraintFingerprint,
                )
            )
            HasCurrentMaterializedJointCandidate = (
                HasCurrentMaterializedJointPlacementCandidate(
                    OrderedPlacements,
                    AttemptedFingerprints,
                    CurrentCutFingerprint,
                    CurrentConstraintFingerprint,
                )
            )
            HasActiveMaterializedJointCandidate = (
                HasActiveMaterializedJointPlacementCandidate(
                    CandidateRecords,
                    AttemptedFingerprints,
                    ActiveJointPortfolioIdentityFingerprint,
                )
            )
            HasActivePendingJointState = (
                bool(ActiveJointPortfolioIdentityFingerprint)
                and any(
                    BuildPendingJointPlacementPortfolioFingerprint(State)
                    == ActiveJointPortfolioIdentityFingerprint
                    for State in PendingJointPlacementStates
                )
            )
            if (
                ActiveJointPortfolioIdentityFingerprint
                and not HasActiveMaterializedJointCandidate
                and not HasActivePendingJointState
            ):
                JointPlacementStateEvents.append({
                    "Status": "active-joint-portfolio-exhausted",
                    "PortfolioIdentityFingerprint": (
                        ActiveJointPortfolioIdentityFingerprint
                    ),
                })
                ActiveJointPortfolioIdentityFingerprint = ""
                if DeferredActivePortfolioAssignmentCuts:
                    # Every remaining retained recipe either routed and
                    # produced an authoritative lease cut or failed exact
                    # placement prescreening. Replay the newest deferred
                    # failure through the ordinary cut path; that path first
                    # commits the earlier sibling cuts, then opens one
                    # aggregate constrained geometry epoch.
                    TerminalEvidence = (
                        DeferredActivePortfolioAssignmentCuts.pop()
                    )
                    JointPlacementStateEvents.append({
                        "Status": (
                            "active-portfolio-exhausted-committing-cuts"
                        ),
                        "DeferredCutCount": (
                            len(DeferredActivePortfolioAssignmentCuts) + 1
                        ),
                        "TerminalCandidateId": (
                            TerminalEvidence.SourceCandidateId
                        ),
                        "NextAction": "open-aggregate-geometry-epoch",
                    })
                    _RecordAssignmentCut(
                        TerminalEvidence.Error,
                        TerminalEvidence.Candidate,
                    )
                    continue
            if (
                NeedsFeedbackPlacementGeneration
                and not HasActiveMaterializedJointCandidate
                and (
                    (
                        ImmediateStructuredCutRelocation
                        and not HasCurrentPendingJointState
                        and not HasCurrentMaterializedJointCandidate
                    )
                    or (
                        not PendingJointPlacementStates
                        and not any(
                            Candidate.PlacementFingerprint
                            not in AttemptedFingerprints
                            and Candidate.JointPortfolioCandidate
                            for Candidate in OrderedPlacements
                        )
                    )
                )
                and (
                    (
                        PlacementRelocationSignals
                        and (
                            PlacementRelocationPrioritySignals
                            != LastRelocationPrioritySignalsUsed
                            or PlacementRequiredRelocationSignals
                            != LastRequiredRelocationSignalsUsed
                            or PlacementAssignmentConstraints.Fingerprint
                            != LastAssignmentConstraintFingerprintUsed
                        )
                    )
                    or bool(AttemptedFingerprints)
                )
            ):
                if JointPortfolioPrimaryCandidateId is not None:
                    # The retained joint portfolio is the complete bounded
                    # orientation/slot hypothesis set for this deadline.
                    # A global capacity cut after every member has routed is
                    # evidence about that portfolio, not authorization to
                    # start an unrelated deferred generator and replace the
                    # typed cut with a deadline timeout.
                    JointPlacementStateEvents.append({
                        "Status": (
                            "portfolio-preempted-exact-assignment-cut"
                            if ImmediateStructuredCutRelocation
                            else "portfolio-exhausted-global-capacity-cut"
                        ),
                        "AttemptedCandidateCount": len(AttemptedFingerprints),
                        "DeferredGeneratorSuppressed": False,
                        "NextAction": "generate-exact-cut-relocation",
                        "AssignmentCut": (
                            CurrentPlacementAssignmentCut.ToDictionary()
                            if CurrentPlacementAssignmentCut is not None
                            else None
                        ),
                    })
                    JointPortfolioPrimaryCandidateId = None
                CurrentConstraintFingerprint = (
                    PlacementAssignmentConstraints.Fingerprint
                )
                if CurrentCutFingerprint:
                    StalePendingCount = sum(
                        1
                        for State in PendingJointPlacementStates
                        if (
                            not PendingJointPlacementStateMatchesIdentity(
                                State,
                                CurrentCutFingerprint,
                                CurrentConstraintFingerprint,
                            )
                        )
                    )
                    if StalePendingCount:
                        PendingJointPlacementStates[:] = [
                            State
                            for State in PendingJointPlacementStates
                            if PendingJointPlacementStateMatchesIdentity(
                                State,
                                CurrentCutFingerprint,
                                CurrentConstraintFingerprint,
                            )
                        ]
                        JointPlacementStateEvents.append({
                            "Status": "stale-cut-portfolios-discarded",
                            "DiscardedStateCount": StalePendingCount,
                            "CurrentConflictFingerprint": (
                                CurrentCutFingerprint
                            ),
                            "CurrentConstraintFingerprint": (
                                CurrentConstraintFingerprint
                            ),
                        })
                # A typed fixed-access cut is geometry feedback. Generate its
                # deterministic packed repair before routing an unrelated
                # retained placement, which would consume the shared deadline
                # and dilute the cut.
                NeedsFeedbackPlacementGeneration = False
                PreferDirectOnly = (
                    not ImmediateStructuredCutRelocation
                    and LastStructuredRoutingError is not None
                    and LastAttemptedCandidate is not None
                    and FailurePrefersDirectOnlyPlacement(
                        LastStructuredRoutingError.Failure,
                        LastAttemptedCandidate,
                    )
                )
                DirectOnlyPrioritySignals = (
                    ExtractCandidateStarvationSignals(
                        LastStructuredRoutingError.Failure
                    )
                    if (
                        PreferDirectOnly
                        and LastStructuredRoutingError is not None
                        # Candidate-starvation ownership steering was added
                        # for the reconvergent access portfolio.  Feeding it
                        # into an ordinary compact ripple retry changes the
                        # historical direct-only geometry, even though that
                        # circuit has no topology-pressure trigger.
                        and TopologyDemand.RequiresJointPortfolio
                    )
                    else None
                )
                Request = _TakeNextDeferredRequest(
                    PreferRelocation=not PreferDirectOnly,
                    PreferDirectOnly=PreferDirectOnly,
                    RequireExactCutBeforeBroad=(
                        ImmediateStructuredCutRelocation
                        or ConstraintIdentityActive
                    ),
                )
                GeneratedUniquePlacement = False
                LeadPortfolioConstraintFingerprint = (
                    PlacementAssignmentConstraints.Fingerprint
                )
                LeadPortfolioEpochChanged = False
                LeadPortfolioGenerationNotAfter = (
                    PlacementPortfolioGenerationNotAfter(
                        Policy,
                        DeadlineExpiresAt=Deadline.ExpiresAt,
                        CurrentTime=monotonic(),
                        RequiresDenseBoundaryRouting=DenseBoundaryRoutingReserve,
                    )
                    if (
                        Request is not None
                        and Request.PackingPolicy.EnableJointClusterOrientation
                    )
                    else None
                )
                if Request is not None:
                    try:
                        GeneratedUniquePlacement = _TryPlacement(
                            Request,
                            FixedRelocationPrioritySignals=(
                                DirectOnlyPrioritySignals
                                if (
                                    Request.SourceGenerator
                                    == "row-beam-direct-only"
                                )
                                else None
                            ),
                            PlacementGenerationNotAfter=(
                                LeadPortfolioGenerationNotAfter
                            ),
                        )
                    except RoutingStageError as Error:
                        LastRoutingError = Error
                        LastStructuredRoutingError = Error
                        return
                    LeadPortfolioEpochChanged = (
                        PlacementAssignmentConstraints.Fingerprint
                        != LeadPortfolioConstraintFingerprint
                    )
                    # The lead state of an exact-cut portfolio can itself
                    # fail immutable access prescreening.  Continue through
                    # that same retained portfolio until one genuinely new
                    # exact-legal geometry exists; do not route a stale
                    # pre-cut candidate merely because candidate zero was
                    # rejected.
                    while (
                        not GeneratedUniquePlacement
                        and CurrentCutFingerprint
                        and not LeadPortfolioEpochChanged
                        and (
                            LeadPortfolioGenerationNotAfter is None
                            or monotonic()
                            < LeadPortfolioGenerationNotAfter
                        )
                    ):
                        MatchingStateIndex = next(
                            (
                                Index
                                for Index, State in enumerate(
                                    PendingJointPlacementStates
                                )
                                if PendingJointPlacementStateMatchesIdentity(
                                    State,
                                    CurrentCutFingerprint,
                                    CurrentConstraintFingerprint,
                                )
                            ),
                            None,
                        )
                        if MatchingStateIndex is None:
                            break
                        JointState = PendingJointPlacementStates.pop(
                            MatchingStateIndex
                        )
                        JointPlacementStateEvents.append({
                            "CandidateIndex": JointState.CandidateIndex,
                            "Status": (
                                "materializing-after-lead-prescreen-rejection"
                            ),
                            "SourceGenerator": (
                                JointState.Request.SourceGenerator
                            ),
                            "RoutingSpacing": JointState.RoutingSpacing,
                            "ConflictFingerprint": CurrentCutFingerprint,
                        })
                        try:
                            GeneratedUniquePlacement = _TryPlacement(
                                JointState.Request,
                                JointPlacementCandidateIndex=(
                                    JointState.CandidateIndex
                                ),
                                FixedRelocationVariant=(
                                    JointState.RelocationVariant
                                ),
                                FixedCandidateSpacing=(
                                    JointState.RoutingSpacing
                                ),
                                FixedRelocationSignals=(
                                    JointState.RelocationSignals
                                ),
                                FixedRelocationPrioritySignals=(
                                    JointState.RelocationPrioritySignals
                                ),
                                FixedRequiredRelocationSignals=(
                                    JointState.RequiredRelocationSignals
                                ),
                                FixedAssignmentCut=JointState.AssignmentCut,
                                FixedAssignmentConstraints=(
                                    JointState.AssignmentConstraints
                                ),
                                FixedCoordinatedCandidateDiversificationSignals=(
                                    JointState
                                    .CoordinatedCandidateDiversificationSignals
                                ),
                                FixedTopologyCutFrontier=(
                                    JointState.TopologyCutFrontier
                                ),
                                MaterializeRoutingResources=False,
                                PlacementGenerationNotAfter=(
                                    LeadPortfolioGenerationNotAfter
                                ),
                            )
                        except RoutingStageError as Error:
                            LastRoutingError = Error
                            LastStructuredRoutingError = Error
                            return
                        LeadPortfolioEpochChanged = (
                            PlacementAssignmentConstraints.Fingerprint
                            != LeadPortfolioConstraintFingerprint
                        )
                    if (
                        not GeneratedUniquePlacement
                        and LeadPortfolioGenerationNotAfter is not None
                        and monotonic()
                        >= LeadPortfolioGenerationNotAfter
                    ):
                        JointPlacementStateEvents.append({
                            "Status": (
                                "lead-portfolio-stopped-routing-floor"
                            ),
                            "GenerationNotAfter": (
                                LeadPortfolioGenerationNotAfter
                            ),
                            "DeferredStateCount": len(
                                PendingJointPlacementStates
                            ),
                        })
                    CandidateRecords = _BuildCandidateRecords()
                    OrderedPlacements = CandidateRecords[
                        : RetainedRoutingCandidateLimit(CandidateRecords)
                    ]
                    PlacementFeedback[:] = [
                        Candidate.ToDictionary()
                        for Candidate in CandidateRecords
                    ]
                    if LeadPortfolioEpochChanged:
                        JointPlacementStateEvents.append({
                            "Status": "lead-portfolio-constraint-epoch-changed",
                            "ConstraintFingerprintBefore": (
                                LeadPortfolioConstraintFingerprint
                            ),
                            "ConstraintFingerprintAfter": (
                                PlacementAssignmentConstraints.Fingerprint
                            ),
                            "NextAction": "restart-scheduler-before-broad",
                        })
                        continue
            DensePrimaryPendingRouting = bool(
                DenseBoundaryRoutingReserve
                and any(
                    Candidate.JointPortfolioCandidate
                    and Candidate.PlacementFingerprint
                    not in AttemptedFingerprints
                    and bool(
                        dict(
                            Candidate.Placement.Placed
                            .LocalRouteDiagnostics or {}
                        ).get("__PlacementRecipe__", {}).get(
                            "IsPostPinBankRepairEpoch",
                            False,
                        )
                    )
                    for Candidate in CandidateRecords
                )
            )
            if (
                PendingJointPlacementStates
                and not DensePrimaryPendingRouting
                and (
                    not HasActiveMaterializedJointCandidate
                    or (
                        TerminalConstraintEpochPortfolioNeedsMaterialization
                    )
                )
            ):
                MaterializingTerminalConstraintEpochPortfolio = (
                    TerminalConstraintEpochPortfolioNeedsMaterialization
                )
                TerminalConstraintEpochPortfolioNeedsMaterialization = False
                # Materialize one immutable six-state portfolio at a time so
                # legality/scarcity/demand/footprint/HPWL records from another
                # cut, constraint epoch, or relocation variant cannot be
                # described or ranked as part of this one. Routing graphs
                # remain lazy.
                PortfolioIdentity = (
                    BuildPendingJointPlacementPortfolioIdentity(
                        PendingJointPlacementStates[0]
                    )
                )
                PortfolioIdentityFingerprint = BuildStableFingerprint(
                    repr(PortfolioIdentity)
                )
                PortfolioGenerationNotAfter = (
                    JointPortfolioGenerationNotAfterByIdentity.setdefault(
                        PortfolioIdentity,
                        PlacementPortfolioGenerationNotAfter(
                            Policy,
                            DeadlineExpiresAt=Deadline.ExpiresAt,
                            CurrentTime=monotonic(),
                            RequiresDenseBoundaryRouting=(
                                DenseBoundaryRoutingReserve
                            ),
                        ),
                    )
                )
                PortfolioStates = [
                    State
                    for State in PendingJointPlacementStates
                    if (
                        BuildPendingJointPlacementPortfolioIdentity(State)
                        == PortfolioIdentity
                    )
                ]
                PendingJointPlacementStates[:] = [
                    State
                    for State in PendingJointPlacementStates
                    if (
                        BuildPendingJointPlacementPortfolioIdentity(State)
                        != PortfolioIdentity
                    )
                ]
                UniquePortfolioStates: list[
                    PendingJointPlacementState
                ] = []
                SeenPortfolioStateKeys: set[
                    tuple[PendingJointPlacementPortfolioIdentity, int]
                ] = set()
                for State in PortfolioStates:
                    StateKey = BuildPendingJointPlacementStateKey(State)
                    if (
                        StateKey in SeenPortfolioStateKeys
                        or StateKey in MaterializedJointPlacementStateKeys
                    ):
                        continue
                    SeenPortfolioStateKeys.add(StateKey)
                    UniquePortfolioStates.append(State)
                PortfolioStates = sorted(
                    UniquePortfolioStates,
                    key=lambda State: State.CandidateIndex,
                )
                PortfolioConstraintFingerprint = (
                    PlacementAssignmentConstraints.Fingerprint
                )
                PortfolioEpochChanged = False
                for PortfolioStateIndex, JointState in enumerate(
                    PortfolioStates
                ):
                    if monotonic() >= PortfolioGenerationNotAfter:
                        DeferredStates = PortfolioStates[
                            PortfolioStateIndex:
                        ]
                        if DeferredStates:
                            # PortfolioStates were removed from the shared
                            # pending queue before materialization began.  The
                            # routing floor protects time for an exact route;
                            # it must not silently discard the exact,
                            # access-distinct states that have not yet been
                            # materialized.  Return the untouched suffix to
                            # the front of the queue so the existing
                            # single-state path below can spend that reserve
                            # on one bounded exact candidate before any broad
                            # generator is considered.
                            PendingJointPlacementStates[:] = (
                                RetainUnmaterializedJointPlacementStates(
                                    PendingJointPlacementStates,
                                    DeferredStates,
                                    MaterializedJointPlacementStateKeys,
                                )
                            )
                        JointPlacementStateEvents.append({
                            "Status": (
                                "portfolio-materialization-stopped-routing-floor"
                            ),
                            "PortfolioIdentityFingerprint": (
                                PortfolioIdentityFingerprint
                            ),
                            "StoppedBeforeCandidateIndex": (
                                JointState.CandidateIndex
                            ),
                            "UnmaterializedStateCount": (
                                len(DeferredStates)
                            ),
                            "GenerationNotAfter": (
                                PortfolioGenerationNotAfter
                            ),
                            "BroadGenerationDeferred": bool(
                                DeferredStates
                            ),
                            "NextAction": (
                                "materialize-retained-exact-state"
                                if DeferredStates
                                else "route-materialized-exact-state"
                            ),
                        })
                        break
                    StateKey = BuildPendingJointPlacementStateKey(
                        JointState
                    )
                    MaterializedJointPlacementStateKeys.add(StateKey)
                    JointPlacementStateEvents.append({
                        "CandidateIndex": JointState.CandidateIndex,
                        "Status": "materializing",
                        "SourceGenerator": (
                            JointState.Request.SourceGenerator
                        ),
                        "RoutingSpacing": JointState.RoutingSpacing,
                    })
                    try:
                        _TryPlacement(
                            JointState.Request,
                            JointPlacementCandidateIndex=(
                                JointState.CandidateIndex
                            ),
                            FixedRelocationVariant=(
                                JointState.RelocationVariant
                            ),
                            FixedCandidateSpacing=JointState.RoutingSpacing,
                            FixedRelocationSignals=(
                                JointState.RelocationSignals
                            ),
                            FixedRelocationPrioritySignals=(
                                JointState.RelocationPrioritySignals
                            ),
                            FixedRequiredRelocationSignals=(
                                JointState.RequiredRelocationSignals
                            ),
                            FixedAssignmentCut=JointState.AssignmentCut,
                            FixedAssignmentConstraints=(
                                JointState.AssignmentConstraints
                            ),
                            FixedCoordinatedCandidateDiversificationSignals=(
                                JointState
                                .CoordinatedCandidateDiversificationSignals
                            ),
                            FixedTopologyCutFrontier=(
                                JointState.TopologyCutFrontier
                            ),
                            MaterializeRoutingResources=False,
                            PlacementGenerationNotAfter=(
                                PortfolioGenerationNotAfter
                            ),
                        )
                    except RoutingStageError as Error:
                        LastRoutingError = Error
                        LastStructuredRoutingError = Error
                        return
                    CurrentConstraintFingerprint = (
                        PlacementAssignmentConstraints.Fingerprint
                    )
                    if (
                        CurrentConstraintFingerprint
                        != PortfolioConstraintFingerprint
                    ):
                        PortfolioEpochChanged = True
                        JointPlacementStateEvents.append({
                            "Status": (
                                "portfolio-constraint-epoch-changed"
                            ),
                            "PortfolioIdentityFingerprint": (
                                PortfolioIdentityFingerprint
                            ),
                            "ConstraintFingerprintBefore": (
                                PortfolioConstraintFingerprint
                            ),
                            "ConstraintFingerprintAfter": (
                                CurrentConstraintFingerprint
                            ),
                            "DiscardedUnmaterializedStateCount": (
                                len(PortfolioStates)
                                - len(MaterializedJointPlacementStateKeys
                                      & SeenPortfolioStateKeys)
                            ),
                            "NextAction": (
                                "restart-exact-cut-placement"
                            ),
                        })
                        break
                    if (
                        DenseBoundaryRoutingReserve
                        and (
                            CurrentCutFingerprint
                            or JointState.AssignmentCut is not None
                        )
                    ):
                        # The dense proof scheduler has already consumed its
                        # bounded share. Retain every sibling, but do not
                        # materialize a second relocated geometry before the
                        # ranked first state receives the endgame route
                        # budget that the proof reserved for it.
                        DeferredStates = PortfolioStates[
                            PortfolioStateIndex + 1:
                        ]
                        if DeferredStates:
                            PendingJointPlacementStates[:0] = DeferredStates
                        JointPlacementStateEvents.append({
                            "Status": (
                                "dense-endgame-primary-materialized"
                            ),
                            "PortfolioIdentityFingerprint": (
                                PortfolioIdentityFingerprint
                            ),
                            "CandidateId": (
                                CandidateRecords[-1].CandidateId
                                if CandidateRecords
                                else None
                            ),
                            "DeferredStateCount": len(DeferredStates),
                            "RemainingRoutingSeconds": round(
                                Deadline.RemainingSeconds(),
                                6,
                            ),
                        })
                        break
                if PortfolioEpochChanged:
                    # The top of the scheduler removes materialized candidates
                    # from the superseded constraint epoch before selecting
                    # anything to route.
                    continue
                CandidateRecords = _BuildCandidateRecords()
                if (
                    PortfolioIdentity.SourceGenerator
                    == "row-beam-conflict-relocation"
                    and any(
                        Candidate.JointPortfolioIdentityFingerprint
                        == PortfolioIdentityFingerprint
                        for Candidate in CandidateRecords
                    )
                ):
                    ActiveJointPortfolioIdentityFingerprint = (
                        PortfolioIdentityFingerprint
                    )
                OrderedPlacements = CandidateRecords[
                    : RetainedRoutingCandidateLimit(CandidateRecords)
                ]
                JointPortfolioSliceSeconds = (
                    Deadline.RemainingSeconds()
                    * TopologyPortfolioRoutingFraction(
                        HasRemainingPlacementAlternative=(
                            len(OrderedPlacements) > 1
                        ),
                        AttemptedCandidateCount=0,
                    )
                )
                JointPortfolioPrimaryCandidateId = (
                    OrderedPlacements[0].CandidateId
                    if OrderedPlacements
                    else None
                )
                if MaterializingTerminalConstraintEpochPortfolio:
                    TerminalConstraintEpochPrimaryCandidateId = (
                        JointPortfolioPrimaryCandidateId
                    )
                JointPlacementStateEvents.append({
                    "Status": "portfolio-materialized",
                    "CandidateCount": len(OrderedPlacements),
                    "EqualRoutingSliceSeconds": round(
                        JointPortfolioSliceSeconds,
                        6,
                    ),
                    "PrimaryCandidateId": JointPortfolioPrimaryCandidateId,
                    "PortfolioIdentityFingerprint": (
                        PortfolioIdentityFingerprint
                    ),
                })
                PlacementFeedback[:] = [
                    Candidate.ToDictionary() for Candidate in CandidateRecords
                ]
            Pending = [
                Candidate
                for Candidate in OrderedPlacements
                if Candidate.PlacementFingerprint not in AttemptedFingerprints
            ]
            ActivePending = [
                Candidate
                for Candidate in CandidateRecords
                if (
                    Candidate.PlacementFingerprint
                    not in AttemptedFingerprints
                    and PlacementCandidateMatchesActiveJointPortfolio(
                        Candidate,
                        ActiveJointPortfolioIdentityFingerprint,
                    )
                )
            ]
            if ActivePending:
                Pending = ActivePending
            PrescreenRejectedPending = [
                Candidate
                for Candidate in Pending
                if not PlacementCandidateIsExactAccessLegal(Candidate)
            ]
            if PrescreenRejectedPending:
                for Candidate in PrescreenRejectedPending:
                    AttemptedFingerprints.add(
                        Candidate.PlacementFingerprint
                    )
                    CandidateDemand = Candidate.TopologyDemand
                    assert CandidateDemand is not None
                    JointPlacementStateEvents.append({
                        "Status": (
                            "exact-mandatory-access-prescreen-rejected"
                        ),
                        "CandidateId": Candidate.CandidateId,
                        "SourceGenerator": Candidate.SourceGenerator,
                        "MandatoryAccessConflictResources": (
                            CandidateDemand
                            .MandatoryAccessConflictResources
                        ),
                        "MandatoryAccessConflictSignals": list(
                            CandidateDemand
                            .MandatoryAccessConflictSignals
                        ),
                        "MandatoryAccessConflictFingerprint": (
                            CandidateDemand
                            .MandatoryAccessConflictFingerprint
                        ),
                        "JointOrderKey": list(
                            CandidateDemand.JointOrderKey
                        ),
                        "NextAction": (
                            "route-next-exact-access-legal-candidate"
                        ),
                    })
                    PlacementGenerationDecisions.append({
                        "Result": (
                            "exact-mandatory-access-prescreen-rejected"
                        ),
                        "CandidateId": Candidate.CandidateId,
                        "SourceGenerator": Candidate.SourceGenerator,
                        "MandatoryAccessConflictResources": (
                            CandidateDemand
                            .MandatoryAccessConflictResources
                        ),
                        "MandatoryAccessConflictSignals": list(
                            CandidateDemand
                            .MandatoryAccessConflictSignals
                        ),
                        "MandatoryAccessConflictFingerprint": (
                            CandidateDemand
                            .MandatoryAccessConflictFingerprint
                        ),
                    })
                # Recompute active-portfolio and remaining-candidate state
                # without the proved-illegal geometries before allocating any
                # routing slice.
                continue
            if Pending:
                FeedbackPending = [
                    Candidate
                    for Candidate in Pending
                    if Candidate.SourceGenerator
                    == "row-beam-conflict-relocation"
                ]
                if FeedbackPending and not ActivePending:
                    Pending = FeedbackPending
                NextCandidate = Pending[0]
                if (
                    TerminalConstraintEpochPortfolioIdentityFingerprint
                    and NextCandidate
                    .JointPortfolioIdentityFingerprint
                    == TerminalConstraintEpochPortfolioIdentityFingerprint
                ):
                    IsTerminalRankedPrimary = (
                        ShouldGiveRankedJointPortfolioLeadSlice(
                            ActiveRelocatedPortfolioCandidate=True,
                            CandidateId=NextCandidate.CandidateId,
                            PrimaryCandidateId=(
                                TerminalConstraintEpochPrimaryCandidateId
                            ),
                        )
                    )
                    JointPlacementStateEvents.append({
                        "Status": (
                            "terminal-constraint-epoch-ranked-candidate-"
                            "selected"
                            if IsTerminalRankedPrimary
                            else (
                                "terminal-constraint-epoch-sibling-"
                                "selected"
                            )
                        ),
                        "CandidateId": NextCandidate.CandidateId,
                        "RankedPrimaryCandidateId": (
                            TerminalConstraintEpochPrimaryCandidateId
                        ),
                        "PortfolioIdentityFingerprint": (
                            TerminalConstraintEpochPortfolioIdentityFingerprint
                        ),
                        "RemainingRuntimeSeconds": round(
                            Deadline.RemainingSeconds(),
                            6,
                        ),
                    })
                if ShouldRefreshTerminalActiveJointPlacementConstraintEpoch(
                    ActivePendingCount=len(ActivePending),
                    CandidateSourceGenerator=(
                        NextCandidate.SourceGenerator
                    ),
                    CandidateMatchesActivePortfolio=(
                        PlacementCandidateMatchesActiveJointPortfolio(
                            NextCandidate,
                            ActiveJointPortfolioIdentityFingerprint,
                        )
                    ),
                    CandidateConstraintFingerprint=(
                        NextCandidate.AssignmentConstraintFingerprint
                    ),
                    CurrentConstraintFingerprint=(
                        PlacementAssignmentConstraints.Fingerprint
                    ),
                    RefreshAlreadyPerformed=(
                        TerminalConstraintEpochRefreshPerformed
                    ),
                ) and not (
                    DenseBoundaryRoutingReserve
                    and bool(ActivePending)
                ):
                    OriginalFingerprint = (
                        NextCandidate.PlacementFingerprint
                    )
                    OriginalState = (
                        JointPlacementStateByPlacementFingerprint.get(
                            OriginalFingerprint
                        )
                    )
                    if OriginalState is None:
                        Failure = RoutingFailure(
                            Reason=RoutingFailureReason.PlacementOverlap,
                            Stage="PlacementConstraintEpochRefresh",
                            Detail=(
                                "the terminal retained placement was missing "
                                "its immutable recipe state"
                            ),
                            RepairActions=(
                                "InspectJointPlacementStateRetention",
                            ),
                            Diagnostics={
                                "CandidateId": NextCandidate.CandidateId,
                                "PlacementFingerprint": (
                                    OriginalFingerprint
                                ),
                                "CurrentConstraintFingerprint": (
                                    PlacementAssignmentConstraints.Fingerprint
                                ),
                            },
                        )
                        Error = RoutingStageError(Failure)
                        PlacementGenerationFailures.append({
                            "SourceGenerator": (
                                NextCandidate.SourceGenerator
                            ),
                            "RoutingSpacing": (
                                NextCandidate.RoutingSpacing
                            ),
                            "Failure": Failure.Detail,
                            "Diagnostics": Failure.ToDictionary(),
                        })
                        LastRoutingError = Error
                        LastStructuredRoutingError = Error
                        LastCompletedAssignmentCutError = None
                        return
                    ReboundState = RebindTerminalJointPlacementConstraintEpoch(
                        OriginalState,
                        CurrentPlacementAssignmentCut,
                        PlacementAssignmentConstraints,
                    )
                    ReboundPortfolioFingerprint = (
                        BuildPendingJointPlacementPortfolioFingerprint(
                            ReboundState
                        )
                    )
                    TerminalConstraintEpochRefreshPerformed = True
                    JointPlacementStateEvents.append({
                        "Status": (
                            "terminal-constraint-epoch-refresh-started"
                        ),
                        "CandidateId": NextCandidate.CandidateId,
                        "CandidateIndex": ReboundState.CandidateIndex,
                        "PlacementFingerprintBefore": (
                            OriginalFingerprint
                        ),
                        "AssignmentConstraintFingerprintBefore": (
                            OriginalState.AssignmentConstraints.Fingerprint
                        ),
                        "AssignmentConstraintFingerprintAfter": (
                            ReboundState.AssignmentConstraints.Fingerprint
                        ),
                        "PortfolioIdentityFingerprintBefore": (
                            ActiveJointPortfolioIdentityFingerprint
                        ),
                        "PortfolioIdentityFingerprintAfter": (
                            ReboundPortfolioFingerprint
                        ),
                        "TotalRelocationGenerationCount": (
                            TotalRelocationGenerationCount
                        ),
                    })
                    _DiscardPlacementFingerprint(OriginalFingerprint)
                    RefreshGenerationNotAfter = (
                        PlacementPortfolioGenerationNotAfter(
                            Policy,
                            DeadlineExpiresAt=Deadline.ExpiresAt,
                            CurrentTime=monotonic(),
                            RequiresDenseBoundaryRouting=(
                                DenseBoundaryRoutingReserve
                            ),
                        )
                    )
                    PendingRefreshStateKeysBefore = frozenset(
                        BuildPendingJointPlacementStateKey(State)
                        for State in PendingJointPlacementStates
                    )
                    try:
                        Refreshed = _TryPlacement(
                            ReboundState.Request,
                            JointPlacementCandidateIndex=(
                                ReboundState.CandidateIndex
                            ),
                            FixedRelocationVariant=(
                                ReboundState.RelocationVariant
                            ),
                            FixedCandidateSpacing=(
                                ReboundState.RoutingSpacing
                            ),
                            FixedRelocationSignals=(
                                ReboundState.RelocationSignals
                            ),
                            FixedRelocationPrioritySignals=(
                                ReboundState.RelocationPrioritySignals
                            ),
                            FixedRequiredRelocationSignals=(
                                ReboundState.RequiredRelocationSignals
                            ),
                            FixedAssignmentCut=(
                                ReboundState.AssignmentCut
                            ),
                            FixedAssignmentConstraints=(
                                ReboundState.AssignmentConstraints
                            ),
                            FixedCoordinatedCandidateDiversificationSignals=(
                                ReboundState
                                .CoordinatedCandidateDiversificationSignals
                            ),
                            FixedTopologyCutFrontier=(
                                ReboundState.TopologyCutFrontier
                            ),
                            MaterializeRoutingResources=False,
                            PlacementGenerationNotAfter=(
                                RefreshGenerationNotAfter
                            ),
                            CountPlacementGenerationAttempt=False,
                            QueueRetainedJointPortfolioStates=True,
                        )
                    except RoutingStageError as Error:
                        LastRoutingError = Error
                        LastStructuredRoutingError = Error
                        LastCompletedAssignmentCutError = None
                        return
                    RefreshedSiblingPortfolioFingerprint = (
                        SelectNewPendingJointPlacementPortfolioFingerprint(
                            PendingJointPlacementStates,
                            PendingRefreshStateKeysBefore,
                            ReboundState
                            .AssignmentConstraints.Fingerprint,
                        )
                    )
                    if (
                        not Refreshed
                        and RefreshedSiblingPortfolioFingerprint is not None
                    ):
                        # Candidate zero is only the deterministic entry point
                        # used to regenerate and score a complete constraint-
                        # epoch portfolio.  An exact mandatory-access rejection
                        # of that lead must not discard the access-distinct
                        # siblings it just queued; rank those siblings under
                        # the same cumulative constraints.
                        ActiveJointPortfolioIdentityFingerprint = (
                            RefreshedSiblingPortfolioFingerprint
                        )
                        TerminalConstraintEpochPortfolioIdentityFingerprint = (
                            RefreshedSiblingPortfolioFingerprint
                        )
                        TerminalConstraintEpochPortfolioNeedsMaterialization = (
                            True
                        )
                        TerminalConstraintEpochPrimaryCandidateId = None
                        JointPlacementStateEvents.append({
                            "Status": (
                                "terminal-constraint-epoch-lead-prescreen-"
                                "rejected"
                            ),
                            "CandidateIndex": (
                                ReboundState.CandidateIndex
                            ),
                            "AssignmentConstraintFingerprint": (
                                ReboundState
                                .AssignmentConstraints.Fingerprint
                            ),
                            "PortfolioIdentityFingerprint": (
                                RefreshedSiblingPortfolioFingerprint
                            ),
                            "RequestedPortfolioIdentityFingerprint": (
                                ReboundPortfolioFingerprint
                            ),
                            "PendingSiblingStateCount": sum(
                                BuildPendingJointPlacementPortfolioFingerprint(
                                    State
                                )
                                == RefreshedSiblingPortfolioFingerprint
                                for State in PendingJointPlacementStates
                            ),
                            "NextAction": (
                                "materialize-access-distinct-siblings"
                            ),
                        })
                        continue
                    if not Refreshed:
                        Failure = RoutingFailure(
                            Reason=RoutingFailureReason.PlacementOverlap,
                            Stage="PlacementConstraintEpochRefresh",
                            Detail=(
                                "the terminal retained placement could not "
                                "be rematerialized as a unique exact-legal "
                                "candidate under the current cumulative "
                                "assignment constraints"
                            ),
                            RepairActions=(
                                "InspectJointPlacementConstraintProjection",
                            ),
                            Diagnostics={
                                "CandidateIndex": (
                                    ReboundState.CandidateIndex
                                ),
                                "AssignmentConstraintFingerprint": (
                                    ReboundState
                                    .AssignmentConstraints.Fingerprint
                                ),
                                "PortfolioIdentityFingerprint": (
                                    ReboundPortfolioFingerprint
                                ),
                                "TotalRelocationGenerationCount": (
                                    TotalRelocationGenerationCount
                                ),
                            },
                        )
                        Error = RoutingStageError(Failure)
                        PlacementGenerationFailures.append({
                            "SourceGenerator": (
                                ReboundState.Request.SourceGenerator
                            ),
                            "RoutingSpacing": (
                                ReboundState.RoutingSpacing
                            ),
                            "JointPlacementCandidateIndex": (
                                ReboundState.CandidateIndex
                            ),
                            "Failure": Failure.Detail,
                            "Diagnostics": Failure.ToDictionary(),
                        })
                        LastRoutingError = Error
                        LastStructuredRoutingError = Error
                        LastCompletedAssignmentCutError = None
                        return
                    CandidateRecords = _BuildCandidateRecords()
                    OrderedPlacements = CandidateRecords[
                        : RetainedRoutingCandidateLimit(CandidateRecords)
                    ]
                    RefreshedCandidates = [
                        Candidate
                        for Candidate in CandidateRecords
                        if (
                            Candidate.SourceGenerator
                            == "row-beam-conflict-relocation"
                            and Candidate.AssignmentConstraintFingerprint
                            == PlacementAssignmentConstraints.Fingerprint
                            and (
                                JointPlacementStateByPlacementFingerprint.get(
                                    Candidate.PlacementFingerprint
                                )
                                is not None
                            )
                            and JointPlacementStateByPlacementFingerprint[
                                Candidate.PlacementFingerprint
                            ].CandidateIndex
                            == ReboundState.CandidateIndex
                        )
                    ]
                    if (
                        not RefreshedCandidates
                        or (
                            len(RefreshedCandidates) != 1
                            and not DenseBoundaryRoutingReserve
                        )
                    ):
                        Failure = RoutingFailure(
                            Reason=RoutingFailureReason.PlacementOverlap,
                            Stage="PlacementConstraintEpochRefresh",
                            Detail=(
                                "the terminal constraint-epoch refresh did "
                                "not publish exactly one active candidate"
                            ),
                            RepairActions=(
                                "InspectJointPlacementPortfolioIdentity",
                            ),
                            Diagnostics={
                                "PublishedCandidateCount": len(
                                    RefreshedCandidates
                                ),
                                "PortfolioIdentityFingerprint": (
                                    ReboundPortfolioFingerprint
                                ),
                            },
                        )
                        Error = RoutingStageError(Failure)
                        PlacementGenerationFailures.append({
                            "SourceGenerator": (
                                ReboundState.Request.SourceGenerator
                            ),
                            "RoutingSpacing": (
                                ReboundState.RoutingSpacing
                            ),
                            "JointPlacementCandidateIndex": (
                                ReboundState.CandidateIndex
                            ),
                            "Failure": Failure.Detail,
                            "Diagnostics": Failure.ToDictionary(),
                        })
                        LastRoutingError = Error
                        LastStructuredRoutingError = Error
                        LastCompletedAssignmentCutError = None
                        return
                    PlacementFeedback[:] = [
                        Candidate.ToDictionary()
                        for Candidate in CandidateRecords
                    ]
                    RefreshedCandidate = min(
                        RefreshedCandidates,
                        key=lambda Candidate: (
                            Candidate.JointExactScore,
                            Candidate.PlacementFingerprint,
                        ),
                    )
                    ActiveJointPortfolioIdentityFingerprint = (
                        RefreshedCandidate
                        .JointPortfolioIdentityFingerprint
                    )
                    TerminalConstraintEpochPortfolioIdentityFingerprint = (
                        ActiveJointPortfolioIdentityFingerprint
                    )
                    TerminalConstraintEpochPortfolioNeedsMaterialization = (
                        bool(PendingJointPlacementStates)
                    )
                    JointPlacementStateEvents.append({
                        "Status": (
                            "terminal-constraint-epoch-refresh-complete"
                        ),
                        "CandidateId": RefreshedCandidate.CandidateId,
                        "CandidateIndex": ReboundState.CandidateIndex,
                        "PlacementFingerprintBefore": (
                            OriginalFingerprint
                        ),
                        "PlacementFingerprintAfter": (
                            RefreshedCandidate.PlacementFingerprint
                        ),
                        "AssignmentConstraintFingerprint": (
                            RefreshedCandidate
                            .AssignmentConstraintFingerprint
                        ),
                        "PortfolioIdentityFingerprint": (
                            ActiveJointPortfolioIdentityFingerprint
                        ),
                        "RequestedPortfolioIdentityFingerprint": (
                            ReboundPortfolioFingerprint
                        ),
                        "TotalRelocationGenerationCount": (
                            TotalRelocationGenerationCount
                        ),
                        "DeferredEquivalentCandidateCount": (
                            len(RefreshedCandidates) - 1
                        ),
                    })
                    continue
                (
                    ActiveConstraintsRebound,
                    ActiveConstraintsFingerprint,
                ) = ApplyActivePlacementAssignmentConstraints(
                    NextCandidate.Placement,
                    PlacementAssignmentConstraints,
                )
                if ActiveConstraintsRebound:
                    JointPlacementStateEvents.append({
                        "Status": (
                            "active-assignment-constraints-rebound"
                        ),
                        "CandidateId": NextCandidate.CandidateId,
                        "PlacementFingerprint": (
                            NextCandidate.PlacementFingerprint
                        ),
                        "ActiveAssignmentConstraintFingerprint": (
                            ActiveConstraintsFingerprint
                        ),
                        "ActiveAssignmentConstraints": (
                            PlacementAssignmentConstraints.ToDictionary()
                        ),
                    })
                (
                    RoutingControlProfileRebound,
                    RoutingControlProfileFingerprint,
                ) = ApplyCoordinatedCandidateDiversificationProfile(
                    NextCandidate.Placement,
                    CandidateCoordinatedCandidateDiversificationSignals := (
                        PlacementCoordinatedCandidateDiversificationSignals
                    ),
                    EnableClusterPinBankRepair=(
                        bool(PlacementClusterPinBankRepairSignals)
                        and PlacementClusterPinBankRepairSignals.issubset(
                            CandidateCoordinatedCandidateDiversificationSignals
                        )
                    ),
                )
                if RoutingControlProfileRebound:
                    JointPlacementStateEvents.append({
                        "Status": (
                            "coordinated-routing-control-profile-rebound"
                        ),
                        "CandidateId": NextCandidate.CandidateId,
                        "PlacementFingerprint": (
                            NextCandidate.PlacementFingerprint
                        ),
                        "CoordinatedCandidateDiversificationSignals": sorted(
                            CandidateCoordinatedCandidateDiversificationSignals
                        ),
                        "RoutingControlProfileFingerprint": (
                            RoutingControlProfileFingerprint
                        ),
                    })
                if RoutingControlProfileFingerprint:
                    AttemptedRoutingControlIdentities.add(
                        RoutingControlAttemptIdentity(
                            PlacementFingerprint=(
                                NextCandidate.PlacementFingerprint
                            ),
                            RoutingControlProfileFingerprint=(
                                RoutingControlProfileFingerprint
                            ),
                        )
                    )
                AttemptedFingerprints.add(NextCandidate.PlacementFingerprint)
                LastAttemptedCandidate = NextCandidate
                yield NextCandidate
                RetryState = PendingSamePlacementRoutingControlRetry
                PendingSamePlacementRoutingControlRetry = None
                EffectiveRetryState = (
                    BuildSamePlacementRoutingControlRetryState(
                        PlacementFingerprint=(
                            RetryState.AttemptIdentity
                            .PlacementFingerprint
                        ),
                        AssignmentCutFingerprint=(
                            RetryState.AssignmentCutFingerprint
                        ),
                        Signals=(
                            *CandidateCoordinatedCandidateDiversificationSignals,
                            *RetryState.Profile.Signals,
                        ),
                        Evidence=RetryState.Evidence,
                    )
                    if RetryState is not None
                    else None
                )
                HasRemainingActivePortfolioSibling = (
                    HasActiveMaterializedJointPlacementCandidate(
                        CandidateRecords,
                        AttemptedFingerprints,
                        ActiveJointPortfolioIdentityFingerprint,
                    )
                )
                if ShouldDeferSamePlacementRoutingControlRetry(
                    EffectiveRetryState,
                    HasRemainingActivePortfolioSibling=(
                        HasRemainingActivePortfolioSibling
                    ),
                ) and not PlacementClusterPinBankRepairSignals:
                    # Preserve the deferred state. A later sibling may be
                    # the last access-distinct geometry, after which this
                    # exact same-placement retry must still remain available.
                    PendingSamePlacementRoutingControlRetry = RetryState
                    JointPlacementStateEvents.append({
                        "Status": (
                            "same-placement-routing-control-retry-deferred"
                        ),
                        "CandidateId": NextCandidate.CandidateId,
                        **EffectiveRetryState.ToDictionary(),
                        "NextAction": (
                            "route-active-access-distinct-sibling"
                        ),
                    })
                    continue
                if ShouldRetrySamePlacementRoutingControl(
                    EffectiveRetryState,
                    NextCandidate.PlacementFingerprint,
                    AttemptedRoutingControlIdentities,
                ):
                    assert EffectiveRetryState is not None
                    (
                        RetryProfileRebound,
                        RetryProfileFingerprint,
                    ) = ApplyCoordinatedCandidateDiversificationProfile(
                        NextCandidate.Placement,
                        frozenset(EffectiveRetryState.Profile.Signals),
                        EnableClusterPinBankRepair=(
                            bool(PlacementClusterPinBankRepairSignals)
                            and PlacementClusterPinBankRepairSignals.issubset(
                                EffectiveRetryState.Profile.Signals
                            )
                        ),
                        EnableRepeaterReadyPortalRepair=(
                            EffectiveRetryState.Evidence
                            .ExhaustedRepeaterAccessCut
                        ),
                    )
                    if (
                        RetryProfileRebound
                        and RetryProfileFingerprint
                        == EffectiveRetryState.AttemptIdentity
                        .RoutingControlProfileFingerprint
                    ):
                        AttemptedRoutingControlIdentities.add(
                            EffectiveRetryState.AttemptIdentity
                        )
                        JointPlacementStateEvents.append({
                            "Status": (
                                "same-placement-routing-control-retry"
                            ),
                            "CandidateId": NextCandidate.CandidateId,
                            **EffectiveRetryState.ToDictionary(),
                            "PreviousRoutingControlProfileFingerprint": (
                                RoutingControlProfileFingerprint
                            ),
                            "ReusedPlacedGeometry": True,
                            "ReusedRoutingResources": (
                                NextCandidate.PlacementFingerprint
                                in RoutingResourcesByFingerprint
                            ),
                            "RemainingRuntimeSeconds": round(
                                Deadline.RemainingSeconds(),
                                6,
                            ),
                            "NextAction": (
                                "route-same-placement-before-placement-generation"
                            ),
                        })
                        LastAttemptedCandidate = NextCandidate
                        yield NextCandidate
                        # Exactly one cut-scoped routing retry is admitted for
                        # this immutable placement. Any successor cut becomes
                        # geometry evidence rather than a recursive retry.
                        PendingSamePlacementRoutingControlRetry = None
                continue
            # A retained joint state is already exact-screened and has a
            # reserved deterministic routing slice.  Do not let feedback or
            # any later deferred recipe consume that slice first.
            if PendingJointPlacementStates:
                JointState = PendingJointPlacementStates.pop(0)
                JointPlacementStateEvents.append({
                    "CandidateIndex": JointState.CandidateIndex,
                    "Status": "materializing",
                    "SourceGenerator": JointState.Request.SourceGenerator,
                    "RoutingSpacing": JointState.RoutingSpacing,
                })
                try:
                    _TryPlacement(
                        JointState.Request,
                        JointPlacementCandidateIndex=(
                            JointState.CandidateIndex
                        ),
                        FixedRelocationVariant=JointState.RelocationVariant,
                        FixedCandidateSpacing=JointState.RoutingSpacing,
                        FixedRelocationSignals=(
                            JointState.RelocationSignals
                        ),
                        FixedRelocationPrioritySignals=(
                            JointState.RelocationPrioritySignals
                        ),
                        FixedRequiredRelocationSignals=(
                            JointState.RequiredRelocationSignals
                        ),
                        FixedAssignmentCut=JointState.AssignmentCut,
                        FixedAssignmentConstraints=(
                            JointState.AssignmentConstraints
                        ),
                        FixedCoordinatedCandidateDiversificationSignals=(
                            JointState.CoordinatedCandidateDiversificationSignals
                        ),
                        FixedTopologyCutFrontier=(
                            JointState.TopologyCutFrontier
                        ),
                    )
                except RoutingStageError as Error:
                    LastRoutingError = Error
                    LastStructuredRoutingError = Error
                    return
                CandidateRecords = _BuildCandidateRecords()
                OrderedPlacements = CandidateRecords[
                    : RetainedRoutingCandidateLimit(CandidateRecords)
                ]
                PlacementFeedback[:] = [
                    Candidate.ToDictionary() for Candidate in CandidateRecords
                ]
                continue
            if Deadline.IsExpired():
                return
            if ExactClusterInterfaceSolveEnabled:
                PlacementGenerationDecisions.append({
                    "Result": "dense-broad-generation-disabled",
                    "ExecutableLegacyRepairCascade": False,
                    "RemainingDeferredGeneratorCount": (
                        len(GenerationPlan.DeferredRequests)
                        - len(ConsumedDeferredRequestIndexes)
                    ),
                })
                return
            Request = _TakeNextDeferredRequest()
            if Request is None:
                return
            try:
                _TryPlacement(Request)
            except RoutingStageError as Error:
                LastRoutingError = Error
                LastStructuredRoutingError = Error
                return
            CandidateRecords = _BuildCandidateRecords()
            OrderedPlacements = CandidateRecords[
                : RetainedRoutingCandidateLimit(CandidateRecords)
            ]
            PlacementFeedback[:] = [
                Candidate.ToDictionary() for Candidate in CandidateRecords
            ]

    CandidateRoutingIterable: Iterable[PcbPlacementCandidate] = (
        _PlacementCandidatesForRouting()
    )
    if ExactClusterInterfaceSolveEnabled:
        RawInterfaceCandidates = tuple(islice(
            CandidateRoutingIterable,
            12,
        ))
        (
            InterfaceCandidates,
            InterfacePortfolioAudits,
        ) = SelectInterfaceDiversePlacementStates(
            RawInterfaceCandidates,
            MaximumStates=6,
        )
        InterfaceGeneratorRejectionAudit: list[dict[str, object]] = []
        for Decision in PlacementGenerationDecisions:
            Result = str(Decision.get("Result", ""))
            Classification = {
                "duplicate-placement": "duplicate-access-topology",
                "rejected-mandatory-access-conflict": (
                    "mandatory-access-unsat"
                ),
                "rejected-packed-area-growth": (
                    "pruned-by-scoring-budget"
                ),
                "skipped-routing-reserve": (
                    "pruned-by-scoring-budget"
                ),
            }.get(Result)
            if Classification is None:
                continue
            InterfaceGeneratorRejectionAudit.append({
                "Classification": Classification,
                "SourceResult": Result,
                "CandidateIndex": Decision.get(
                    "JointPlacementCandidateIndex"
                ),
                "PlacementFingerprint": Decision.get(
                    "PlacementFingerprint",
                    "",
                ),
                "PlacementRetentionFingerprint": Decision.get(
                    "PlacementRetentionFingerprint",
                    "",
                ),
                "Detail": (
                    "classified before exact interface portfolio retention"
                ),
            })
        for FailureEntry in PlacementGenerationFailures:
            FailureDiagnostics = FailureEntry.get("Diagnostics", {})
            FailureReason = (
                FailureDiagnostics.get("Reason", "")
                if isinstance(FailureDiagnostics, dict)
                else ""
            )
            if FailureReason != RoutingFailureReason.PlacementOverlap.value:
                continue
            InterfaceGeneratorRejectionAudit.append({
                "Classification": "geometric-overlap-illegal-placement",
                "SourceResult": "placement-generation-failure",
                "CandidateIndex": FailureEntry.get(
                    "JointPlacementCandidateIndex"
                ),
                "Detail": FailureEntry.get("Failure", ""),
            })
        for Candidate in RawInterfaceCandidates:
            CandidateJointDiagnostics = dict(
                Candidate.Placement.Placed.LocalRouteDiagnostics or {}
            ).get("__JointClusterPlacement__", {})
            if not isinstance(CandidateJointDiagnostics, dict):
                continue
            for Attrition in CandidateJointDiagnostics.get(
                "InterfacePortfolioAttrition",
                (),
            ):
                if not isinstance(Attrition, dict):
                    continue
                Record = {
                    "SearchCandidateIndex": Attrition.get(
                        "SearchCandidateIndex"
                    ),
                    "Classification": Attrition.get(
                        "Classification",
                        "pruned-by-scoring-budget",
                    ),
                    "SourceResult": (
                        "joint-interface-portfolio-attrition"
                    ),
                    "PlacementFingerprint": "",
                    "PlacementRetentionFingerprint": Attrition.get(
                        "InterfaceOwnershipFingerprint",
                        "",
                    ),
                    "Detail": (
                        "classified during exact joint placement screen"
                    ),
                }
                if Record not in InterfaceGeneratorRejectionAudit:
                    InterfaceGeneratorRejectionAudit.append(Record)
        ResolvedPortfolioAudits = list(InterfacePortfolioAudits)
        for Rejection in InterfaceGeneratorRejectionAudit:
            CandidateIndex = Rejection.get("CandidateIndex")
            if not isinstance(CandidateIndex, int):
                continue
            ResolvedPortfolioAudits = [
                Audit
                for Audit in ResolvedPortfolioAudits
                if not (
                    Audit.StateIndex == CandidateIndex
                    and not Audit.PlacementStateFingerprint
                )
            ]
            if any(
                Audit.StateIndex == CandidateIndex
                for Audit in ResolvedPortfolioAudits
            ):
                continue
            ResolvedPortfolioAudits.append(
                ClusterInterfacePortfolioStateAudit(
                    StateIndex=CandidateIndex,
                    Classification=str(
                        Rejection["Classification"]
                    ),
                    InterfaceTopologyFingerprint=str(
                        Rejection.get(
                            "PlacementRetentionFingerprint",
                            "",
                        )
                    ),
                    Detail=str(Rejection.get("Detail", "")),
                )
            )
        InterfaceSearchStateCount = 6
        for Candidate in RawInterfaceCandidates:
            CandidateJointDiagnostics = dict(
                Candidate.Placement.Placed.LocalRouteDiagnostics or {}
            ).get("__JointClusterPlacement__", {})
            if isinstance(CandidateJointDiagnostics, dict):
                InterfaceSearchStateCount = max(
                    InterfaceSearchStateCount,
                    int(CandidateJointDiagnostics.get(
                        "SearchRetentionLimit",
                        0,
                    )),
                )
        ClassifiedSearchIndexes = {
            Audit.StateIndex for Audit in ResolvedPortfolioAudits
        }
        for SearchCandidateIndex in range(InterfaceSearchStateCount):
            if SearchCandidateIndex in ClassifiedSearchIndexes:
                continue
            ResolvedPortfolioAudits.append(
                ClusterInterfacePortfolioStateAudit(
                    StateIndex=SearchCandidateIndex,
                    Classification="pruned-by-scoring-budget",
                    Detail=(
                        "bounded generator stopped after six legal "
                        "interface-distinct states"
                    ),
                )
            )
        InterfacePortfolioAudits = tuple(sorted(
            ResolvedPortfolioAudits,
            key=lambda Audit: (
                Audit.StateIndex,
                Audit.Classification,
            ),
        ))
        InterfaceFeasibleCandidates: list[
            tuple[
                tuple[object, ...],
                PcbPlacementCandidate,
                Any,
            ]
        ] = []
        InterfaceAttemptDiagnostics: list[dict[str, object]] = []
        InterfaceStateProofs: list[ClusterInterfaceStateProof] = []
        InterfacePlacementStatesByFingerprint: dict[
            str, ClusterInterfacePlacementState
        ] = {}
        ActiveComponentCutSignals: set[str] = set()
        InterfaceSolveIncompleteError: RoutingStageError | None = None
        LastGlobalHandoffError: RoutingStageError | None = None
        MaximumRetainedComponentSelections = 6
        ComponentPlacementSearchDomain = (
            BuildRetainedComponentPlacementSearchDomain(
                (
                    Candidate.PlacementFingerprint
                    for Candidate in InterfaceCandidates
                ),
                MaximumComponentSelections=(
                    MaximumRetainedComponentSelections
                ),
            )
        )
        InterfaceStageSchedule = BuildClusterInterfaceStageSchedule(
            Deadline,
            (
                BuildStableFingerprint((
                    ComponentVariant,
                    PlacementFingerprint,
                ))
                for (
                    ComponentVariant,
                    _PlacementIndex,
                    PlacementFingerprint,
                ) in ComponentPlacementSearchDomain
            ),
            GlobalRoutingReserveSeconds=max(
                0.0,
                Policy.MaterialObjective
                .MinimumRemainingRoutingPercentageSearchSeconds,
            ),
            PublicationReserveSeconds=2.0,
        )
        SharedInterfaceDeadline = RoutingDeadline(
            StartedAt=Deadline.StartedAt,
            ExpiresAt=InterfaceStageSchedule.ExpiresAt,
        )
        PrimaryTransforms: dict[object, object] = {}
        InterfaceCandidateQueue = [
            (
                "prepare-eligibility",
                InterfaceIndex,
                InterfaceCandidates[InterfaceIndex],
                0,
                ComponentVariant,
            )
            for (
                ComponentVariant,
                InterfaceIndex,
                _PlacementFingerprint,
            ) in ComponentPlacementSearchDomain
        ]
        SeenComponentSelectionsByPlacement: dict[
            str, set[str]
        ] = {}
        RoutingResourcesByRetainedPlacementFingerprint: dict[
            str, Any
        ] = {}
        PreparedEligibilityByState: dict[
            tuple[int, str], Any
        ] = {}

        def ReorderRemainingPlacementsForAccessCore(
            CurrentPlacementFingerprint: str,
        ) -> None:
            IndexedQueue = tuple(enumerate(InterfaceCandidateQueue))
            InterfaceCandidateQueue[:] = [
                Value
                for _Index, Value in sorted(
                    IndexedQueue,
                    key=lambda Entry: (
                        Entry[1][4],
                        0
                        if Entry[1][0] == "prepare-eligibility"
                        else 1,
                        1
                        if Entry[1][2].PlacementFingerprint
                        == CurrentPlacementFingerprint
                        else 0,
                        (
                            (0, 0, 0, 0, 0)
                            if Entry[1][2].PlacementFingerprint
                            == CurrentPlacementFingerprint
                            else BuildComponentAccessFeedbackPlacementScore(
                                Entry[1][2],
                                ActiveComponentCutSignals,
                            )
                        ),
                        Entry[0],
                    ),
                )
            ]
        while InterfaceCandidateQueue:
            (
                InterfaceWorkPhase,
                InterfaceIndex,
                InterfaceCandidate,
                InterfaceCutEpoch,
                ComponentVariantForState,
            ) = InterfaceCandidateQueue.pop(0)
            RetainedBaseInterfaceCandidate = InterfaceCandidate
            RetainedPlacementFingerprint = (
                InterfaceCandidate.PlacementFingerprint
            )
            EligibilityStateKey = (
                ComponentVariantForState,
                RetainedPlacementFingerprint,
            )
            InterfaceDeadline = SharedInterfaceDeadline
            StateRealizabilityNogoods: list[
                ClusterInterfaceRealizabilityNogood
            ] = []
            StateAssignmentFingerprints: list[str] = []
            StateAttemptDiagnostics: list[dict[str, object]] = []
            StateFrozenPatternFingerprints: dict[str, str] = {}
            StateFrozenReservations: tuple[Any, ...] = ()
            StateActiveComponentSignals: set[str] = set()
            Transforms: dict[object, object] = {}
            NormalizedTransforms: tuple[
                tuple[str, int, bool], ...
            ] = ()
            TransformFingerprint = ""
            LocalRouteFingerprint = ""
            ChannelFingerprint = ""
            Channel = None
            ComponentProblem = None
            ComponentSolve = None
            ComponentTemplate = None
            RoutedComponentHandoffEntered = False
            RetainedPlacementResourceCacheHit = False
            try:
                MaterializedInterfacePlacement = (
                    MaterializeSelectedJointPlacementLocalRouting(
                        InterfaceCandidate,
                        lambda Diagnostics, Candidate=InterfaceCandidate:
                        InterfaceDeadline.RaiseIfExpired(
                            "ClusterInterfacePlacementMaterialization",
                            {
                                "CandidateId": Candidate.CandidateId,
                                **Diagnostics,
                            },
                        ),
                    )
                )
                if (
                    MaterializedInterfacePlacement
                    is not InterfaceCandidate.Placement
                ):
                    InterfaceCandidate = replace(
                        InterfaceCandidate,
                        Placement=MaterializedInterfacePlacement,
                    )
                try:
                    PreviewInterfacePlacement = (
                        BuildBoundedInterClusterRoutingDeck(
                            MaterializedInterfacePlacement,
                            TrackPitch=Technology.TrackPitch,
                            MaximumAffectedClusters=3,
                            MaximumDeckLanes=12,
                            InterfaceDeckLayer=3,
                            # Placement candidates already provide the six
                            # access-distinct states. Keep component selection
                            # structural and stable across those geometries;
                            # coupling the state index to a different cluster
                            # partition made five states test unrelated,
                            # exact-unsatisfiable components.
                            ComponentVariant=(
                                ComponentVariantForState
                            ),
                            PreferredSignals=(),
                        )
                    )
                    PreviewChannel = (
                        PreviewInterfacePlacement
                        .InterClusterRoutingChannel
                    )
                    if PreviewChannel is None:
                        raise ValueError(
                            "component envelope preview produced no channel"
                        )
                    SelectedComponentClusters = tuple(
                        PreviewChannel.AffectedClusters
                    )
                    MaterializedInterfacePlacement = (
                        BuildBoundedInterClusterRoutingChannel(
                            MaterializedInterfacePlacement,
                            # The closed component needs one routing track on
                            # each side of an inter-cluster seam: one for the
                            # local access tree and one for the globally owned
                            # portal/corridor.  The deck itself retains the
                            # technology pitch; only the placement envelope
                            # reserves this two-track physical margin.
                            TrackPitch=Technology.TrackPitch * 2,
                            MaximumAffectedClusters=3,
                            MaximumBoundaryStrips=2,
                            RoutingLayerCount=3,
                            ForcedAffectedClusters=(
                                SelectedComponentClusters
                            ),
                        )
                    )
                    MaterializedInterfacePlacement = (
                        BuildBoundedInterClusterRoutingDeck(
                            MaterializedInterfacePlacement,
                            TrackPitch=Technology.TrackPitch,
                            MaximumAffectedClusters=3,
                            MaximumDeckLanes=12,
                            InterfaceDeckLayer=3,
                            ComponentVariant=(
                                ComponentVariantForState
                            ),
                            PreferredSignals=(),
                            ForcedAffectedClusters=(
                                SelectedComponentClusters
                            ),
                        )
                    )
                except ValueError as Error:
                    raise RoutingStageError(RoutingFailure(
                        Reason=(
                            RoutingFailureReason
                            .ClusterInterfaceArchitectureUnsatisfiable
                        ),
                        Stage=(
                            "InterClusterRoutingChannelMaterialization"
                        ),
                        Detail=str(Error),
                        RepairActions=(),
                        Diagnostics={
                            "CandidateId": (
                                InterfaceCandidate.CandidateId
                            ),
                            "ComponentFabricConstructionComplete": True,
                            "ClusterInterfaceDomainComplete": True,
                            "OwnershipSearchComplete": True,
                            "BroadFallbackAllowed": False,
                            "ExecutableLegacyRepairCascade": False,
                        },
                    )) from Error
                Channel = (
                    MaterializedInterfacePlacement
                    .InterClusterRoutingChannel
                )
                ChannelFingerprint = (
                    Channel.ChannelFingerprint
                    if Channel is not None
                    else ""
                )
                ComponentSelectionFingerprint = BuildStableFingerprint((
                    getattr(Channel, "ComponentId", None),
                    tuple(sorted(map(
                        str,
                        getattr(Channel, "AffectedSignals", ()),
                    ))),
                    tuple(sorted(map(
                        int,
                        getattr(Channel, "AffectedClusters", ()),
                    ))),
                ))
                SeenComponentSelections = (
                    SeenComponentSelectionsByPlacement.setdefault(
                        RetainedPlacementFingerprint,
                        set(),
                    )
                )
                if (
                    InterfaceWorkPhase == "prepare-eligibility"
                    and
                    ComponentSelectionFingerprint
                    in SeenComponentSelections
                ):
                    InterfaceAttemptDiagnostics.append({
                        "CandidateId": InterfaceCandidate.CandidateId,
                        "PlacementFingerprint": (
                            RetainedPlacementFingerprint
                        ),
                        "ComponentVariant": ComponentVariantForState,
                        "ComponentSelectionFingerprint": (
                            ComponentSelectionFingerprint
                        ),
                        "Result": (
                            "duplicate-component-selection-pruned"
                        ),
                    })
                    continue
                if InterfaceWorkPhase == "prepare-eligibility":
                    SeenComponentSelections.add(
                        ComponentSelectionFingerprint
                    )
                ChannelizedPlacementFingerprint = (
                    BuildPlacementFingerprint(
                        MaterializedInterfacePlacement,
                        (
                            InterfaceCandidate.TopologyDemand
                            .MandatoryAccessOwnershipFingerprint
                            if InterfaceCandidate.TopologyDemand
                            is not None
                            else ""
                        ),
                    )
                )
                InterfaceCandidate = replace(
                    InterfaceCandidate,
                    CandidateId=(
                        "ChannelPlacement-"
                        f"{ChannelizedPlacementFingerprint[:12]}"
                    ),
                    PlacementFingerprint=(
                        ChannelizedPlacementFingerprint
                    ),
                    Placement=MaterializedInterfacePlacement,
                    PlacementRetentionFingerprint=(
                        BuildPlacementRetentionFingerprint(
                            MaterializedInterfacePlacement,
                            (
                                InterfaceCandidate.TopologyDemand
                                .MandatoryAccessOwnershipFingerprint
                                if InterfaceCandidate.TopologyDemand
                                is not None
                                else ""
                            ),
                        )
                    ),
                    InterfaceTopologyFingerprint=(
                        BuildClusterInterfacePlacementTopologyFingerprint(
                            MaterializedInterfacePlacement,
                            SignalTopologyFingerprints,
                        )
                    ),
                )
                (
                    InterfaceResources,
                    RetainedPlacementResourceCacheHit,
                ) = ReuseRetainedPlacementRoutingResources(
                    RoutingResourcesByRetainedPlacementFingerprint,
                    RetainedPlacementFingerprint,
                    lambda: BuildRoutingResources(
                        MaterializedInterfacePlacement.Placed,
                        WorkCheck=lambda Diagnostics,
                        Candidate=InterfaceCandidate:
                        InterfaceDeadline.RaiseIfExpired(
                            "ClusterInterfaceResourceMaterialization",
                            {
                                "CandidateId": Candidate.CandidateId,
                                **Diagnostics,
                            },
                        ),
                    ),
                )
                RoutingResourcesByFingerprint[
                    InterfaceCandidate.PlacementFingerprint
                ] = InterfaceResources
                # Physical plans, rejection sets, and routed templates are
                # component-specific. Only immutable placement geometry and
                # raw whole-design portal/guide preparation survive when the
                # the component rank advances within one retained placement.
                InterfaceResources.FrozenRoutedComponentTemplate = None
                SeedPortableRawPortalGeometryCaches(
                    InterfaceResources
                )
                JointDiagnostics = dict(
                    MaterializedInterfacePlacement
                    .Placed.LocalRouteDiagnostics or {}
                ).get("__JointClusterPlacement__", {})
                Transforms = (
                    JointDiagnostics.get("SelectedTransforms", {})
                    if isinstance(JointDiagnostics, dict)
                    else {}
                )
                if InterfaceIndex == 0:
                    PrimaryTransforms = dict(Transforms)
                NormalizedTransforms = tuple(sorted(
                    (
                        str(Cluster),
                        int(
                            Transform.get("Rotation", 0)
                            if isinstance(Transform, dict)
                            else getattr(Transform, "Rotation", 0)
                        ),
                        bool(
                            Transform.get("MirrorX", False)
                            if isinstance(Transform, dict)
                            else getattr(Transform, "MirrorX", False)
                        ),
                    )
                    for Cluster, Transform in Transforms.items()
                ))
                TransformFingerprint = BuildStableFingerprint(
                    NormalizedTransforms
                )
                LocalRouteFingerprint = BuildStableFingerprint(tuple(
                    sorted(
                        str(Template.LocalClaimFingerprint)
                        for Template in getattr(
                            MaterializedInterfacePlacement.Placed,
                            "ClusterLocalRouteTemplates",
                            (),
                        )
                    )
                ) + (ChannelFingerprint,))
                ChangedClusterCount = sum(
                    Transforms.get(Key) != PrimaryTransforms.get(Key)
                    for Key in set(Transforms) | set(PrimaryTransforms)
                )
                Demand = InterfaceCandidate.TopologyDemand
                InterfacePlacementStatesByFingerprint[
                    InterfaceCandidate.PlacementFingerprint
                ] = ClusterInterfacePlacementState(
                    StateFingerprint=(
                        InterfaceCandidate.PlacementFingerprint
                    ),
                    ClusterTransforms=NormalizedTransforms,
                    ChangedClusterCount=ChangedClusterCount,
                    LocalRouteFingerprint=LocalRouteFingerprint,
                    Footprint=(
                        Demand.GateFootprint
                        if Demand is not None
                        else 0
                    ),
                    Hpwl=Demand.Hpwl if Demand is not None else 0,
                    PeakBoundaryPressure=(
                        Demand.PeakBoundaryDemand
                        if Demand is not None
                        else 0
                    ),
                    TotalBoundaryPressure=(
                        (
                            Demand.InputTerminalCount
                            + Demand.OutputTerminalCount
                        )
                        if Demand is not None
                        else 0
                    ),
                    InterfaceTopologyFingerprint=(
                        InterfaceCandidate
                        .InterfaceTopologyFingerprint
                    ),
                    ChannelFingerprint=ChannelFingerprint,
                    InterClusterChannel=Channel,
                )
                InterfaceRemainingSeconds = max(
                    0.001,
                    InterfaceDeadline.RemainingSeconds(),
                )
                InterfacePolicy = replace(
                    Policy,
                    RuntimeBudgetSeconds=InterfaceRemainingSeconds,
                    AdaptiveRouting=replace(
                        Policy.AdaptiveRouting,
                        MaximumRuntimeSeconds=min(
                            Policy.AdaptiveRouting.MaximumRuntimeSeconds,
                            InterfaceRemainingSeconds,
                        ),
                    ),
                )
                (
                    InterfaceResources
                    .RejectedPhysicalComponentPortAssignmentFingerprints
                    .clear()
                )
                (
                    InterfaceResources
                    .RejectedPhysicalComponentPortReservationsBySignal
                    .clear()
                )
                (
                    InterfaceResources
                    .RejectedPhysicalComponentPortReservationSets
                    .clear()
                )
                if InterfaceWorkPhase == "prepare-eligibility":
                    PreparedEligibility = (
                        PreparePhysicalComponentEligibility(
                            MaterializedInterfacePlacement,
                            Resources=InterfaceResources,
                            Policy=InterfacePolicy,
                            Deadline=InterfaceDeadline,
                            StateFingerprint=(
                                InterfaceCandidate.PlacementFingerprint
                            ),
                            LocalRouteFingerprint=LocalRouteFingerprint,
                        )
                    )
                    if not PreparedEligibility.Complete:
                        raise RoutingStageError(RoutingFailure(
                            Reason=(
                                RoutingFailureReason
                                .PhysicalComponentAssemblyIncomplete
                            ),
                            Stage="PhysicalComponentEligibility",
                            Detail=(
                                "the physical component port factor domain "
                                "is incomplete"
                            ),
                            Diagnostics={
                                "DomainFingerprint": (
                                    PreparedEligibility.DomainFingerprint
                                ),
                                "Complete": False,
                                "Feasible": False,
                            },
                        ))
                    if not PreparedEligibility.Feasible:
                        raise RoutingStageError(RoutingFailure(
                            Reason=(
                                RoutingFailureReason
                                .ComponentPortAssignmentUnsatisfiable
                            ),
                            Stage="PhysicalComponentEligibility",
                            Detail=(
                                "the complete physical port factor domain "
                                "has an empty port bank"
                            ),
                            AffectedNets=tuple(
                                Signal
                                for Signal, Values in (
                                    PreparedEligibility
                                    .LaneFactorsBySignal
                                )
                                if not Values
                            ),
                            Diagnostics={
                                "DomainFingerprint": (
                                    PreparedEligibility.DomainFingerprint
                                ),
                                "Complete": True,
                                "Feasible": False,
                                "DomainDiagnosticsBySignal": dict(
                                    PreparedEligibility
                                    .DiagnosticsBySignal
                                ),
                                "ComponentFabricConstructionComplete": True,
                                "OwnershipSearchComplete": True,
                                "ImplicitForeignTransitDomainCount": 0,
                            },
                        ))
                    PreparedEligibilityByState[
                        EligibilityStateKey
                    ] = PreparedEligibility
                    InterfaceAttemptDiagnostics.append({
                        "CandidateId": InterfaceCandidate.CandidateId,
                        "PlacementFingerprint": (
                            InterfaceCandidate.PlacementFingerprint
                        ),
                        "ComponentVariant": ComponentVariantForState,
                        "Result": "physical-eligibility-prepared",
                        "DomainFingerprint": (
                            PreparedEligibility.DomainFingerprint
                        ),
                        "Complete": True,
                        "Feasible": True,
                    })
                    InterfaceCandidateQueue.append((
                        "solve-prepared-eligibility",
                        InterfaceIndex,
                        RetainedBaseInterfaceCandidate,
                        InterfaceCutEpoch,
                        ComponentVariantForState,
                    ))
                    ReorderRemainingPlacementsForAccessCore("")
                    continue
                PreparedEligibility = PreparedEligibilityByState.get(
                    EligibilityStateKey
                )
                if PreparedEligibility is None:
                    raise RuntimeError(
                        "physical component solve was scheduled without "
                        "a complete eligibility domain"
                    )
                PreparedAssembly = (
                    SolvePreparedPhysicalComponentEligibility(
                        PreparedEligibility,
                        Resources=InterfaceResources,
                        Deadline=InterfaceDeadline,
                    )
                )
                PhysicalAssemblyPlan = PreparedAssembly.Plan
                ComponentProblem = PreparedAssembly.Problem
                ComponentBasePlacement = (
                    MaterializedInterfacePlacement
                )
                ComponentBaseCandidate = InterfaceCandidate
                while True:
                    # A failed post-handoff feedback solve belongs to the
                    # component proof for this state, not to the preceding
                    # global attempt.  Clear the handoff identity before each
                    # solve so the outer state scheduler cannot report a
                    # stale feasible template as the source of a later
                    # architectural-unsat or incomplete result.
                    RoutedComponentHandoffEntered = False
                    ComponentSolve = None
                    ComponentTemplate = None
                    ActiveComponentDeadline = InterfaceDeadline
                    ComponentSolve = CompileClosedComponent(
                        ComponentProblem,
                        AssemblyPlan=PhysicalAssemblyPlan,
                        DeadlineSeconds=max(
                            0.001,
                            ActiveComponentDeadline.RemainingSeconds(),
                        ),
                        WorkCheck=lambda Diagnostics:
                        ActiveComponentDeadline.RaiseIfExpired(
                            "ComponentRoutingSolve",
                            {
                                "CandidateId": (
                                    ComponentBaseCandidate.CandidateId
                                ),
                                **Diagnostics,
                            },
                        ),
                    )
                    if not ComponentSolve.Feasible:
                        Incomplete = (
                            ComponentSolve.Status == "incomplete"
                        )
                        if (
                            not Incomplete
                            and not ActiveComponentDeadline.IsExpired()
                        ):
                            RejectedPortAssignmentFingerprint = (
                                PhysicalAssemblyPlan
                                .PortAssignmentFingerprint
                            )
                            (
                                InterfaceResources
                                .RejectedPhysicalComponentPortAssignmentFingerprints
                                .add(RejectedPortAssignmentFingerprint)
                            )
                            StateAttemptDiagnostics.append({
                                "Result": (
                                    "local-unsat-reject-complete-assembly-plan"
                                ),
                                "PhysicalAssemblyPlanFingerprint": (
                                    PhysicalAssemblyPlan.PlanFingerprint
                                ),
                                "RejectedPortAssignmentFingerprint": (
                                    RejectedPortAssignmentFingerprint
                                ),
                                "LocalSolveDetail": ComponentSolve.Detail,
                                "LocalTemplateReopened": False,
                                "PerSignalReservationFeedbackUsed": False,
                                "ImplicitForeignTransitDomainCount": 0,
                            })
                            PreparedAssembly = (
                                ReplanPhysicalComponentAssembly(
                                    ComponentBasePlacement,
                                    Resources=InterfaceResources,
                                    Deadline=InterfaceDeadline,
                                )
                            )
                            PhysicalAssemblyPlan = PreparedAssembly.Plan
                            ComponentProblem = PreparedAssembly.Problem
                            continue
                        raise RoutingStageError(RoutingFailure(
                            Reason=(
                                RoutingFailureReason
                                .PhysicalComponentAssemblyIncomplete
                                if Incomplete
                                else RoutingFailureReason
                                .ComponentLocalCompilationUnsatisfiable
                            ),
                            Stage=(
                                "ClosedComponentCompilationIncomplete"
                                if Incomplete
                                else
                                "ClosedComponentCompilationUnsatisfiable"
                            ),
                            Detail=ComponentSolve.Detail,
                            RepairActions=(),
                            Diagnostics={
                                "ComponentRoutingProblem": (
                                    ComponentProblem.ToDictionary()
                                ),
                                "ComponentRoutingSolve": {
                                    "Status": ComponentSolve.Status,
                                    "ProofFingerprint": (
                                        ComponentSolve.ProofFingerprint
                                    ),
                                    "ExpansionCount": (
                                        ComponentSolve.ExpansionCount
                                    ),
                                    "Complete": not Incomplete,
                                    "Diagnostics": (
                                        ComponentSolve.Diagnostics
                                    ),
                                },
                                "PhysicalAssemblyPlanFingerprint": (
                                    PhysicalAssemblyPlan.PlanFingerprint
                                ),
                                "PerSignalReservationFeedbackUsed": False,
                                "BroadFallbackAllowed": False,
                                "ExecutableLegacyRepairCascade": False,
                            },
                        ))
                    assert ComponentSolve.Template is not None
                    ComponentTemplate = ComponentSolve.Template
                    ComponentAssembly = (
                        AssembleClosedComponentForGlobalRouting(
                            ComponentBasePlacement.Placed,
                            ComponentTemplate,
                            PhysicalAssemblyPlan=(
                                PhysicalAssemblyPlan
                            ),
                            PlacementFingerprint=(
                                ComponentBaseCandidate
                                .PlacementFingerprint
                            ),
                            LocalTemplateFingerprint=(
                                LocalRouteFingerprint
                            ),
                        )
                    )
                    RoutedComponentPlaced = ComponentAssembly.Placed
                    MaterializedInterfacePlacement = replace(
                        ComponentBasePlacement,
                        Placed=RoutedComponentPlaced,
                    )
                    InterfaceCandidate = replace(
                        ComponentBaseCandidate,
                        Placement=MaterializedInterfacePlacement,
                    )
                    InterfaceResources.FrozenRoutedComponentTemplate = (
                        ComponentTemplate
                    )
                    InterfaceResources.FrozenPhysicalComponentAssemblyPlan = (
                        PhysicalAssemblyPlan
                    )
                    HandoffDiagnostics = (
                        ComponentAssembly.HandoffDiagnostics
                    )
                    RoutedComponentHandoffEntered = True
                    GlobalRemainingSeconds = max(
                        0.001,
                        Deadline.RemainingSeconds(),
                    )
                    GlobalHandoffPolicy = replace(
                        Policy,
                        RuntimeBudgetSeconds=GlobalRemainingSeconds,
                        AdaptiveRouting=replace(
                            Policy.AdaptiveRouting,
                            MaximumRuntimeSeconds=min(
                                Policy.AdaptiveRouting
                                .MaximumRuntimeSeconds,
                                GlobalRemainingSeconds,
                            ),
                        ),
                    )
                    try:
                        PreRoutedDesign = RoutePcbDesign(
                            MaterializedInterfacePlacement,
                            Policy=GlobalHandoffPolicy,
                            Deadline=Deadline,
                            Resources=InterfaceResources,
                        )
                        break
                    except RoutingStageError as GlobalError:
                        LastGlobalHandoffError = GlobalError
                        if not Deadline.IsExpired():
                            if IsComponentKeepoutGlobalFailure(
                                GlobalError.Failure,
                                PhysicalAssemblyPlan,
                            ):
                                raise RoutingStageError(RoutingFailure(
                                    Reason=(
                                        RoutingFailureReason
                                        .ComponentDetailedRoutingFailed
                                    ),
                                    Stage=(
                                        "ComponentGlobalKeepoutAdmission"
                                    ),
                                    AffectedNets=(
                                        GlobalError.Failure.AffectedNets
                                    ),
                                    Detail=(
                                        "an ordinary global net disproved "
                                        "the immutable component keepout; "
                                        "advance to another retained "
                                        "placement instead of reopening "
                                        "ports inside the same envelope"
                                    ),
                                    RepairActions=(),
                                    Diagnostics={
                                        "PhysicalAssemblyPlanFingerprint": (
                                            PhysicalAssemblyPlan
                                            .PlanFingerprint
                                        ),
                                        "RejectedComponentEnvelope": [
                                            list(
                                                PhysicalAssemblyPlan
                                                .EnvelopeMinimum
                                            ),
                                            list(
                                                PhysicalAssemblyPlan
                                                .EnvelopeMaximum
                                            ),
                                        ],
                                        "UnderlyingFailure": (
                                            GlobalError.Failure
                                            .ToDictionary()
                                        ),
                                        "LocalTemplateReopened": False,
                                        "PortPlanReopened": False,
                                        "ImplicitForeignTransitDomainCount": 0,
                                        "BroadFallbackAllowed": False,
                                    },
                                )) from GlobalError
                            RejectedPortAssignmentFingerprint = (
                                PhysicalAssemblyPlan
                                .PortAssignmentFingerprint
                            )
                            (
                                InterfaceResources
                                .RejectedPhysicalComponentPortAssignmentFingerprints
                                .add(
                                    RejectedPortAssignmentFingerprint
                                )
                            )
                            StateAttemptDiagnostics.append({
                                "Result": (
                                    "detailed-failure-reject-physical-plan"
                                ),
                                "PhysicalAssemblyPlanFingerprint": (
                                    PhysicalAssemblyPlan.PlanFingerprint
                                ),
                                "RejectedPortAssignmentFingerprint": (
                                    RejectedPortAssignmentFingerprint
                                ),
                                "UnderlyingFailure": (
                                    GlobalError.Failure.ToDictionary()
                                ),
                                "LocalTemplateReopened": False,
                                "ImplicitForeignTransitDomainCount": 0,
                            })
                            MaterializedInterfacePlacement = (
                                ComponentBasePlacement
                            )
                            InterfaceCandidate = ComponentBaseCandidate
                            InterfaceResources.FrozenRoutedComponentTemplate = (
                                None
                            )
                            PreparedAssembly = (
                                ReplanPhysicalComponentAssembly(
                                    ComponentBasePlacement,
                                    Resources=InterfaceResources,
                                    Deadline=InterfaceDeadline,
                                )
                            )
                            PhysicalAssemblyPlan = PreparedAssembly.Plan
                            ComponentProblem = PreparedAssembly.Problem
                            continue
                        raise RoutingStageError(RoutingFailure(
                            Reason=(
                                RoutingFailureReason
                                .ComponentDetailedRoutingFailed
                            ),
                            Stage=(
                                "AuthoritativeDetailedRoutingAfter"
                                "PhysicalComponentAssembly"
                            ),
                            AffectedNets=(
                                GlobalError.Failure.AffectedNets
                            ),
                            Detail=(
                                "authoritative detailed routing rejected "
                                "the immutable physical assembly plan"
                            ),
                            RepairActions=(
                                "RejectPhysicalAssemblyPlan",
                            ),
                            Diagnostics={
                                "PhysicalAssemblyPlanFingerprint": (
                                    PhysicalAssemblyPlan.PlanFingerprint
                                ),
                                "RejectedGlobalPlan": (
                                    PhysicalAssemblyPlan.ToDictionary()
                                ),
                                "UnderlyingFailure": (
                                    GlobalError.Failure.ToDictionary()
                                ),
                                "LocalTemplateReopened": False,
                                "ImplicitForeignTransitDomainCount": 0,
                                "BroadFallbackAllowed": False,
                            },
                        )) from GlobalError
                PreRoutedClusterInterfaceDesignsByPlacementFingerprint[
                    InterfaceCandidate.PlacementFingerprint
                ] = PreRoutedDesign
                RoutedComponentTemplatesByPlacementFingerprint[
                    InterfaceCandidate.PlacementFingerprint
                ] = ComponentTemplate
                Demand = InterfaceCandidate.TopologyDemand
                Objective = (
                    0,
                    ChangedClusterCount,
                    (
                        Demand.PeakBoundaryDemand
                        if Demand is not None
                        else 0
                    ),
                    (
                        Demand.GateFootprint
                        if Demand is not None
                        else 0
                    ),
                    Demand.Hpwl if Demand is not None else 0,
                    ComponentTemplate.RoutedTemplateFingerprint,
                )
                InterfaceFeasibleCandidates.append((
                    Objective,
                    InterfaceCandidate,
                    ComponentTemplate,
                ))
                InterfaceStateProofs.append(
                    ClusterInterfaceStateProof(
                        PlacementStateFingerprint=(
                            InterfaceCandidate.PlacementFingerprint
                        ),
                        Status="feasible",
                        ChannelFingerprint=ChannelFingerprint,
                        TransformFingerprint=TransformFingerprint,
                        AssignmentFingerprints=(
                            ComponentTemplate
                            .RoutedTemplateFingerprint,
                        ),
                        DomainFingerprint=(
                            ComponentProblem.ProblemFingerprint
                        ),
                        ExpansionCount=(
                            ComponentSolve.ExpansionCount
                        ),
                        DomainComplete=(
                            ComponentProblem.DomainComplete
                        ),
                        OwnershipComplete=True,
                        RealizabilityComplete=True,
                        Exhaustive=False,
                    )
                )
                InterfaceAttemptDiagnostics.append({
                    "CandidateId": InterfaceCandidate.CandidateId,
                    "PlacementFingerprint": (
                        InterfaceCandidate.PlacementFingerprint
                    ),
                    "ComponentCutEpoch": InterfaceCutEpoch,
                    "ComponentVariant": ComponentVariantForState,
                    "RetainedPlacementResourceCacheHit": (
                        RetainedPlacementResourceCacheHit
                    ),
                    "ActiveComponentCutSignals": sorted(
                        ActiveComponentCutSignals
                    ),
                    "Result": "feasible-routed-component",
                    "Objective": list(Objective),
                    "Transforms": Transforms,
                    "ComponentRoutingProblem": (
                        ComponentProblem.ToDictionary()
                    ),
                    "RoutedComponentTemplate": (
                        ComponentTemplate.ToDictionary()
                    ),
                    "ComponentRoutingSolve": {
                        "Status": ComponentSolve.Status,
                        "ProofFingerprint": (
                            ComponentSolve.ProofFingerprint
                        ),
                        "ExpansionCount": (
                            ComponentSolve.ExpansionCount
                        ),
                        "Diagnostics": (
                            ComponentSolve.Diagnostics
                        ),
                    },
                    "OrdinaryGlobalHandoff": {
                        "Entered": True,
                        "ImmutableClaims": True,
                        **HandoffDiagnostics,
                        "ExportedPortFingerprint": (
                            ComponentTemplate
                            .ExportedPortFingerprint
                        ),
                    },
                })
                CapturePortableRawPortalGeometryCaches(
                    InterfaceResources
                )
                # A routed component is a complete physical state, so the
                # first feasible ranked state deterministically ends the
                # shared component-stage search.
                break
                while True:
                    try:
                        Assignment = PrepareClusterInterfaceAssignment(
                            MaterializedInterfacePlacement,
                            Resources=InterfaceResources,
                            Policy=InterfacePolicy,
                            Deadline=InterfaceDeadline,
                            RealizabilityNogoods=tuple(
                                StateRealizabilityNogoods
                            ),
                            StateFingerprint=(
                                InterfaceCandidate
                                .PlacementFingerprint
                            ),
                            LocalRouteFingerprint=(
                                LocalRouteFingerprint
                            ),
                            ForbiddenAssignmentFingerprints=(
                                frozenset(
                                    StateAssignmentFingerprints
                                )
                            ),
                            FrozenPatternFingerprints=(
                                StateFrozenPatternFingerprints
                            ),
                            FrozenReservations=(
                                StateFrozenReservations
                            ),
                            RequireCompleteDomain=True,
                        )
                        if (
                            Assignment.AssignmentFingerprint
                            in StateAssignmentFingerprints
                        ):
                            raise RoutingStageError(RoutingFailure(
                                Reason=(
                                    RoutingFailureReason
                                    .ClusterInterfaceUnsatisfiable
                                ),
                                Stage=(
                                    "ClusterInterfaceUnsatisfiable"
                                ),
                                Detail=(
                                    "the exact interface re-solve "
                                    "repeated a complete assignment"
                                ),
                                Diagnostics={
                                    "RepeatedAssignmentFingerprint": (
                                        Assignment
                                        .AssignmentFingerprint
                                    ),
                                    "RouteTreeRealizabilityAttempted": (
                                        False
                                    ),
                                },
                            ))
                        StateAssignmentFingerprints.extend(
                            Fingerprint
                            for Fingerprint in (
                                Assignment.AssignmentFingerprint,
                                Assignment
                                .OwnershipAssignmentFingerprint,
                            )
                            if (
                                Fingerprint
                                and Fingerprint
                                not in StateAssignmentFingerprints
                            )
                        )
                        try:
                            ForeignAccessDiagnostics = (
                                ValidateClusterInterfaceForeignAccess(
                                    MaterializedInterfacePlacement,
                                    Resources=InterfaceResources,
                                    Assignment=Assignment,
                                    Policy=InterfacePolicy,
                                    Deadline=InterfaceDeadline,
                                )
                            )
                        except RoutingStageError as AccessError:
                            if (
                                AccessError.Failure.Stage
                                != "ClusterInterfaceForeignAccessStarved"
                            ):
                                raise
                            AccessDiagnostics = dict(
                                AccessError.Failure.Diagnostics or {}
                            )
                            ForeignAccessValidation = dict(
                                AccessDiagnostics.get(
                                    "ForeignAccessValidation",
                                    {},
                                )
                            )
                            BlockingPatterns = {
                                (str(Signal), str(Fingerprint))
                                for EmptyDomain in (
                                    ForeignAccessValidation.get(
                                        "EmptyTerminalDomains",
                                        (),
                                    )
                                )
                                if isinstance(EmptyDomain, dict)
                                for Signal, Fingerprint in dict(
                                    EmptyDomain.get(
                                        "BlockingInterfacePatternFingerprints",
                                        {},
                                    )
                                ).items()
                                if Fingerprint
                            }
                            StateAttemptDiagnostics.append({
                                "Result": "foreign-access-no-good",
                                "Failure": {
                                    "Reason": (
                                        AccessError
                                        .Failure.Reason.value
                                    ),
                                    "Stage": (
                                        AccessError.Failure.Stage
                                    ),
                                    "AffectedNets": list(
                                        AccessError
                                        .Failure.AffectedNets
                                    ),
                                    "Detail": (
                                        AccessError.Failure.Detail
                                    ),
                                },
                                "RejectedAssignmentFingerprint": (
                                    Assignment
                                    .AssignmentFingerprint
                                ),
                                "ForeignAccessValidation": (
                                    ForeignAccessValidation
                                ),
                                "RouteTreeRealizabilityAttempted": False,
                            })
                            if not BlockingPatterns:
                                raise RoutingStageError(replace(
                                    AccessError.Failure,
                                    Reason=(
                                        RoutingFailureReason
                                        .ClusterInterfaceSolveIncomplete
                                    ),
                                    Stage=(
                                        "ClusterInterfaceSolveIncomplete"
                                    ),
                                    Detail=(
                                        "foreign terminal starvation did "
                                        "not identify an exact blocking "
                                        "component access assignment"
                                    ),
                                    Diagnostics={
                                        **AccessDiagnostics,
                                        "BlockingInterfacePatterns": [
                                            list(Value)
                                            for Value in sorted(
                                                BlockingPatterns
                                            )
                                        ],
                                    },
                                )) from AccessError
                            continue
                        StateAttemptDiagnostics.append({
                            "Result": "foreign-access-validated",
                            "AssignmentFingerprint": (
                                Assignment.AssignmentFingerprint
                            ),
                            **ForeignAccessDiagnostics,
                        })
                        ValidatedForeignAccess = dict(
                            ForeignAccessDiagnostics.get(
                                "ForeignAccessValidation",
                                {},
                            )
                        )
                        InterfaceResources.FrozenClusterInterfaceAssignment = (
                            Assignment
                        )
                        try:
                            PreRoutedDesign = RoutePcbDesign(
                                MaterializedInterfacePlacement,
                                Policy=InterfacePolicy,
                                Deadline=InterfaceDeadline,
                                Resources=InterfaceResources,
                            )
                        except RoutingStageError as GlobalError:
                            GlobalDiagnostics = dict(
                                GlobalError.Failure.Diagnostics or {}
                            )
                            ConflictGraph = dict(
                                GlobalDiagnostics.get(
                                    "ConflictGraph",
                                    {},
                                )
                            )
                            ComponentSignals = frozenset(
                                str(Signal)
                                for Signal in getattr(
                                    Channel,
                                    "AffectedSignals",
                                    (),
                                )
                            )
                            CrossingComponentSignals = {
                                str(First)
                                for Edge in ConflictGraph.get(
                                    "PairwiseIncompatibleEdges",
                                    (),
                                )
                                if (
                                    isinstance(Edge, list | tuple)
                                    and len(Edge) == 2
                                )
                                for First, Second in (Edge,)
                                if (
                                    (str(First) in ComponentSignals)
                                    != (str(Second) in ComponentSignals)
                                )
                                and str(First) in ComponentSignals
                            } | {
                                str(Second)
                                for Edge in ConflictGraph.get(
                                    "PairwiseIncompatibleEdges",
                                    (),
                                )
                                if (
                                    isinstance(Edge, list | tuple)
                                    and len(Edge) == 2
                                )
                                for First, Second in (Edge,)
                                if (
                                    (str(First) in ComponentSignals)
                                    != (str(Second) in ComponentSignals)
                                )
                                and str(Second) in ComponentSignals
                            }
                            PatternFingerprints = dict(
                                ValidatedForeignAccess.get(
                                    "InterfacePatternFingerprints",
                                    {},
                                )
                            )
                            if (
                                not CrossingComponentSignals
                                and GlobalError.Failure.Stage
                                == "ClusterBoundaryLease"
                            ):
                                CrossingComponentSignals = {
                                    str(Signal)
                                    for Signal
                                    in GlobalError.Failure.AffectedNets
                                    if str(Signal) in ComponentSignals
                                }
                            RepairableSignals = tuple(sorted(
                                Signal
                                for Signal in CrossingComponentSignals
                                if PatternFingerprints.get(Signal)
                            ))
                            StateAttemptDiagnostics.append({
                                "Result": (
                                    "ordinary-global-interface-no-good"
                                    if RepairableSignals
                                    else "ordinary-global-terminal"
                                ),
                                "Failure": {
                                    "Reason": (
                                        GlobalError.Failure.Reason.value
                                    ),
                                    "Stage": GlobalError.Failure.Stage,
                                    "AffectedNets": list(
                                        GlobalError
                                        .Failure.AffectedNets
                                    ),
                                    "Detail": (
                                        GlobalError.Failure.Detail
                                    ),
                                },
                                "CrossingComponentSignals": list(
                                    RepairableSignals
                                ),
                                "RouteTreeRealizabilityAttempted": True,
                            })
                            if (
                                not RepairableSignals
                                or InterfaceDeadline.IsExpired()
                            ):
                                raise
                            continue
                        PreRoutedClusterInterfaceDesignsByPlacementFingerprint[
                            InterfaceCandidate.PlacementFingerprint
                        ] = PreRoutedDesign
                        break
                    except RoutingStageError as StateError:
                        StateDiagnostics = dict(
                            StateError.Failure.Diagnostics or {}
                        )
                        RejectedAssignment = StateDiagnostics.get(
                            "RejectedInterfaceAssignment"
                        )
                        RequiresNogood = bool(
                            StateDiagnostics.get(
                                "ExactInterfaceRealizabilityNogoodRequired",
                                False,
                            )
                        )
                        PatternFingerprint = str(
                            StateDiagnostics.get(
                                "CurrentLeasePatternFingerprint",
                                "",
                            )
                        )
                        SelectedPatternFingerprints = dict(
                            StateDiagnostics.get(
                                "SelectedClusterInterfacePatternFingerprints",
                                {},
                            )
                        )
                        FailureSignal = (
                            StateError.Failure.AffectedNets[0]
                            if StateError.Failure.AffectedNets
                            else ""
                        )
                        SignalPatternFingerprint = str(
                            SelectedPatternFingerprints.get(
                                FailureSignal,
                                PatternFingerprint,
                            )
                            or PatternFingerprint
                        )
                        FailureFingerprint = str(
                            StateDiagnostics.get(
                                "CandidateFailureFingerprint",
                                "",
                            )
                        )
                        RejectedFingerprint = (
                            str(
                                RejectedAssignment.get(
                                    "AssignmentFingerprint",
                                    "",
                                )
                            )
                            if isinstance(
                                RejectedAssignment,
                                dict,
                            )
                            else ""
                        )
                        RejectedProblem = (
                            RejectedAssignment.get("Problem", {})
                            if isinstance(
                                RejectedAssignment,
                                dict,
                            )
                            else {}
                        )
                        ComponentFingerprint = str(
                            RejectedProblem.get(
                                "ComponentFingerprint",
                                "",
                            )
                        )
                        DomainFingerprint = str(
                            StateDiagnostics.get(
                                "AuthoritativeAccessDomainFingerprint",
                                ComponentFingerprint,
                            )
                            or ComponentFingerprint
                        )
                        CanAddNogood = bool(
                            RequiresNogood
                            and SignalPatternFingerprint
                            and FailureFingerprint
                            and RejectedFingerprint
                            and RejectedFingerprint
                            not in StateAssignmentFingerprints
                            and not InterfaceDeadline.IsExpired()
                        )
                        StateAttemptDiagnostics.append({
                            "Result": (
                                "realizability-no-good"
                                if CanAddNogood
                                else "terminal"
                            ),
                            "Failure": {
                                "Reason": (
                                    StateError.Failure.Reason.value
                                ),
                                "Stage": StateError.Failure.Stage,
                                "AffectedNets": list(
                                    StateError.Failure.AffectedNets
                                ),
                                "Detail": StateError.Failure.Detail,
                                "CurrentLeasePatternFingerprint": (
                                    PatternFingerprint
                                ),
                                "RejectedSignalPatternFingerprint": (
                                    SignalPatternFingerprint
                                ),
                                "CandidateFailureFingerprint": (
                                    FailureFingerprint
                                ),
                            },
                            "RejectedAssignmentFingerprint": (
                                RejectedFingerprint
                            ),
                            "RouteTreeRealizabilityAttempted": bool(
                                StateDiagnostics.get(
                                    "RouteTreeRealizabilityAttempted",
                                    True,
                                )
                            ),
                        })
                        if not CanAddNogood:
                            raise
                        PatternSearch = StateDiagnostics.get(
                            "ClusterInterfacePatternSearch",
                            {},
                        )
                        ConflictEdges = (
                            PatternSearch.get("ConflictEdges", ())
                            if isinstance(PatternSearch, dict)
                            else ()
                        )
                        StateActiveComponentSignals.add(
                            FailureSignal
                        )
                        ComponentChanged = True
                        while ComponentChanged:
                            ComponentChanged = False
                            for Edge in ConflictEdges:
                                if (
                                    not isinstance(Edge, list | tuple)
                                    or len(Edge) != 2
                                ):
                                    continue
                                FirstSignal, SecondSignal = map(
                                    str,
                                    Edge,
                                )
                                if (
                                    FirstSignal
                                    in StateActiveComponentSignals
                                    and SecondSignal
                                    not in StateActiveComponentSignals
                                ):
                                    StateActiveComponentSignals.add(
                                        SecondSignal
                                    )
                                    ComponentChanged = True
                                elif (
                                    SecondSignal
                                    in StateActiveComponentSignals
                                    and FirstSignal
                                    not in StateActiveComponentSignals
                                ):
                                    StateActiveComponentSignals.add(
                                        FirstSignal
                                    )
                                    ComponentChanged = True
                        StateFrozenPatternFingerprints = {
                            str(Signal): str(Fingerprint)
                            for Signal, Fingerprint
                            in SelectedPatternFingerprints.items()
                            if (
                                str(Signal)
                                not in StateActiveComponentSignals
                            )
                        }
                        PreparedReservations = (
                            InterfaceResources
                            .PreparedPortalDomainCaches[-1]
                            .Reservations
                            if InterfaceResources
                            .PreparedPortalDomainCaches
                            else ()
                        )
                        StateFrozenReservations = tuple(
                            Reservation
                            for Reservation in PreparedReservations
                            if (
                                Reservation.Signal
                                not in StateActiveComponentSignals
                            )
                        )
                        ComponentFingerprint = (
                            BuildStableFingerprint(tuple(sorted(
                                SelectedPatternFingerprints.get(
                                    Signal,
                                    "",
                                )
                                for Signal
                                in StateActiveComponentSignals
                            )))
                            if SelectedPatternFingerprints
                            else ComponentFingerprint
                        )
                        StateAssignmentFingerprints.append(
                            RejectedFingerprint
                        )
                        StateRealizabilityNogoods.append(
                            ClusterInterfaceRealizabilityNogood(
                                PlacementStateFingerprint=(
                                    InterfaceCandidate
                                    .PlacementFingerprint
                                ),
                                ComponentFingerprint=(
                                    ComponentFingerprint
                                ),
                                Signal=(
                                    FailureSignal
                                ),
                                TerminalPatternFingerprint=(
                                    SignalPatternFingerprint
                                ),
                                CandidateDomainFingerprint=(
                                    DomainFingerprint
                                ),
                                RouteFailureFingerprint=(
                                    FailureFingerprint
                                ),
                                RejectedAssignmentFingerprint=(
                                    RejectedFingerprint
                                ),
                            )
                        )
                CapturePortableRawPortalGeometryCaches(
                    InterfaceResources
                )
                PlacementState = (
                    InterfacePlacementStatesByFingerprint[
                        InterfaceCandidate.PlacementFingerprint
                    ]
                )
                Assignment = replace(
                    Assignment,
                    Problem=replace(
                        Assignment.Problem,
                        PlacementVariantFingerprint=(
                            InterfaceCandidate.PlacementFingerprint
                        ),
                        PlacementStates=(PlacementState,),
                        PolicyFingerprint=BuildStableFingerprint(
                            InterfacePolicy.ToDictionary()
                        ),
                        LocalRouteFingerprint=LocalRouteFingerprint,
                        ChannelFingerprint=ChannelFingerprint,
                        InterClusterChannel=Channel,
                    ),
                )
                Objective = (
                    0,
                    ChangedClusterCount,
                    sum(
                        DomainSize == 1
                        for DomainSize
                        in Assignment.Problem.TerminalDomainSizes
                    ),
                    (
                        Demand.PeakBoundaryDemand
                        if Demand is not None
                        else 0
                    ),
                    (
                        Demand.AccessCandidateScarcity
                        if Demand is not None
                        else 0
                    ),
                    (
                        len(Channel.InsertedBoundaryStrips)
                        if Channel is not None
                        else 0
                    ),
                    (
                        Demand.GateFootprint
                        if Demand is not None
                        else 0
                    ),
                    Demand.Hpwl if Demand is not None else 0,
                    Assignment.AssignmentFingerprint,
                )
                Assignment = replace(
                    Assignment,
                    Objective=Objective,
                )
                InterfaceResources.FrozenClusterInterfaceAssignment = (
                    Assignment
                )
                FrozenClusterInterfaceAssignmentsByPlacementFingerprint[
                    InterfaceCandidate.PlacementFingerprint
                ] = Assignment
                FrozenPreparedPortalDomainCachesByPlacementFingerprint[
                    InterfaceCandidate.PlacementFingerprint
                ] = InterfaceResources.FrozenPreparedPortalDomainCache
                InterfaceFeasibleCandidates.append((
                    Objective,
                    InterfaceCandidate,
                    Assignment,
                ))
                InterfaceStateProofs.append(
                    ClusterInterfaceStateProof(
                        PlacementStateFingerprint=(
                            InterfaceCandidate.PlacementFingerprint
                        ),
                        Status="feasible",
                        ChannelFingerprint=ChannelFingerprint,
                        TransformFingerprint=TransformFingerprint,
                        AssignmentFingerprints=tuple(
                            StateAssignmentFingerprints
                        ),
                        RealizabilityNogoods=tuple(
                            StateRealizabilityNogoods
                        ),
                        DomainFingerprint=(
                            Assignment.Problem.ComponentFingerprint
                        ),
                        DomainComplete=(
                            Assignment.Problem.DomainComplete
                        ),
                        OwnershipComplete=True,
                        RealizabilityComplete=True,
                        Exhaustive=False,
                    )
                )
                InterfaceAttemptDiagnostics.append({
                    "CandidateId": InterfaceCandidate.CandidateId,
                    "PlacementFingerprint": (
                        InterfaceCandidate.PlacementFingerprint
                    ),
                    "Result": "feasible",
                    "Objective": list(Objective),
                    "Transforms": Transforms,
                    "InterfaceAssignment": Assignment.ToDictionary(),
                    "RealizabilityAttempts": (
                        StateAttemptDiagnostics
                    ),
                    "RealizabilityNogoods": [
                        Nogood.ToDictionary()
                        for Nogood in StateRealizabilityNogoods
                    ],
                })
                # The shared component-stage budget is sequential, not a
                # best-of-six tournament.  The first complete feasible state
                # is deterministic under the ranked state order and must hand
                # its reserved global-routing time back immediately.
                break
            except RoutingStageError as Error:
                CaptureResources = RoutingResourcesByFingerprint.get(
                    InterfaceCandidate.PlacementFingerprint
                )
                if CaptureResources is not None:
                    CapturePortableRawPortalGeometryCaches(
                        CaptureResources
                    )
                if (
                    RoutedComponentHandoffEntered
                    and ComponentProblem is not None
                    and ComponentSolve is not None
                    and ComponentTemplate is not None
                ):
                    LastGlobalHandoffError = Error
                    InterfaceStateProofs.append(
                        ClusterInterfaceStateProof(
                            PlacementStateFingerprint=(
                                InterfaceCandidate
                                .PlacementFingerprint
                            ),
                            Status="global-handoff-failed",
                            ChannelFingerprint=(
                                ChannelFingerprint
                            ),
                            TransformFingerprint=(
                                TransformFingerprint
                            ),
                            AssignmentFingerprints=(
                                ComponentTemplate
                                .RoutedTemplateFingerprint,
                            ),
                            DomainFingerprint=(
                                ComponentProblem.ProblemFingerprint
                            ),
                            ExpansionCount=(
                                ComponentSolve.ExpansionCount
                            ),
                            DomainComplete=True,
                            OwnershipComplete=True,
                            RealizabilityComplete=True,
                            Exhaustive=False,
                        )
                    )
                    InterfaceAttemptDiagnostics.append({
                        "CandidateId": (
                            InterfaceCandidate.CandidateId
                        ),
                        "PlacementFingerprint": (
                            InterfaceCandidate
                            .PlacementFingerprint
                        ),
                        "ComponentCutEpoch": InterfaceCutEpoch,
                        "ComponentVariant": ComponentVariantForState,
                        "RetainedPlacementResourceCacheHit": (
                            RetainedPlacementResourceCacheHit
                        ),
                        "ActiveComponentCutSignals": sorted(
                            ActiveComponentCutSignals
                        ),
                        "Result": "global-handoff-failed",
                        "Failure": (
                            Error.Failure.ToDictionary()
                        ),
                        "ComponentRoutingProblem": (
                            ComponentProblem.ToDictionary()
                        ),
                        "RoutedComponentTemplate": (
                            ComponentTemplate.ToDictionary()
                        ),
                        "ComponentRoutingSolve": {
                            "Status": ComponentSolve.Status,
                            "ProofFingerprint": (
                                ComponentSolve.ProofFingerprint
                            ),
                            "ExpansionCount": (
                                ComponentSolve.ExpansionCount
                            ),
                            "Diagnostics": (
                                ComponentSolve.Diagnostics
                            ),
                        },
                        "OrdinaryGlobalHandoff": {
                            "Entered": True,
                            "Completed": False,
                            "ImmutableClaims": True,
                        },
                        "ComponentCutEpochAttempts": (
                            StateAttemptDiagnostics
                        ),
                    })
                    continue
                FailureDiagnostics = dict(
                    Error.Failure.Diagnostics or {}
                )
                ComponentSolveDiagnostics = (
                    FailureDiagnostics.get(
                        "ComponentRoutingSolve",
                        {},
                    )
                )
                ComponentProblemDiagnostics = (
                    FailureDiagnostics.get(
                        "ComponentRoutingProblem",
                        {},
                    )
                )
                ComponentSolveStatus = (
                    str(
                        ComponentSolveDiagnostics.get(
                            "Status",
                            "",
                        )
                    )
                    if isinstance(
                        ComponentSolveDiagnostics,
                        dict,
                    )
                    else ""
                )
                RejectedAssignment = FailureDiagnostics.get(
                    "RejectedInterfaceAssignment",
                    {},
                )
                RejectedProblem = (
                    RejectedAssignment.get("Problem", {})
                    if isinstance(RejectedAssignment, dict)
                    else {}
                )
                ExactEmptyTerminalProof = bool(
                    Error.Failure.Stage == "ClusterBoundaryLease"
                    and Error.Failure.Detail.startswith(
                        "boundary lease terminal has no legal portal stem"
                    )
                    and not InterfaceDeadline.IsExpired()
                )
                CompleteAccessCertificateProof = bool(
                    Error.Failure.Stage
                    == "ComponentAccessCertification"
                    and FailureDiagnostics.get("Complete", False)
                    and not FailureDiagnostics.get("Feasible", True)
                )
                CompletePhysicalAssemblyUnsat = bool(
                    Error.Failure.Reason
                    in {
                        RoutingFailureReason
                        .ComponentPortAssignmentUnsatisfiable,
                        RoutingFailureReason
                        .ComponentChannelCapacityUnsatisfiable,
                    }
                    and not InterfaceDeadline.IsExpired()
                )
                DomainComplete = bool(
                    RejectedProblem.get("DomainComplete", False)
                    if isinstance(RejectedProblem, dict)
                    else False
                ) or bool(
                    FailureDiagnostics.get(
                        "ClusterInterfaceDomainComplete",
                        False,
                    )
                    or FailureDiagnostics.get(
                        "ComponentFabricConstructionComplete",
                        False,
                    )
                ) or ExactEmptyTerminalProof or (
                    CompleteAccessCertificateProof
                ) or CompletePhysicalAssemblyUnsat
                if isinstance(ComponentProblemDiagnostics, dict):
                    DomainComplete = bool(
                        DomainComplete
                        or ComponentProblemDiagnostics.get(
                            "DomainComplete",
                            False,
                        )
                    )
                StateIncomplete = bool(
                    Error.Failure.Reason
                    in {
                        RoutingFailureReason
                        .ClusterInterfaceSolveIncomplete,
                        RoutingFailureReason
                        .PhysicalComponentAssemblyIncomplete,
                        RoutingFailureReason.RuntimeBudgetExceeded,
                    }
                    or InterfaceDeadline.IsExpired()
                )
                if ComponentSolveStatus:
                    StateIncomplete = (
                        ComponentSolveStatus == "incomplete"
                        or InterfaceDeadline.IsExpired()
                    )
                OwnershipCoreFingerprint = str(
                    FailureDiagnostics.get(
                        "AuthoritativeCutAccessDomainFingerprint",
                        "",
                    )
                    or FailureDiagnostics.get(
                        "RepeatedAssignmentFingerprint",
                        "",
                    )
                    or BuildStableFingerprint(
                        FailureDiagnostics.get(
                            "ConflictGraph",
                            {},
                        )
                    )
                )
                DomainFingerprint = str(
                    FailureDiagnostics.get(
                        "AuthoritativeAccessDomainFingerprint",
                        "",
                    )
                    or FailureDiagnostics.get(
                        "CertificateFingerprint",
                        "",
                    )
                    or (
                        RejectedProblem.get(
                            "ComponentFingerprint",
                            "",
                        )
                        if isinstance(RejectedProblem, dict)
                        else ""
                    )
                )
                FinalOwnershipUnsatisfiable = bool(
                    ExactEmptyTerminalProof
                    or CompleteAccessCertificateProof
                    or CompletePhysicalAssemblyUnsat
                    or
                    FailureDiagnostics.get(
                        "OwnershipSearchComplete",
                        False,
                    )
                    or FailureDiagnostics.get(
                        "ComponentFabricConstructionComplete",
                        False,
                    )
                    or
                    FailureDiagnostics.get(
                        "CompleteAssignmentCutProof",
                        False,
                    )
                    or
                    (
                        isinstance(
                            FailureDiagnostics.get(
                                "MandatoryAccessProof"
                            ),
                            dict,
                        )
                        and FailureDiagnostics[
                            "MandatoryAccessProof"
                        ].get("Complete", False)
                        and not FailureDiagnostics[
                            "MandatoryAccessProof"
                        ].get("BudgetExhausted", False)
                        and not FailureDiagnostics[
                            "MandatoryAccessProof"
                        ].get("DeadlineExceeded", False)
                    )
                    or
                    FailureDiagnostics.get(
                        "AuthoritativeCutAccessDomainFingerprint",
                        "",
                    )
                    or Error.Failure.Detail.startswith(
                        "no complete cluster-interface"
                    )
                    or Error.Failure.Detail.startswith(
                        "candidate-realizability nogoods exhausted"
                    )
                )
                if ComponentSolveStatus:
                    FinalOwnershipUnsatisfiable = (
                        ComponentSolveStatus
                        == "architectural-unsatisfiable"
                    )
                RealizabilityComplete = bool(
                    FinalOwnershipUnsatisfiable
                    or Error.Failure.Detail.startswith(
                        "candidate-realizability nogoods exhausted"
                    )
                )
                if ComponentSolveStatus:
                    RealizabilityComplete = (
                        ComponentSolveStatus
                        == "architectural-unsatisfiable"
                    )
                StateExhaustive = bool(
                    DomainComplete
                    and not StateIncomplete
                    and RealizabilityComplete
                )
                if not StateExhaustive:
                    StateIncomplete = True
                StateStatus = (
                    "incomplete"
                    if StateIncomplete
                    else (
                        "ownership-unsatisfiable"
                        if FinalOwnershipUnsatisfiable
                        else "realizability-unsatisfiable"
                    )
                )
                InterfaceStateProofs.append(
                    ClusterInterfaceStateProof(
                        PlacementStateFingerprint=(
                            InterfaceCandidate.PlacementFingerprint
                        ),
                        Status=StateStatus,
                        ChannelFingerprint=ChannelFingerprint,
                        TransformFingerprint=TransformFingerprint,
                        OwnershipUnsatCoreFingerprint=(
                            OwnershipCoreFingerprint
                            if FinalOwnershipUnsatisfiable
                            else ""
                        ),
                        OwnershipUnsatSignals=tuple(
                            Error.Failure.AffectedNets
                            if FinalOwnershipUnsatisfiable
                            else ()
                        ),
                        AssignmentFingerprints=tuple(
                            StateAssignmentFingerprints
                        ),
                        RealizabilityNogoods=tuple(
                            StateRealizabilityNogoods
                        ),
                        DomainFingerprint=DomainFingerprint,
                        ExpansionCount=int(
                            FailureDiagnostics.get(
                                "ExpansionCount",
                                0,
                            )
                        ),
                        DomainComplete=DomainComplete,
                        OwnershipComplete=(
                            DomainComplete and not StateIncomplete
                        ),
                        RealizabilityComplete=RealizabilityComplete,
                        Exhaustive=StateExhaustive,
                    )
                )
                InterfaceAttemptDiagnostics.append({
                    "CandidateId": InterfaceCandidate.CandidateId,
                    "PlacementFingerprint": (
                        InterfaceCandidate.PlacementFingerprint
                    ),
                    "ComponentCutEpoch": InterfaceCutEpoch,
                    "ComponentVariant": ComponentVariantForState,
                    "RetainedPlacementResourceCacheHit": (
                        RetainedPlacementResourceCacheHit
                    ),
                    "ActiveComponentCutSignals": sorted(
                        ActiveComponentCutSignals
                    ),
                    "Result": (
                        "incomplete"
                        if StateIncomplete
                        else "unsatisfiable"
                    ),
                    "Failure": {
                        "Reason": Error.Failure.Reason.value,
                        "Stage": Error.Failure.Stage,
                        "AffectedNets": list(
                            Error.Failure.AffectedNets
                        ),
                        "Detail": Error.Failure.Detail,
                        "OwnershipUnsatCoreFingerprint": (
                            OwnershipCoreFingerprint
                        ),
                        "DomainFingerprint": DomainFingerprint,
                        "ExpansionCount": int(
                            FailureDiagnostics.get(
                                "ExpansionCount",
                                0,
                            )
                        ),
                        "DomainComplete": DomainComplete,
                        "OwnershipComplete": (
                            DomainComplete and not StateIncomplete
                        ),
                        "RealizabilityComplete": (
                            RealizabilityComplete
                        ),
                        "Exhaustive": StateExhaustive,
                        "ComponentRoutingSolve": (
                            ComponentSolveDiagnostics
                        ),
                        "ComponentRoutingProblem": (
                            ComponentProblemDiagnostics
                        ),
                        "Diagnostics": FailureDiagnostics,
                    },
                    "Transforms": Transforms,
                    "RealizabilityAttempts": (
                        StateAttemptDiagnostics
                    ),
                    "RealizabilityNogoods": [
                        Nogood.ToDictionary()
                        for Nogood in StateRealizabilityNogoods
                    ],
                })
                if StateExhaustive and not StateIncomplete:
                    PortDomainSizes = FailureDiagnostics.get(
                        "PortDomainSizes",
                        {},
                    )
                    PortDomainComplete = FailureDiagnostics.get(
                        "PortDomainGenerationComplete",
                        {},
                    )
                    CompleteEmptyPortSignals = tuple(sorted(
                        str(Signal)
                        for Signal, Size in (
                            PortDomainSizes.items()
                            if isinstance(PortDomainSizes, dict)
                            else ()
                        )
                        if (
                            int(Size) == 0
                            and isinstance(PortDomainComplete, dict)
                            and bool(PortDomainComplete.get(Signal, False))
                        )
                    ))
                    ProvenPortAssignmentCore = tuple(sorted(set(map(
                        str,
                        FailureDiagnostics.get(
                            "PortAssignmentUnsatCoreSignals",
                            (),
                        ),
                    ))))
                    ComponentAccessCoreSignals = (
                        ProvenPortAssignmentCore
                        or CompleteEmptyPortSignals
                        or tuple(map(str, Error.Failure.AffectedNets))
                    )
                    if (
                        ComponentAccessCoreSignals
                        and Error.Failure.Reason
                        != RoutingFailureReason
                        .ComponentChannelCapacityUnsatisfiable
                        and not InterfaceDeadline.IsExpired()
                    ):
                        ActiveComponentCutSignals.update(
                            ComponentAccessCoreSignals
                        )
                        ReorderRemainingPlacementsForAccessCore(
                            RetainedPlacementFingerprint
                        )
                        InterfaceAttemptDiagnostics.append({
                            "CandidateId": (
                                RetainedBaseInterfaceCandidate.CandidateId
                            ),
                            "PlacementFingerprint": (
                                RetainedPlacementFingerprint
                            ),
                            "ComponentCutEpoch": InterfaceCutEpoch,
                            "ComponentVariant": (
                                ComponentVariantForState
                            ),
                            "Result": (
                                "component-access-core-ranked-remaining-"
                                "placements"
                            ),
                            "ActiveComponentCutSignals": sorted(
                                ActiveComponentCutSignals
                            ),
                            "SourceFailureFingerprint": (
                                FailureDiagnostics.get(
                                    "PortAssignmentUnsatCoreFingerprint",
                                    OwnershipCoreFingerprint,
                                )
                            ),
                        })
                if StateIncomplete:
                    PreservePhysicalReason = (
                        Error.Failure.Reason
                        in {
                            RoutingFailureReason
                            .ClusterInterfaceSolveIncomplete,
                            RoutingFailureReason
                            .PhysicalComponentAssemblyIncomplete,
                        }
                    )
                    InterfaceSolveIncompleteError = RoutingStageError(
                        replace(
                            Error.Failure,
                            Reason=(
                                Error.Failure.Reason
                                if PreservePhysicalReason
                                else RoutingFailureReason
                                .ClusterInterfaceSolveIncomplete
                            ),
                            Stage=(
                                Error.Failure.Stage
                                if PreservePhysicalReason
                                else "ClusterInterfaceSolveIncomplete"
                            ),
                            RepairActions=(),
                            Diagnostics={
                                **FailureDiagnostics,
                                "CompletedComponentStateAttempts": list(
                                    InterfaceAttemptDiagnostics
                                ),
                                "ComponentPlacementSearchOrder": (
                                    "component-outer-placement-inner"
                                ),
                                "InterfaceSolve": {
                                    "Complete": False,
                                    "DomainComplete": DomainComplete,
                                    "OwnershipComplete": False,
                                    "RealizabilityComplete": (
                                        RealizabilityComplete
                                    ),
                                    "ExecutableRepairAllowed": False,
                                },
                            },
                        )
                    )
                    break
        LatestInterfaceProofByPlacement: dict[
            str, ClusterInterfaceStateProof
        ] = {}
        for StateProof in InterfaceStateProofs:
            LatestInterfaceProofByPlacement[
                StateProof.PlacementStateFingerprint
            ] = StateProof
        InterfaceStateProofs = list(
            LatestInterfaceProofByPlacement.values()
        )
        InterfaceProof = BuildClusterInterfaceUnsatProof(
            InterfaceStateProofs
        )
        InterfaceProofFingerprint = str(
            InterfaceProof["ProofFingerprint"]
        )
        InterfacePortfolioProblem = ClusterInterfacePortfolioProblem(
            PlacementStates=tuple(sorted(
                InterfacePlacementStatesByFingerprint.values(),
                key=lambda State: State.StateFingerprint,
            )),
            MaximumPlacementStates=6,
            MaximumAffectedClusters=3,
            StateAudits=InterfacePortfolioAudits,
        )
        PlacementGenerationDecisions.append({
            "Result": "exact-cluster-interface-solve",
            "PhysicalResourceModel": (
                "dedicated-cluster-interface-deck-v1"
            ),
            "RequestedStateCount": 6,
            "MaximumComponentSelectionCount": (
                MaximumRetainedComponentSelections
            ),
            "ComponentPlacementSearchStateCount": len(
                ComponentPlacementSearchDomain
            ),
            "ComponentSelectionOrder": (
                "component-outer-placement-inner"
            ),
            "GeneratedStateCount": len(RawInterfaceCandidates),
            "InterfaceDistinctStateCount": len(InterfaceCandidates),
            "PortfolioStateAudit": [
                Audit.ToDictionary()
                for Audit in InterfacePortfolioAudits
            ],
            "GeneratorRejectionAudit": (
                InterfaceGeneratorRejectionAudit
            ),
            "PortfolioProblem": (
                InterfacePortfolioProblem.ToDictionary()
            ),
            "AttemptedStateCount": len(InterfaceAttemptDiagnostics),
            "FeasibleStateCount": len(InterfaceFeasibleCandidates),
            "Attempts": InterfaceAttemptDiagnostics,
            "StateProofs": InterfaceProof["StateProofs"],
            "ProofFingerprint": InterfaceProofFingerprint,
            "StageSchedule": InterfaceStageSchedule.ToDictionary(),
            "BroadFallbackAllowed": False,
            "ExecutableLegacyRepairCascade": False,
        })
        if InterfaceSolveIncompleteError is not None:
            LastRoutingError = InterfaceSolveIncompleteError
            LastStructuredRoutingError = InterfaceSolveIncompleteError
            CandidateRoutingIterable = ()
        elif (
            not InterfaceFeasibleCandidates
            and LastGlobalHandoffError is not None
        ):
            GlobalHandoffAttempts = tuple(
                StateAttempt
                for Attempt in InterfaceAttemptDiagnostics
                for StateAttempt in (
                    *Attempt.get("RealizabilityAttempts", ()),
                    *Attempt.get("ComponentCutEpochAttempts", ()),
                )
                if StateAttempt.get("Result") == (
                    "detailed-failure-reject-physical-plan"
                )
            )
            ComponentPortfolioNogoodFingerprint = (
                BuildStableFingerprint({
                    "InterfaceProofFingerprint": (
                        InterfaceProofFingerprint
                    ),
                    "PlacementStateFingerprints": [
                        Proof.PlacementStateFingerprint
                        for Proof in InterfaceStateProofs
                    ],
                    "GlobalHandoffFailureFingerprints": [
                        BuildStableFingerprint(
                            Attempt.get("UnderlyingFailure", {})
                        )
                        for Attempt in GlobalHandoffAttempts
                    ],
                })
            )
            ComponentPortfolioNogoodRecord = {
                "Fingerprint": ComponentPortfolioNogoodFingerprint,
                "InterfaceProofFingerprint": (
                    InterfaceProofFingerprint
                ),
                "AffectedClusterLimit": 3,
                "RetainedStateCount": len(
                    InterfaceStateProofs
                ),
                "GlobalHandoffFailureCount": len(
                    GlobalHandoffAttempts
                ),
                "AffectedSignals": sorted({
                    str(Signal)
                    for Attempt in GlobalHandoffAttempts
                    for Signal in (
                        Attempt.get("UnderlyingFailure", {})
                        .get("AffectedNets", ())
                    )
                }),
                "ActiveSignals": sorted(
                    ActiveComponentCutSignals
                ),
                "ElapsedSeconds": round(
                    monotonic() - Started,
                    6,
                ),
            }
            PortfolioFailure = RoutingStageError(RoutingFailure(
                Reason=(
                    RoutingFailureReason
                    .ComponentDetailedRoutingFailed
                ),
                Stage="PhysicalComponentAssemblyDomainExhausted",
                AffectedNets=tuple(sorted({
                    str(Signal)
                    for Attempt in GlobalHandoffAttempts
                    for Signal in (
                        Attempt.get("UnderlyingFailure", {})
                        .get("AffectedNets", ())
                    )
                })),
                Detail=(
                    "detailed routing rejected every complete physical "
                    "assembly plan across the retained placement states"
                ),
                RepairActions=(),
                Diagnostics={
                    "RejectedPhysicalAssemblyDomain": {
                        **ComponentPortfolioNogoodRecord,
                        "RejectedPlanFingerprints": sorted({
                            str(Attempt.get(
                                "PhysicalAssemblyPlanFingerprint",
                                "",
                            ))
                            for Attempt in GlobalHandoffAttempts
                            if Attempt.get(
                                "PhysicalAssemblyPlanFingerprint"
                            )
                        }),
                        "BroadFallbackAllowed": False,
                        "SignalLevelRepairAllowed": False,
                        "PlacementRegenerationAllowed": False,
                    },
                    "InterfaceSolve": {
                        **InterfaceProof,
                        "Attempts": InterfaceAttemptDiagnostics,
                        "PortfolioProblem": (
                            InterfacePortfolioProblem.ToDictionary()
                        ),
                    },
                    "LastGlobalHandoffFailure": (
                        LastGlobalHandoffError
                        .Failure.ToDictionary()
                    ),
                },
            ))
            LastRoutingError = PortfolioFailure
            LastStructuredRoutingError = PortfolioFailure
            CandidateRoutingIterable = ()
        elif not InterfaceFeasibleCandidates:
            InterfaceFailure = RoutingStageError(RoutingFailure(
                Reason=(
                    RoutingFailureReason
                    .ClusterInterfaceArchitectureUnsatisfiable
                ),
                Stage="ClusterInterfaceArchitectureUnsatisfiable",
                Detail=(
                    "no retained channelized placement state admits a "
                    "complete capacity-one interface assignment"
                ),
                RepairActions=(),
                Diagnostics={
                    "InterfaceSolve": {
                        **InterfaceProof,
                        "Attempts": InterfaceAttemptDiagnostics,
                        "PortfolioProblem": (
                            InterfacePortfolioProblem.ToDictionary()
                        ),
                        "PhysicalResourceModel": (
                            "dedicated-cluster-interface-deck-v1"
                        ),
                        "ArchitectureInsufficient": True,
                        "BroadFallbackAllowed": False,
                    },
                },
            ))
            LastRoutingError = InterfaceFailure
            LastStructuredRoutingError = InterfaceFailure
            CandidateRoutingIterable = ()
        else:
            (
                _SelectedInterfaceObjective,
                SelectedInterfaceCandidate,
                SelectedRoutedComponentTemplate,
            ) = min(
                InterfaceFeasibleCandidates,
                key=lambda Value: Value[0],
            )
            CandidateRoutingIterable = (SelectedInterfaceCandidate,)
            JointPlacementStateEvents.append({
                "Status": "routed-component-template-selected",
                "CandidateId": SelectedInterfaceCandidate.CandidateId,
                "PlacementFingerprint": (
                    SelectedInterfaceCandidate.PlacementFingerprint
                ),
                "Objective": list(_SelectedInterfaceObjective),
                "RoutedTemplateFingerprint": (
                    SelectedRoutedComponentTemplate
                    .RoutedTemplateFingerprint
                ),
                "ExportedPortFingerprint": (
                    SelectedRoutedComponentTemplate
                    .ExportedPortFingerprint
                ),
                "ExecutableLegacyRepairCascade": False,
            })

    for CandidateRecord in CandidateRoutingIterable:
        try:
            Deadline.RaiseIfExpired(
                "PlacementCandidateSelection",
                {"PlacementAttempts": PlacementAttemptFailures},
            )
        except RoutingStageError as Error:
            LastRoutingError = Error
            LastStructuredRoutingError = Error
            break
        CandidatePlacement = CandidateRecord.Placement
        Placement = CandidatePlacement
        RoutingSpacing = CandidateRecord.RoutingSpacing
        # Materialize the selected packed local geometry before allocating its
        # routing slice.  This is immutable placement work, not routing work;
        # charging it to a dense lease ownership state leaves that state too
        # little time to exercise its cached portal/guide alternatives.
        CandidatePlacement = MaterializeSelectedJointPlacementLocalRouting(
            CandidateRecord,
            lambda Diagnostics: Deadline.RaiseIfExpired(
                "PlacementCandidateMaterialization",
                {
                    "CandidateId": CandidateRecord.CandidateId,
                    **Diagnostics,
                },
            ),
        )
        if (
            not ExactClusterInterfaceSolveEnabled
            and CandidateRecord.TopologyDemand is not None
            and RequiresDenseBoundaryRoutingReserve(
                CandidateRecord.TopologyDemand,
                Policy,
            )
            and ConsumedPairedLeaseRepairProfileFingerprints
            and PlacementCoordinatedCandidateDiversificationSignals
            and not dict(
                CandidatePlacement.Placed.LocalRouteDiagnostics or {}
            ).get("__ClusterPinBankRepair__", {})
        ):
            (
                PreappliedDenseProfile,
                PreappliedDenseProfileFingerprint,
            ) = ApplyCoordinatedCandidateDiversificationProfile(
                CandidatePlacement,
                PlacementCoordinatedCandidateDiversificationSignals,
            )
            if PreappliedDenseProfile:
                JointPlacementStateEvents.append({
                    "Status": "dense-profile-preapplied-before-routing",
                    "CandidateId": CandidateRecord.CandidateId,
                    "Signals": sorted(
                        PlacementCoordinatedCandidateDiversificationSignals
                    ),
                    "ProfileFingerprint": PreappliedDenseProfileFingerprint,
                })
        if CandidatePlacement is not CandidateRecord.Placement:
            CandidateRecord = replace(
                CandidateRecord,
                Placement=CandidatePlacement,
            )
        if (
            not ExactClusterInterfaceSolveEnabled
            and TopologyDemand.RequiresJointPortfolio
            and CandidateRecord.CutInterfaceDifference > 0
        ):
            CandidatePlacement.Placed.LocalRouteDiagnostics = {
                **(
                    CandidatePlacement.Placed.LocalRouteDiagnostics
                    or {}
                ),
                "__CandidateRealizabilityContinuation__": {
                    "Eligible": (
                        CandidateRecord.AccessDistinctCandidateCount == 1
                    ),
                    "CandidateId": CandidateRecord.CandidateId,
                    "CutInterfaceDifference": (
                        CandidateRecord.CutInterfaceDifference
                    ),
                    "AccessDistinctCandidateCount": (
                        CandidateRecord.AccessDistinctCandidateCount
                    ),
                    "AssignmentCutFingerprint": (
                        CandidateRecord.AssignmentCutFingerprint
                    ),
                },
            }
        Placement = CandidatePlacement
        CandidateResources = RoutingResourcesByFingerprint.get(
            CandidateRecord.PlacementFingerprint
        )
        if CandidateResources is None:
            CandidateResources = BuildRoutingResources(
                CandidatePlacement.Placed,
                WorkCheck=lambda Diagnostics: Deadline.RaiseIfExpired(
                    "PlacementCandidateResourceMaterialization",
                    {
                        "CandidateId": CandidateRecord.CandidateId,
                        **Diagnostics,
                    },
                ),
            )
            RoutingResourcesByFingerprint[
                CandidateRecord.PlacementFingerprint
            ] = CandidateResources
        if ExactClusterInterfaceSolveEnabled:
            FrozenComponentTemplate = (
                RoutedComponentTemplatesByPlacementFingerprint.get(
                    CandidateRecord.PlacementFingerprint
                )
            )
            if FrozenComponentTemplate is None:
                raise RoutingStageError(RoutingFailure(
                    Reason=(
                        RoutingFailureReason
                        .ClusterInterfaceInvariantViolation
                    ),
                    Stage="RoutedComponentHandoff",
                    Detail=(
                        "the selected placement lost its immutable routed "
                        "component before global routing"
                    ),
                    Diagnostics={
                        "CandidateId": CandidateRecord.CandidateId,
                        "PlacementFingerprint": (
                            CandidateRecord.PlacementFingerprint
                        ),
                    },
                ))
            PlacedTemplates = tuple(
                getattr(
                    CandidatePlacement.Placed,
                    "RoutedComponentTemplates",
                    (),
                )
                or ()
            )
            if not any(
                Value.RoutedTemplateFingerprint
                == FrozenComponentTemplate.RoutedTemplateFingerprint
                for Value in PlacedTemplates
            ):
                raise RoutingStageError(RoutingFailure(
                    Reason=(
                        RoutingFailureReason
                        .ClusterInterfaceInvariantViolation
                    ),
                    Stage="RoutedComponentHandoff",
                    Detail=(
                        "the selected placement identity does not contain "
                        "the selected routed-component template"
                    ),
                    Diagnostics={
                        "CandidateId": CandidateRecord.CandidateId,
                        "PlacementFingerprint": (
                            CandidateRecord.PlacementFingerprint
                        ),
                    },
                ))
            CandidateResources.FrozenRoutedComponentTemplate = (
                FrozenComponentTemplate
            )
        SeedPortableRawPortalGeometryCaches(
            CandidateResources
        )
        CandidateJointDiagnostics = dict(
            CandidatePlacement.Placed.LocalRouteDiagnostics or {}
        ).get("__JointClusterPlacement__", {})
        CandidateJointIndex = CandidateJointDiagnostics.get(
            "SelectedCandidateIndex"
        )
        AttemptedCandidateIds = {
            str(Entry.get("CandidateId"))
            for Entry in PlacementAttemptFailures
            if Entry.get("CandidateId") is not None
        }
        HasRemainingPlacementAlternative = (
            any(
                Candidate.CandidateId != CandidateRecord.CandidateId
                and Candidate.CandidateId not in AttemptedCandidateIds
                for Candidate in OrderedPlacements
            )
            or bool(PendingJointPlacementStates)
            or len(ConsumedDeferredRequestIndexes)
            < len(GenerationPlan.DeferredRequests)
        )
        RemainingRuntimeSeconds = max(0.001, Deadline.RemainingSeconds())
        ActiveRelocatedPortfolioCandidate = (
            PlacementCandidateMatchesActiveJointPortfolio(
                CandidateRecord,
                ActiveJointPortfolioIdentityFingerprint,
            )
        )
        RemainingRetainedCandidates = sum(
            Candidate.CandidateId not in AttemptedCandidateIds
            for Candidate in (
                [
                    ActiveCandidate
                    for ActiveCandidate in CandidateRecords
                    if PlacementCandidateMatchesActiveJointPortfolio(
                        ActiveCandidate,
                        ActiveJointPortfolioIdentityFingerprint,
                    )
                ]
                if ActiveRelocatedPortfolioCandidate
                else OrderedPlacements
            )
        ) + len(PendingJointPlacementStates)
        RemainingStateCountRebound = (
            ApplyRemainingExactLegalJointStateCount(
                CandidatePlacement,
                max(1, RemainingRetainedCandidates),
            )
        )
        if RemainingStateCountRebound:
            JointPlacementStateEvents.append({
                "Status": "remaining-joint-state-count-rebound",
                "CandidateId": CandidateRecord.CandidateId,
                "RemainingExactLegalRetainedStateCount": (
                    max(1, RemainingRetainedCandidates)
                ),
            })
        HighFanoutFeedbackRoutingSlots = PlacementFeedbackRoutingSlotCount(
            HasRemainingPlacementAlternative=(
                HasRemainingPlacementAlternative
            ),
            ReconvergentAccessPressure=(
                TopologyPressure.ReconvergentAccessPressure
            ),
            AttemptedCandidateCount=len(AttemptedCandidateIds),
        )
        # Lease requests are attached by local-route materialization below,
        # after this budget must be fixed. Bind allocation to the immutable
        # demand profile retained with this candidate, not mutable flow state.
        DenseBoundaryLeaseRouting = bool(
            CandidateRecord.TopologyDemand is not None
            and RequiresDenseBoundaryRoutingReserve(
                CandidateRecord.TopologyDemand,
                Policy,
            )
        )
        CandidateRouteDiagnostics = (
            CandidatePlacement.Placed.LocalRouteDiagnostics or {}
        )
        CandidateRelocationDiagnostics = (
            CandidateRouteDiagnostics.get("__PlacementRelocation__", {})
        )
        CandidateJointDiagnostics = (
            CandidateRouteDiagnostics.get("__JointClusterPlacement__", {})
        )
        CandidateSerializedAssignmentConstraints = (
            CandidateJointDiagnostics.get(
                "ActiveAssignmentConstraints",
                CandidateJointDiagnostics.get(
                    "AssignmentConstraints",
                    {},
                ),
            )
            if isinstance(CandidateJointDiagnostics, dict)
            else {}
        )
        CandidateHasHigherOrderCutConstraints = bool(
            isinstance(
                CandidateSerializedAssignmentConstraints,
                dict,
            )
            and (
                CandidateSerializedAssignmentConstraints.get(
                    "ActiveHigherOrderSignalSets"
                )
                or CandidateSerializedAssignmentConstraints.get(
                    "HigherOrderSignalSets"
                )
            )
        )
        IsBoundedDenseLeaseControlRetry = bool(
            DenseBoundaryLeaseRouting
            and isinstance(CandidateRelocationDiagnostics, dict)
            and int(
                CandidateRelocationDiagnostics.get(
                    "CoordinatedCandidateDiversificationFixedLevel",
                    0,
                )
            ) == 1
            and not (
                isinstance(CandidateJointDiagnostics, dict)
                and SerializedPlacementAssignmentConstraintsAreActive(
                    CandidateJointDiagnostics.get(
                        "ActiveAssignmentConstraints"
                    )
                )
            )
        )
        HasClusterPinBankRepair = bool(
            DenseBoundaryLeaseRouting
            and isinstance(
                CandidateRouteDiagnostics.get(
                    "__ClusterPinBankRepair__",
                    {},
                ),
                dict,
            )
            and CandidateRouteDiagnostics.get("__ClusterPinBankRepair__", {})
            .get("Signals")
        )
        IsTransactionalEndpointRepair = bool(
            isinstance(
                CandidateRouteDiagnostics.get(
                    "__PlacementRecipe__",
                    {},
                ),
                dict,
            )
            and CandidateRouteDiagnostics.get(
                "__PlacementRecipe__",
                {},
            ).get("TransactionalClusterEndpointRepair", False)
        )
        ReserveClusterPinBankRepairSeconds = bool(
            DenseBoundaryLeaseRouting
            and not HasClusterPinBankRepair
            and isinstance(CandidateJointDiagnostics, dict)
            and SerializedPlacementAssignmentConstraintsAreActive(
                CandidateJointDiagnostics.get(
                    "ActiveAssignmentConstraints"
                )
            )
        )
        # The ranked initial topology state must run long enough to expose the
        # complete assignment cut that creates one relocated portfolio. Once
        # that exact-cut portfolio is active, divide its live deadline across
        # every unattempted sibling so one geometry cannot consume the floor
        # reserved for the others. A terminal constraint-epoch refresh remains
        # part of that active relocated portfolio; its rank determines order,
        # not a renewed lead-time premium.
        PlannedRoutingSlots = RetainedPlacementRoutingSlotCount(
            RemainingRetainedCandidates=RemainingRetainedCandidates,
            HighFanoutFeedbackRoutingSlots=(
                HighFanoutFeedbackRoutingSlots
            ),
            HasRemainingPlacementAlternative=(
                HasRemainingPlacementAlternative
            ),
            TopologyPortfolioTriggered=(
                TopologyDemand.RequiresJointPortfolio
            ),
            AttemptedCandidateCount=len(AttemptedCandidateIds),
        )
        # A dense lease interface is itself a bounded ownership portfolio.
        # RoutePcbDesign divides this candidate's allocation into exact lease
        # states after raw geometry is cached. Dividing again here would leave
        # no state enough time to materialize a capacity-one assignment.
        if DenseBoundaryLeaseRouting:
            PlannedRoutingSlots = (
                max(1, RemainingRetainedCandidates)
                if ActiveRelocatedPortfolioCandidate
                else 1
            )
        CandidateRoutingSeconds = (
            RemainingRuntimeSeconds
            * TopologyPortfolioRoutingFraction(
                HasRemainingPlacementAlternative=(
                    HasRemainingPlacementAlternative
                ),
                AttemptedCandidateCount=len(AttemptedCandidateIds),
                AuthoritativeMandatoryAccessConflictObserved=(
                    TerminalConstraintEpochAuthoritativeAccessConflictObserved
                ),
            )
            if (
                ActiveRelocatedPortfolioCandidate
                and TerminalConstraintEpochPortfolioIdentityFingerprint
                and CandidateRecord.JointPortfolioIdentityFingerprint
                == TerminalConstraintEpochPortfolioIdentityFingerprint
                and TerminalConstraintEpochAuthoritativeAccessConflictObserved
            )
            else
            RemainingRuntimeSeconds / PlannedRoutingSlots
            if (
                ActiveRelocatedPortfolioCandidate
                or not CandidateJointDiagnostics
                or not TopologyDemand.RequiresJointPortfolio
            )
            else (
                RemainingRuntimeSeconds
                * TopologyPortfolioRoutingFraction(
                    HasRemainingPlacementAlternative=(
                        HasRemainingPlacementAlternative
                    ),
                    AttemptedCandidateCount=len(
                        AttemptedCandidateIds
                    ),
                )
            )
        )
        AdaptiveAttemptRuntimeSeconds = min(
            Policy.AdaptiveRouting.MaximumRuntimeSeconds,
            max(
                0.001,
                min(
                    CandidateRoutingSeconds,
                    JointPortfolioSliceSeconds,
                )
                if (
                    CandidateJointDiagnostics
                    and JointPortfolioSliceSeconds is not None
                    and not DenseBoundaryLeaseRouting
                )
                else DenseRetainedLeaseProofSliceSeconds(
                    RemainingSeconds=RemainingRuntimeSeconds,
                    RemainingRetainedCandidates=(
                        max(1, RemainingRetainedCandidates)
                    ),
                    PrioritizeHigherOrderCutProof=(
                        CandidateHasHigherOrderCutConstraints
                        and int(CandidateJointIndex or 0) == 0
                    ),
                )
                if (
                    DenseBoundaryLeaseRouting
                    and ActiveRelocatedPortfolioCandidate
                )
                else RemainingRuntimeSeconds / PlannedRoutingSlots,
            ),
        )
        if (
            CandidateRouteDiagnostics.get(
                "__PlacementRecipe__",
                {},
            ).get("IsPostPinBankRepairEpoch", False)
            and CandidateRouteDiagnostics.get(
                "__PlacementRecipe__",
                {},
            ).get("EnableInternalPinBankGeometryRepair", False)
        ):
            # The targeted pin-bank epoch intentionally contains one exact
            # state. Do not strand that state behind the inherited six-state
            # portfolio slice; it may consume only the remaining shared time
            # less the established publication reserve.
            AdaptiveAttemptRuntimeSeconds = min(
                Policy.AdaptiveRouting.MaximumRuntimeSeconds,
                max(0.001, RemainingRuntimeSeconds - 2.0),
            )
        if (
            ReserveClusterPinBankRepairSeconds
            and RemainingRuntimeSeconds > 6.0
        ):
            # Preserve one five-second exact ownership attempt for a
            # repeated paired subcut discovered by this relocated geometry.
            # This reassigns its existing slice; it never expands the RCA8
            # wall-clock budget or adds a broad generator.
            AdaptiveAttemptRuntimeSeconds = min(
                AdaptiveAttemptRuntimeSeconds,
                RemainingRuntimeSeconds - 5.0,
            )
        if IsBoundedDenseLeaseControlRetry:
            # This is the single paired-cut domain probe. It establishes a
            # new candidate/failure fingerprint but must leave the shared
            # deadline for the exact relocated geometry it may authorize.
            AdaptiveAttemptRuntimeSeconds = min(
                AdaptiveAttemptRuntimeSeconds,
                1.5,
            )
        elif ShouldBoundClusterPinBankRepairProbe(
            HasClusterPinBankRepair,
            IsTransactionalEndpointRepair,
        ):
            # The pin-bank state is an authoritative discriminating probe,
            # not another broad route search. Its first failure must return
            # the resulting complete cut to joint geometry while sufficient
            # time remains for that exact placement repair.
            AdaptiveAttemptRuntimeSeconds = min(
                AdaptiveAttemptRuntimeSeconds,
                5.0,
            )
        if ExactClusterInterfaceSolveEnabled:
            # The feasibility gate has already evaluated the complete bounded
            # placement portfolio and frozen exactly one ownership state.
            # Old retained-state/probe slices no longer apply; the selected
            # state owns the remaining route budget less publication time.
            AdaptiveAttemptRuntimeSeconds = min(
                Policy.AdaptiveRouting.MaximumRuntimeSeconds,
                max(0.001, RemainingRuntimeSeconds - 2.0),
            )
        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
            print(
                "[debug] authoritative: trying placement candidate "
                f"id={CandidateRecord.CandidateId} "
                f"claims={len(CandidatePlacement.Placed.LocalRouteClaims or ())} "
                f"packed={bool(CandidatePlacement.PackedClusters)} "
                f"spacing={RoutingSpacing}",
                flush=True,
            )
            print(
                "[debug] authoritative: policy budgets "
                f"overall={Policy.RuntimeBudgetSeconds:.3f}s "
                f"adaptive_max={AdaptiveAttemptRuntimeSeconds:.3f}s "
                f"has_alternative={HasRemainingPlacementAlternative}",
                flush=True,
            )
        AttemptPolicy = replace(
            Policy,
            # RouteAuthoritativeResources derives its internal adaptive
            # expiry from RuntimeBudgetSeconds.  Keeping the global remaining
            # duration here silently lets the first joint candidate outlive
            # its absolute portfolio slice even though AttemptDeadline is
            # shorter.  Give both layers of the router the same candidate
            # budget.
            RuntimeBudgetSeconds=AdaptiveAttemptRuntimeSeconds,
            AdaptiveRouting=replace(
                Policy.AdaptiveRouting,
                MaximumRuntimeSeconds=AdaptiveAttemptRuntimeSeconds,
            ),
        )
        AttemptStarted = monotonic()
        AdaptiveAttemptExpiresAt = min(
            Deadline.ExpiresAt,
            AttemptStarted + AdaptiveAttemptRuntimeSeconds,
        )
        AttemptDeadline = RoutingDeadline(
            StartedAt=Deadline.StartedAt,
            ExpiresAt=AdaptiveAttemptExpiresAt,
        )
        if CandidateJointDiagnostics:
            JointPlacementStateEvents.append({
                "CandidateIndex": CandidateJointIndex,
                "Status": "routing",
                "CandidateId": CandidateRecord.CandidateId,
                "AllocatedRoutingSeconds": round(
                    AdaptiveAttemptRuntimeSeconds,
                    6,
                ),
                "Transforms": CandidateJointDiagnostics.get(
                    "SelectedTransforms", {}
                ),
            })

        def CheckCandidateValidation(
            Diagnostics: dict[str, object],
        ) -> None:
            AttemptDeadline.RaiseIfExpired(
                "PlacementCandidateValidation",
                {
                    "CandidateId": CandidateRecord.CandidateId,
                    "AdaptiveAttemptStartedAt": AttemptStarted,
                    "AdaptiveAttemptExpiresAt": AdaptiveAttemptExpiresAt,
                    **Diagnostics,
                },
            )

        try:
            CandidatePlacement = (
                MaterializeSelectedJointPlacementLocalRouting(
                    CandidateRecord,
                    CheckCandidateValidation,
                )
            )
            if CandidatePlacement is not CandidateRecord.Placement:
                CandidateRecord = replace(
                    CandidateRecord,
                    Placement=CandidatePlacement,
                )
                Placement = CandidatePlacement
            # The scoring-only joint placement owns the live retained-state
            # count used by the staged candidate scheduler.  Full local-route
            # materialization reconstructs immutable placement diagnostics,
            # whose recipe still contains the original six-state portfolio.
            # Rebind the scheduler marker after both a fresh materialization
            # and a cache hit so a sole remaining exact-legal candidate cannot
            # be mistaken for an unattempted sibling and advanced early.
            if ApplyRemainingExactLegalJointStateCount(
                CandidatePlacement,
                max(1, RemainingRetainedCandidates),
            ):
                JointPlacementStateEvents.append({
                    "Status": (
                        "materialized-remaining-joint-state-count-rebound"
                    ),
                    "CandidateId": CandidateRecord.CandidateId,
                    "RemainingExactLegalRetainedStateCount": (
                        max(1, RemainingRetainedCandidates)
                    ),
                })
            # Skip impossible candidates early and keep the retry budget for
            # deterministic alternatives that can actually be legalized.
            ValidatePlacedCellElectricalIsolation(
                CandidatePlacement.Placed,
                WorkCheck=CheckCandidateValidation,
            )
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: remaining_runtime_for_attempt="
                    f"{AttemptPolicy.RuntimeBudgetSeconds:.3f}s "
                    f"elapsed_from_start={monotonic()-Started:.3f}s",
                    flush=True,
                )
            Routed = (
                PreRoutedClusterInterfaceDesignsByPlacementFingerprint.get(
                    CandidateRecord.PlacementFingerprint
                )
            )
            if Routed is None:
                Routed = RoutePcbDesign(
                    CandidatePlacement,
                    ProgressCallback=ReportRoutingProgress,
                    Policy=AttemptPolicy,
                    Deadline=AttemptDeadline,
                    Resources=CandidateResources,
                )
            CapturePortableRawPortalGeometryCaches(
                CandidateResources
            )
            Deadline.RaiseIfExpired(
                "Routing",
                {"PlacementCandidate": CandidateRecord.CandidateId},
            )
            Deadline.RaiseIfExpired(
                "RoutedValidation",
                {
                    "Phase": "before",
                    "PlacementCandidate": CandidateRecord.CandidateId,
                },
            )
            if RoutedValidationCallback is not None:
                RoutedValidationCallback(Routed)
            Deadline.RaiseIfExpired(
                "RoutedValidation",
                {
                    "Phase": "after",
                    "PlacementCandidate": CandidateRecord.CandidateId,
                },
            )
            if RoutingPercentageSelectionEnabled:
                RecordRoutedCandidate(CandidateRecord, Placement, Routed)
            PlacementAttemptFailures.append({
                **CandidateRecord.ToDictionary(),
                "Result": "routed",
                "AdaptiveRuntimeBudgetSeconds": round(
                    AdaptiveAttemptRuntimeSeconds,
                    6,
                ),
                "ElapsedSeconds": round(monotonic() - AttemptStarted, 6),
            })
            if CandidateJointDiagnostics:
                JointPlacementStateEvents.append({
                    "CandidateIndex": CandidateJointIndex,
                    "Status": "routed",
                    "CandidateId": CandidateRecord.CandidateId,
                    "ElapsedSeconds": round(
                        monotonic() - AttemptStarted,
                        6,
                    ),
                })
            if not RoutingPercentageSelectionEnabled:
                SelectedCandidate = CandidateRecord
                break
            if (
                Deadline.RemainingSeconds()
                < Policy.MaterialObjective
                .MinimumRemainingRoutingPercentageSearchSeconds
            ):
                # A legal route is more valuable than spending the final
                # shared-deadline slice comparing alternatives we cannot
                # complete and validate.
                break
            # Keep routing bounded retained alternatives under the same
            # absolute deadline, then publish the smallest real route share.
            continue
        except (RoutingStageError, ValueError) as Error:
            CapturePortableRawPortalGeometryCaches(
                CandidateResources
            )
            LastRoutingError = Error
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: placement route rejected "
                    f"candidate={CandidateRecord.CandidateId} "
                    f"error_type={type(Error).__name__} error={Error}",
                    flush=True,
                )
            # A returned route is not eligible until every routed validation
            # and deadline check has completed successfully.
            Routed = None
            if ExactClusterInterfaceSolveEnabled:
                if isinstance(Error, RoutingStageError):
                    LastStructuredRoutingError = Error
                PlacementAttemptFailures.append({
                    **CandidateRecord.ToDictionary(),
                    "RoutingSpacing": RoutingSpacing,
                    "PackedNandPlacement": bool(
                        CandidatePlacement.PackedClusters
                    ),
                    "Failure": str(Error),
                    "AdaptiveRuntimeBudgetSeconds": round(
                        AdaptiveAttemptRuntimeSeconds,
                        6,
                    ),
                    "Diagnostics": (
                        Error.Failure.ToDictionary()
                        if isinstance(Error, RoutingStageError)
                        else {}
                    ),
                    "ElapsedSeconds": round(
                        monotonic() - AttemptStarted,
                        6,
                    ),
                    "DenseControlPath": (
                        "frozen-cluster-interface-assignment"
                    ),
                    "ExecutableLegacyRepairCascade": False,
                })
                PlacementGenerationDecisions.append({
                    "Result": "dense-routing-failure-terminal",
                    "CandidateId": CandidateRecord.CandidateId,
                    "ExecutableLegacyRepairCascade": False,
                    "BroadFallbackAllowed": False,
                    "Reason": (
                        Error.Failure.Reason.value
                        if isinstance(Error, RoutingStageError)
                        else type(Error).__name__
                    ),
                })
                break
            if (
                isinstance(Error, RoutingStageError)
                and Error.Failure.Reason
                == RoutingFailureReason.RuntimeBudgetExceeded
            ):
                LastStructuredRoutingError = Error
                TimedOutConflictSignals = (
                    ExtractCompletedEscalationRelocationSignals(
                        Error.Failure
                    )
                )
                if (
                    TimedOutConflictSignals
                    and TimedOutConflictSignals
                    != PlacementRelocationPrioritySignals
                ):
                    PlacementRelocationPrioritySignals = (
                        TimedOutConflictSignals
                    )
                    PlacementRelocationSignals = frozenset((
                        *PlacementRelocationSignals,
                        *TimedOutConflictSignals,
                    ))
                    NeedsFeedbackPlacementGeneration = True
                    PlacementGenerationDecisions.append({
                        "Result": "routing-timeout-conflict-feedback",
                        "CandidateId": CandidateRecord.CandidateId,
                        "PriorityRelocationSignals": sorted(
                            TimedOutConflictSignals
                        ),
                        "RelocationSignals": sorted(
                            PlacementRelocationSignals
                        ),
                    })
                PlacementAttemptFailures.append(
                    {
                        **CandidateRecord.ToDictionary(),
                        "RoutingSpacing": RoutingSpacing,
                        "PackedNandPlacement": bool(
                            CandidatePlacement.PackedClusters
                        ),
                        "Failure": str(Error),
                        "AdaptiveRuntimeBudgetSeconds": round(
                            AdaptiveAttemptRuntimeSeconds,
                            6,
                        ),
                        "Diagnostics": Error.Failure.ToDictionary(),
                        "ElapsedSeconds": round(
                            monotonic() - AttemptStarted,
                            6,
                        ),
                    }
                )
                if CandidateJointDiagnostics:
                    JointPlacementStateEvents.append({
                        "CandidateIndex": CandidateJointIndex,
                        "Status": "slice-expired",
                        "CandidateId": CandidateRecord.CandidateId,
                        "ElapsedSeconds": round(
                            monotonic() - AttemptStarted,
                            6,
                        ),
                        "AllocatedRoutingSeconds": round(
                            AdaptiveAttemptRuntimeSeconds,
                            6,
                        ),
                        "Failure": Error.Failure.ToDictionary(),
                    })
                # An adaptive candidate slice is not the absolute routing
                # deadline.  Preserve the latter and advance to the next
                # deterministic placement while publication time remains.
                if not Deadline.IsExpired():
                    continue
                break
            if isinstance(Error, RoutingStageError):
                ReportedAssignmentCut = RoutingAssignmentCut.FromFailure(
                    Error.Failure,
                    SourceCandidateId=CandidateRecord.CandidateId,
                    MandatoryAccessOwnershipFingerprint=(
                        CandidateRecord.TopologyDemand
                        .MandatoryAccessOwnershipFingerprint
                        if CandidateRecord.TopologyDemand is not None
                        else ""
                    ),
                )
                CandidateIsTransactionalEndpointRepair = (
                    CandidateRecord.SourceGenerator
                    == "transactional-cluster-endpoint-repair"
                )
                ParentTransactionalRepairSignals = frozenset(map(
                    str,
                    dict(
                        CandidatePlacement.Placed.LocalRouteDiagnostics
                        or {}
                    ).get(
                        "__PlacementRecipe__",
                        {},
                    ).get(
                        "InternalPinBankGeometryRepairSignals",
                        (),
                    ),
                ))
                CandidateTransactionalRepairSignalHistory = tuple(
                    frozenset(map(str, Signals))
                    for Signals in dict(
                        CandidatePlacement.Placed.LocalRouteDiagnostics
                        or {}
                    ).get(
                        "__PlacementRecipe__",
                        {},
                    ).get(
                        "TransactionalRepairSignalHistory",
                        (),
                    )
                    if isinstance(
                        Signals,
                        tuple | list | set | frozenset,
                    )
                )
                ParentTransactionalRepairClusterCount = int(
                    dict(
                        CandidatePlacement.Placed.LocalRouteDiagnostics
                        or {}
                    ).get(
                        "__PlacementRecipe__",
                        {},
                    ).get(
                        "TransactionalRepairClusterCount",
                        1,
                    )
                    or 1
                )
                ReportedTransactionalCutSignals = (
                    TransactionalCutRepairSignals(
                        ReportedAssignmentCut
                    )
                )
                SameInterfaceClusterEscalation = (
                    CandidateIsTransactionalEndpointRepair
                    and TransactionalCutMayEscalateRepairClusterCount(
                        ParentTransactionalRepairSignals,
                        ReportedTransactionalCutSignals,
                        ParentTransactionalRepairClusterCount,
                    )
                )
                ReportedTransactionalPriorCuts = (
                    *(
                        ()
                        if CandidateIsTransactionalEndpointRepair
                        else tuple(PlacementAssignmentCutHistory)
                    ),
                    *tuple(
                        Evidence.AssignmentCut
                        for Evidence in (
                            DeferredActivePortfolioAssignmentCuts
                        )
                    ),
                )
                RepeatedReportedTransactionalCut = bool(
                    CandidateIsTransactionalEndpointRepair
                    and (
                        SameInterfaceClusterEscalation
                        or
                        ShouldDiversifyRepeatedAssignmentCut(
                            ReportedTransactionalPriorCuts,
                            ReportedAssignmentCut,
                            SignalTopologyFingerprints,
                        )
                        or ShouldDiversifyRepeatedAssignmentCut(
                            ReportedTransactionalPriorCuts,
                            ReportedAssignmentCut,
                        )
                        or BoundedAssignmentCutRepeatsAcrossDistinctOwnership(
                            ReportedTransactionalPriorCuts,
                            ReportedAssignmentCut,
                        )
                        or (
                            AssignmentCutRepeatsAcrossDistinctPlacementOwnership(
                                ReportedTransactionalPriorCuts,
                                ReportedAssignmentCut,
                            )
                        )
                        or (
                            BoundedAssignmentSignalCutRepeatsAcrossDistinctOwnership(
                                ReportedTransactionalPriorCuts,
                                ReportedAssignmentCut,
                            )
                        )
                    )
                )
                AssignmentCut = _RecordAssignmentCut(
                    Error,
                    CandidateRecord,
                    DeferTopologyEpochForMaterializedSibling=(
                        ShouldDeferTopologyCutForRetainedPortfolioSibling(
                            TopologyRequiresJointPortfolio=(
                                TopologyDemand.RequiresJointPortfolio
                            ),
                            ActiveRelocatedPortfolioCandidate=(
                                ActiveRelocatedPortfolioCandidate
                            ),
                            RemainingRetainedActiveCandidates=(
                                RemainingRetainedCandidates
                            ),
                            Failure=Error.Failure,
                            ActiveTransactionalEndpointPortfolioCandidate=(
                                CandidateIsTransactionalEndpointRepair
                            ),
                            TransactionalCutStrictlyNarrowsParentInterface=(
                                TransactionalCutStrictlyNarrowsParentInterface(
                                    ParentTransactionalRepairSignals,
                                    ReportedTransactionalCutSignals,
                                )
                            ),
                            TransactionalCutRepeatedAcrossAccessDistinctPlacements=(
                                RepeatedReportedTransactionalCut
                            ),
                            TransactionalCutRevisitsAncestorInterface=(
                                TransactionalCutRevisitsAncestorInterface(
                                    CandidateTransactionalRepairSignalHistory,
                                    ReportedTransactionalCutSignals,
                                )
                                and not (
                                    TransactionalCutMayEscalateRepairClusterCount(
                                        ParentTransactionalRepairSignals,
                                        ReportedTransactionalCutSignals,
                                        ParentTransactionalRepairClusterCount,
                                    )
                                )
                            ),
                            TransactionalExactPairAfterCoordinatedRepair=(
                                ParentTransactionalRepairClusterCount >= 2
                                and ParentTransactionalRepairSignals
                                == ReportedTransactionalCutSignals
                                and (
                                    TransactionalCutRequiresCoordinatedClusterRepair(
                                        ReportedAssignmentCut
                                    )
                                )
                            ),
                        )
                    ),
                )
                if IsAuthoritativeMandatoryAccessConflict(Error.Failure):
                    PromotedTopologyDemand = (
                        PromoteAuthoritativeMandatoryAccessConflict(
                            CandidateRecord.TopologyDemand or TopologyDemand,
                            Error.Failure,
                        )
                    )
                    TopologyDemandByFingerprint[
                        CandidateRecord.PlacementFingerprint
                    ] = PromotedTopologyDemand
                    CandidateRecord = replace(
                        CandidateRecord,
                        TopologyDemand=PromotedTopologyDemand,
                    )
                    if (
                        ActiveRelocatedPortfolioCandidate
                        and TerminalConstraintEpochPortfolioIdentityFingerprint
                        and CandidateRecord
                        .JointPortfolioIdentityFingerprint
                        == TerminalConstraintEpochPortfolioIdentityFingerprint
                    ):
                        TerminalConstraintEpochAuthoritativeAccessConflictObserved = (
                            True
                        )
                        JointPlacementStateEvents.append({
                            "Status": (
                                "terminal-authoritative-access-conflict-"
                                "promoted"
                            ),
                            "CandidateId": CandidateRecord.CandidateId,
                            "MandatoryAccessConflictResources": (
                                PromotedTopologyDemand
                                .MandatoryAccessConflictResources
                            ),
                            "MandatoryAccessConflictSignals": list(
                                PromotedTopologyDemand
                                .MandatoryAccessConflictSignals
                            ),
                            "MandatoryAccessConflictFingerprint": (
                                PromotedTopologyDemand
                                .MandatoryAccessConflictFingerprint
                            ),
                            "NextAction": (
                                "promote-next-placement-screen-clear-"
                                "untried-candidate"
                            ),
                        })
                ReportedAssignmentCutIsActive = (
                    AssignmentCut is None
                    or CurrentPlacementAssignmentCut == AssignmentCut
                )
                ConflictSignals = ExtractPlacementRelocationSignals(
                    Error.Failure
                )
                if (
                    IsHigherOrderAssignmentCut(AssignmentCut)
                    and AssignmentCut is not None
                    and AssignmentCut.PriorityRelocationSignals
                ):
                    ConflictSignals = frozenset(
                        AssignmentCut.PriorityRelocationSignals
                    )
                EscalationConflictSignals = (
                    ExtractCompletedEscalationRelocationSignals(
                        Error.Failure
                    )
                )
                if not ReportedAssignmentCutIsActive:
                    ConflictSignals = frozenset()
                    EscalationConflictSignals = frozenset()
                if not ConflictSignals:
                    ConflictSignals = EscalationConflictSignals
                ConflictGraph = (
                    (Error.Failure.Diagnostics or {}).get(
                        "ConflictGraph",
                        {},
                    )
                )
                PriorityRelocationSignals = frozenset(
                    str(Signal)
                    for Signal in (
                        ConflictGraph.get(
                            "PriorityRelocationSignals",
                            (),
                        )
                        if isinstance(ConflictGraph, dict)
                        else ()
                    )
                )
                if not ReportedAssignmentCutIsActive:
                    PriorityRelocationSignals = frozenset()
                if (
                    not PriorityRelocationSignals
                    and EscalationConflictSignals
                ):
                    PriorityRelocationSignals = (
                        EscalationConflictSignals
                    )
                    NeedsFeedbackPlacementGeneration = True
                if FailureRequestsPlacementAdvance(Error.Failure):
                    NeedsFeedbackPlacementGeneration = True
                if ConflictSignals:
                    # Preserve the cumulative cut in RelocationSignals for
                    # diagnostics and broad cluster ownership, but drive the
                    # next bounded geometry repair from the latest exact cut.
                    # Unioning every historical cut into the priority set
                    # makes later variants repair stale terminals instead of
                    # the newly proved dead end.
                    PlacementRelocationPrioritySignals = (
                        PriorityRelocationSignals
                        if PriorityRelocationSignals
                        else ConflictSignals
                    )
                    if FailureRequiresPackedAccessRepair(Error.Failure):
                        RequiredRepairSignals = (
                            PriorityRelocationSignals
                            if PriorityRelocationSignals
                            else ConflictSignals
                        )
                        RequiredRepairSignals = (
                            ExpandAnalogousMandatoryRepairSignals(
                                Netlist.Modules[Netlist.Top],
                                RequiredRepairSignals,
                            )
                        )
                        PlacementRequiredRelocationSignals = (
                            RequiredRepairSignals
                        )
                    PlacementRelocationSignals = frozenset((
                        *PlacementRelocationSignals,
                        *ConflictSignals,
                    ))
                    PlacementGenerationDecisions.append({
                        "Result": "routing-conflict-feedback",
                        "CandidateId": CandidateRecord.CandidateId,
                        "RelocationSignals": sorted(
                            PlacementRelocationSignals
                        ),
                        "AssignmentCut": (
                            AssignmentCut.ToDictionary()
                            if AssignmentCut is not None
                            else None
                        ),
                    })
                if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                    print(
                        "[debug] authoritative: routing failure "
                        f"reason={Error.Failure.Reason} stage={Error.Failure.Stage} "
                        f"affected={tuple(Error.Failure.AffectedNets)}",
                        flush=True,
                    )
                LastStructuredRoutingError = Error
                if not FailureRequestsPlacementAdvance(Error.Failure):
                    try:
                        Released = _RouteWithFailedLocalClaimsReleased(
                            CandidatePlacement,
                            AttemptPolicy,
                            AttemptDeadline,
                            Error.Failure,
                            AdaptiveStartedAt=AttemptStarted,
                            AdaptiveExpiresAt=AdaptiveAttemptExpiresAt,
                        )
                    except (RoutingStageError, ValueError) as ReleaseError:
                        LastRoutingError = ReleaseError
                        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                            print(
                                "[debug] authoritative: local-claim recovery rejected "
                                f"signals={list(Error.Failure.AffectedNets)} "
                                f"error_type={type(ReleaseError).__name__} "
                                f"error={ReleaseError}",
                                flush=True,
                        )
                        if isinstance(ReleaseError, RoutingStageError):
                            ReleaseAssignmentCut = _RecordAssignmentCut(
                                ReleaseError,
                                CandidateRecord,
                            )
                            LastStructuredRoutingError = ReleaseError
                            ReleaseConflictSignals = (
                                ExtractPlacementRelocationSignals(
                                    ReleaseError.Failure
                                )
                            )
                            if ReleaseConflictSignals:
                                PlacementRelocationPrioritySignals = (
                                    ReleaseConflictSignals
                                    if PlacementRelocationSignals
                                    else frozenset(
                                        ReleaseError.Failure.AffectedNets
                                    )
                                )
                                PlacementRelocationSignals = frozenset((
                                    *PlacementRelocationSignals,
                                    *ReleaseConflictSignals,
                                ))
                                PlacementGenerationDecisions.append({
                                    "Result": (
                                        "local-claim-recovery-conflict-feedback"
                                    ),
                                    "CandidateId": CandidateRecord.CandidateId,
                                    "RelocationSignals": sorted(
                                        PlacementRelocationSignals
                                    ),
                                    "AssignmentCut": (
                                        ReleaseAssignmentCut.ToDictionary()
                                        if ReleaseAssignmentCut is not None
                                        else None
                                    ),
                                })
                        if (
                            isinstance(ReleaseError, RoutingStageError)
                            and ReleaseError.Failure.Reason
                            == RoutingFailureReason.RuntimeBudgetExceeded
                        ):
                            PlacementAttemptFailures.append(
                                {
                                    **CandidateRecord.ToDictionary(),
                                    "RoutingSpacing": RoutingSpacing,
                                    "PackedNandPlacement": bool(
                                        CandidatePlacement.PackedClusters
                                    ),
                                    "Failure": str(ReleaseError),
                                    "AdaptiveRuntimeBudgetSeconds": round(
                                        AdaptiveAttemptRuntimeSeconds,
                                        6,
                                    ),
                                    "Diagnostics": (
                                        ReleaseError.Failure.ToDictionary()
                                    ),
                                    "ElapsedSeconds": round(
                                        monotonic() - AttemptStarted,
                                        6,
                                    ),
                                }
                            )
                            break
                    else:
                        if Released is not None:
                            Placement, Routed = Released
                            if RoutingPercentageSelectionEnabled:
                                RecordRoutedCandidate(
                                    CandidateRecord,
                                    Placement,
                                    Routed,
                                )
                            PlacementAttemptFailures.append(
                                {
                                    **CandidateRecord.ToDictionary(),
                                    "RoutingSpacing": RoutingSpacing,
                                    "PackedNandPlacement": bool(
                                        CandidatePlacement.PackedClusters
                                    ),
                                    "Failure": str(Error),
                                    "AdaptiveRuntimeBudgetSeconds": round(
                                        AdaptiveAttemptRuntimeSeconds,
                                        6,
                                    ),
                                    "AdaptiveAttemptStartedAt": AttemptStarted,
                                    "AdaptiveAttemptExpiresAt": (
                                        AdaptiveAttemptExpiresAt
                                    ),
                                    "Recovery": "released-affected-local-claims",
                                    "ReleasedSignals": list(
                                        Error.Failure.AffectedNets
                                    ),
                                }
                            )
                            if not RoutingPercentageSelectionEnabled:
                                SelectedCandidate = CandidateRecord
                                break
                            if (
                                Deadline.RemainingSeconds()
                                < Policy.MaterialObjective
                                .MinimumRemainingRoutingPercentageSearchSeconds
                            ):
                                break
                            continue
                RepeatedSiblingStarvationCut = bool(
                    TransactionalCutRepairSignals(
                        AssignmentCut
                    ).intersection(
                        PlacementRepeatedCandidateStarvationSignals
                    )
                )
                RepeatedAccessDistinctTransactionalCut = bool(
                    RepeatedReportedTransactionalCut
                    or (
                        not CandidateIsTransactionalEndpointRepair
                        and (
                            ShouldDiversifyRepeatedAssignmentCut(
                                tuple(
                                    PlacementAssignmentCutHistory[:-1]
                                ),
                                AssignmentCut,
                                SignalTopologyFingerprints,
                            )
                            or ShouldDiversifyRepeatedAssignmentCut(
                                tuple(
                                    PlacementAssignmentCutHistory[:-1]
                                ),
                                AssignmentCut,
                            )
                            or (
                                BoundedAssignmentCutRepeatsAcrossDistinctOwnership(
                                    tuple(
                                        PlacementAssignmentCutHistory[:-1]
                                    ),
                                    AssignmentCut,
                                )
                            )
                            or (
                                AssignmentCutRepeatsAcrossDistinctPlacementOwnership(
                                    tuple(
                                        PlacementAssignmentCutHistory[:-1]
                                    ),
                                    AssignmentCut,
                                )
                            )
                            or (
                                BoundedAssignmentSignalCutRepeatsAcrossDistinctOwnership(
                                    tuple(
                                        PlacementAssignmentCutHistory[:-1]
                                    ),
                                    AssignmentCut,
                                )
                            )
                        )
                    )
                )
                CandidateDiversificationFixedLevel = int(
                    dict(
                        CandidatePlacement.Placed.LocalRouteDiagnostics
                        or {}
                    ).get("__PlacementRelocation__", {}).get(
                        "CoordinatedCandidateDiversificationFixedLevel", 0
                    )
                    or 0
                )
                PostCoordinatedStarvationSignals = frozenset(map(
                    str,
                    Error.Failure.AffectedNets,
                ))
                if (
                    TopologyDemand.RequiresJointPortfolio
                    and CandidateIsTransactionalEndpointRepair
                    and ParentTransactionalRepairClusterCount >= 2
                    and CandidateDiversificationFixedLevel == 0
                    and Error.Failure.Reason
                    == RoutingFailureReason.TrackAssignmentConflict
                    and Error.Failure.Stage == "Candidate"
                    and PostCoordinatedStarvationSignals
                ):
                    PlacementCoordinatedCandidateDiversificationSignals = (
                        PostCoordinatedStarvationSignals
                    )
                    PlacementGenerationDecisions.append({
                        "Result": "post-coordinated-cut-candidate-diversification",
                        "CandidateId": CandidateRecord.CandidateId,
                        "Signals": sorted(PostCoordinatedStarvationSignals),
                        "Level": 1,
                    })
                CandidateRecipeDiagnostics = dict(
                    CandidatePlacement.Placed.LocalRouteDiagnostics or {}
                ).get("__PlacementRecipe__", {})
                CandidateRepairDiagnostics = dict(
                    CandidatePlacement.Placed.LocalRouteDiagnostics or {}
                ).get("__TransactionalClusterEndpointRepair__", {})
                CandidateHasWitnessedMacroRotation = any(
                    bool(ClusterDiagnostics.get(
                        "PriorityEndpointRotationDelta"
                    ))
                    for ClusterDiagnostics in dict(
                        CandidateRepairDiagnostics.get("Clusters", {})
                    ).values()
                    if isinstance(ClusterDiagnostics, dict)
                )
                CandidateRepairSignalHistory = tuple(
                    frozenset(map(str, SignalSet))
                    for SignalSet in CandidateRecipeDiagnostics.get(
                        "TransactionalRepairSignalHistory", ()
                    )
                )
                CurrentTransactionalCutSignals = (
                    TransactionalCutRepairSignals(AssignmentCut)
                )
                PostDiversificationOwnershipRepair = (
                    ShouldAdmitPostDiversificationOwnershipRepair(
                        AssignmentCut,
                        TopologyRequiresJointPortfolio=(
                            TopologyDemand.RequiresJointPortfolio
                        ),
                        CandidateIsTransactionalEndpointRepair=(
                            CandidateIsTransactionalEndpointRepair
                        ),
                        ParentTransactionalRepairClusterCount=(
                            ParentTransactionalRepairClusterCount
                        ),
                        CandidateDiversificationFixedLevel=(
                            CandidateDiversificationFixedLevel
                        ),
                        ParentTransactionalRepairSignals=(
                            ParentTransactionalRepairSignals
                        ),
                        TransactionalRepairSignalHistory=(
                            CandidateRepairSignalHistory
                        ),
                    )
                )
                AncestorCutLocalRepair = bool(
                    CandidateRecord.SourceGenerator
                    == "transactional-cluster-endpoint-repair"
                    and CandidateHasWitnessedMacroRotation
                    and CurrentTransactionalCutSignals
                    and TransactionalCutRevisitsAncestorInterface(
                        tuple(
                            TransactionalCutRepairSignals(PriorCut)
                            for PriorCut in (
                                PlacementAssignmentCutHistory[:-1]
                            )
                        ),
                        CurrentTransactionalCutSignals,
                    )
                    and not TransactionalCutRevisitsAncestorInterface(
                        CandidateRepairSignalHistory,
                        CurrentTransactionalCutSignals,
                    )
                )
                TransactionalEndpointRepairSignals = (
                    SelectTransactionalEndpointRepairSignals(
                        AssignmentCut,
                        InternalPinBankGeometryRepairActive=(
                            InternalPinBankGeometryRepairActive
                        ),
                        PinBankRepairSignals=(
                            PlacementClusterPinBankRepairSignals
                        ),
                        CandidateIsTransactionalEndpointRepair=(
                            CandidateRecord.SourceGenerator
                            == "transactional-cluster-endpoint-repair"
                        ),
                        ParentTransactionalRepairSignals=frozenset(map(
                            str,
                            dict(
                                CandidatePlacement.Placed
                                .LocalRouteDiagnostics or {}
                            ).get(
                                "__PlacementRecipe__",
                                {},
                            ).get(
                                "InternalPinBankGeometryRepairSignals",
                                (),
                            ),
                        )),
                        RepeatedAccessDistinctTransactionalCut=(
                            RepeatedAccessDistinctTransactionalCut
                        ),
                        ProvenSiblingStarvationSignals=(
                            PlacementRepeatedCandidateStarvationSignals
                        ),
                        AncestorTransactionalRepairSignalSets=(
                            CandidateTransactionalRepairSignalHistory
                        ),
                        ParentTransactionalRepairClusterCount=(
                            ParentTransactionalRepairClusterCount
                        ),
                        AllowAncestorCutLocalRepair=(
                            AncestorCutLocalRepair
                        ),
                        AllowPostDiversificationOwnershipRepair=(
                            PostDiversificationOwnershipRepair
                        ),
                    )
                )
                if (
                    AssignmentCutHasBoundedExactCore(AssignmentCut)
                    and CandidateIsTransactionalEndpointRepair
                    and ParentTransactionalRepairClusterCount >= 2
                ):
                    PlacementGenerationDecisions.append({
                        "Result": "ownership-repair-admission",
                        "Outcome": (
                            "admitted"
                            if PostDiversificationOwnershipRepair
                            else "not-required-or-rejected"
                        ),
                        "CandidateId": CandidateRecord.CandidateId,
                        "Signals": sorted(
                            CurrentTransactionalCutSignals
                        ),
                        "ParentSignals": sorted(
                            ParentTransactionalRepairSignals
                        ),
                        "ParentRepairClusterCount": (
                            ParentTransactionalRepairClusterCount
                        ),
                        "CandidateDiversificationFixedLevel": (
                            CandidateDiversificationFixedLevel
                        ),
                        "AssignmentCutFingerprint": (
                            AssignmentCut.ConflictFingerprint
                            if AssignmentCut is not None
                            else ""
                        ),
                        "MandatoryAccessOwnershipFingerprint": (
                            AssignmentCut
                            .MandatoryAccessOwnershipFingerprint
                            if AssignmentCut is not None
                            else ""
                        ),
                    })
                if AncestorCutLocalRepair:
                    PlacementGenerationDecisions.append({
                        "Result": "rotated-macro-ancestor-cut-local-eco",
                        "CandidateId": CandidateRecord.CandidateId,
                        "Signals": sorted(
                            TransactionalEndpointRepairSignals
                        ),
                        "AssignmentCutFingerprint": (
                            AssignmentCut.ConflictFingerprint
                            if AssignmentCut is not None
                            else ""
                        ),
                    })
                if (
                    CandidateRecord.SourceGenerator
                    == "transactional-cluster-endpoint-repair"
                    and AssignmentCutHasBoundedExactCore(AssignmentCut)
                    and not TransactionalEndpointRepairSignals
                ):
                    ParentTransactionalSignals = frozenset(map(
                        str,
                        dict(
                            CandidatePlacement.Placed
                            .LocalRouteDiagnostics or {}
                        ).get(
                            "__PlacementRecipe__",
                            {},
                        ).get(
                            "InternalPinBankGeometryRepairSignals",
                            (),
                        ),
                    ))
                    PlacementGenerationDecisions.append({
                        "Result": (
                            "transactional-cut-frontier-not-narrower"
                        ),
                        "CandidateId": CandidateRecord.CandidateId,
                        "ParentSignals": sorted(
                            ParentTransactionalSignals
                        ),
                        "ChildSignals": sorted(
                            TransactionalCutRepairSignals(AssignmentCut)
                        ),
                        "AssignmentCutFingerprint": (
                            AssignmentCut.ConflictFingerprint
                            if AssignmentCut is not None
                            else ""
                        ),
                        "NextAction": (
                            "route-retained-parent-portfolio-sibling"
                        ),
                    })
                if TransactionalEndpointRepairSignals:
                    PublishedTransactionalRepair = False
                    TransactionalRepairClusterCount = (
                        SelectTransactionalRepairClusterCount(
                            CandidateIsTransactionalEndpointRepair=(
                                CandidateRecord.SourceGenerator
                                == (
                                    "transactional-cluster-endpoint-"
                                    "repair"
                                )
                            ),
                            RepeatedAccessDistinctTransactionalCut=(
                                RepeatedAccessDistinctTransactionalCut
                                or RepeatedSiblingStarvationCut
                            ),
                            CutStrictlyNarrowsParentInterface=(
                                TransactionalCutStrictlyNarrowsParentInterface(
                                    ParentTransactionalRepairSignals,
                                    TransactionalEndpointRepairSignals,
                                )
                            ),
                            ExactBoundaryPairCut=(
                                TransactionalCutRequiresCoordinatedClusterRepair(
                                    AssignmentCut
                                )
                            ),
                            AllowInitialExactBoundaryCutRepair=(
                                TopologyDemand.RequiresJointPortfolio
                                and not CandidateIsTransactionalEndpointRepair
                                and AssignmentCut is not None
                                and TransactionalCutRequiresCoordinatedClusterRepair(
                                    AssignmentCut
                                )
                                and not any(
                                    PriorCut.ConflictFingerprint
                                    == AssignmentCut.ConflictFingerprint
                                    for PriorCut in (
                                        PlacementAssignmentCutHistory[:-1]
                                    )
                                )
                            ),
                        )
                    )
                    TransactionalRepairVariantCount = (
                        3
                        if CandidateRecord.SourceGenerator
                        == "transactional-cluster-endpoint-repair"
                        else 6
                    )
                    for RepairVariant in range(
                        TransactionalRepairVariantCount
                    ):
                        VariantPublished = (
                            _PublishTransactionalClusterEndpointRepair(
                                CandidateRecord,
                                TransactionalEndpointRepairSignals,
                                RepairVariant=RepairVariant,
                                RepairClusterCount=(
                                    TransactionalRepairClusterCount
                                ),
                                RepairTerminalPositions=(
                                    frozenset(
                                        AssignmentCut
                                        .PriorityRelocationTerminals
                                    )
                                    if AssignmentCut is not None
                                    else frozenset()
                                ),
                            )
                        )
                        PublishedTransactionalRepair = (
                            VariantPublished
                            or PublishedTransactionalRepair
                        )
                        if ShouldStopTransactionalRepairVariantGeneration(
                            CandidateIsTransactionalEndpointRepair=(
                                CandidateRecord.SourceGenerator
                                == (
                                    "transactional-cluster-endpoint-"
                                    "repair"
                                )
                            ),
                            RepairClusterCount=(
                                TransactionalRepairClusterCount
                            ),
                            VariantPublished=VariantPublished,
                        ):
                            break
                    if PublishedTransactionalRepair:
                        ActiveJointPortfolioIdentityFingerprint = (
                            _TransactionalEndpointRepairPortfolioFingerprint(
                                CandidateRecord,
                                TransactionalEndpointRepairSignals,
                                TransactionalRepairClusterCount,
                            )
                        )
                        PlacementGenerationDecisions.append({
                            "Result": (
                                "transactional-cluster-endpoint-portfolio-"
                                "complete"
                            ),
                            "SourceCandidateId": (
                                CandidateRecord.CandidateId
                            ),
                            "RequestedVariantCount": (
                                TransactionalRepairVariantCount
                            ),
                            "RepairClusterCount": (
                                TransactionalRepairClusterCount
                            ),
                            "JointPortfolioIdentityFingerprint": (
                                ActiveJointPortfolioIdentityFingerprint
                            ),
                            "RemainingRoutingSeconds": round(
                                max(0.0, Deadline.RemainingSeconds()),
                                6,
                            ),
                        })
                    if PostDiversificationOwnershipRepair:
                        PlacementGenerationDecisions.append({
                            "Result": "ownership-repair-publication",
                            "Outcome": (
                                "published"
                                if PublishedTransactionalRepair
                                else (
                                    "deadline-skipped"
                                    if Deadline.IsExpired()
                                    else "rejected-or-deduplicated"
                                )
                            ),
                            "SourceCandidateId": (
                                CandidateRecord.CandidateId
                            ),
                            "Signals": sorted(
                                TransactionalEndpointRepairSignals
                            ),
                            "RequestedRepairClusterCount": (
                                TransactionalRepairClusterCount
                            ),
                            "RemainingRoutingSeconds": round(
                                max(0.0, Deadline.RemainingSeconds()),
                                6,
                            ),
                        })
            PlacementAttemptFailures.append(
                {
                    **CandidateRecord.ToDictionary(),
                    "RoutingSpacing": RoutingSpacing,
                    "PackedNandPlacement": bool(CandidatePlacement.PackedClusters),
                    "Failure": str(Error),
                    "AdaptiveRuntimeBudgetSeconds": round(
                        AdaptiveAttemptRuntimeSeconds,
                        6,
                    ),
                    "Diagnostics": (
                        Error.Failure.ToDictionary()
                        if isinstance(Error, RoutingStageError)
                        else {}
                    ),
                    "ElapsedSeconds": round(monotonic() - AttemptStarted, 6),
                }
            )
    if RoutedCandidates:
        (
            _Score,
            SelectedCandidate,
            Placement,
            Routed,
            SelectedCompositionDiagnostics,
        ) = min(RoutedCandidates, key=lambda Value: Value[0])
        RoutingSpacing = SelectedCandidate.RoutingSpacing
    if Routed is None:
        if LastCompletedAssignmentCutError is not None:
            BaseFailure = LastCompletedAssignmentCutError.Failure
        elif LastStructuredRoutingError is not None:
            BaseFailure = LastStructuredRoutingError.Failure
        else:
            BaseFailure = RoutingFailure(
                Reason=RoutingFailureReason.DetailedSearchExhausted,
                Stage="PlacementRouting",
                Detail=str(LastRoutingError or "all placement candidates failed"),
            )
        FailureDiagnostics = dict(BaseFailure.Diagnostics or {})
        FailureDiagnostics.update({
            "PlacementCandidates": PlacementFeedback,
            "PlacementGenerationFailures": PlacementGenerationFailures,
            "PlacementGenerationDecisions": PlacementGenerationDecisions,
            "PlacementAttempts": PlacementAttemptFailures,
            "JointPlacementStateEvents": JointPlacementStateEvents,
            "AssignmentCutHistory": [
                AssignmentCut.ToDictionary()
                for AssignmentCut in PlacementAssignmentCutHistory
            ],
            "CurrentAssignmentCut": (
                CurrentPlacementAssignmentCut.ToDictionary()
                if CurrentPlacementAssignmentCut is not None
                else None
            ),
            "ActivePlacementConstraints": (
                PlacementAssignmentConstraints.ToDictionary()
            ),
            "CoordinatedCandidateDiversificationSignals": sorted(
                PlacementCoordinatedCandidateDiversificationSignals
            ),
            "Deadline": Deadline.ToDictionary(),
        })
        raise RoutingStageError(
            RoutingFailure(
                Reason=BaseFailure.Reason,
                Stage=BaseFailure.Stage,
                AffectedNets=BaseFailure.AffectedNets,
                Resources=BaseFailure.Resources,
                Locations=BaseFailure.Locations,
                RepairActions=BaseFailure.RepairActions,
                Detail=BaseFailure.Detail,
                Diagnostics=FailureDiagnostics,
            )
        ) from LastRoutingError
    ValidateNandOnlyDesign(Placement.Placed, Netlist)
    Routed.RoutingControlEffectiveness["PlacementFeedbackCandidates"] = (
        PlacementFeedback
    )
    Routed.RoutingControlEffectiveness["SelectedPlacementCandidate"] = (
        SelectedCandidate.ToDictionary() if SelectedCandidate is not None else None
    )
    Routed.RoutingControlEffectiveness["TopologyDemandProfile"] = (
        TopologyDemand.ToDictionary()
    )
    Routed.RoutingControlEffectiveness[
        "SelectedPlacementTopologyDemand"
    ] = (
        SelectedCandidate.TopologyDemand.ToDictionary()
        if (
            SelectedCandidate is not None
            and SelectedCandidate.TopologyDemand is not None
        )
        else None
    )
    Routed.RoutingControlEffectiveness["SelectedRoutingSpacing"] = RoutingSpacing
    Routed.RoutingControlEffectiveness["RoutingPercentageSelection"] = {
        "Enabled": RoutingPercentageSelectionEnabled,
        "Configured": Policy.MaterialObjective.OptimizeRoutingPercentage,
        "MinimumNandCount": (
            Policy.MaterialObjective.MinimumRoutingPercentageSelectionNandCount
        ),
        "NandGateCount": NandGateCount,
        "CandidateCount": len(RoutedCandidates),
        "Selected": SelectedCompositionDiagnostics if RoutedCandidates else None,
        "Candidates": [
            Diagnostics
            for _Score, _Candidate, _Placement, _Routed, Diagnostics
            in sorted(RoutedCandidates, key=lambda Value: Value[0])
        ],
    }
    Routed.RoutingControlEffectiveness["PlacementAttempts"] = (
        PlacementAttemptFailures
    )
    Routed.RoutingControlEffectiveness["PlacementGenerationFailures"] = (
        PlacementGenerationFailures
    )
    Routed.RoutingControlEffectiveness["JointPlacementStateEvents"] = (
        JointPlacementStateEvents
    )
    Routed.RoutingControlEffectiveness["PlacementGenerationDecisions"] = (
        PlacementGenerationDecisions
    )
    Routed.RoutingControlEffectiveness["AssignmentCutHistory"] = [
        AssignmentCut.ToDictionary()
        for AssignmentCut in PlacementAssignmentCutHistory
    ]
    Routed.RoutingControlEffectiveness["ActivePlacementConstraints"] = (
        PlacementAssignmentConstraints.ToDictionary()
    )
    Routed.SupportBlock = Technology.DefaultSupportBlock
    Footprint, EstimatedBlocks, Width, Depth = MeasurePcbDesign(
        Placement.Placed,
        Routed,
    )
    Snapshot = BuildLocalFirstSnapshot(
        Placement,
        Routed,
        LocalFanoutDistance=Policy.Placement.LocalFanoutDistance,
        LocalRouteBudget=10,
    )
    PlanningContracts = Snapshot.ToDictionary()
    PlanningContracts["PackedNandClusters"] = [
        {
            "ClusterId": Cluster.ClusterId,
            "MemberNands": list(Cluster.MemberNands),
            "BoundarySignals": list(Cluster.BoundarySignals),
            "InternalSignals": list(Cluster.InternalSignals),
            "RelativePlacements": {
                Name: list(Value)
                for Name, Value in sorted(Cluster.RelativePlacements.items())
            },
            "DirectConnections": list(Cluster.DirectConnections),
            "LocalClaimSignals": list(Cluster.LocalClaimSignals),
            "BoundaryTerminals": [
                list(Position) for Position in Cluster.BoundaryTerminals
            ],
            "ExactLocalRoutingBlocks": Cluster.ExactLocalRoutingBlocks,
            "GlobalEntrances": Cluster.GlobalEntrances,
            "RejectionReasons": list(Cluster.RejectionReasons),
            "StructuralSignature": Cluster.StructuralSignature,
            "ReusedFromClusterId": Cluster.ReusedFromClusterId,
            "StructuralMapping": dict(sorted(
                (Cluster.StructuralMapping or {}).items()
            )),
            "StackId": Cluster.StackId,
            "StackLevel": Cluster.StackLevel,
            "BaseY": Cluster.BaseY,
            "BoundaryDemand": dict(sorted((Cluster.BoundaryDemand or {}).items())),
            "EstimatedCorridorLanes": Cluster.EstimatedCorridorLanes,
            "LocalClaimCoverage": Cluster.LocalClaimCoverage,
            "BoundaryDemandRecords": [
                {
                    "Signal": Record.Signal,
                    "UnresolvedTargets": Record.UnresolvedTargets,
                    "RequiredPortalSlots": Record.RequiredPortalSlots,
                    "RequiredCorridorLanes": Record.RequiredCorridorLanes,
                    "PreferredBoundarySide": Record.PreferredBoundarySide,
                }
                for Record in Cluster.BoundaryDemandRecords
            ],
            "BoundaryCapacityRecords": [
                {
                    "BoundarySide": Record.BoundarySide,
                    "LegalPortalSlots": Record.LegalPortalSlots,
                    "LegalCorridorLanes": Record.LegalCorridorLanes,
                    "Overflow": Record.Overflow,
                }
                for Record in Cluster.BoundaryCapacityRecords
            ],
            "BoundaryOverflow": Cluster.BoundaryOverflow,
            "PinScarcityCount": Cluster.PinScarcityCount,
            "OrientationRotation": Cluster.OrientationRotation,
            "OrientationMirrorX": Cluster.OrientationMirrorX,
        }
        for Cluster in Placement.PackedClusters
    ]
    PlanningContracts["StructuralReuse"] = {
        "Enabled": Policy.NandPacking.EnableStructuralReuse,
        "ReuseScope": "relative-layout-with-joint-world-transform",
        "JointClusterOrientationEnabled": (
            Policy.NandPacking.EnableJointClusterOrientation
        ),
        "LocalRoutesRecomputedAndValidated": True,
        "UniqueTemplates": len({
            Cluster.StructuralSignature
            for Cluster in Placement.PackedClusters
            if Cluster.StructuralSignature
        }),
        "ReusedClusters": sum(
            Cluster.ReusedFromClusterId is not None
            for Cluster in Placement.PackedClusters
        ),
    }
    PlanningContracts["LocalRouteClaims"] = [
        {
            "Signal": Claim.Signal,
            "ClusterId": Claim.ClusterId,
            "Root": list(Claim.Root),
            "ConnectedTargets": [list(Value) for Value in Claim.ConnectedTargets],
            "BoundaryNodes": [list(Value) for Value in Claim.BoundaryNodes],
            "NodeCount": len(Claim.Nodes),
            "EdgeCount": len(Claim.Edges),
            "PreOwnedResourceCount": len(Claim.Claims.ResourceIds),
            "ExactRouteSignalBlocks": Claim.ExactRouteSignalBlocks,
            "ExactRouteRefreshBlocks": Claim.ExactRouteRefreshBlocks,
            "ExactRouteSupportBlocks": Claim.ExactRouteSupportBlocks,
        }
        for Claim in Placement.Placed.LocalRouteClaims
    ]
    PlanningContracts["LocalRouteDiagnostics"] = (
        Placement.Placed.LocalRouteDiagnostics or {}
    )
    PlanningContracts["ClusterBoundaryLeases"] = {
        "Enabled": bool(getattr(
            Placement,
            "ClusterBoundaryLeaseRequests",
            (),
        )),
        "LeaseExtent": "terminal-access-plus-first-routing-segment",
        "Requests": [
            Request.ToDictionary()
            for Request in getattr(
                Placement,
                "ClusterBoundaryLeaseRequests",
                (),
            )
        ],
    }
    PlanningContracts["ClusterLocalRouteTemplates"] = {
        "Enabled": bool(getattr(
            Placement,
            "ClusterLocalRouteTemplates",
            (),
        )),
        "Templates": [
            Template.ToDictionary()
            for Template in getattr(
                Placement,
                "ClusterLocalRouteTemplates",
                (),
            )
        ],
    }
    PlanningContracts["TopologyDemandProfile"] = (
        TopologyDemand.ToDictionary()
    )
    PlanningContracts["SelectedPlacementTopologyDemand"] = (
        SelectedCandidate.TopologyDemand.ToDictionary()
        if (
            SelectedCandidate is not None
            and SelectedCandidate.TopologyDemand is not None
        )
        else None
    )
    PlanningContracts["RoutingDemandEstimate"] = (
        Routed.RoutingControlEffectiveness.get("RoutingDemandEstimate", {})
    )
    PlanningContracts["DerivedRoutingBudget"] = (
        Routed.RoutingControlEffectiveness.get("DerivedRoutingBudget", {})
    )
    PlanningContracts["PortalReservations"] = (
        Routed.RoutingControlEffectiveness.get("PortalReservations", [])
    )
    if Deadline.IsExpired() and RoutedCandidates:
        # A later optional comparison exhausted the shared deadline after at
        # least one candidate had already completed routing and validation.
        # Publish the best validated candidate instead of discarding it.
        Routed.RoutingControlEffectiveness[
            "RoutingPercentageSelection"]["DeadlineLimited"] = True
    else:
        Deadline.RaiseIfExpired("RoutingFinalization")
    Routed.RoutingControlEffectiveness["Deadline"] = Deadline.ToDictionary()
    Result = PcbResult(
        Placed=Placement.Placed,
        Routed=Routed,
        Footprint=Footprint,
        EstimatedBlocks=EstimatedBlocks,
        Width=Width,
        Depth=Depth,
        Policy=Policy,
        Technology=Technology,
        RequestedStrategy=RequestedStrategy.value,
        UsedStrategy=UsedStrategy.value,
        PlanningContracts=PlanningContracts,
    )
    if ProgressCallback is not None:
        ProgressCallback(
            PcbProgress(
                Completed=1,
                Total=1,
                Workers=0,
                Valid=1,
                BestBlocks=EstimatedBlocks,
                BestWidth=Width,
                BestDepth=Depth,
                BestFootprint=Footprint,
                Failed=0,
                Stage="routing complete",
            )
        )
    return Result

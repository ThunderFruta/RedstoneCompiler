"""Placement candidates, retained resources, and stage scheduling."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
    replace,
)
from math import (
    isfinite,
    prod,
)
from time import (
    monotonic,
)
from typing import (
    Any,
    Callable,
    Collection,
    Iterable,
    Mapping,
    Sequence,
)
from Compiler.Routing.Contracts.PhysicalInterface import (
    PhysicalGlobalPlanDescriptorProgressState,
    PhysicalGlobalPlanResumeCursor,
    PhysicalGlobalPlanContinuationState,
    PhysicalSignalRouteDomainDescriptorProgressState,
)
from Compiler.Routing.Failures import (
    RoutingFailure,
    RoutingFailureReason,
)
from Compiler.Routing.Reliability import (
    BuildStableFingerprint,
    RoutingDeadline,
)
from Compiler.Routing.Policy import (
    PhysicalDesignPolicy,
)
from Compiler.Placement.PreRouteInterface import (
    DerivedRoutingEnvelope,
)
from Compiler.Placement.Access.Geometry import (
    DerivedPerimeterFabricShell,
)
from Compiler.Placement.Core.Clusters import (
    PcbPlacement,
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
    RoutingEnvelope: DerivedRoutingEnvelope | None = None

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
class PreRouteFabricDescriptor:
    """One fixed geometry/layer member before escape-domain construction.

    The shell contains exact physical perimeter bounds for derived members.
    The legacy fixed-access band has only its declared pre-route envelope at
    this point; its full materialized access-contract extent is reported
    separately after construction and must not be mistaken for this prefix.
    In both cases ``ObjectivePrefix`` is fixed before the expensive legal
    escape and authoritative portal-domain construction begins.
    """

    Candidate: PcbPlacementCandidate
    StaticResources: Any = field(compare=False, repr=False)
    TopologyKind: str
    AccessRingTrackCount: int
    DeriveLegalEscapeWorkLimit: bool
    ObjectivePrefix: tuple[int, int, int]
    MaterializationInputFingerprint: str
    Shell: DerivedPerimeterFabricShell | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.Candidate.RoutingEnvelope is None:
            raise ValueError(
                "pre-route fabric descriptor requires a routing envelope"
            )
        if self.TopologyKind not in {
            "fixed-access-band-v1",
            "derived-perimeter-access-v1",
        }:
            raise ValueError("pre-route fabric descriptor topology is invalid")
        if len(self.ObjectivePrefix) != 3 or any(
            Value < 0 for Value in self.ObjectivePrefix
        ):
            raise ValueError(
                "pre-route fabric descriptor objective prefix is invalid"
            )
        if not self.MaterializationInputFingerprint:
            raise ValueError(
                "pre-route fabric descriptor requires an input fingerprint"
            )
        if (
            self.TopologyKind == "derived-perimeter-access-v1"
            and self.Shell is not None
            and self.Shell.AccessRingTrackCount != self.AccessRingTrackCount
        ):
            raise ValueError(
                "derived pre-route fabric shell track count does not match"
            )

    @property
    def CandidateId(self) -> str:
        return self.Candidate.CandidateId

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CandidateId": self.CandidateId,
            "PlacementFingerprint": self.Candidate.PlacementFingerprint,
            "RoutingEnvelopeFingerprint": (
                self.Candidate.RoutingEnvelope.EnvelopeFingerprint
                if self.Candidate.RoutingEnvelope is not None
                else ""
            ),
            "TopologyKind": self.TopologyKind,
            "AccessRingTrackCount": self.AccessRingTrackCount,
            "ObjectivePrefix": list(self.ObjectivePrefix),
            "MaterializationInputFingerprint": (
                self.MaterializationInputFingerprint
            ),
            "ShellFingerprint": (
                self.Shell.ShellFingerprint if self.Shell is not None else ""
            ),
            "ShellBounds": (
                list(self.Shell.OuterBounds)
                if self.Shell is not None
                else None
            ),
            "ShellActiveFaces": (
                list(self.Shell.ActiveFaces)
                if self.Shell is not None
                else []
            ),
        }

def HasDistinctRetainedPhysicalEligibilityState(
    Queue: Iterable[tuple[str, int, Any, int, int]],
    *,
    ComponentVariant: int,
    PlacementFingerprint: str,
) -> bool:
    """Return whether any retained sibling must precede new placement work.

    A complete physical port core is evidence against one placed interface,
    not permission to synchronously regenerate geometry while another
    interface-distinct placement is already retained.  Component variants
    are scheduling identities, not independent placement frontiers; service
    their frozen retained states before generating new geometry for any one
    variant.  Keep this admission rule pure so the portfolio order is
    deterministic and directly testable.
    """

    return any(
        Phase == "prepare-eligibility"
        and Candidate.PlacementFingerprint != PlacementFingerprint
        for (
            Phase,
            _InterfaceIndex,
            Candidate,
            _InterfaceCutEpoch,
            _QueuedComponentVariant,
        ) in Queue
    )


def HasQueuedGeneratedProofGuidedEligibilityState(
    Queue: Iterable[tuple[str, int, Any, int, int]],
    GeneratedPlacementFingerprints: Collection[str],
) -> bool:
    """Return whether a generated repair is waiting for physical evaluation."""

    return any(
        Phase == "prepare-eligibility"
        and Candidate.PlacementFingerprint in GeneratedPlacementFingerprints
        for (
            Phase,
            _InterfaceIndex,
            Candidate,
            _InterfaceCutEpoch,
            _QueuedComponentVariant,
        ) in Queue
    )


def QueuedPhysicalEligibilityPlacementFingerprints(
    Queue: Iterable[tuple[str, int, Any, int, int]],
) -> frozenset[str]:
    """Return immutable placement identities waiting for eligibility work."""

    return frozenset(
        Candidate.PlacementFingerprint
        for (
            Phase,
            _InterfaceIndex,
            Candidate,
            _InterfaceCutEpoch,
            _QueuedComponentVariant,
        ) in Queue
        if Phase == "prepare-eligibility"
    )

@dataclass(frozen=True)
class ClusterInterfaceStageSchedule:
    """Shared deadline and immutable state order for one component solve."""

    StartedAt: float
    PlanningExpiresAt: float
    ProofGuidedPlanningExpiresAt: float
    AccessRepairPlanningExpiresAt: float
    AccessRepairExpiresAt: float
    ExpiresAt: float
    LocalCompilationReserveSeconds: float
    ProofGuidedLocalCompilationReserveSeconds: float
    GlobalRoutingReserveSeconds: float
    AccessRepairGlobalRoutingReserveSeconds: float
    PublicationReserveSeconds: float
    StateFingerprints: tuple[str, ...]

    @property
    def AvailableSeconds(self) -> float:
        return max(0.0, self.ExpiresAt - self.StartedAt)

    @property
    def PlanningSeconds(self) -> float:
        return max(0.0, self.PlanningExpiresAt - self.StartedAt)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "StartedAt": self.StartedAt,
            "PlanningExpiresAt": self.PlanningExpiresAt,
            "ProofGuidedPlanningExpiresAt": (
                self.ProofGuidedPlanningExpiresAt
            ),
            "AccessRepairPlanningExpiresAt": (
                self.AccessRepairPlanningExpiresAt
            ),
            "AccessRepairExpiresAt": self.AccessRepairExpiresAt,
            "ExpiresAt": self.ExpiresAt,
            "AvailableSeconds": round(self.AvailableSeconds, 6),
            "PlanningSeconds": round(self.PlanningSeconds, 6),
            "LocalCompilationReserveSeconds": (
                self.LocalCompilationReserveSeconds
            ),
            "ProofGuidedLocalCompilationReserveSeconds": (
                self.ProofGuidedLocalCompilationReserveSeconds
            ),
            "GlobalRoutingReserveSeconds": (
                self.GlobalRoutingReserveSeconds
            ),
            "AccessRepairGlobalRoutingReserveSeconds": (
                self.AccessRepairGlobalRoutingReserveSeconds
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
) -> tuple[int, int, int, int, int, int, int, int]:
    """Rank retained placements by learned component-port escape geometry.

    Packed placement already proves a finite set of legal boundary escapes
    for every cluster signal.  Reuse those exact counts as a cheap estimate
    of the port-factor preparation domain instead of selecting the next
    retained state from terminal depth alone.  This changes only deterministic
    portfolio order: every retained state remains in the complete queue.
    """
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
    EscapeCandidateCountByClusterSignal = {
        (int(Cluster.ClusterId), str(Signal)): max(0, int(Count))
        for Cluster in Placement.PackedClusters
        for Signal, Count in Cluster.LegalEscapeCandidateCounts
    }
    RequiredClusterSignals = set()

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
        RequiredClusterSignals.add((int(Request.SourceCluster), Signal))
        RequiredClusterSignals.add((int(Request.TargetCluster), Signal))
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
    EscapeCandidateCounts = tuple(
        EscapeCandidateCountByClusterSignal[Key]
        for Key in sorted(RequiredClusterSignals)
        if Key in EscapeCandidateCountByClusterSignal
    )
    return (
        len(SignalSet - SeenSignals),
        sum(
            Key not in EscapeCandidateCountByClusterSignal
            for Key in RequiredClusterSignals
        ),
        max(DirectionPenalties, default=0),
        sum(DirectionPenalties),
        max(EscapeCandidateCounts, default=0),
        sum(EscapeCandidateCounts),
        max(PerimeterDepths, default=0),
        sum(PerimeterDepths),
    )

def SelectRetainedPhysicalPlacementForAccessCore(
    Candidates: Iterable[PcbPlacementCandidate],
    KnownPlacementFingerprints: Iterable[str],
    Signals: Iterable[str],
) -> PcbPlacementCandidate | None:
    """Select an existing immutable placement before generating geometry."""
    Known = frozenset(map(str, KnownPlacementFingerprints))
    StableSignals = tuple(sorted(map(str, Signals)))
    Eligible = tuple(
        Candidate
        for Candidate in Candidates
        if (
            Candidate.PlacementFingerprint not in Known
            and Candidate.JointPlacementState is not None
            and Candidate.TopologyDemand is not None
        )
    )
    if not Eligible:
        return None
    return min(
        Eligible,
        key=lambda Candidate: (
            BuildComponentAccessFeedbackPlacementScore(
                Candidate,
                StableSignals,
            ),
            BuildStableFingerprint((
                "component-access-core-contract-order-v1",
                StableSignals,
                str(getattr(
                    Candidate,
                    "InterfaceTopologyFingerprint",
                    Candidate.PlacementFingerprint,
                )),
            )),
            Candidate.PlacementFingerprint,
        ),
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

def FindPhysicalGlobalDiagnosticValues(
    Value: Any,
    Key: str,
) -> list[Any]:
    """Collect one diagnostic field through nested routed-stage failures."""
    Result = []
    if isinstance(Value, dict):
        if Key in Value:
            Result.append(Value[Key])
        for Nested in Value.values():
            Result.extend(FindPhysicalGlobalDiagnosticValues(Nested, Key))
    elif isinstance(Value, (list, tuple)):
        for Nested in Value:
            Result.extend(FindPhysicalGlobalDiagnosticValues(Nested, Key))
    return Result

def BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
    PlanFingerprint: str,
    ApertureDomainFingerprint: str,
    Diagnostics: Mapping[str, Any],
) -> tuple[PhysicalGlobalPlanResumeCursor | None, int]:
    """Build a typed cursor from exact same-plan portal or route progress."""
    def MaximumInteger(Key: str) -> int:
        return max(
            (
                int(Value)
                for Value in FindPhysicalGlobalDiagnosticValues(
                    Diagnostics,
                    Key,
                )
                if isinstance(Value, (int, float))
            ),
            default=0,
        )

    PortalCompletedWork = MaximumInteger("PortalCompletedWork")
    PortalRequestCount = MaximumInteger("PortalRequestCount")
    DescriptorProgressValues = FindPhysicalGlobalDiagnosticValues(
        Diagnostics,
        "PhysicalSignalRouteDomainDescriptorProgress",
    )
    DescriptorProgressBySignal = next((
        Value
        for Value in reversed(DescriptorProgressValues)
        if isinstance(Value, dict) and Value
    ), {})
    if DescriptorProgressBySignal:
        if not PlanFingerprint or not ApertureDomainFingerprint:
            return None, 0
        SignalStates = []
        try:
            for Signal, RawState in sorted(
                DescriptorProgressBySignal.items()
            ):
                if not isinstance(RawState, Mapping):
                    raise ValueError(
                        "physical descriptor progress record is malformed"
                    )
                RawCompleted = RawState.get(
                    "CompletedDescriptorFingerprints",
                    (),
                )
                if not isinstance(RawCompleted, (list, tuple, set, frozenset)):
                    raise ValueError(
                        "physical descriptor completion set is malformed"
                    )
                Completed = frozenset(str(Value) for Value in RawCompleted)
                if any(not Value for Value in Completed):
                    raise ValueError(
                        "physical descriptor completion is unidentified"
                    )
                DeclaredCompletedCount = int(RawState.get(
                    "CompletedDescriptorCount",
                    len(Completed),
                ))
                if DeclaredCompletedCount != len(Completed):
                    raise ValueError(
                        "physical descriptor completion count disagrees"
                    )
                SignalStates.append(
                    PhysicalSignalRouteDomainDescriptorProgressState(
                        Signal=str(Signal),
                        PreSiblingDomainFingerprint=str(RawState.get(
                            "PreSiblingDomainFingerprint",
                            "",
                        )),
                        RequestDomainFingerprint=str(RawState.get(
                            "RequestDomainFingerprint",
                            "",
                        )),
                        DescriptorUniverseFingerprint=str(RawState.get(
                            "DescriptorUniverseFingerprint",
                            "",
                        )),
                        DescriptorCount=int(RawState.get(
                            "DescriptorCount",
                            0,
                        )),
                        CompletedDescriptorFingerprints=Completed,
                    )
                )
        except (TypeError, ValueError):
            return None, 0
        DescriptorState = PhysicalGlobalPlanDescriptorProgressState(
            PlanFingerprint=str(PlanFingerprint),
            ApertureDomainFingerprint=str(ApertureDomainFingerprint),
            Signals=tuple(SignalStates),
        )
        CompletedWork = DescriptorState.CompletedDescriptorCount
        if CompletedWork <= 0:
            return None, 0
        return PhysicalGlobalPlanResumeCursor(
            CursorFingerprint=BuildStableFingerprint((
                "physical-global-descriptor-resume-cursor-v1",
                DescriptorState.PlanFingerprint,
                DescriptorState.ApertureDomainFingerprint,
                tuple(
                    (
                        Value.UniverseIdentity,
                        tuple(sorted(
                            Value.CompletedDescriptorFingerprints
                        )),
                    )
                    for Value in DescriptorState.Signals
                ),
            )),
            PlanFingerprint=str(PlanFingerprint),
            ApertureDomainFingerprint=str(ApertureDomainFingerprint),
            CompletedWork=CompletedWork,
            State=DescriptorState,
        ), CompletedWork

    PortalCacheModes = tuple(
        str(Value)
        for Value in FindPhysicalGlobalDiagnosticValues(
            Diagnostics,
            "PortalCacheMode",
        )
        if str(Value)
    )
    RawPortalCacheSelections = tuple(
        bool(Value)
        for Value in FindPhysicalGlobalDiagnosticValues(
            Diagnostics,
            "RawPortalResourceCacheSelected",
        )
    )
    PortalProgressAvailable = bool(
        PortalCompletedWork > 0
        and PortalRequestCount > 0
        and (
            any(Value in {"partial-signal", "complete"}
                for Value in PortalCacheModes)
            or any(RawPortalCacheSelections)
        )
    )
    CompletedWork = PortalCompletedWork
    if (
        not PlanFingerprint
        or not ApertureDomainFingerprint
        or CompletedWork <= 0
        or not PortalProgressAvailable
    ):
        return None, CompletedWork
    ResumeState = (
        "physical-global-portal-resume-v1",
        PortalCompletedWork,
        PortalRequestCount,
        tuple(sorted(PortalCacheModes)),
        bool(any(RawPortalCacheSelections)),
    )
    return PhysicalGlobalPlanResumeCursor(
        CursorFingerprint=BuildStableFingerprint((
            "physical-global-resume-cursor-v2",
            PlanFingerprint,
            ApertureDomainFingerprint,
            ResumeState,
        )),
        PlanFingerprint=PlanFingerprint,
        ApertureDomainFingerprint=ApertureDomainFingerprint,
        CompletedWork=CompletedWork,
        State=ResumeState,
    ), CompletedWork

def ClassifyPhysicalGlobalPlanRetentionAdmission(
    ApertureDiagnostics: object,
    *,
    Continuation: PhysicalGlobalPlanContinuationState,
    ExistingEntry: Any = None,
) -> dict[str, object]:
    """Admit only certified, monotonic, actually resumable continuations."""
    Aperture = (
        ApertureDiagnostics
        if isinstance(ApertureDiagnostics, dict)
        else {}
    )
    ApertureFingerprint = str(Aperture.get("DomainFingerprint", ""))
    ApertureComplete = bool(
        ApertureFingerprint and Aperture.get("Complete", False)
    )
    ExistingContinuation = getattr(
        ExistingEntry,
        "Continuation",
        None,
    )
    ExistingCertificates = frozenset(
        str(Value)
        for Value in getattr(
            ExistingContinuation,
            "CertificateFingerprints",
            (),
        )
        if str(Value)
    )
    ResumeCursor = Continuation.ResumeCursor
    ExistingCursor = getattr(
        ExistingContinuation,
        "ResumeCursor",
        None,
    )
    CursorAvailable = bool(
        ResumeCursor is not None
        and ResumeCursor.CursorFingerprint
        and ResumeCursor.PlanFingerprint == Continuation.PlanFingerprint
        and ResumeCursor.ApertureDomainFingerprint == ApertureFingerprint
        and ResumeCursor.State is not None
    )
    DescriptorState = (
        ResumeCursor.State
        if (
            ResumeCursor is not None
            and isinstance(
                ResumeCursor.State,
                PhysicalGlobalPlanDescriptorProgressState,
            )
        )
        else None
    )
    ExistingDescriptorState = (
        ExistingCursor.State
        if (
            ExistingCursor is not None
            and isinstance(
                ExistingCursor.State,
                PhysicalGlobalPlanDescriptorProgressState,
            )
        )
        else None
    )
    ExistingSameAperture = bool(
        ApertureFingerprint
        and ApertureFingerprint in ExistingCertificates
    )
    DescriptorIdentityMatch = True
    DescriptorCompletedSetSuperset = True
    DescriptorStrictAddition = True
    if DescriptorState is not None:
        DescriptorIdentityMatch = bool(
            DescriptorState.PlanFingerprint
            == Continuation.PlanFingerprint
            and DescriptorState.ApertureDomainFingerprint
            == ApertureFingerprint
        )
        if ExistingCursor is not None:
            DescriptorIdentityMatch = bool(
                DescriptorIdentityMatch
                and ExistingDescriptorState is not None
                and DescriptorState.UniverseIdentities
                == ExistingDescriptorState.UniverseIdentities
            )
            if DescriptorIdentityMatch:
                PriorBySignal = {
                    Value.Signal: Value.CompletedDescriptorFingerprints
                    for Value in ExistingDescriptorState.Signals
                }
                CurrentBySignal = {
                    Value.Signal: Value.CompletedDescriptorFingerprints
                    for Value in DescriptorState.Signals
                }
                DescriptorCompletedSetSuperset = all(
                    CurrentBySignal[Signal] >= PriorCompleted
                    for Signal, PriorCompleted in PriorBySignal.items()
                )
                DescriptorStrictAddition = bool(
                    DescriptorCompletedSetSuperset
                    and any(
                        CurrentBySignal[Signal] > PriorCompleted
                        for Signal, PriorCompleted in PriorBySignal.items()
                    )
                )
            else:
                DescriptorCompletedSetSuperset = False
                DescriptorStrictAddition = False
        else:
            DescriptorStrictAddition = bool(
                DescriptorState.CompletedDescriptorCount > 0
            )
        MonotonicProgress = bool(
            CursorAvailable
            and DescriptorIdentityMatch
            and DescriptorCompletedSetSuperset
            and DescriptorStrictAddition
        )
    elif ExistingDescriptorState is not None:
        # Never replace exact descriptor proof state with a scalar/portal
        # cursor, even when its reported work count is larger.
        DescriptorIdentityMatch = False
        DescriptorCompletedSetSuperset = False
        DescriptorStrictAddition = False
        MonotonicProgress = False
    else:
        MonotonicProgress = bool(
            CursorAvailable
            and Continuation.CompletedWork > 0
            and (
                ExistingCursor is None
                or (
                    ResumeCursor.ApertureDomainFingerprint
                    == ExistingCursor.ApertureDomainFingerprint
                    and ResumeCursor.CompletedWork
                    > ExistingCursor.CompletedWork
                )
            )
        )
    Admitted = bool(
        ApertureComplete
        and CursorAvailable
        and MonotonicProgress
    )
    if not ApertureComplete:
        Reason = "aperture-certificate-incomplete"
    elif not CursorAvailable:
        Reason = "resume-cursor-unavailable"
    elif ExistingCursor is not None and not ExistingSameAperture:
        Reason = "existing-continuation-aperture-mismatch"
    elif not DescriptorIdentityMatch:
        Reason = "descriptor-universe-or-identity-mismatch"
    elif not DescriptorCompletedSetSuperset:
        Reason = "descriptor-completion-is-not-a-superset"
    elif not DescriptorStrictAddition:
        Reason = "descriptor-completion-has-no-strict-addition"
    elif not MonotonicProgress:
        Reason = "non-monotonic-or-zero-progress"
    else:
        Reason = "typed-resumable-progress"
    return {
        "Retained": Admitted,
        "Reason": Reason,
        "CompletedWork": max(0, int(Continuation.CompletedWork)),
        "ApertureDomainFingerprint": ApertureFingerprint,
        "ApertureCertificateComplete": ApertureComplete,
        "ResumeCursorAvailable": CursorAvailable,
        "MonotonicProgress": MonotonicProgress,
        "DescriptorProgress": DescriptorState is not None,
        "DescriptorIdentityMatch": DescriptorIdentityMatch,
        "DescriptorCompletedSetSuperset": (
            DescriptorCompletedSetSuperset
        ),
        "DescriptorStrictAddition": DescriptorStrictAddition,
        "ExistingContinuationSameAperture": ExistingSameAperture,
    }

def BuildClusterInterfaceStageSchedule(
    Deadline: RoutingDeadline,
    StateFingerprints: Iterable[str],
    *,
    LocalCompilationReserveSeconds: float,
    GlobalRoutingReserveSeconds: float,
    PublicationReserveSeconds: float = 2.0,
    ProofGuidedLocalCompilationReserveSeconds: float = 2.0,
) -> ClusterInterfaceStageSchedule:
    """Reserve global routing while funding complete interface states."""
    if LocalCompilationReserveSeconds < 0:
        raise ValueError("local compilation reserve cannot be negative")
    if GlobalRoutingReserveSeconds < 0:
        raise ValueError("global routing reserve cannot be negative")
    if PublicationReserveSeconds < 0:
        raise ValueError("publication reserve cannot be negative")
    if ProofGuidedLocalCompilationReserveSeconds < 0:
        raise ValueError(
            "proof-guided local compilation reserve cannot be negative"
        )
    AvailableSeconds = max(0.0, Deadline.RemainingSeconds())
    # Keep fixed per-interface reserves for generous deadlines, but avoid
    # consuming the entire tail for late-stage cases (CLA4) where remaining
    # time is already scarce.
    RequestedTailSeconds = (
        LocalCompilationReserveSeconds
        + GlobalRoutingReserveSeconds
        + PublicationReserveSeconds
    )
    HasGenerousDeadline = (
        AvailableSeconds >= RequestedTailSeconds * 2.0
    )
    ScaledGlobalReserveSeconds = (
        GlobalRoutingReserveSeconds
        if HasGenerousDeadline
        else min(
            GlobalRoutingReserveSeconds,
            # Before a component reaches global handoff, an unused global
            # reserve cannot legalize the design.  Keep a bounded tail for
            # global routing, but fund the complete local port proof instead
            # of expiring it with the overall deadline still idle.
            AvailableSeconds * 0.07,
        )
    )
    ScaledLocalReserveSeconds = (
        LocalCompilationReserveSeconds
        if HasGenerousDeadline
        else min(
            LocalCompilationReserveSeconds,
            AvailableSeconds * 0.20,
        )
    )
    StartedAt = monotonic()
    ExpiresAt = max(
        StartedAt,
        Deadline.ExpiresAt
        - ScaledGlobalReserveSeconds
        - PublicationReserveSeconds,
    )
    PlanningExpiresAt = max(
        StartedAt,
        ExpiresAt - ScaledLocalReserveSeconds,
    )
    ScaledProofGuidedLocalCompilationReserveSeconds = min(
        ScaledLocalReserveSeconds,
        ProofGuidedLocalCompilationReserveSeconds,
    )
    ProofGuidedPlanningExpiresAt = max(
        PlanningExpiresAt,
        ExpiresAt - ScaledProofGuidedLocalCompilationReserveSeconds,
    )
    AccessRepairGlobalRoutingReserveSeconds = min(
        ScaledGlobalReserveSeconds,
        10.0,
    )
    AccessRepairPlanningExpiresAt = max(
        ProofGuidedPlanningExpiresAt,
        Deadline.ExpiresAt
        - PublicationReserveSeconds
        - AccessRepairGlobalRoutingReserveSeconds
        - ScaledProofGuidedLocalCompilationReserveSeconds,
    )
    AccessRepairExpiresAt = max(
        AccessRepairPlanningExpiresAt,
        Deadline.ExpiresAt
        - PublicationReserveSeconds
        - AccessRepairGlobalRoutingReserveSeconds,
    )
    return ClusterInterfaceStageSchedule(
        StartedAt=StartedAt,
        PlanningExpiresAt=PlanningExpiresAt,
        ProofGuidedPlanningExpiresAt=ProofGuidedPlanningExpiresAt,
        AccessRepairPlanningExpiresAt=AccessRepairPlanningExpiresAt,
        AccessRepairExpiresAt=AccessRepairExpiresAt,
        ExpiresAt=ExpiresAt,
        LocalCompilationReserveSeconds=ScaledLocalReserveSeconds,
        ProofGuidedLocalCompilationReserveSeconds=(
            ScaledProofGuidedLocalCompilationReserveSeconds
        ),
        GlobalRoutingReserveSeconds=ScaledGlobalReserveSeconds,
        AccessRepairGlobalRoutingReserveSeconds=(
            AccessRepairGlobalRoutingReserveSeconds
        ),
        PublicationReserveSeconds=PublicationReserveSeconds,
        StateFingerprints=tuple(StateFingerprints),
    )

def SelectFocusedPlacementInterfacePressureSignals(
    AttemptDiagnostics: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    """Select one deterministic leaf of a repeated exact conflict core.

    Exterior failures can report several pair clauses at once.  Moving every
    endpoint produces a broad geometry whose local feasibility must then be
    reproved before the useful signal is known.  Prefer a signal present in
    the most attempts, then a low-degree conflict endpoint with the largest
    exact aperture domain.  Every input is topology/resource evidence; no net
    name or circuit identity participates in the choice.
    """
    OccurrenceCountBySignal: dict[str, int] = {}
    OccurrenceCountByEdge: dict[tuple[str, str], int] = {}
    ConflictNeighborsBySignal: dict[str, set[str]] = {}
    OptionCountBySignal: dict[str, int] = {}
    for Attempt in AttemptDiagnostics:
        AttemptSignals: set[str] = set()
        AttemptEdges: set[tuple[str, str]] = set()
        for ReservationSet in Attempt.get("NoGoodReservationSets", ()):  # type: ignore[union-attr]
            Signals = tuple(sorted({
                str(Entry[0])
                for Entry in ReservationSet
                if isinstance(Entry, (tuple, list)) and Entry
            }))
            AttemptSignals.update(Signals)
            for LeftIndex, LeftSignal in enumerate(Signals):
                for RightSignal in Signals[LeftIndex + 1:]:
                    Edge = tuple(sorted((LeftSignal, RightSignal)))
                    AttemptEdges.add(Edge)
                    ConflictNeighborsBySignal.setdefault(
                        LeftSignal,
                        set(),
                    ).add(RightSignal)
                    ConflictNeighborsBySignal.setdefault(
                        RightSignal,
                        set(),
                    ).add(LeftSignal)
        for Edge in AttemptEdges:
            OccurrenceCountByEdge[Edge] = (
                OccurrenceCountByEdge.get(Edge, 0) + 1
            )
        if not AttemptSignals:
            AttemptSignals.update(map(
                str,
                Attempt.get("NoGoodSignals", ()),  # type: ignore[union-attr]
            ))
        for Signal in AttemptSignals:
            if Signal:
                OccurrenceCountBySignal[Signal] = (
                    OccurrenceCountBySignal.get(Signal, 0) + 1
                )
        FactorStatus = Attempt.get(  # type: ignore[union-attr]
            "PreparedMandatoryPortalPairFactorStatus",
            {},
        )
        if isinstance(FactorStatus, Mapping):
            OptionCounts = FactorStatus.get("OptionCountsBySignal", {})
            if isinstance(OptionCounts, Mapping):
                for Signal, Count in OptionCounts.items():
                    OptionCountBySignal[str(Signal)] = max(
                        OptionCountBySignal.get(str(Signal), 0),
                        int(Count),
                    )
    if not OccurrenceCountBySignal:
        return ()
    CandidateEdges = tuple(OccurrenceCountByEdge)
    if CandidateEdges:
        return min(
            CandidateEdges,
            key=lambda Edge: (
                -OccurrenceCountByEdge[Edge],
                -sum(OccurrenceCountBySignal[Signal] for Signal in Edge),
                -prod(
                    max(1, OptionCountBySignal.get(Signal, 1))
                    for Signal in Edge
                ),
                BuildStableFingerprint((
                    "component-interface-pressure-edge-v1",
                    Edge,
                )),
                Edge,
            ),
        )
    SelectedSignal = min(
        OccurrenceCountBySignal,
        key=lambda Signal: (
            -OccurrenceCountBySignal[Signal],
            len(ConflictNeighborsBySignal.get(Signal, set())),
            -OptionCountBySignal.get(Signal, 0),
            BuildStableFingerprint((
                "component-interface-pressure-signal-v1",
                Signal,
            )),
            Signal,
        ),
    )
    return (SelectedSignal,)

def BuildLocalComponentCompilationAdmissionFailure(
    Schedule: ClusterInterfaceStageSchedule,
    *,
    RemainingSeconds: float,
) -> RoutingFailure:
    """Return typed incomplete when no authoritative local solve can start."""
    return RoutingFailure(
        Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete,
        Stage="ClosedComponentCompilationIncomplete",
        Detail=(
            "local component compilation was not admitted before its "
            "reserved stage deadline"
        ),
        RepairActions=(),
        Diagnostics={
            "ComponentRoutingSolve": {
                "Status": "incomplete",
                "Complete": False,
                "ExpansionCount": 0,
                "Diagnostics": {
                    "DeadlineExceeded": True,
                    "WorkCapReached": False,
                    "LocalCompilationEntered": False,
                    "RemainingSeconds": max(0.0, RemainingSeconds),
                },
            },
            "DeadlineExceeded": True,
            "WorkCapReached": False,
            "LocalCompilationEntered": False,
            "ExecutableLegacyRepairCascade": False,
            "StageSchedule": Schedule.ToDictionary(),
        },
    )

def BuildPhysicalAssemblyPlanningIncompleteFailure(
    Schedule: ClusterInterfaceStageSchedule,
    *,
    RemainingSeconds: float,
    GlobalPlanningEntered: bool,
) -> RoutingFailure:
    """Return typed incomplete before an immutable plan reaches handoff."""
    return RoutingFailure(
        Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete,
        Stage="PhysicalAssemblyPlanningIncomplete",
        Detail=(
            "physical component planning did not produce a bound assembly "
            "plan before its planning deadline"
        ),
        RepairActions=(),
        Diagnostics={
            "DeadlineExceeded": True,
            "WorkCapReached": False,
            "GlobalPlanningEntered": GlobalPlanningEntered,
            "LocalCompilationEntered": False,
            "RemainingSeconds": max(0.0, RemainingSeconds),
            "ExecutableLegacyRepairCascade": False,
            "StageSchedule": Schedule.ToDictionary(),
        },
    )

def BuildClosedComponentExecutionIncompleteFailure(
    Schedule: ClusterInterfaceStageSchedule,
    ComponentSolve: Any,
    *,
    PhysicalAssemblyPlanFingerprint: str,
    RemainingSeconds: float,
) -> RoutingFailure:
    """Return typed incomplete after local compilation was admitted."""
    return RoutingFailure(
        Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete,
        Stage="ClosedComponentCompilationIncomplete",
        Detail=(
            "the admitted closed-component proof did not complete before "
            "its execution deadline"
        ),
        RepairActions=(),
        Diagnostics={
            "ComponentRoutingSolve": {
                "Status": "incomplete",
                "UnderlyingStatus": str(ComponentSolve.Status),
                "ProofFingerprint": str(
                    ComponentSolve.ProofFingerprint
                ),
                "ExpansionCount": int(ComponentSolve.ExpansionCount),
                "Complete": False,
                "Diagnostics": dict(ComponentSolve.Diagnostics),
            },
            "PhysicalAssemblyPlanFingerprint": (
                PhysicalAssemblyPlanFingerprint
            ),
            "DeadlineExceeded": True,
            "WorkCapReached": False,
            "GlobalPlanningEntered": True,
            "LocalCompilationEntered": True,
            "RemainingSeconds": max(0.0, RemainingSeconds),
            "ExecutableLegacyRepairCascade": False,
            "StageSchedule": Schedule.ToDictionary(),
        },
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
            MaximumRuntimeSeconds=EffectiveSeconds,
        ),
    )

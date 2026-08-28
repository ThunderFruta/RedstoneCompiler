"""Joint placement portfolio identities, cuts, and repair controls."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    replace,
)
from itertools import (
    combinations,
)
from typing import (
    Any,
    Iterable,
    Mapping,
)
from Compiler.Routing.Failures import (
    RoutingAssignmentCut,
    RoutingAssignmentCutClassification,
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from Compiler.Routing.Reliability import (
    BuildStableFingerprint,
)
from Compiler.Routing.Policy import (
    PhysicalDesignPolicy,
)
from Compiler.Placement.Core.Clusters import (
    PcbPlacement,
)
from Compiler.Placement.Core.Constraints import (
    BuildAssignmentCutHigherOrderSignalSet,
    PlacementAssignmentConstraintSet,
)
from Compiler.Placement.Core.MandatoryAccess import (
    MandatoryAccessConflictProfile,
)
@dataclass(frozen=True)
class PlacementGenerationRequest:
    """One deterministic placement recipe, before its expensive construction."""

    SourceGenerator: str
    RoutingSpacing: int
    PackingPolicy: Any
    UseCurrentAssignmentCutRelocationSignals: bool = False
    # An explicit state from the finite pin-aligned graph-core portfolio.
    # ``None`` preserves the ordinary row-beam request; a value is never a
    # retry index and is materialized before the shared pre-route solve.
    GraphCoreCandidateIndex: int | None = None
    # An explicit complete terminal layout from the derived perimeter-domain
    # solver.  The index is materialized as part of the fixed primary domain,
    # not advanced after a failed capacity or routing result.
    TerminalLayoutVariantIndex: int = 0

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
    deliberately never receives this topology-driven routing control.
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

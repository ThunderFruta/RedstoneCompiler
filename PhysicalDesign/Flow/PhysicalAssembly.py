"""Importable placement-flow helpers with explicit run state."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence
from PhysicalDesign.Routing.Pcb import ReplanPhysicalComponentAssembly, ValidatePhysicalComponentForeignPortalSupport
from PhysicalDesign.Contracts.Results import RoutedDesign
from PhysicalDesign.Contracts.Failures import RoutingAssignmentCut, RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Execution.Reliability import BuildStableFingerprint
from PhysicalDesign.Placement.Core.MandatoryAccess import MeasureMandatoryAccessConflictProfile
from PhysicalDesign.Placement.Core.Repair import BuildTransactionalClusterEndpointRepair
from PhysicalDesign.Routing.Global.Candidates.CandidateCache import BuildFrozenPostClosurePortalHandoffTelemetry
from PhysicalDesign.Routing.Global.Candidates.CandidateGuides import BuildPhysicalGlobalPlanContinuationState, BuildPhysicalGlobalPlanYieldDeadline, RetainIncompletePhysicalGlobalPlan, SelectNextRetainedPhysicalGlobalPlan, ShouldScheduleRetainedPhysicalGlobalPlan
from PhysicalDesign.Routing.Regions.Proofs.Certification import ProveClosedComponentOwnedSignalFrontiers, SelectContractIndependentOwnedSignalFrontierUnsatCore
from PhysicalDesign.Routing.Regions.Proofs.GlobalNoGoods import RecordPhysicalComponentGlobalPlanNoGood
from PhysicalDesign.Routing.Regions.Proofs.NoGoods import RecordPhysicalComponentSymbolicCapacityEligibilityNoGood
from PhysicalDesign.Routing.Regions.Planning.PhysicalPlanning import BindPhysicalComponentAssemblyGlobalChannels, BindPhysicalComponentAssemblyLocalPortSupports, ClassifyPhysicalComponentGlobalPlanningFailure, PreparePhysicalComponentGlobalPlanningPlacement, PruneRetainedPhysicalGlobalPlansByRejectedApertureClauses, SelectPhysicalAssemblyGlobalBoundaryPorts, SelectPhysicalComponentExactGlobalChannelSignals
from PhysicalDesign.Routing.Regions.Symbolic.SymbolicDomains import CompilePhysicalComponentSymbolicPortPairDomain, ProjectCompletePhysicalPortPairCertificateToApertureClauses, ProveClosedComponentSymbolicCapacityEligibility
from PhysicalDesign.Routing.Regions.Proofs.Validation import BuildPhysicalPortLocalContractFingerprint
from .Candidates import BuildComponentAccessFeedbackPlacementScore, BuildPhysicalAssemblyPlanningIncompleteFailure, BuildPhysicalGlobalPlanResumeCursorFromDiagnostics, ClassifyPhysicalGlobalPlanRetentionAdmission, FindPhysicalGlobalDiagnosticValues, HasDistinctRetainedPhysicalEligibilityState, PcbPlacementCandidate, QueuedPhysicalEligibilityPlacementFingerprints, SelectRetainedPhysicalPlacementForAccessCore
from .Demand import MeasurePlacementTopologyDemand
from .Feedback import BuildPlacementFingerprint
from .Portfolios import BuildTopologyCutEpochIdentity, PlacementGenerationRequest
from .Preparation import BuildClusterInterfaceComponentStateFingerprint, BuildClusterInterfacePlacementTopologyFingerprint, BuildPlacementRetentionFingerprint
from .Results import BuildCapacityRepairGeometryFingerprint, BuildPhysicalComponentPlacementFeedback, BuildPhysicalInterfaceRepairCore, BuildPhysicalLocalFactorDiversificationCore, BuildPhysicalOwnedFrontierTopologyRepairCore, BuildSymbolicCapacityRepairEvidence, ComposePhysicalInterfaceRepairCores, FreezePhysicalAssemblyGlobalChannels, PhysicalComponentPlacementFeedback, PhysicalInterfaceRepairCore
from functools import partial
from .State import (
    PlacementFlowState,
    SetPlacementFlowState,
    _PlacementFlowDefault,
)
from .RoutingAttempts import (
    _BuildCandidateRecords,
    _PublishTransactionalClusterEndpointRepair,
)
from .PlacementAttempts import (
    _TakeNextDeferredRequest,
    _TryPlacement,
)


def ReorderRemainingPlacementsForAccessCore(Context, CurrentPlacementFingerprint: str) -> None:
    IndexedQueue = tuple(enumerate(Context.InterfaceCandidateQueue))
    Context.InterfaceCandidateQueue[:] = [Value for _Index, Value in sorted(IndexedQueue, key=lambda Entry: (Entry[1][4], 0 if Entry[1][0] == 'prepare-eligibility' else 1, 1 if Entry[1][2].PlacementFingerprint == CurrentPlacementFingerprint else 0, 0 if Entry[1][2].PlacementFingerprint in Context.LocalFactorDiversificationCandidateByPlacementFingerprint else 1, (0, 0, 0, 0, 0) if Entry[1][2].PlacementFingerprint == CurrentPlacementFingerprint else BuildComponentAccessFeedbackPlacementScore(Entry[1][2], Context.ActiveComponentCutSignals), Entry[0]))]


def PendingJointPlacementStateMatchesPhysicalProof(
    State: Any,
    PhysicalProofCoreSignals: tuple[str, ...],
    PhysicalProofFingerprint: str,
    AssignmentCut: RoutingAssignmentCut | None,
    AssignmentConstraints: Any,
) -> bool:
    """Retain a beam sibling only while its complete proof identity is live."""
    ExpectedCutFingerprint = (
        AssignmentCut.ConflictFingerprint
        if AssignmentCut is not None
        else ''
    )
    StateCutFingerprint = str(getattr(
        getattr(State, 'AssignmentCut', None),
        'ConflictFingerprint',
        '',
    ))
    ExpectedConstraintFingerprint = str(getattr(
        AssignmentConstraints,
        'Fingerprint',
        '',
    ))
    StateConstraintFingerprint = str(getattr(
        getattr(State, 'AssignmentConstraints', None),
        'Fingerprint',
        '',
    ))
    ExpectedProofCoreSignals = frozenset(PhysicalProofCoreSignals)
    return bool(
        frozenset(getattr(State, 'PhysicalProofCoreSignals', ()))
        == ExpectedProofCoreSignals
        and str(getattr(State, 'PhysicalProofFingerprint', ''))
        == str(PhysicalProofFingerprint)
        and ExpectedProofCoreSignals
        <= frozenset(getattr(State, 'RelocationSignals', ()))
        and ExpectedProofCoreSignals
        <= frozenset(getattr(State, 'RequiredRelocationSignals', ()))
        and StateCutFingerprint == ExpectedCutFingerprint
        and StateConstraintFingerprint == ExpectedConstraintFingerprint
    )


def SelectFreshProofGuidedPlacementCandidate(
    Candidates: Iterable[PcbPlacementCandidate],
    ExistingPlacementFingerprints: set[str] | frozenset[str],
    PlacementFingerprintsBeforeGeneration: set[str] | frozenset[str],
    *,
    RequireCurrentGeneration: bool = False,
) -> PcbPlacementCandidate | None:
    """Prefer geometry created for the current proof, then stable backlog."""
    IndexedCandidates = tuple(enumerate(Candidates))
    return next((
        Candidate
        for _Index, Candidate in sorted(
            IndexedCandidates,
            key=lambda Entry: (
                Entry[1].PlacementFingerprint
                in PlacementFingerprintsBeforeGeneration,
                Entry[0],
            ),
        )
        if Candidate.PlacementFingerprint
        not in ExistingPlacementFingerprints
        and (
            not RequireCurrentGeneration
            or Candidate.PlacementFingerprint
            not in PlacementFingerprintsBeforeGeneration
        )
    ), None)


def InheritOwnedFrontierTopologyRepairKind(
    Context,
    SourcePlacementFingerprint: str,
    CandidatePlacementFingerprint: str,
) -> str:
    """Carry a proven topology strategy into a proof-guided descendant."""
    Kind = Context.OwnedFrontierTopologyRepairKindByPlacementFingerprint.get(
        SourcePlacementFingerprint,
        "",
    )
    if Kind:
        Context.OwnedFrontierTopologyRepairKindByPlacementFingerprint.setdefault(
            CandidatePlacementFingerprint,
            Kind,
        )
        if not hasattr(
            Context,
            "OwnedFrontierTopologyRepairSignalsByPlacementFingerprint",
        ):
            Context.OwnedFrontierTopologyRepairSignalsByPlacementFingerprint = {}
        RepairSignals = (
            Context.OwnedFrontierTopologyRepairSignalsByPlacementFingerprint.get(
                SourcePlacementFingerprint,
                (),
            )
        )
        if RepairSignals:
            Context.OwnedFrontierTopologyRepairSignalsByPlacementFingerprint.setdefault(
                CandidatePlacementFingerprint,
                tuple(RepairSignals),
            )
    return Kind


def SelectInheritedTopologyJointPlacementCandidateIndex(
    Kind: str,
    *,
    ComposedSignalCount: int = 0,
) -> int:
    """Use one alternate branch, then let a composed core stop early."""
    if ComposedSignalCount >= 3:
        return 0
    return 1 if Kind in {
        "split-interface-cut",
        "relocate-endpoint-cluster",
    } else 0


def AccumulateProofGuidedRelocationSignals(
    CumulativeSignals: set[str],
    CurrentSignals: Iterable[str],
    *,
    Reset: bool,
) -> frozenset[str]:
    """Compose exact repair cores until an explicitly transient reset."""
    if Reset:
        CumulativeSignals.clear()
    CumulativeSignals.update(map(str, CurrentSignals))
    return frozenset(CumulativeSignals)


def SelectCapacityRepairGeometryConstraint(
    FreshConstraint: PhysicalInterfaceRepairCore | None,
    InheritedGeometryConstraint: PhysicalInterfaceRepairCore | None,
    AuthoritativeConstraint: PhysicalInterfaceRepairCore | None,
) -> PhysicalInterfaceRepairCore | None:
    """Keep the last exact child focus through a non-refining failure."""
    return (
        FreshConstraint
        or InheritedGeometryConstraint
        or AuthoritativeConstraint
    )


def SelectCapacityRepairGeometryFocus(
    FreshFocus: PhysicalComponentPlacementFeedback | None,
    InheritedFocus: PhysicalComponentPlacementFeedback | None,
    FallbackFocus: PhysicalComponentPlacementFeedback,
    AuthoritativeSignals: Iterable[str] = (),
) -> PhysicalComponentPlacementFeedback:
    """Use the smallest exact focus, preferring newer proof identity on ties."""
    StableAuthoritativeSignals = frozenset(map(str, AuthoritativeSignals))
    if StableAuthoritativeSignals:
        FreshFocus = (
            FreshFocus
            if FreshFocus is not None
            and frozenset(map(str, FreshFocus.RelocationSignals))
            <= StableAuthoritativeSignals
            else None
        )
        InheritedFocus = (
            InheritedFocus
            if InheritedFocus is not None
            and frozenset(map(str, InheritedFocus.RelocationSignals))
            <= StableAuthoritativeSignals
            else None
        )
    if FreshFocus is None:
        return InheritedFocus or FallbackFocus
    if InheritedFocus is None:
        return FreshFocus
    return min(
        (FreshFocus, InheritedFocus),
        key=lambda Focus: (
            len(frozenset(Focus.RelocationSignals)),
            0 if Focus is FreshFocus else 1,
        ),
    )


def EnqueueProofGuidedPhysicalPlacement(Context, Failure: RoutingFailure, SourceCandidate: PcbPlacementCandidate, ComponentVariant: int) -> bool:
    """Turn one complete global-plan cut into a new placement state."""
    Diagnostics = Failure.Diagnostics if isinstance(Failure.Diagnostics, dict) else {}
    CompleteGlobalAssignmentCut = bool(Diagnostics.get('GlobalPlanDomainComplete', False) and Diagnostics.get('CompleteAssignmentCutProof', False))
    PhysicalPlacementFeedback = BuildPhysicalComponentPlacementFeedback(Failure)
    PlacementPressureSignals = tuple(sorted({str(Signal) for Signal in Diagnostics.get('PlacementInterfacePressureSignals', ()) if str(Signal)}))
    SymbolicCapacityFeedback = bool(Diagnostics.get('SymbolicCapacityPlacementFeedback', False) and PlacementPressureSignals)
    FreshCapacityRepairConstraint = BuildPhysicalInterfaceRepairCore(
        Failure,
        SourceCandidate,
    )
    CapacityRepairConstraint = FreshCapacityRepairConstraint
    InheritedCapacityRepairConstraint = Context.CapacityRepairConstraintByPlacementFingerprint.get(SourceCandidate.PlacementFingerprint)
    InheritedCapacityRepairGeometryConstraint = (
        Context.CapacityRepairGeometryConstraintByPlacementFingerprint.get(
            SourceCandidate.PlacementFingerprint
        )
    )
    InheritedCapacityRepairGeometryFocus = (
        Context.CapacityRepairGeometryFocusByPlacementFingerprint.get(
            SourceCandidate.PlacementFingerprint
        )
    )
    FreshCapacityRepairRefinement = bool(
        FreshCapacityRepairConstraint is not None
        and InheritedCapacityRepairConstraint is not None
        and FreshCapacityRepairConstraint.SourceProofFingerprint
        != InheritedCapacityRepairConstraint.SourceProofFingerprint
    )
    if FreshCapacityRepairRefinement:
        CapacityRepairConstraint = ComposePhysicalInterfaceRepairCores(
            InheritedCapacityRepairConstraint,
            FreshCapacityRepairConstraint,
            SourceCandidate,
        )
    InheritedCapacityRepairRefinement = bool(
        CapacityRepairConstraint is None
        and InheritedCapacityRepairConstraint is not None
        and PhysicalPlacementFeedback is not None
    )
    if CapacityRepairConstraint is None and InheritedCapacityRepairConstraint is not None:
        CapacityRepairConstraint = InheritedCapacityRepairConstraint
    CapacityRepairGeometryConstraint = SelectCapacityRepairGeometryConstraint(
        FreshCapacityRepairConstraint,
        InheritedCapacityRepairGeometryConstraint,
        CapacityRepairConstraint,
    )
    CapacityRepairGeometryFocus = None
    if CapacityRepairConstraint is not None:
        FreshCapacityRepairGeometryFocus = (
            PhysicalComponentPlacementFeedback(
                ProofFingerprint=(
                    FreshCapacityRepairConstraint.SourceProofFingerprint
                ),
                RelocationSignals=FreshCapacityRepairConstraint.Signals,
                DomainFingerprint=(
                    FreshCapacityRepairConstraint.RepairDomainFingerprint
                ),
            )
            if FreshCapacityRepairConstraint is not None
            else PhysicalPlacementFeedback
        )
        CapacityRepairGeometryFocus = SelectCapacityRepairGeometryFocus(
            FreshCapacityRepairGeometryFocus,
            InheritedCapacityRepairGeometryFocus,
            PhysicalComponentPlacementFeedback(
                ProofFingerprint=(
                    CapacityRepairGeometryConstraint.SourceProofFingerprint
                ),
                RelocationSignals=CapacityRepairGeometryConstraint.Signals,
                DomainFingerprint=(
                    CapacityRepairGeometryConstraint.RepairDomainFingerprint
                ),
            ),
            CapacityRepairConstraint.Signals,
        )
    CapacityRepairFocusSignals = (
        CapacityRepairGeometryFocus.RelocationSignals
        if CapacityRepairGeometryFocus is not None
        else ()
    )
    CapacityRepairGenerationSignals = SelectCapacityRepairGenerationSignals(
        CapacityRepairConstraint,
        CapacityRepairFocusSignals,
    )
    CapacityRepairGeometryFocusProofFingerprint = (
        CapacityRepairGeometryFocus.ProofFingerprint
        if CapacityRepairGeometryFocus is not None
        else ''
    )
    CapacityRepairGenerationProofFingerprint = (
        BuildStableFingerprint((
            'capacity-repair-geometry-focus-v2',
            CapacityRepairConstraint.SourceProofFingerprint,
            CapacityRepairGeometryFocusProofFingerprint,
            tuple(CapacityRepairFocusSignals),
            tuple(sorted(CapacityRepairGenerationSignals)),
        ))
        if CapacityRepairConstraint is not None
        else ''
    )
    if CapacityRepairConstraint is not None and PhysicalPlacementFeedback is None:
        PhysicalPlacementFeedback = CapacityRepairGeometryFocus
    CapacityRepairActive = bool(CapacityRepairConstraint is not None)
    TransactionalCapacityRepairSignals = (
        SelectCapacityRepairTransactionalSignals(
            CapacityRepairConstraint,
            CapacityRepairFocusSignals,
        )
    )
    if (
        CapacityRepairActive
        and TransactionalCapacityRepairSignals
    ):
        TransactionalAttempt = (
            Context.CapacityRepairGeneratedCountByProofFingerprint.get(
                CapacityRepairGenerationProofFingerprint,
                0,
            )
        )
        TransactionalVariants = SelectCapacityRepairTransactionalVariants(
            TransactionalAttempt,
            TransactionalCapacityRepairSignals,
        )
        if TransactionalVariants:
            ComposeTransactionalDescendant = bool(
                len(TransactionalCapacityRepairSignals) > 2
            )
            TransactionalSourceCandidate = SourceCandidate
            PublishedTransactionalCandidates: list[
                tuple[int, str, PcbPlacementCandidate]
            ] = []
            PrefetchedSingletonSignalByPlacementFingerprint: dict[
                str, str
            ] = {}
            PrefetchedSingletonRepairVariantByPlacementFingerprint: dict[
                str, int
            ] = {}
            for RepairVariant in TransactionalVariants:
                ExistingTransactionalFingerprints = frozenset(
                    Context.UniquePlacements
                )
                TransactionalPublished = (
                    _PublishTransactionalClusterEndpointRepair(
                        Context,
                        TransactionalSourceCandidate,
                        TransactionalCapacityRepairSignals,
                        RepairVariant=RepairVariant,
                        RepairClusterCount=2,
                        AllowStableMandatoryAccessOwnership=True,
                    )
                )
                Context.CapacityRepairGeneratedCountByProofFingerprint[
                    CapacityRepairGenerationProofFingerprint
                ] = RepairVariant + 1
                if not TransactionalPublished:
                    break
                TransactionalCandidates = tuple(
                    Candidate
                    for Candidate in _BuildCandidateRecords(Context)
                    if (
                        Candidate.PlacementFingerprint
                        not in ExistingTransactionalFingerprints
                        and Candidate.SourceGenerator
                        == 'transactional-cluster-endpoint-repair'
                    )
                )
                if not TransactionalCandidates:
                    raise RuntimeError(
                        'published capacity endpoint repair has no candidate record'
                    )
                Candidate = min(
                    TransactionalCandidates,
                    key=lambda Value: Value.PlacementFingerprint,
                )
                PublishedTransactionalCandidates.append((
                    RepairVariant,
                    TransactionalSourceCandidate.CandidateId,
                    Candidate,
                ))
                TransactionalSourceCandidate = Candidate
            PrefetchSignal = (
                SelectCapacityRepairCumulativeSingletonPrefetchSignal(
                    TransactionalCapacityRepairSignals,
                    (*Context.Module.Inputs, *Context.Module.Outputs),
                )
                if (
                    ComposeTransactionalDescendant
                    and TransactionalAttempt == 0
                    and len(PublishedTransactionalCandidates) == 2
                )
                else ''
            )
            if PrefetchSignal:
                (
                    CumulativeVariant,
                    _CumulativeSourceCandidateId,
                    CumulativeCandidate,
                ) = max(
                    PublishedTransactionalCandidates,
                    key=lambda Value: Value[0],
                )
                ExistingTransactionalFingerprints = frozenset(
                    Context.UniquePlacements
                )
                PrefetchPublished = (
                    _PublishTransactionalClusterEndpointRepair(
                        Context,
                        CumulativeCandidate,
                        frozenset((PrefetchSignal,)),
                        RepairVariant=1,
                        RepairClusterCount=1,
                        AllowStableMandatoryAccessOwnership=True,
                    )
                )
                if PrefetchPublished:
                    PrefetchCandidates = tuple(
                        Candidate
                        for Candidate in _BuildCandidateRecords(Context)
                        if (
                            Candidate.PlacementFingerprint
                            not in ExistingTransactionalFingerprints
                            and Candidate.SourceGenerator
                            == 'transactional-cluster-endpoint-repair'
                        )
                    )
                    if not PrefetchCandidates:
                        raise RuntimeError(
                            'published capacity singleton prefetch has no '
                            'candidate record'
                        )
                    PrefetchCandidate = min(
                        PrefetchCandidates,
                        key=lambda Value: Value.PlacementFingerprint,
                    )
                    PrefetchSequence = CumulativeVariant + 1
                    PublishedTransactionalCandidates.append((
                        PrefetchSequence,
                        CumulativeCandidate.CandidateId,
                        PrefetchCandidate,
                    ))
                    PrefetchedSingletonSignalByPlacementFingerprint[
                        PrefetchCandidate.PlacementFingerprint
                    ] = PrefetchSignal
                    PrefetchedSingletonRepairVariantByPlacementFingerprint[
                        PrefetchCandidate.PlacementFingerprint
                    ] = 1
                    LearnedTransition = (
                        SelectLearnedAdvancingSingletonRepairTransition(
                            Context
                            .LocalFactorDiversificationNextSignalByRepairKey
                        )
                    )
                    if LearnedTransition is not None:
                        LearnedRepairKey, LearnedNextSignal = (
                            LearnedTransition
                        )
                        LearnedSignal = str(LearnedRepairKey[1])
                        LearnedVariant = int(LearnedRepairKey[2])
                        LearnedPreview = (
                            BuildTransactionalClusterEndpointRepair(
                                PrefetchCandidate.Placement,
                                frozenset((LearnedSignal,)),
                                BeamWidth=min(
                                    16,
                                    Context.Policy.NandPacking.BeamWidth,
                                ),
                                RepairVariant=LearnedVariant,
                                RepairClusterCount=1,
                            )
                        )
                        PreviewRepairKey = (
                            BuildSingletonLocalFactorRepairTransitionKey(
                                LearnedSignal,
                                LearnedVariant,
                                LearnedPreview.Diagnostics,
                            )
                            if LearnedPreview.Accepted
                            else ()
                        )
                        if PreviewRepairKey == LearnedRepairKey:
                            ExistingTransactionalFingerprints = frozenset(
                                Context.UniquePlacements
                            )
                            LearnedRepairPublished = (
                                _PublishTransactionalClusterEndpointRepair(
                                    Context,
                                    PrefetchCandidate,
                                    frozenset((LearnedSignal,)),
                                    RepairVariant=LearnedVariant,
                                    RepairClusterCount=1,
                                    AllowStableMandatoryAccessOwnership=True,
                                )
                            )
                            LearnedRepairCandidates = (
                                tuple(
                                    Candidate
                                    for Candidate
                                    in _BuildCandidateRecords(Context)
                                    if (
                                        Candidate.PlacementFingerprint
                                        not in ExistingTransactionalFingerprints
                                        and Candidate.SourceGenerator
                                        == 'transactional-cluster-endpoint-repair'
                                    )
                                )
                                if LearnedRepairPublished
                                else ()
                            )
                            if LearnedRepairCandidates:
                                LearnedRepairCandidate = min(
                                    LearnedRepairCandidates,
                                    key=lambda Value: (
                                        Value.PlacementFingerprint
                                    ),
                                )
                                LearnedRepairSequence = PrefetchSequence + 1
                                PublishedTransactionalCandidates.append((
                                    LearnedRepairSequence,
                                    PrefetchCandidate.CandidateId,
                                    LearnedRepairCandidate,
                                ))
                                PrefetchedSingletonSignalByPlacementFingerprint[
                                    LearnedRepairCandidate.PlacementFingerprint
                                ] = LearnedSignal
                                PrefetchedSingletonRepairVariantByPlacementFingerprint[
                                    LearnedRepairCandidate.PlacementFingerprint
                                ] = LearnedVariant
                                for NextVariant in range(2):
                                    ExistingTransactionalFingerprints = (
                                        frozenset(Context.UniquePlacements)
                                    )
                                    NextPublished = (
                                        _PublishTransactionalClusterEndpointRepair(
                                            Context,
                                            LearnedRepairCandidate,
                                            frozenset((LearnedNextSignal,)),
                                            RepairVariant=NextVariant,
                                            RepairClusterCount=1,
                                            AllowStableMandatoryAccessOwnership=True,
                                        )
                                    )
                                    if not NextPublished:
                                        continue
                                    NextCandidates = tuple(
                                        Candidate
                                        for Candidate
                                        in _BuildCandidateRecords(Context)
                                        if (
                                            Candidate.PlacementFingerprint
                                            not in ExistingTransactionalFingerprints
                                            and Candidate.SourceGenerator
                                            == 'transactional-cluster-endpoint-repair'
                                        )
                                    )
                                    for NextCandidate in NextCandidates:
                                        LearnedRepairSequence += 1
                                        PublishedTransactionalCandidates.append((
                                            LearnedRepairSequence,
                                            LearnedRepairCandidate.CandidateId,
                                            NextCandidate,
                                        ))
                                        PrefetchedSingletonSignalByPlacementFingerprint[
                                            NextCandidate.PlacementFingerprint
                                        ] = LearnedNextSignal
                                        PrefetchedSingletonRepairVariantByPlacementFingerprint[
                                            NextCandidate.PlacementFingerprint
                                        ] = NextVariant
                                        AlternateVariant = (
                                            SelectAlternateBinarySingletonRepairVariant(
                                                LearnedVariant
                                            )
                                        )
                                        if AlternateVariant is None:
                                            continue
                                        ExistingTransactionalFingerprints = (
                                            frozenset(Context.UniquePlacements)
                                        )
                                        AlternatePublished = (
                                            _PublishTransactionalClusterEndpointRepair(
                                                Context,
                                                NextCandidate,
                                                frozenset((LearnedSignal,)),
                                                RepairVariant=AlternateVariant,
                                                RepairClusterCount=1,
                                                AllowStableMandatoryAccessOwnership=True,
                                            )
                                        )
                                        if not AlternatePublished:
                                            continue
                                        AlternateDecision = next((
                                            Decision
                                            for Decision in reversed(
                                                Context.PlacementGenerationDecisions
                                            )
                                            if (
                                                Decision.get('Result')
                                                == 'transactional-cluster-endpoint-repair-published'
                                                and Decision.get('SourceCandidateId')
                                                == NextCandidate.CandidateId
                                                and int(Decision.get(
                                                    'RepairVariant',
                                                    -1,
                                                )) == AlternateVariant
                                            )
                                        ), {})
                                        AlternateRepairKey = (
                                            BuildSingletonLocalFactorRepairTransitionKey(
                                                LearnedSignal,
                                                AlternateVariant,
                                                dict(AlternateDecision.get(
                                                    'Diagnostics',
                                                    {},
                                                )),
                                            )
                                        )
                                        AlternateCandidates = tuple(
                                            Candidate
                                            for Candidate
                                            in _BuildCandidateRecords(Context)
                                            if (
                                                Candidate.PlacementFingerprint
                                                not in ExistingTransactionalFingerprints
                                                and Candidate.SourceGenerator
                                                == 'transactional-cluster-endpoint-repair'
                                            )
                                        )
                                        for AlternateCandidate in (
                                            AlternateCandidates
                                        ):
                                            LearnedRepairSequence += 1
                                            PublishedTransactionalCandidates.append((
                                                LearnedRepairSequence,
                                                NextCandidate.CandidateId,
                                                AlternateCandidate,
                                            ))
                                            AlternateFingerprint = (
                                                AlternateCandidate
                                                .PlacementFingerprint
                                            )
                                            PrefetchedSingletonSignalByPlacementFingerprint[
                                                AlternateFingerprint
                                            ] = LearnedSignal
                                            PrefetchedSingletonRepairVariantByPlacementFingerprint[
                                                AlternateFingerprint
                                            ] = AlternateVariant
                                            if AlternateRepairKey:
                                                Context.LocalFactorDiversificationRepairKeyByPlacementFingerprint[
                                                    AlternateFingerprint
                                                ] = AlternateRepairKey
            if PublishedTransactionalCandidates:
                PublishedTransactionalCandidates.sort(
                    key=lambda Value: (
                        -Value[0]
                        if ComposeTransactionalDescendant
                        else 0,
                        BuildTransactionalRepairRoutingPriority(
                            Value[2],
                            (*Context.Module.Inputs, *Context.Module.Outputs),
                        ),
                    )
                )
                QueueEntries: list[tuple[object, ...]] = []
                for (
                    RepairVariant,
                    TransactionalSourceCandidateId,
                    Candidate,
                ) in PublishedTransactionalCandidates:
                    CandidateFingerprint = Candidate.PlacementFingerprint
                    Context.CapacityRepairConstraintByPlacementFingerprint[
                        CandidateFingerprint
                    ] = CapacityRepairConstraint
                    Context.CapacityRepairGeometryConstraintByPlacementFingerprint[
                        CandidateFingerprint
                    ] = CapacityRepairGeometryConstraint
                    Context.CapacityRepairGeometryFocusByPlacementFingerprint[
                        CandidateFingerprint
                    ] = CapacityRepairGeometryFocus
                    Context.CapacityRepairCandidateByPlacementFingerprint[
                        CandidateFingerprint
                    ] = Candidate
                    Context.CapacityRepairGeometryKindByPlacementFingerprint[
                        CandidateFingerprint
                    ] = (
                        'transactional-composed-singleton-prefetch'
                        if CandidateFingerprint in (
                            PrefetchedSingletonSignalByPlacementFingerprint
                        )
                        else 'transactional-composed-endpoint'
                        if ComposeTransactionalDescendant
                        else 'transactional-pair-endpoint'
                    )
                    Context.ProofGuidedPlacementFingerprints.add(
                        CandidateFingerprint
                    )
                    Context.GeneratedProofGuidedPlacementFingerprints.add(
                        CandidateFingerprint
                    )
                    Context.ProofGuidedPlacementGenerationCount += 1
                    Context.ProofGuidedPlacementGenerationCountByCore[
                        tuple(sorted(TransactionalCapacityRepairSignals))
                    ] = (
                        Context.ProofGuidedPlacementGenerationCountByCore.get(
                            tuple(sorted(TransactionalCapacityRepairSignals)),
                            0,
                        ) + 1
                    )
                    Context.RequestedComponentStateFingerprints.add(
                        BuildClusterInterfaceComponentStateFingerprint(
                            CandidateFingerprint,
                            ComponentVariant,
                        )
                    )
                    Context.ProofGuidedGenerationSourceByPlacementFingerprint[
                        CandidateFingerprint
                    ] = (Failure, SourceCandidate, ComponentVariant)
                    QueueEntries.append((
                        'prepare-eligibility',
                        len(Context.InterfaceCandidates)
                        + Context.ProofGuidedPlacementGenerationCount,
                        Candidate,
                        0,
                        ComponentVariant,
                    ))
                    Context.CapacityRepairPortfolioDiagnostics.append({
                        'Result': (
                            'capacity-composed-transactional-repair-generated'
                            if ComposeTransactionalDescendant
                            else 'capacity-pair-transactional-repair-generated'
                        ),
                        'SourceCandidateId': TransactionalSourceCandidateId,
                        'SourceProofFingerprint': (
                            CapacityRepairConstraint.SourceProofFingerprint
                        ),
                        'Signals': sorted(TransactionalCapacityRepairSignals),
                        'GeometryFocusSignals': list(
                            CapacityRepairFocusSignals
                        ),
                        'PlacementFingerprint': CandidateFingerprint,
                        'RepairVariant': (
                            PrefetchedSingletonRepairVariantByPlacementFingerprint.get(
                                CandidateFingerprint,
                                RepairVariant,
                            )
                        ),
                        'PortfolioSequence': RepairVariant,
                        'PrefetchedSingletonSignal': (
                            PrefetchedSingletonSignalByPlacementFingerprint.get(
                                CandidateFingerprint,
                                '',
                            )
                        ),
                        'RepairClusterCount': 2,
                        'CumulativeDescendant': bool(
                            ComposeTransactionalDescendant
                            and RepairVariant > TransactionalAttempt
                        ),
                        'GeometryFocusGenerationFingerprint': (
                            CapacityRepairGenerationProofFingerprint
                        ),
                        'ElapsedSeconds': round(
                            Context.Services.monotonic()
                            - Context.Deadline.StartedAt,
                            6,
                        ),
                    })
                Context.InterfaceCandidateQueue[0:0] = QueueEntries
                return True
    CapacityRepairConnectivityClusters = ()
    if CapacityRepairActive:
        SourceConnectivityClusters = tuple(
            tuple(map(str, Cluster))
            for Cluster in SourceCandidate.Placement.Clusters
        )
        if (
            CapacityRepairConstraint.ProofKind
            == 'composed-complete-capacity-core'
            and CapacityRepairGeometryConstraint is not None
            and frozenset(CapacityRepairFocusSignals)
            == frozenset(CapacityRepairGeometryConstraint.Signals)
            and len(CapacityRepairGeometryConstraint.ComponentGateNames) > 1
        ):
            MergedGateNames = frozenset(
                CapacityRepairGeometryConstraint.ComponentGateNames
            )
            CapacityRepairConnectivityClusters = tuple(
                RetainedCluster
                for Cluster in SourceConnectivityClusters
                if (
                    RetainedCluster := tuple(
                        Name for Name in Cluster
                        if Name not in MergedGateNames
                    )
                )
            ) + (tuple(sorted(MergedGateNames)),)
        else:
            CapacityRepairConnectivityClusters = SourceConnectivityClusters
    if CapacityRepairActive:
        Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'interface-repair-epoch-started', 'SourceCandidateId': SourceCandidate.CandidateId, 'SourceProofFingerprint': CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(CapacityRepairConstraint.Signals), 'GeometryFocusSignals': list(CapacityRepairFocusSignals), 'GeometryFocusProofFingerprint': CapacityRepairGeometryFocusProofFingerprint, 'GeometryFocusGenerationFingerprint': CapacityRepairGenerationProofFingerprint, 'ProofComplete': True, 'CoreSignalCount': len(CapacityRepairConstraint.Signals), 'RepairLevel': CapacityRepairConstraint.RepairLevel, 'ProofKind': CapacityRepairConstraint.ProofKind, 'ClusterIds': list(CapacityRepairConstraint.ClusterIds), 'BoundaryClasses': list(CapacityRepairConstraint.BoundaryClasses), 'RepairDomainFingerprint': CapacityRepairConstraint.RepairDomainFingerprint, 'ForcedSeamClasses': [list(Value) for Value in CapacityRepairConstraint.ForcedSeamClasses], 'PreemptedCandidateIds': [], 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
    PressureGuidance = bool(Diagnostics.get('PlacementWorkSliceExpired', False) and PlacementPressureSignals)
    UnderlyingFailure = Diagnostics.get('UnderlyingFailure', {})
    UnderlyingDiagnostics = UnderlyingFailure.get('Diagnostics', {}) if isinstance(UnderlyingFailure, dict) else {}
    UnderlyingConflictGraph = UnderlyingDiagnostics.get('ConflictGraph', {}) if isinstance(UnderlyingDiagnostics, dict) else {}
    for Edge in (*(UnderlyingDiagnostics.get('PairwisePortReservationNoGoodEdges', ()) if isinstance(UnderlyingDiagnostics, dict) else ()), *(UnderlyingConflictGraph.get('PairwiseIncompatibleEdges', ()) if isinstance(UnderlyingConflictGraph, dict) else ())):
        if isinstance(Edge, (tuple, list)) and len(Edge) == 2:
            OrderedEdge = tuple(sorted(map(str, Edge)))
            if OrderedEdge[0] and OrderedEdge[0] != OrderedEdge[1]:
                Context.ProofGuidedPlacementPressureEdges.add(OrderedEdge)
    if PhysicalPlacementFeedback is None and (PressureGuidance or SymbolicCapacityFeedback):
        PhysicalPlacementFeedback = PhysicalComponentPlacementFeedback(ProofFingerprint=BuildStableFingerprint(('physical-component-interface-pressure-v1', SourceCandidate.PlacementFingerprint, PlacementPressureSignals)), RelocationSignals=PlacementPressureSignals, DomainFingerprint=str(Diagnostics.get('DomainFingerprint', '')))
    if not CompleteGlobalAssignmentCut and PhysicalPlacementFeedback is None:
        return False
    AssignmentCut = RoutingAssignmentCut.FromFailure(Failure, SourceCandidateId=SourceCandidate.CandidateId, MandatoryAccessOwnershipFingerprint=SourceCandidate.TopologyDemand.MandatoryAccessOwnershipFingerprint if SourceCandidate.TopologyDemand is not None else '') if CompleteGlobalAssignmentCut else None
    if CompleteGlobalAssignmentCut and AssignmentCut is None and (CapacityRepairConstraint is None):
        return False
    RelocationSignals = frozenset(AssignmentCut.PriorityRelocationSignals or AssignmentCut.RelocationSignals or AssignmentCut.ConflictSignals or AssignmentCut.NoCandidateSignals if AssignmentCut is not None else PhysicalPlacementFeedback.RelocationSignals)
    if PlacementPressureSignals:
        RelocationSignals = frozenset(PlacementPressureSignals)
    if CapacityRepairConstraint is not None:
        RelocationSignals = CapacityRepairGenerationSignals
    InheritedOwnedFrontierTopologyRepairSignals = frozenset(
        getattr(
            Context,
            "OwnedFrontierTopologyRepairSignalsByPlacementFingerprint",
            {},
        ).get(
            SourceCandidate.PlacementFingerprint,
            (),
        )
    )
    if (
        InheritedOwnedFrontierTopologyRepairSignals
        and CapacityRepairConstraint is None
    ):
        RelocationSignals = frozenset((
            *RelocationSignals,
            *InheritedOwnedFrontierTopologyRepairSignals,
        ))
    RoutabilityCore = Diagnostics.get('ComponentRoutabilityCore', {})
    CompleteRoutabilityFeedback = bool(
        isinstance(RoutabilityCore, Mapping)
        and RoutabilityCore.get('Complete', False)
    )
    ExactRoutabilityCoreSignals = RelocationSignals
    if not RelocationSignals:
        return False
    SourceRelocationGeometryFingerprint = (
        BuildCapacityRepairGeometryFingerprint(
            SourceCandidate,
            RelocationSignals,
        )
    )
    RelocationCore = tuple(sorted(RelocationSignals))
    PlacementGenerationProofCore = tuple(sorted(
        ExactRoutabilityCoreSignals or RelocationSignals
    ))
    PriorRelocationCoreCount = Context.ProofGuidedRelocationCoreCounts.get(RelocationCore, 0)
    Context.ProofGuidedRelocationCoreCounts[RelocationCore] = PriorRelocationCoreCount + 1
    RepeatedPlacementLocalCore = bool(PriorRelocationCoreCount > 0)
    ImmediatePhysicalGeometryFeedback = bool(PressureGuidance or SymbolicCapacityFeedback or CompleteRoutabilityFeedback or RepeatedPlacementLocalCore or (SourceCandidate.PlacementFingerprint in Context.GeneratedProofGuidedPlacementFingerprints))
    ImmediateRelocationVariant = 12 + (Context.ProofGuidedPlacementGenerationCount + int(RepeatedPlacementLocalCore)) % 4 if ImmediatePhysicalGeometryFeedback else None
    ImmediateRoutingSpacing = Context.ConfiguredRoutingSpacing + 1 if ImmediatePhysicalGeometryFeedback else None
    if ImmediatePhysicalGeometryFeedback:
        FocusedEdge = tuple(sorted(RelocationSignals)) if len(RelocationSignals) == 2 else None
        if FocusedEdge is not None and FocusedEdge in Context.AppliedProofGuidedPlacementPressureEdges:
            UntriedPressureEdges = tuple((Edge for Edge in Context.ProofGuidedPlacementPressureEdges if Edge not in Context.AppliedProofGuidedPlacementPressureEdges))
            if UntriedPressureEdges:
                FocusedEdge = min(UntriedPressureEdges, key=lambda Edge: (BuildStableFingerprint(('component-interface-pressure-edge-v1', Edge)), Edge))
                RelocationSignals = frozenset(FocusedEdge)
        if FocusedEdge is not None:
            Context.AppliedProofGuidedPlacementPressureEdges.add(FocusedEdge)
        RelocationSignals = AccumulateProofGuidedRelocationSignals(
            Context.CumulativeProofGuidedRelocationSignals,
            RelocationSignals,
            Reset=PressureGuidance or CapacityRepairActive,
        )
    QueuedGeneratedProofBlockers = (
        QueuedPhysicalEligibilityPlacementFingerprints(
            Context.InterfaceCandidateQueue
        )
        & Context.GeneratedProofGuidedPlacementFingerprints
    )
    CompletedProofGuidedPlacementFingerprints = frozenset(
        str(Attempt.get('PlacementFingerprint', ''))
        for Attempt in Context.InterfaceAttemptDiagnostics
        if str(Attempt.get('PlacementFingerprint', ''))
    )
    QueuedGeneratedProofBlockers -= CompletedProofGuidedPlacementFingerprints
    if CapacityRepairConstraint is not None:
        QueuedGeneratedProofBlockers = frozenset(
            PlacementFingerprint
            for PlacementFingerprint in QueuedGeneratedProofBlockers
            if (
                Context.CapacityRepairConstraintByPlacementFingerprint.get(
                    PlacementFingerprint
                )
                is not None
                and Context.CapacityRepairConstraintByPlacementFingerprint[
                    PlacementFingerprint
                ].SourceProofFingerprint
                == CapacityRepairConstraint.SourceProofFingerprint
            )
        )
    if (AssignmentCut is not None or QueuedGeneratedProofBlockers) and (not InheritedCapacityRepairRefinement) and HasDistinctRetainedPhysicalEligibilityState(Context.InterfaceCandidateQueue, ComponentVariant=ComponentVariant, PlacementFingerprint=SourceCandidate.PlacementFingerprint):
        ExistingPendingProofGuidedPlacement = Context.PendingProofGuidedPlacementByComponentVariant.get(ComponentVariant)
        if (
            ExistingPendingProofGuidedPlacement is not None
            and ExistingPendingProofGuidedPlacement[4]
            and not FreshCapacityRepairRefinement
        ):
            Context.PlacementGenerationDecisions.append({
                'Result': 'proof-guided-pending-capacity-repair-preserved',
                'SourceCandidateId': SourceCandidate.CandidateId,
                'PendingSourceCandidateId': (
                    ExistingPendingProofGuidedPlacement[1].CandidateId
                ),
                'ComponentVariant': ComponentVariant,
                'RelocationSignals': sorted(RelocationSignals),
                'IncomingCapacityRepair': CapacityRepairConstraint is not None,
                'ExecutableLegacyRepairCascade': False,
            })
            return True
        Context.PendingProofGuidedPlacementByComponentVariant[ComponentVariant] = (Failure, SourceCandidate, AssignmentCut is not None, QueuedGeneratedProofBlockers, CapacityRepairConstraint is not None)
        Context.PlacementGenerationDecisions.append({'Result': 'physical-global-proof-guided-placement-deferred-for-retained-state', 'SourceCandidateId': SourceCandidate.CandidateId, 'ComponentVariant': ComponentVariant, 'RelocationSignals': sorted(RelocationSignals), 'ExecutableLegacyRepairCascade': False})
        return True
    KnownFingerprints = {Candidate.PlacementFingerprint for Candidate in Context.RawInterfaceCandidates} | Context.ProofGuidedPlacementFingerprints
    RetainedCandidate = None if ImmediatePhysicalGeometryFeedback or Context.ProofGuidedRetainedPlacementCount >= Context.MaximumProofGuidedRetainedPlacements else SelectRetainedPhysicalPlacementForAccessCore(Context.OrderedPlacements, KnownFingerprints, RelocationSignals)
    if RetainedCandidate is not None:
        InheritedTopologyRepairKind = InheritOwnedFrontierTopologyRepairKind(
            Context,
            SourceCandidate.PlacementFingerprint,
            RetainedCandidate.PlacementFingerprint,
        )
        Context.ProofGuidedPlacementFingerprints.add(RetainedCandidate.PlacementFingerprint)
        Context.ProofGuidedRetainedPlacementCount += 1
        Context.RequestedComponentStateFingerprints.add(BuildClusterInterfaceComponentStateFingerprint(RetainedCandidate.PlacementFingerprint, ComponentVariant))
        Context.InterfaceCandidateQueue.insert(0, ('prepare-eligibility', len(Context.RawInterfaceCandidates) + Context.ProofGuidedRetainedPlacementCount, RetainedCandidate, 0, ComponentVariant))
        Context.PlacementGenerationDecisions.append({'Result': 'physical-global-proof-guided-retained-placement', 'SourceCandidateId': SourceCandidate.CandidateId, 'ProofKind': 'global-assignment-cut' if AssignmentCut is not None else 'physical-port-unsat-core', 'RelocationSignals': sorted(RelocationSignals), 'PlacementFingerprint': RetainedCandidate.PlacementFingerprint, 'GeneratedNewGeometry': False, 'InheritedOwnedFrontierTopologyRepairKind': InheritedTopologyRepairKind, 'ExecutableLegacyRepairCascade': False})
        return True
    PhysicalProofFingerprint = CapacityRepairGenerationProofFingerprint
    MaximumPlacementsForRelocationCore = Context.MaximumProofGuidedSymbolicCapacityPairPlacements if CapacityRepairActive else Context.MaximumProofGuidedGeneratedPlacements
    CapacityRepairGenerationCount = (
        Context.CapacityRepairGeneratedCountByProofFingerprint.get(
            CapacityRepairGenerationProofFingerprint,
            0,
        )
        if CapacityRepairConstraint is not None
        else 0
    )
    PlacementGenerationLimitReached = (
        CapacityRepairGenerationCount
        >= MaximumPlacementsForRelocationCore
        if CapacityRepairActive
        else Context.ProofGuidedPlacementGenerationCountByCore.get(
            PlacementGenerationProofCore,
            0,
        ) >= MaximumPlacementsForRelocationCore
    )
    CurrentProofPendingStateExists = any(
        PendingJointPlacementStateMatchesPhysicalProof(
            State,
            PlacementGenerationProofCore,
            PhysicalProofFingerprint,
            AssignmentCut,
            Context.PlacementAssignmentConstraints,
        )
        for State in Context.PendingJointPlacementStates
    )
    if PlacementGenerationLimitReached and not CurrentProofPendingStateExists:
        return False
    if PlacementGenerationLimitReached:
        Context.PlacementGenerationDecisions.append({
            'Result': 'capacity-repair-generation-limit-retained-sibling-admitted',
            'PlacementGenerationProofCoreSignals': list(
                PlacementGenerationProofCore
            ),
            'PhysicalProofFingerprint': PhysicalProofFingerprint,
            'PendingImmutablePlacementStateCount': len(
                Context.PendingJointPlacementStates
            ),
        })
    if AssignmentCut is not None:
        Context.CurrentPlacementAssignmentCut = AssignmentCut
        Context.PlacementAssignmentCutHistory.append(AssignmentCut)
        Context.PlacementAssignmentConstraints = Context.PlacementAssignmentConstraints.WithCut(AssignmentCut)
        Context.PendingTopologyCutEpoch = BuildTopologyCutEpochIdentity(AssignmentCut, Context.PlacementAssignmentConstraints)
    else:
        Context.CurrentPlacementAssignmentCut = None
        Context.PendingTopologyCutEpoch = None
    CapacityRepairGeometryKind = ''
    CapacityRepairRelocationVariant = ImmediateRelocationVariant
    CapacityRepairRoutingSpacing = ImmediateRoutingSpacing
    InheritedTopologyRepairKind = (
        Context.OwnedFrontierTopologyRepairKindByPlacementFingerprint.get(
            SourceCandidate.PlacementFingerprint,
            "",
        )
    )
    ProofGuidedJointPlacementCandidateIndex = (
        SelectInheritedTopologyJointPlacementCandidateIndex(
            InheritedTopologyRepairKind,
            ComposedSignalCount=len(RelocationSignals),
        )
    )
    if CapacityRepairActive:
        RepairAttempt = Context.CapacityRepairGeneratedCountByProofFingerprint.get(CapacityRepairGenerationProofFingerprint, 0)
        PreferSplitFirst = CapacityRepairConstraint.ProofKind.startswith('composed-')
        UseWidenRepair = RepairAttempt == (1 if PreferSplitFirst else 0)
        CapacityRepairGeometryKind = ('widen-channel-deck' if CapacityRepairConstraint.RepairLevel == 'channel-capacity' else 'widen-interface') if UseWidenRepair else 'split-channel-endpoints' if CapacityRepairConstraint.RepairLevel == 'channel-capacity' else 'split-relocate'
        CapacityRepairFallbackGeometryKind = ('split-channel-endpoints' if CapacityRepairConstraint.RepairLevel == 'channel-capacity' else 'split-relocate') if UseWidenRepair else 'widen-channel-deck' if CapacityRepairConstraint.RepairLevel == 'channel-capacity' else 'widen-interface'
        CapacityRepairRelocationVariant = None if CapacityRepairGeometryKind.startswith('widen-') else ImmediateRelocationVariant
        CapacityRepairRoutingSpacing = Context.ConfiguredRoutingSpacing + (1 if CapacityRepairGeometryKind.startswith('widen-') else 2)
    Context.PlacementRelocationSignals = RelocationSignals
    Context.PlacementRelocationPrioritySignals = frozenset(
        CapacityRepairFocusSignals
    ) or RelocationSignals
    Context.PlacementRequiredRelocationSignals = RelocationSignals
    Context.NeedsFeedbackPlacementGeneration = True
    Context.NeedsCurrentStructuredCutRegeneration = AssignmentCut is not None
    Context.JointPortfolioPrimaryCandidateId = None
    PriorPendingJointPlacementStateCount = len(
        Context.PendingJointPlacementStates
    )
    Context.PendingJointPlacementStates[:] = [
        State
        for State in Context.PendingJointPlacementStates
        if PendingJointPlacementStateMatchesPhysicalProof(
            State,
            PlacementGenerationProofCore,
            PhysicalProofFingerprint,
            AssignmentCut,
            Context.PlacementAssignmentConstraints,
        )
    ]
    RetainedPendingJointPlacementStateCount = len(
        Context.PendingJointPlacementStates
    )
    if PriorPendingJointPlacementStateCount:
        Context.PlacementGenerationDecisions.append({
            'Result': 'proof-guided-pending-sibling-filtered',
            'PlacementGenerationProofCoreSignals': list(
                PlacementGenerationProofCore
            ),
            'PriorPendingStateCount': (
                PriorPendingJointPlacementStateCount
            ),
            'RetainedPendingStateCount': (
                RetainedPendingJointPlacementStateCount
            ),
            'PrunedStalePendingStateCount': (
                PriorPendingJointPlacementStateCount
                - RetainedPendingJointPlacementStateCount
            ),
        })
    AttemptedPlacementFingerprints = {
        str(Attempt.get('PlacementFingerprint', ''))
        for Attempt in Context.InterfaceAttemptDiagnostics
        if str(Attempt.get('PlacementFingerprint', ''))
    }
    ExistingPlacementFingerprints = (
        {Candidate.PlacementFingerprint for Candidate in Context.OrderedPlacements}
        | Context.ProofGuidedPlacementFingerprints
        | AttemptedPlacementFingerprints
    )
    PlacementFingerprintsBeforeGeneration = frozenset(
        Context.UniquePlacements
    )
    Request = None if Context.PendingJointPlacementStates else _TakeNextDeferredRequest(Context, PreferRelocation=True, RequireExactCutBeforeBroad=AssignmentCut is not None, AllowCapacityPairRepair=CapacityRepairActive)
    if not Context.PendingJointPlacementStates and (CapacityRepairActive or ImmediatePhysicalGeometryFeedback):
        Request = PlacementGenerationRequest(SourceGenerator='row-beam-conflict-relocation', RoutingSpacing=Context.ConfiguredRoutingSpacing, PackingPolicy=replace(Context.Policy.NandPacking, GraphBeamEnabled=False, EnableJointClusterOrientation=True))
    GeneratedUniquePlacement = False
    while Request is not None:
        try:
            GeneratedUniquePlacement = _TryPlacement(Context, Request, JointPlacementCandidateIndex=ProofGuidedJointPlacementCandidateIndex, FixedRelocationVariant=CapacityRepairRelocationVariant, FixedCandidateSpacing=CapacityRepairRoutingSpacing, FixedPhysicalProofCoreSignals=frozenset(PlacementGenerationProofCore), FixedPhysicalProofFingerprint=PhysicalProofFingerprint, FixedConnectivityClusters=CapacityRepairConnectivityClusters, MaterializeRoutingResources=False, SkipMandatoryAccessPreScreen=True, PlacementGenerationNotAfter=Context.AccessRepairInterfacePlanningDeadline.ExpiresAt if CapacityRepairActive or ImmediatePhysicalGeometryFeedback else Context.SharedInterfacePlanningDeadline.ExpiresAt, UseCompletePlacementGenerationBudget=True, AllowCapacityPairRepair=CapacityRepairActive)
        except RoutingStageError as Error:
            Context.LastRoutingError = Error
            Context.LastStructuredRoutingError = Error
            if Error.Failure.Reason == RoutingFailureReason.Stagnated and 'AdvancePlacementGenerator' in Error.Failure.RepairActions:
                Request = _TakeNextDeferredRequest(Context, PreferRelocation=True, RequireExactCutBeforeBroad=AssignmentCut is not None)
                continue
            return False
        break
    while True:
        Candidate = SelectFreshProofGuidedPlacementCandidate(
            _BuildCandidateRecords(Context),
            ExistingPlacementFingerprints,
            PlacementFingerprintsBeforeGeneration,
            RequireCurrentGeneration=CapacityRepairActive,
        )
        if (
            Candidate is not None
            and Candidate.PlacementFingerprint
            in PlacementFingerprintsBeforeGeneration
            and Context.PendingJointPlacementStates
        ):
            Context.PlacementGenerationDecisions.append({
                'Result': (
                    'proof-guided-backlog-deferred-for-retained-sibling'
                ),
                'PlacementGenerationProofCoreSignals': list(
                    PlacementGenerationProofCore
                ),
                'DeferredPlacementFingerprint': (
                    Candidate.PlacementFingerprint
                ),
                'RetainedPendingStateCount': len(
                    Context.PendingJointPlacementStates
                ),
            })
            Candidate = None
        if Candidate is not None:
            CandidateRelocationGeometryFingerprint = (
                BuildCapacityRepairGeometryFingerprint(
                    Candidate,
                    RelocationSignals,
                )
            )
            if (
                CandidateRelocationGeometryFingerprint
                == SourceRelocationGeometryFingerprint
            ):
                if CapacityRepairConstraint is not None:
                    Context.PlacementGenerationDecisions.append({'Result': 'capacity-repair-equivalent-geometry-rejected', 'SourceCandidateId': SourceCandidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'CapacityRepairConstraint': CapacityRepairConstraint.ToDictionary()})
                    Context.CapacityRepairEquivalentGeometryRejectsByProofFingerprint[CapacityRepairGenerationProofFingerprint] = Context.CapacityRepairEquivalentGeometryRejectsByProofFingerprint.get(CapacityRepairGenerationProofFingerprint, 0) + 1
                else:
                    Context.PlacementGenerationDecisions.append({'Result': 'proof-guided-access-geometry-equivalent-rejected', 'SourceCandidateId': SourceCandidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'RelocationSignals': sorted(RelocationSignals), 'SourceGeometryFingerprint': SourceRelocationGeometryFingerprint, 'CandidateGeometryFingerprint': CandidateRelocationGeometryFingerprint})
                ExistingPlacementFingerprints.add(Candidate.PlacementFingerprint)
                continue
            InheritedTopologyRepairKind = InheritOwnedFrontierTopologyRepairKind(
                Context,
                SourceCandidate.PlacementFingerprint,
                Candidate.PlacementFingerprint,
            )
            SelectedFreshProofGeneration = (
                Candidate.PlacementFingerprint
                not in PlacementFingerprintsBeforeGeneration
            )
            Context.ProofGuidedPlacementFingerprints.add(Candidate.PlacementFingerprint)
            if SelectedFreshProofGeneration:
                Context.GeneratedProofGuidedPlacementFingerprints.add(Candidate.PlacementFingerprint)
            if CapacityRepairConstraint is not None:
                Context.CapacityRepairConstraintByPlacementFingerprint[Candidate.PlacementFingerprint] = CapacityRepairConstraint
                Context.CapacityRepairGeometryConstraintByPlacementFingerprint[Candidate.PlacementFingerprint] = CapacityRepairGeometryConstraint
                Context.CapacityRepairGeometryFocusByPlacementFingerprint[Candidate.PlacementFingerprint] = CapacityRepairGeometryFocus
                Context.CapacityRepairCandidateByPlacementFingerprint[Candidate.PlacementFingerprint] = Candidate
                Context.CapacityRepairGeometryKindByPlacementFingerprint[Candidate.PlacementFingerprint] = CapacityRepairGeometryKind
                Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-pair-repair-generated', 'SourceCandidateId': SourceCandidate.CandidateId, 'SourceProofFingerprint': CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(CapacityRepairConstraint.Signals), 'GeometryFocusSignals': list(CapacityRepairFocusSignals), 'GeometryFocusProofFingerprint': CapacityRepairGeometryFocusProofFingerprint, 'GeometryFocusGenerationFingerprint': CapacityRepairGenerationProofFingerprint, 'ProofComplete': True, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'GeometryFingerprint': CandidateRelocationGeometryFingerprint, 'GeometryKind': CapacityRepairGeometryKind, 'CoreSignalCount': len(CapacityRepairConstraint.Signals), 'RepairDomainFingerprint': CapacityRepairConstraint.RepairDomainFingerprint, 'EquivalentGeometryRejectCount': Context.CapacityRepairEquivalentGeometryRejectsByProofFingerprint.get(CapacityRepairGenerationProofFingerprint, 0), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
            Context.ProofGuidedPlacementGenerationCount += 1
            if CapacityRepairConstraint is not None:
                Context.CapacityRepairGeneratedCountByProofFingerprint[CapacityRepairGenerationProofFingerprint] = Context.CapacityRepairGeneratedCountByProofFingerprint.get(CapacityRepairGenerationProofFingerprint, 0) + 1
            Context.ProofGuidedPlacementGenerationCountByCore[PlacementGenerationProofCore] = Context.ProofGuidedPlacementGenerationCountByCore.get(PlacementGenerationProofCore, 0) + 1
            Context.RequestedComponentStateFingerprints.add(BuildClusterInterfaceComponentStateFingerprint(Candidate.PlacementFingerprint, ComponentVariant))
            Context.ProofGuidedGenerationSourceByPlacementFingerprint[Candidate.PlacementFingerprint] = (Failure, SourceCandidate, ComponentVariant)
            Context.InterfaceCandidateQueue.insert(0, ('prepare-eligibility', len(Context.InterfaceCandidates) + Context.ProofGuidedPlacementGenerationCount, Candidate, 0, ComponentVariant))
            Context.PlacementGenerationDecisions.append({'Result': 'physical-global-proof-guided-placement', 'SourceCandidateId': SourceCandidate.CandidateId, 'SourcePlanFingerprint': Diagnostics.get('PhysicalAssemblyPlanFingerprint', ''), 'ProofKind': 'global-assignment-cut' if AssignmentCut is not None else 'physical-port-unsat-core', 'ProofFingerprint': AssignmentCut.ConflictFingerprint if AssignmentCut is not None else PhysicalPlacementFeedback.ProofFingerprint, 'CapacityRepairConstraint': CapacityRepairConstraint.ToDictionary() if CapacityRepairConstraint is not None else None, 'ExactRoutabilityCoreSignals': sorted(ExactRoutabilityCoreSignals), 'PlacementGenerationProofCoreSignals': list(PlacementGenerationProofCore), 'ComposedPriorProofSignals': sorted(RelocationSignals - ExactRoutabilityCoreSignals), 'RelocationSignals': sorted(RelocationSignals), 'RelocationVariant': CapacityRepairRelocationVariant, 'JointPlacementCandidateIndex': ProofGuidedJointPlacementCandidateIndex, 'ImmediatePhysicalGeometryFeedback': ImmediatePhysicalGeometryFeedback, 'RoutingSpacing': CapacityRepairRoutingSpacing if CapacityRepairRoutingSpacing is not None else Context.ConfiguredRoutingSpacing, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'SelectedFreshProofGeneration': SelectedFreshProofGeneration, 'InheritedOwnedFrontierTopologyRepairKind': InheritedTopologyRepairKind, 'LivePlacementStateBound': MaximumPlacementsForRelocationCore, 'PendingImmutablePlacementStateCount': len(Context.PendingJointPlacementStates), 'IncrementalPlacementMaterialization': True, 'ExecutableLegacyRepairCascade': False, 'CapacityRepairGeometryKind': CapacityRepairGeometryKind})
            if CapacityRepairActive and RepairAttempt == 0:
                EnqueueProofGuidedPhysicalPlacement(Context, Failure, SourceCandidate, ComponentVariant)
                Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-repair-portfolio-prefetched', 'SourceProofFingerprint': CapacityRepairConstraint.SourceProofFingerprint, 'GeometryKinds': [CapacityRepairGeometryKind, CapacityRepairFallbackGeometryKind], 'PreferSplitFirst': PreferSplitFirst, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
            return True
        if not Context.PendingJointPlacementStates:
            if CapacityRepairConstraint is not None:
                Context.CapacityRepairGeneratedCountByProofFingerprint[
                    CapacityRepairGenerationProofFingerprint
                ] = max(
                    RepairAttempt + 1,
                    Context.CapacityRepairGeneratedCountByProofFingerprint.get(
                        CapacityRepairGenerationProofFingerprint,
                        0,
                    ),
                )
                Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-repair-geometry-portfolio-exhausted', 'SourceCandidateId': SourceCandidate.CandidateId, 'SourceProofFingerprint': CapacityRepairConstraint.SourceProofFingerprint, 'GeometryKind': CapacityRepairGeometryKind, 'Signals': list(CapacityRepairConstraint.Signals), 'RetainedStateCount': RetainedPendingJointPlacementStateCount, 'GeneratedFreshPlacement': False, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
                if (
                    Context.CapacityRepairGeneratedCountByProofFingerprint[
                        CapacityRepairGenerationProofFingerprint
                    ] < MaximumPlacementsForRelocationCore
                    and not Context.Deadline.IsExpired()
                ):
                    return EnqueueProofGuidedPhysicalPlacement(
                        Context,
                        Failure,
                        SourceCandidate,
                        ComponentVariant,
                    )
                Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'bounded-proof-driven-repair-exhausted', 'SourceCandidateId': SourceCandidate.CandidateId, 'SourceProofFingerprint': CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(CapacityRepairConstraint.Signals), 'EquivalentGeometryFingerprint': CapacityRepairConstraint.EquivalentGeometryFingerprint, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
            return False
        JointState = Context.PendingJointPlacementStates.pop(0)
        try:
            GeneratedUniquePlacement = _TryPlacement(Context, JointState.Request, JointPlacementCandidateIndex=JointState.CandidateIndex, FixedRelocationVariant=JointState.RelocationVariant, FixedCandidateSpacing=JointState.RoutingSpacing, FixedRelocationSignals=JointState.RelocationSignals, FixedRelocationPrioritySignals=JointState.RelocationPrioritySignals, FixedRequiredRelocationSignals=JointState.RequiredRelocationSignals, FixedAssignmentCut=JointState.AssignmentCut, FixedAssignmentConstraints=JointState.AssignmentConstraints, FixedCoordinatedCandidateDiversificationSignals=JointState.CoordinatedCandidateDiversificationSignals, FixedTopologyCutFrontier=JointState.TopologyCutFrontier, FixedPhysicalProofCoreSignals=JointState.PhysicalProofCoreSignals, FixedPhysicalProofFingerprint=JointState.PhysicalProofFingerprint, FixedConnectivityClusters=JointState.FixedConnectivityClusters, MaterializeRoutingResources=False, SkipMandatoryAccessPreScreen=True, PlacementGenerationNotAfter=Context.AccessRepairInterfacePlanningDeadline.ExpiresAt, UseCompletePlacementGenerationBudget=True, AllowCapacityPairRepair=CapacityRepairActive)
        except RoutingStageError as Error:
            Context.LastRoutingError = Error
            Context.LastStructuredRoutingError = Error
            return False


def BuildTransactionalRepairRoutingPriority(
    Candidate: PcbPlacementCandidate,
    ExternalSignals: Iterable[str],
) -> tuple[int, int, tuple[str, ...], str]:
    """Prefer local ECOs that preserve top-level terminal geometry."""
    Diagnostics = dict(
        Candidate.Placement.Placed.LocalRouteDiagnostics or {}
    ).get('__TransactionalClusterEndpointRepair__', {})
    InvalidatedSignals = tuple(sorted(map(
        str,
        Diagnostics.get('InvalidatedSignals', ()),
    ))) if isinstance(Diagnostics, Mapping) else ()
    ExternalSignalSet = frozenset(map(str, ExternalSignals))
    return (
        len(ExternalSignalSet.intersection(InvalidatedSignals)),
        len(InvalidatedSignals),
        InvalidatedSignals,
        Candidate.PlacementFingerprint,
    )


def MeasureTransactionalRepairClusterFootprint(
    Candidate: PcbPlacementCandidate,
) -> tuple[int, int]:
    """Measure the exact cluster area invalidated by one local ECO."""
    Diagnostics = dict(
        Candidate.Placement.Placed.LocalRouteDiagnostics or {}
    ).get('__TransactionalClusterEndpointRepair__', {})
    Clusters = (
        Diagnostics.get('Clusters', {})
        if isinstance(Diagnostics, Mapping)
        else {}
    )
    if not isinstance(Clusters, Mapping) or not Clusters:
        return (1 << 30, 1 << 30)
    Areas: list[int] = []
    for ClusterDiagnostics in Clusters.values():
        if not isinstance(ClusterDiagnostics, Mapping):
            return (1 << 30, 1 << 30)
        Width = max(1, int(ClusterDiagnostics.get('FinalWidth', 1)))
        Depth = max(1, int(ClusterDiagnostics.get('FinalDepth', 1)))
        Areas.append(Width * Depth)
    return (sum(Areas), len(Areas))


def ShouldPreferLeastFootprintLocalRepair(
    CompleteSymbolicCapacityFailure: bool,
    ParentSignalLineage: Sequence[str],
    Signal: str,
) -> bool:
    """Prefer a small ECO on a signal transition and its first repeat."""
    if not CompleteSymbolicCapacityFailure or not ParentSignalLineage:
        return False
    Lineage = tuple(map(str, ParentSignalLineage))
    SignalName = str(Signal)
    return bool(
        SignalName != Lineage[-1]
        or (
            len(Lineage) >= 2
            and SignalName == Lineage[-1]
            and Lineage[-2] != Lineage[-1]
        )
    )


def SelectCapacityRepairGenerationSignals(
    Constraint: PhysicalInterfaceRepairCore | None,
    FocusSignals: Iterable[str],
) -> frozenset[str]:
    """Keep a composed proof complete while retaining its fresh-core focus."""
    Focus = frozenset(map(str, FocusSignals))
    if Constraint is None:
        return Focus
    ConstraintSignals = frozenset(map(str, Constraint.Signals))
    if Constraint.ProofKind.startswith('composed-'):
        return ConstraintSignals
    return Focus or ConstraintSignals


def SelectCapacityRepairTransactionalSignals(
    Constraint: PhysicalInterfaceRepairCore | None,
    FocusSignals: Iterable[str],
) -> frozenset[str]:
    """Admit a bounded complete local core to the transactional ECO path."""
    if Constraint is None or Constraint.RepairLevel != 'local-assembly':
        return frozenset()
    Focus = frozenset(map(str, FocusSignals))
    CompleteSignals = SelectCapacityRepairGenerationSignals(
        Constraint,
        Focus,
    )
    if not (
        len(Focus) == 2
        and Focus <= CompleteSignals
        and 2 <= len(CompleteSignals) <= 3
    ):
        return frozenset()
    return CompleteSignals


def SelectCapacityRepairTransactionalVariants(
    GeneratedCount: int,
    RepairSignals: Iterable[str],
) -> tuple[int, ...]:
    """Compose a bounded descendant only for a complete three-signal core."""
    Start = min(2, max(0, int(GeneratedCount)))
    if Start >= 2:
        return ()
    SignalCount = len(frozenset(map(str, RepairSignals)))
    Stop = 2 if SignalCount == 3 else Start + 1
    return tuple(range(Start, Stop))


def SelectCapacityRepairCumulativeSingletonPrefetchSignal(
    RepairSignals: Iterable[str],
    ExternalSignals: Iterable[str],
) -> str:
    """Select one unambiguous internal descendant of a three-signal core."""
    Signals = frozenset(map(str, RepairSignals))
    InternalSignals = tuple(sorted(
        Signals - frozenset(map(str, ExternalSignals))
    ))
    if len(Signals) != 3 or len(InternalSignals) != 1:
        return ''
    return InternalSignals[0]


def BuildSingletonLocalFactorRepairTransitionKey(
    Signal: str,
    RepairVariant: int,
    Diagnostics: Mapping[str, object],
) -> tuple[object, ...]:
    """Identify one exact singleton endpoint ECO transition."""
    SelectedClusters = tuple(sorted(map(
        int,
        Diagnostics.get('SelectedClusterIndices', ()),
    )))
    InvalidatedSignals = tuple(sorted(map(
        str,
        Diagnostics.get('InvalidatedSignals', ()),
    )))
    if not SelectedClusters or not InvalidatedSignals:
        return ()
    return (
        'singleton-local-factor-repair-transition-v1',
        str(Signal),
        int(RepairVariant),
        SelectedClusters,
        InvalidatedSignals,
    )


def SelectLearnedAdvancingSingletonRepairTransition(
    NextSignalByRepairKey: Mapping[tuple[object, ...], str],
) -> tuple[tuple[object, ...], str] | None:
    """Select one unambiguous proof-learned transition that changes signal."""
    Candidates = tuple(sorted((
        (
            RepairKey,
            str(NextSignal),
        )
        for RepairKey, NextSignal in NextSignalByRepairKey.items()
        if (
            len(RepairKey) == 5
            and RepairKey[0]
            == 'singleton-local-factor-repair-transition-v1'
            and str(NextSignal) not in {'', '<ambiguous>'}
            and str(RepairKey[1]) != str(NextSignal)
        )
    ), key=repr))
    if len(Candidates) != 1:
        return None
    return Candidates[0]


def SelectAlternateBinarySingletonRepairVariant(
    LearnedVariant: int,
) -> int | None:
    """Return the one unexplored sibling of a binary learned repair."""
    if LearnedVariant not in {0, 1}:
        return None
    return 1 - LearnedVariant


def SelectSingletonLocalFactorRepairVariants(
    AttemptCount: int,
    CompleteAccessFailure: bool,
    BeamWidth: int,
) -> tuple[int, ...]:
    """Expose the exact access beam only for a complete typed access proof."""
    VariantLimit = (
        max(2, min(16, max(1, int(BeamWidth))))
        if CompleteAccessFailure
        else 2
    )
    Start = min(VariantLimit, max(0, int(AttemptCount)))
    return tuple(range(Start, VariantLimit))


def SelectCompletePhysicalEligibilityRepairTerminalPositions(
    Failure: RoutingFailure,
    Signal: str,
) -> frozenset[tuple[int, int, int]]:
    """Recover only terminals from one complete projected empty-bank proof."""
    Diagnostics = (
        Failure.Diagnostics
        if isinstance(Failure.Diagnostics, Mapping)
        else {}
    )
    DiagnosticsBySignal = Diagnostics.get('DomainDiagnosticsBySignal', {})
    SignalDiagnostics = (
        DiagnosticsBySignal.get(str(Signal), {})
        if isinstance(DiagnosticsBySignal, Mapping)
        else {}
    )
    CandidateCounts = (
        SignalDiagnostics.get('CandidateCountByTerminal', ())
        if isinstance(SignalDiagnostics, Mapping)
        else ()
    )
    if not (
        Failure.Stage == 'PhysicalComponentEligibility'
        and Failure.Reason
        == RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
        and Diagnostics.get('Complete', False)
        and not Diagnostics.get('Feasible', True)
        and Diagnostics.get('ComponentFabricConstructionComplete', False)
        and Diagnostics.get('OwnershipSearchComplete', False)
        and SignalDiagnostics.get('Reason')
        == 'complete-certified-domain-empty-after-physical-projection'
        and isinstance(CandidateCounts, (tuple, list))
        and CandidateCounts
    ):
        return frozenset()
    Positions: set[tuple[int, int, int]] = set()
    for CandidateCount in CandidateCounts:
        if not isinstance(CandidateCount, Mapping):
            return frozenset()
        Terminal = CandidateCount.get('Terminal')
        if not (
            isinstance(Terminal, (tuple, list))
            and len(Terminal) == 3
            and all(
                isinstance(Coordinate, int)
                and not isinstance(Coordinate, bool)
                for Coordinate in Terminal
            )
        ):
            return frozenset()
        Positions.add(tuple(Terminal))
    return frozenset(Positions)


def SelectCompletePhysicalEligibilityRepairEndpointGateNames(
    Failure: RoutingFailure,
    Signal: str,
    Placement: object | None = None,
) -> frozenset[str]:
    """Map channelized empty-bank terminals to stable logical gate names."""
    Positions = SelectCompletePhysicalEligibilityRepairTerminalPositions(
        Failure,
        Signal,
    )
    if not Positions:
        return frozenset()
    Diagnostics = (
        Failure.Diagnostics
        if isinstance(Failure.Diagnostics, Mapping)
        else {}
    )
    NamesBySignal = Diagnostics.get('RepairEndpointGateNamesBySignal', {})
    AnnotatedNames = (
        NamesBySignal.get(str(Signal), ())
        if isinstance(NamesBySignal, Mapping)
        else ()
    )
    if (
        isinstance(AnnotatedNames, (tuple, list, set, frozenset))
        and AnnotatedNames
        and all(isinstance(Name, str) and Name for Name in AnnotatedNames)
    ):
        return frozenset(AnnotatedNames)
    Placed = getattr(Placement, 'Placed', None)
    Gates = getattr(Placed, 'PlacedGates', ()) if Placed is not None else ()
    SignalName = str(Signal)
    return frozenset(
        str(Gate.Name)
        for Gate in Gates
        if (
            (
                getattr(Gate, 'OutputPin', None) in Positions
                and SignalName in set(map(str, getattr(Gate, 'Outputs', ())))
            )
            or any(
                Pin in Positions and str(InputSignal) == SignalName
                for InputSignal, Pin in zip(
                    getattr(Gate, 'Inputs', ()),
                    getattr(Gate, 'InputPins', ()),
                )
            )
        )
    )


def SelectLocalFactorCandidateQueueInsertionIndex(
    Queue: Sequence[tuple[Any, ...]],
    SignalByPlacementFingerprint: Mapping[str, str],
    Signal: str,
) -> int:
    """Keep pending same-signal siblings ahead of new descendants."""
    InsertionIndex = 0
    for Entry in Queue:
        Candidate = Entry[2] if len(Entry) > 2 else None
        PlacementFingerprint = str(getattr(
            Candidate,
            'PlacementFingerprint',
            '',
        ))
        if (
            SignalByPlacementFingerprint.get(PlacementFingerprint)
            != str(Signal)
        ):
            break
        InsertionIndex += 1
    return InsertionIndex


def SelectLocalFactorCycleSiblingQueueInsertionIndex(
    Queue: Sequence[tuple[Any, ...]],
    SiblingGroupByPlacementFingerprint: Mapping[str, str],
    ParentSiblingGroup: str,
    ParentPortfolioIdentity: str = '',
) -> int:
    """Keep only the cycling child's immediate siblings ahead of descendants."""
    if not ParentSiblingGroup and not ParentPortfolioIdentity:
        return 0
    InsertionIndex = 0
    for Entry in Queue:
        Candidate = Entry[2] if len(Entry) > 2 else None
        PlacementFingerprint = str(getattr(
            Candidate,
            'PlacementFingerprint',
            '',
        ))
        SameRegisteredSiblingGroup = bool(
            ParentSiblingGroup
            and SiblingGroupByPlacementFingerprint.get(PlacementFingerprint)
            == ParentSiblingGroup
        )
        SamePersistentPortfolio = bool(
            ParentPortfolioIdentity
            and str(getattr(
                Candidate,
                'JointPortfolioIdentityFingerprint',
                '',
            )) == ParentPortfolioIdentity
        )
        if not SameRegisteredSiblingGroup and not SamePersistentPortfolio:
            break
        InsertionIndex += 1
    return InsertionIndex


def PrioritizeLocalFactorCycleSiblings(
    Queue: list[tuple[Any, ...]],
    SiblingGroupByPlacementFingerprint: Mapping[str, str],
    ParentSiblingGroup: str,
    ParentPortfolioIdentity: str = '',
) -> int:
    """Promote one exact sibling group after access-core queue rescoring."""
    if not ParentSiblingGroup and not ParentPortfolioIdentity:
        return 0
    Siblings: list[tuple[Any, ...]] = []
    Others: list[tuple[Any, ...]] = []
    for Entry in Queue:
        Candidate = Entry[2] if len(Entry) > 2 else None
        PlacementFingerprint = str(getattr(
            Candidate,
            'PlacementFingerprint',
            '',
        ))
        SameRegisteredSiblingGroup = bool(
            ParentSiblingGroup
            and SiblingGroupByPlacementFingerprint.get(PlacementFingerprint)
            == ParentSiblingGroup
        )
        SamePersistentPortfolio = bool(
            ParentPortfolioIdentity
            and str(getattr(
                Candidate,
                'JointPortfolioIdentityFingerprint',
                '',
            )) == ParentPortfolioIdentity
        )
        (Siblings if (
            SameRegisteredSiblingGroup or SamePersistentPortfolio
        ) else Others).append(Entry)
    Queue[:] = [*Siblings, *Others]
    return len(Siblings)


def SelectLocalFactorRepairSignalLineage(
    Candidate: PcbPlacementCandidate,
    CachedLineage: Sequence[str],
) -> tuple[str, ...]:
    """Recover singleton repair ancestry from persistent placement provenance."""
    Placement = getattr(Candidate, 'Placement', None)
    Placed = getattr(Placement, 'Placed', None)
    Diagnostics = dict(getattr(Placed, 'LocalRouteDiagnostics', {}) or {})
    Recipe = dict(Diagnostics.get('__PlacementRecipe__', {}) or {})
    RepairHistory = Recipe.get('TransactionalRepairSignalHistory', ())
    RecipeLineage = tuple(
        str(Signals[0])
        for Signals in RepairHistory
        if (
            isinstance(Signals, (tuple, list, set, frozenset))
            and len(Signals) == 1
            and str(next(iter(Signals), ''))
        )
    )
    Cached = tuple(map(str, CachedLineage))
    return Cached if len(Cached) >= len(RecipeLineage) else RecipeLineage


def IsLocalFactorRepairBackEdge(
    ParentSignalLineage: Sequence[str],
    Signal: str,
) -> bool:
    """Return whether a repair changed signal and returned to an ancestor."""
    Lineage = tuple(map(str, ParentSignalLineage))
    SignalName = str(Signal)
    return bool(
        Lineage
        and SignalName != Lineage[-1]
        and SignalName in Lineage[:-1]
    )


def ClassifyLearnedTransitionCandidatePriority(
    PlacementFingerprint: str,
    PrefetchedFingerprints: AbstractSet[str],
    ClosedTransitionFingerprints: AbstractSet[str],
) -> int:
    """Order advancing prefetches, fresh siblings, then proven cycles."""
    if PlacementFingerprint in PrefetchedFingerprints:
        return 0
    if PlacementFingerprint in ClosedTransitionFingerprints:
        return 2
    return 1


def ClassifyCompletePhysicalEligibilityCandidatePriority(
    PlacementFingerprint: str,
    RepairKeyByPlacementFingerprint: Mapping[
        str,
        tuple[object, ...],
    ],
) -> tuple[int, int]:
    """Try an access-distinct empty-bank repair before its default sibling."""
    RepairKey = RepairKeyByPlacementFingerprint.get(
        PlacementFingerprint,
        (),
    )
    RepairVariant = (
        int(RepairKey[2])
        if (
            len(RepairKey) == 5
            and RepairKey[0]
            == 'singleton-local-factor-repair-transition-v1'
        )
        else 0
    )
    return (0 if RepairVariant > 0 else 1, RepairVariant)


def EnqueueSingletonLocalFactorDiversification(Context, Failure: RoutingFailure, SourceCandidate: PcbPlacementCandidate, ComponentVariant: int) -> bool:
    """Publish at most two access-distinct local ECO factor domains."""
    Core = BuildPhysicalLocalFactorDiversificationCore(Failure, SourceCandidate)
    if Core is None or Context.Deadline.IsExpired():
        return False
    ParentRepairKey = (
        Context.LocalFactorDiversificationRepairKeyByPlacementFingerprint.get(
            SourceCandidate.PlacementFingerprint,
            (),
        )
    )
    if ParentRepairKey:
        PriorNextSignal = (
            Context.LocalFactorDiversificationNextSignalByRepairKey.get(
                ParentRepairKey,
                '',
            )
        )
        if not PriorNextSignal or PriorNextSignal == Core.Signal:
            Context.LocalFactorDiversificationNextSignalByRepairKey[
                ParentRepairKey
            ] = Core.Signal
        else:
            Context.LocalFactorDiversificationNextSignalByRepairKey[
                ParentRepairKey
            ] = '<ambiguous>'
    CompleteSymbolicCapacityFailure = bool(
        Failure.Stage == 'PhysicalSymbolicCapacityPlacementFeedback'
        and isinstance(Failure.Diagnostics, Mapping)
        and Failure.Diagnostics.get('SymbolicCapacityProofComplete', False)
        and Failure.Diagnostics.get('SymbolicCapacityProofFingerprint', '')
    )
    CompletePhysicalEligibilityFailure = bool(
        Failure.Stage == 'PhysicalComponentEligibility'
        and Failure.Reason
        == RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
        and isinstance(Failure.Diagnostics, Mapping)
        and Failure.Diagnostics.get('Complete', False)
        and not Failure.Diagnostics.get('Feasible', True)
        and Failure.Diagnostics.get(
            'ComponentFabricConstructionComplete',
            False,
        )
        and Failure.Diagnostics.get('OwnershipSearchComplete', False)
    )
    InheritedCapacityRepairConstraint = (
        Context.CapacityRepairConstraintByPlacementFingerprint.get(
            SourceCandidate.PlacementFingerprint
        )
    )
    InheritedCapacityRepairGeometryConstraint = (
        Context.CapacityRepairGeometryConstraintByPlacementFingerprint.get(
            SourceCandidate.PlacementFingerprint
        )
    )
    FreshCapacityRepairGeometryFocus = BuildPhysicalComponentPlacementFeedback(
        Failure
    )
    InheritedCapacityRepairGeometryFocus = (
        Context.CapacityRepairGeometryFocusByPlacementFingerprint.get(
            SourceCandidate.PlacementFingerprint
        )
    )

    def RegisterCandidate(
        Candidate: PcbPlacementCandidate,
        GeometryKind: str,
        RepairTransitionKey: tuple[object, ...] = (),
        ParentCandidate: PcbPlacementCandidate | None = None,
        RepairSignal: str = '',
    ) -> None:
        CandidateFingerprint = Candidate.PlacementFingerprint
        EffectiveParentCandidate = ParentCandidate or SourceCandidate
        EffectiveRepairSignal = str(RepairSignal or Core.Signal)
        ParentLineage = (
            Context
            .LocalFactorDiversificationSignalLineageByPlacementFingerprint
            .get(EffectiveParentCandidate.PlacementFingerprint, ())
        )
        Context.LocalFactorDiversificationCandidateByPlacementFingerprint[
            CandidateFingerprint
        ] = Candidate
        Context.LocalFactorDiversificationSignalByPlacementFingerprint[
            CandidateFingerprint
        ] = Core.Signal
        Context.LocalFactorDiversificationSignalLineageByPlacementFingerprint[
            CandidateFingerprint
        ] = (*ParentLineage, EffectiveRepairSignal)
        Context.LocalFactorDiversificationSiblingGroupByPlacementFingerprint[
            CandidateFingerprint
        ] = BuildStableFingerprint((
            'singleton-local-factor-sibling-group-v1',
            EffectiveParentCandidate.PlacementFingerprint,
            EffectiveRepairSignal,
            Core.SourceProofFingerprint,
        ))
        if RepairTransitionKey:
            Context.LocalFactorDiversificationRepairKeyByPlacementFingerprint[
                CandidateFingerprint
            ] = RepairTransitionKey
        if InheritedCapacityRepairConstraint is not None:
            Context.CapacityRepairConstraintByPlacementFingerprint[
                CandidateFingerprint
            ] = InheritedCapacityRepairConstraint
            Context.CapacityRepairGeometryConstraintByPlacementFingerprint[
                CandidateFingerprint
            ] = (
                InheritedCapacityRepairGeometryConstraint
                or InheritedCapacityRepairConstraint
            )
            Context.CapacityRepairGeometryFocusByPlacementFingerprint[
                CandidateFingerprint
            ] = (
                FreshCapacityRepairGeometryFocus
                or InheritedCapacityRepairGeometryFocus
                or PhysicalComponentPlacementFeedback(
                    ProofFingerprint=(
                        InheritedCapacityRepairConstraint
                        .SourceProofFingerprint
                    ),
                    RelocationSignals=(
                        InheritedCapacityRepairConstraint.Signals
                    ),
                    DomainFingerprint=(
                        InheritedCapacityRepairConstraint
                        .RepairDomainFingerprint
                    ),
                )
            )
            Context.CapacityRepairCandidateByPlacementFingerprint[
                CandidateFingerprint
            ] = Candidate
            Context.CapacityRepairGeometryKindByPlacementFingerprint[
                CandidateFingerprint
            ] = GeometryKind
        Context.RequestedComponentStateFingerprints.add(
            BuildClusterInterfaceComponentStateFingerprint(
                CandidateFingerprint,
                ComponentVariant,
            )
        )

    CompleteTypedAccessFailure = bool(
        Failure.Stage == 'ComponentAccessCertification'
        and Failure.Reason in {
            RoutingFailureReason.ComponentTerminalAccessUnsatisfiable,
            RoutingFailureReason.ComponentPerimeterSeamUnsatisfiable,
        }
        and isinstance(Failure.Diagnostics, Mapping)
        and Failure.Diagnostics.get('Complete', False)
    )
    CompleteAccessFailure = bool(
        CompleteTypedAccessFailure
        or CompleteSymbolicCapacityFailure
        or CompletePhysicalEligibilityFailure
    )
    AttemptCount = Context.LocalFactorDiversificationAttemptCountByProofFingerprint.get(Core.SourceProofFingerprint, 0)
    RepairVariants = SelectSingletonLocalFactorRepairVariants(
        AttemptCount,
        CompleteAccessFailure,
        Context.Policy.NandPacking.BeamWidth,
    )
    if not RepairVariants:
        Context.LocalFactorDiversificationPortfolioDiagnostics.append({'Result': 'singleton-local-factor-portfolio-exhausted', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
        return False
    ExistingFingerprints = frozenset(Context.UniquePlacements)
    PublishedCandidates: list[PcbPlacementCandidate] = []
    RepairTerminalPositions = (
        SelectCompletePhysicalEligibilityRepairTerminalPositions(
            Failure,
            Core.Signal,
        )
        if CompletePhysicalEligibilityFailure
        else frozenset()
    )
    RepairEndpointGateNames = (
        SelectCompletePhysicalEligibilityRepairEndpointGateNames(
            Failure,
            Core.Signal,
        )
        if CompletePhysicalEligibilityFailure
        else frozenset()
    )
    for Variant in RepairVariants:
        Context.LocalFactorDiversificationAttemptCountByProofFingerprint[Core.SourceProofFingerprint] = Variant + 1
        Published = _PublishTransactionalClusterEndpointRepair(Context, SourceCandidate, frozenset((Core.Signal,)), RepairVariant=Variant, RepairClusterCount=1, RepairTerminalPositions=RepairTerminalPositions, RepairEndpointGateNames=RepairEndpointGateNames, AllowStableMandatoryAccessOwnership=CompleteTypedAccessFailure or CompleteSymbolicCapacityFailure or CompletePhysicalEligibilityFailure)
        if not Published:
            Context.LocalFactorDiversificationPortfolioDiagnostics.append({'Result': 'singleton-local-factor-eco-rejected', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'Variant': Variant, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
            continue
        PublishedDecision = next((
            Decision
            for Decision in reversed(Context.PlacementGenerationDecisions)
            if (
                Decision.get('Result')
                == 'transactional-cluster-endpoint-repair-published'
                and Decision.get('SourceCandidateId')
                == SourceCandidate.CandidateId
                and int(Decision.get('RepairVariant', -1)) == Variant
            )
        ), {})
        RepairTransitionKey = BuildSingletonLocalFactorRepairTransitionKey(
            Core.Signal,
            Variant,
            dict(PublishedDecision.get('Diagnostics', {})),
        )
        try:
            CandidateRecords = _BuildCandidateRecords(Context)
        except RoutingStageError as Error:
            Context.LocalFactorDiversificationPortfolioDiagnostics.append({'Result': 'singleton-local-factor-eco-incomplete', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'Variant': Variant, 'Failure': Error.Failure.ToDictionary()})
            continue
        NewCandidates = tuple((Candidate for Candidate in CandidateRecords if Candidate.PlacementFingerprint not in ExistingFingerprints and Candidate.SourceGenerator == 'transactional-cluster-endpoint-repair'))
        for Candidate in NewCandidates:
            if len(PublishedCandidates) >= 2:
                break
            NewGeometryFingerprint = BuildCapacityRepairGeometryFingerprint(Candidate, (Core.Signal,))
            if NewGeometryFingerprint == Core.LocalGeometryFingerprint:
                Context.LocalFactorDiversificationPortfolioDiagnostics.append({'Result': 'singleton-local-factor-geometry-unchanged', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'Variant': Variant, 'PlacementFingerprint': Candidate.PlacementFingerprint})
                continue
            ExistingFingerprints = frozenset((*ExistingFingerprints, Candidate.PlacementFingerprint))
            RegisterCandidate(
                Candidate,
                'local-factor-diversification',
                RepairTransitionKey,
            )
            PublishedCandidates.append(Candidate)
            Context.LocalFactorDiversificationPortfolioDiagnostics.append({'Result': 'singleton-local-factor-eco-published', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'Variant': Variant, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'LocalGeometryFingerprint': NewGeometryFingerprint, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
        if len(PublishedCandidates) >= 2:
            break
    if not PublishedCandidates:
        return False
    CachedParentSignalLineage = (
        Context.LocalFactorDiversificationSignalLineageByPlacementFingerprint
        .get(SourceCandidate.PlacementFingerprint, ())
    )
    ParentSignalLineage = SelectLocalFactorRepairSignalLineage(
        SourceCandidate,
        CachedParentSignalLineage,
    )
    PreferLeastFootprintRepair = ShouldPreferLeastFootprintLocalRepair(
        CompleteSymbolicCapacityFailure,
        ParentSignalLineage,
        Core.Signal,
    )
    OrderedPublishedCandidates = sorted(
        PublishedCandidates,
        key=lambda Candidate: (
            MeasureTransactionalRepairClusterFootprint(Candidate)
            if PreferLeastFootprintRepair
            else (0, 0),
            BuildTransactionalRepairRoutingPriority(
                Candidate,
                (*Context.Module.Inputs, *Context.Module.Outputs),
            ),
        ),
    )
    LearnedTransitionSource = next((
        Candidate
        for Candidate in OrderedPublishedCandidates
        if (
            Context.LocalFactorDiversificationNextSignalByRepairKey.get(
                Context.LocalFactorDiversificationRepairKeyByPlacementFingerprint.get(
                    Candidate.PlacementFingerprint,
                    (),
                ),
                '',
            )
            not in {'', '<ambiguous>'}
        )
    ), None)
    ClosedLearnedTransitionFingerprints: set[str] = set()
    if CompleteSymbolicCapacityFailure and LearnedTransitionSource is not None:
        LearnedRepairKey = (
            Context.LocalFactorDiversificationRepairKeyByPlacementFingerprint[
                LearnedTransitionSource.PlacementFingerprint
            ]
        )
        LearnedNextSignal = (
            Context.LocalFactorDiversificationNextSignalByRepairKey[
                LearnedRepairKey
            ]
        )
        LearnedDescendantPublished = False
        for LearnedVariant in range(2):
            ExistingFingerprints = frozenset(Context.UniquePlacements)
            LearnedPublished = _PublishTransactionalClusterEndpointRepair(
                Context,
                LearnedTransitionSource,
                frozenset((LearnedNextSignal,)),
                RepairVariant=LearnedVariant,
                RepairClusterCount=1,
                AllowStableMandatoryAccessOwnership=True,
            )
            if not LearnedPublished:
                continue
            LearnedDecision = next((
                Decision
                for Decision in reversed(
                    Context.PlacementGenerationDecisions
                )
                if (
                    Decision.get('Result')
                    == 'transactional-cluster-endpoint-repair-published'
                    and Decision.get('SourceCandidateId')
                    == LearnedTransitionSource.CandidateId
                    and int(Decision.get('RepairVariant', -1))
                    == LearnedVariant
                )
            ), {})
            LearnedTransitionKey = (
                BuildSingletonLocalFactorRepairTransitionKey(
                    LearnedNextSignal,
                    LearnedVariant,
                    dict(LearnedDecision.get('Diagnostics', {})),
                )
            )
            try:
                CandidateRecords = _BuildCandidateRecords(Context)
            except RoutingStageError as Error:
                Context.LocalFactorDiversificationPortfolioDiagnostics.append({
                    'Result': 'learned-singleton-transition-prefetch-incomplete',
                    'SourceCandidateId': (
                        LearnedTransitionSource.CandidateId
                    ),
                    'LearnedRepairKey': list(LearnedRepairKey),
                    'LearnedNextSignal': LearnedNextSignal,
                    'Variant': LearnedVariant,
                    'Failure': Error.Failure.ToDictionary(),
                })
                continue
            LearnedCandidates = tuple(
                Candidate
                for Candidate in CandidateRecords
                if (
                    Candidate.PlacementFingerprint
                    not in ExistingFingerprints
                    and Candidate.SourceGenerator
                    == 'transactional-cluster-endpoint-repair'
                )
            )
            for Candidate in LearnedCandidates:
                LearnedDescendantPublished = True
                RegisterCandidate(
                    Candidate,
                    'learned-local-factor-transition-prefetch',
                    LearnedTransitionKey,
                    ParentCandidate=LearnedTransitionSource,
                    RepairSignal=LearnedNextSignal,
                )
                Context.LearnedLocalFactorTransitionPrefetchFingerprints.add(
                    Candidate.PlacementFingerprint
                )
                PublishedCandidates.append(Candidate)
                Context.LocalFactorDiversificationPortfolioDiagnostics.append({
                    'Result': 'learned-singleton-transition-prefetch-published',
                    'SourceCandidateId': (
                        LearnedTransitionSource.CandidateId
                    ),
                    'LearnedRepairKey': list(LearnedRepairKey),
                    'LearnedNextSignal': LearnedNextSignal,
                    'Variant': LearnedVariant,
                    'PlacementFingerprint': Candidate.PlacementFingerprint,
                    'ElapsedSeconds': round(
                        Context.Services.monotonic()
                        - Context.Deadline.StartedAt,
                        6,
                    ),
                })
        if not LearnedDescendantPublished:
            ClosedLearnedTransitionFingerprints.add(
                LearnedTransitionSource.PlacementFingerprint
            )
            Context.LocalFactorDiversificationPortfolioDiagnostics.append({
                'Result': 'learned-singleton-transition-closed',
                'SourceCandidateId': LearnedTransitionSource.CandidateId,
                'LearnedRepairKey': list(LearnedRepairKey),
                'LearnedNextSignal': LearnedNextSignal,
                'PlacementFingerprint': (
                    LearnedTransitionSource.PlacementFingerprint
                ),
                'ElapsedSeconds': round(
                    Context.Services.monotonic()
                    - Context.Deadline.StartedAt,
                    6,
                ),
            })
    PublishedCandidates.sort(key=lambda Candidate: (
        ClassifyCompletePhysicalEligibilityCandidatePriority(
            Candidate.PlacementFingerprint,
            Context.LocalFactorDiversificationRepairKeyByPlacementFingerprint,
        )
        if CompletePhysicalEligibilityFailure
        else (0, 0),
        ClassifyLearnedTransitionCandidatePriority(
            Candidate.PlacementFingerprint,
            Context.LearnedLocalFactorTransitionPrefetchFingerprints,
            ClosedLearnedTransitionFingerprints,
        ),
        (
            MeasureTransactionalRepairClusterFootprint(Candidate)
            if PreferLeastFootprintRepair
            else (0, 0)
        ),
        BuildTransactionalRepairRoutingPriority(
            Candidate,
            (*Context.Module.Inputs, *Context.Module.Outputs),
        )
    ))
    ParentSiblingGroup = (
        Context.LocalFactorDiversificationSiblingGroupByPlacementFingerprint
        .get(SourceCandidate.PlacementFingerprint, '')
    )
    CycleToPriorSignal = bool(
        CompleteSymbolicCapacityFailure
        and IsLocalFactorRepairBackEdge(
            ParentSignalLineage,
            Core.Signal,
        )
    )
    QueueInsertionIndex = (
        PrioritizeLocalFactorCycleSiblings(
            Context.InterfaceCandidateQueue,
            Context.LocalFactorDiversificationSiblingGroupByPlacementFingerprint,
            ParentSiblingGroup,
            str(SourceCandidate.JointPortfolioIdentityFingerprint),
        )
        if CycleToPriorSignal
        else
        SelectLocalFactorCandidateQueueInsertionIndex(
            Context.InterfaceCandidateQueue,
            Context.LocalFactorDiversificationSignalByPlacementFingerprint,
            Core.Signal,
        )
        if CompletePhysicalEligibilityFailure
        else 0
    )
    if CycleToPriorSignal:
        Context.LocalFactorDiversificationPortfolioDiagnostics.append({
            'Result': (
                'cycle-local-factor-sibling-prioritized'
                if QueueInsertionIndex
                else 'cycle-local-factor-sibling-missing'
            ),
            'SourceCandidateId': SourceCandidate.CandidateId,
            'Signal': Core.Signal,
            'ParentSignalLineage': list(ParentSignalLineage),
            'ParentSiblingGroup': ParentSiblingGroup,
            'PendingSiblingCount': QueueInsertionIndex,
            'ElapsedSeconds': round(
                Context.Services.monotonic() - Context.Deadline.StartedAt,
                6,
            ),
        })
    Context.InterfaceCandidateQueue[
        QueueInsertionIndex:QueueInsertionIndex
    ] = [('prepare-eligibility', len(Context.InterfaceCandidates) + Index, Candidate, 0, ComponentVariant) for Index, Candidate in enumerate(PublishedCandidates)]
    return True


def SelectTopologyEquivalentRepairSignals(
    Signals: Iterable[str],
    SignalIncidenceFingerprints: Mapping[str, str],
    MaximumSignals: int = 12,
) -> frozenset[str]:
    """Expand a repair core to its bounded anonymous-topology color class."""
    CoreSignals = frozenset(map(str, Signals))
    if not CoreSignals or MaximumSignals < len(CoreSignals):
        return CoreSignals
    CoreColors = frozenset(
        SignalIncidenceFingerprints.get(Signal, "")
        for Signal in CoreSignals
        if SignalIncidenceFingerprints.get(Signal, "")
    )
    EquivalentSignals = tuple(sorted(
        Signal
        for Signal, Fingerprint in SignalIncidenceFingerprints.items()
        if Fingerprint in CoreColors and Signal not in CoreSignals
    ))
    RemainingCapacity = MaximumSignals - len(CoreSignals)
    return frozenset((
        *CoreSignals,
        *EquivalentSignals[:RemainingCapacity],
    ))


def BuildOwnedFrontierTopologyRepairDomainFingerprint(
    Signals: Iterable[str],
    SignalIncidenceFingerprints: Mapping[str, str],
) -> str:
    """Identify one anonymous logical topology class across placements."""
    TopologyColors = tuple(sorted({
        SignalIncidenceFingerprints.get(Signal, f"signal:{Signal}")
        for Signal in map(str, Signals)
    }))
    return BuildStableFingerprint((
        "owned-frontier-topology-repair-domain-v1",
        TopologyColors,
    ))


def EnqueueOwnedFrontierTopologyRepair(Context, Failure: RoutingFailure, SourceCandidate: PcbPlacementCandidate, ComponentVariant: int) -> bool:
    """Publish the next fresh cluster-topology candidate for one proof.

            This deliberately regenerates a complete packed placement.  It
            must not reuse the endpoint ECO, because a contract-independent
            empty owned frontier is a component-fabric contradiction rather
            than a pin-access contradiction.  The two-member repair domain is
            materialized lazily: a changed proof invalidates the unused member,
            while a repeated proof advances to the second deterministic kind.
            """
    Core = BuildPhysicalOwnedFrontierTopologyRepairCore(Failure, SourceCandidate)
    if Core is None or Context.Deadline.IsExpired():
        return False
    if not hasattr(
        Context,
        "OwnedFrontierTopologyRepairSignalsByPlacementFingerprint",
    ):
        Context.OwnedFrontierTopologyRepairSignalsByPlacementFingerprint = {}
    RepairDomainFingerprint = (
        BuildOwnedFrontierTopologyRepairDomainFingerprint(
            Core.Signals,
            Context.SignalLocalIncidenceFingerprints,
        )
    )
    AttemptCount = Context.OwnedFrontierTopologyRepairAttemptCountByDomainFingerprint.get(RepairDomainFingerprint, 0)
    if AttemptCount >= 2:
        Context.OwnedFrontierTopologyRepairPortfolioDiagnostics.append({'Result': 'owned-frontier-topology-portfolio-exhausted', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
        return False
    ExistingFingerprints = frozenset(Context.UniquePlacements)
    ExistingTopologyFingerprints = frozenset((Candidate.InterfaceTopologyFingerprint for Candidate in Context.OwnedFrontierTopologyRepairCandidateByPlacementFingerprint.values()))
    PublishedCandidates: list[PcbPlacementCandidate] = []
    TopologyCandidateBaseIndex = len(Context.OwnedFrontierTopologyRepairCandidateByPlacementFingerprint)
    MaximumTopologyCandidateCount = max(1, Context.Policy.NandPacking.RetainedJointPlacementCandidates * 2)
    if TopologyCandidateBaseIndex >= MaximumTopologyCandidateCount:
        Context.OwnedFrontierTopologyRepairPortfolioDiagnostics.append({'Result': 'owned-frontier-topology-retained-domain-exhausted', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'RetainedStateCount': MaximumTopologyCandidateCount, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
        return False
    RepairVariants = ((3, 'split-interface-cut'), (12, 'relocate-endpoint-cluster'))
    PendingRepairVariants = RepairVariants[AttemptCount:AttemptCount + 1]
    RepairSignals = SelectTopologyEquivalentRepairSignals(
        Core.Signals,
        Context.SignalLocalIncidenceFingerprints,
    )
    TopologyRepairPackingPolicy = replace(
        Context.Policy.NandPacking,
        RetainedJointPlacementCandidates=1,
        JointPlacementBeamWidth=min(
            Context.Policy.NandPacking.JointPlacementBeamWidth,
            max(
                16,
                Context.Policy.NandPacking.JointPlacementBeamWidth // 2,
            ),
        ),
    )
    for TopologyCandidateOffset, (Variant, Kind) in enumerate(PendingRepairVariants):
        if AttemptCount >= 2 or Context.Deadline.IsExpired() or TopologyCandidateBaseIndex + TopologyCandidateOffset >= MaximumTopologyCandidateCount:
            break
        AttemptCount += 1
        Context.OwnedFrontierTopologyRepairAttemptCountByDomainFingerprint[RepairDomainFingerprint] = AttemptCount
        StartedAt = Context.Services.monotonic()
        TopologyCandidateIndex = TopologyCandidateBaseIndex + TopologyCandidateOffset
        try:
            Candidate = Context.Services.PlacePcbGraph(Context.Netlist, RoutingSpacing=SourceCandidate.RoutingSpacing, PlacementPolicy=Context.Policy.Placement, ClusterPolicy=Context.Policy.Clustering, MaximumBoundaryTerminals=Context.Policy.Organization.MaximumClusterEntrances, MaximumEntrancesPerSignal=Context.Policy.Organization.MaximumClusterEntrancesPerSignal, PackingPolicy=TopologyRepairPackingPolicy, RelocationSignals=RepairSignals, RelocationPrioritySignals=frozenset(Core.Signals), RequiredRelocationSignals=RepairSignals, RelocationVariant=Variant, JointPlacementCandidateIndex=TopologyCandidateIndex, AssignmentConstraints=Context.PlacementAssignmentConstraints, EnableClusterBoundaryLeases=True, EnableClusterInterfacePlacementFeasibility=True, CutDrivenClusterRefinementSignals=RepairSignals, FocusedCutEpochPlacement=True, WorkCheck=lambda Diagnostics: Context.Deadline.RaiseIfExpired('OwnedFrontierTopologyRepair', {'SourceCandidateId': SourceCandidate.CandidateId, 'CandidateKind': Kind, 'CoreFingerprint': Core.CoreFingerprint, 'RepairSignals': sorted(RepairSignals), **Diagnostics}))
            Profile = MeasureMandatoryAccessConflictProfile(Candidate.Placed.PlacedGates, Candidate.SignalOrder)
            MandatoryConflicts = {Resource: set(map(str, Owners)) for Resource, Owners in (*Profile.CrossConflicts, *Profile.SelfConflicts)}
            CandidateTopologyDemand = MeasurePlacementTopologyDemand(Context.TopologyDemand, Candidate, MandatoryConflicts=MandatoryConflicts, MandatoryProfile=Profile)
            CandidateDiagnostics = dict(Candidate.Placed.LocalRouteDiagnostics or {})
            CandidateDiagnostics['__OwnedFrontierTopologyRepair__'] = {'Core': Core.ToDictionary(), 'CandidateKind': Kind, 'SourceCandidateId': SourceCandidate.CandidateId}
            Candidate.Placed.LocalRouteDiagnostics = CandidateDiagnostics
            Fingerprint = BuildPlacementFingerprint(Candidate, CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, IncludeLocalClaims=False)
            RetentionFingerprint = BuildPlacementRetentionFingerprint(Candidate, CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, IncludeLocalClaims=False)
            CandidateTopologyFingerprint = BuildClusterInterfacePlacementTopologyFingerprint(Candidate, Context.SignalTopologyFingerprints)
            if Fingerprint in ExistingFingerprints or RetentionFingerprint in Context.RetainedPlacementTopologyFingerprints or CandidateTopologyFingerprint in ExistingTopologyFingerprints or (CandidateTopologyFingerprint == Core.SourceTopologyFingerprint):
                Context.OwnedFrontierTopologyRepairEquivalentRejectsByProofFingerprint[Core.SourceProofFingerprint] = Context.OwnedFrontierTopologyRepairEquivalentRejectsByProofFingerprint.get(Core.SourceProofFingerprint, 0) + 1
                Context.OwnedFrontierTopologyRepairPortfolioDiagnostics.append({'Result': 'owned-frontier-topology-equivalent-rejected', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'CandidateKind': Kind, 'PlacementFingerprint': Fingerprint, 'TopologyFingerprint': CandidateTopologyFingerprint, 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
                continue
        except (RoutingStageError, ValueError) as Error:
            Context.OwnedFrontierTopologyRepairPortfolioDiagnostics.append({'Result': 'owned-frontier-topology-incomplete', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'CandidateKind': Kind, 'Failure': Error.Failure.ToDictionary() if isinstance(Error, RoutingStageError) else str(Error), 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
            continue
        Context.UniquePlacements[Fingerprint] = ('proof-driven-owned-frontier-topology-repair', SourceCandidate.RoutingSpacing, Candidate)
        Context.PlacementRetentionFingerprintByFingerprint[Fingerprint] = RetentionFingerprint
        Context.RetainedPlacementTopologyFingerprints[RetentionFingerprint] = (Fingerprint, 'proof-driven-owned-frontier-topology-repair')
        Context.TopologyDemandByFingerprint[Fingerprint] = CandidateTopologyDemand
        Context.MaterializedPlacementByFingerprint[Fingerprint] = Candidate
        ExistingFingerprints = frozenset((*ExistingFingerprints, Fingerprint))
        ExistingTopologyFingerprints = frozenset((*ExistingTopologyFingerprints, CandidateTopologyFingerprint))
        CandidateRecord = PcbPlacementCandidate(CandidateId=f'Placement-{Fingerprint[:12]}', SourceGenerator='proof-driven-owned-frontier-topology-repair', RoutingSpacing=SourceCandidate.RoutingSpacing, PlacementFingerprint=Fingerprint, FeedbackScore=(Variant,), BoundaryOverflow=0, PinScarcityCount=0, GuideOverflowPeak=0, GuideOverflowCells=0, PinEscapeConflictCount=0, EstimatedGlobalExtensionNodes=0, EstimatedGlobalExtensionNets=0, PreOwnedNodeCount=0, Placement=Candidate, TopologyDemand=CandidateTopologyDemand, PlacementRetentionFingerprint=RetentionFingerprint, InterfaceTopologyFingerprint=CandidateTopologyFingerprint)
        Context.OwnedFrontierTopologyRepairCandidateByPlacementFingerprint[Fingerprint] = CandidateRecord
        Context.OwnedFrontierTopologyRepairKindByPlacementFingerprint[Fingerprint] = Kind
        Context.OwnedFrontierTopologyRepairSignalsByPlacementFingerprint[Fingerprint] = tuple(sorted(RepairSignals))
        Context.RequestedComponentStateFingerprints.add(BuildClusterInterfaceComponentStateFingerprint(Fingerprint, ComponentVariant))
        PublishedCandidates.append(CandidateRecord)
        Context.OwnedFrontierTopologyRepairPortfolioDiagnostics.append({'Result': 'owned-frontier-topology-published', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'CandidateKind': Kind, 'RepairDomainFingerprint': RepairDomainFingerprint, 'RepairDomainAttemptIndex': AttemptCount - 1, 'RepairSignals': sorted(RepairSignals), 'TopologyEquivalentRepairSignals': sorted(RepairSignals - frozenset(Core.Signals)), 'PlacementFingerprint': Fingerprint, 'TopologyFingerprint': CandidateTopologyFingerprint, 'RoutingResourceConstructionDeferred': True, 'RetainedJointPlacementCandidates': TopologyRepairPackingPolicy.RetainedJointPlacementCandidates, 'JointPlacementBeamWidth': TopologyRepairPackingPolicy.JointPlacementBeamWidth, 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
    if not PublishedCandidates:
        return False
    Context.InterfaceCandidateQueue[0:0] = [('prepare-eligibility', len(Context.InterfaceCandidates) + Index, Candidate, 0, ComponentVariant) for Index, Candidate in enumerate(PublishedCandidates)]
    return True


def RecordPhysicalComponentStageTiming(Context, Stage: str, StartedAt: float, *, Result: str, PlanFingerprint: str='') -> None:
    Context.PhysicalComponentStageTimings.append({'Stage': Stage, 'AttemptIndex': sum((Entry.get('Stage') == Stage for Entry in Context.PhysicalComponentStageTimings)), 'Result': Result, 'DurationSeconds': round(Context.Services.monotonic() - StartedAt, 6), 'ElapsedSinceRoutingStartSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6), 'PhysicalAssemblyPlanFingerprint': PlanFingerprint})


def ProveUnboundOwnedSignalFrontier(Context, UnboundProblem: Any) -> None:
    FrontierProofStartedAt = Context.Services.monotonic()
    FrontierProof = ProveClosedComponentOwnedSignalFrontiers(UnboundProblem, DeadlineSeconds=Context.InterfaceDeadline.RemainingSeconds(), WorkCheck=lambda Diagnostics: Context.InterfaceDeadline.RaiseIfExpired('PhysicalUnboundOwnedSignalFrontierEligibility', Diagnostics), CompletedProofCache=Context.ComponentOwnedSignalFrontierProofCache, RouteClaimsConstructionCache=Context.ComponentRouteClaimsConstructionCache)
    CoreSignals = SelectContractIndependentOwnedSignalFrontierUnsatCore(UnboundProblem, FrontierProof)
    RecordPhysicalComponentStageTiming(Context, 'PhysicalUnboundOwnedSignalFrontierEligibility', FrontierProofStartedAt, Result='contract-independent-unsatisfiable' if CoreSignals else FrontierProof.Status)
    if not CoreSignals:
        return
    FrontierDiagnostics = dict(FrontierProof.Diagnostics or {})
    CoreFingerprint = str(FrontierDiagnostics.get('LocalUnsatCoreFingerprint', FrontierProof.ProofFingerprint))
    raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable, Stage='PhysicalComponentLocalEligibility', AffectedNets=CoreSignals, Detail='the unbound placed component has a port-independent empty owned-signal frontier', RepairActions=(), Diagnostics={**FrontierDiagnostics, 'OwnedSignalFrontierProofComplete': True, 'PortAssignmentProofComplete': True, 'PortAssignmentUnsatCoreMinimal': True, 'PortAssignmentUnsatCoreSignals': list(CoreSignals), 'PortAssignmentUnsatCoreFingerprint': CoreFingerprint, 'DomainFingerprint': str(UnboundProblem.ProblemFingerprint), 'PhysicalPortFactorPreparationEntered': False, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False, 'ImplicitForeignTransitDomainCount': 0}))


def AdmitSymbolicLocalCapacity(Context, Assembly: Any) -> bool:
    """Prove selected local support before exterior routing."""
    LocalCapacityContractFingerprint = BuildStableFingerprint(('closed-component-local-capacity-contract-v1', Assembly.Problem.PlacementFingerprint, Assembly.Problem.Fabric.FabricFingerprint, getattr(Assembly.Problem.ResourceGraph, 'GraphVersion', ''), Assembly.Problem.MaximumPowerDistance, tuple(sorted(((Port.Signal, BuildPhysicalPortLocalContractFingerprint(Port)) for Port in Assembly.Plan.Ports)))))
    if LocalCapacityContractFingerprint in Context.CapacityFeasibleLocalContractFingerprints:
        RecordPhysicalComponentStageTiming(Context, 'PhysicalSymbolicCapacityEligibility', Context.Services.monotonic(), Result='capacity-feasible-cache-hit', PlanFingerprint=Assembly.Plan.PlanFingerprint)
        return True
    AdmissionStartedAt = Context.Services.monotonic()
    Proof = ProveClosedComponentSymbolicCapacityEligibility(Assembly.Problem, DeadlineSeconds=Context.InterfaceDeadline.RemainingSeconds(), WorkCheck=lambda Diagnostics: Context.InterfaceDeadline.RaiseIfExpired('PhysicalSymbolicCapacityEligibility', Diagnostics), CompletedProofCache=Context.ComponentSymbolicCapacityProofCache, RouteClaimsConstructionCache=Context.ComponentRouteClaimsConstructionCache, SymbolicNetStateCache=Context.InterfaceResources.PhysicalComponentSymbolicNetStateCache)
    RecordPhysicalComponentStageTiming(Context, 'PhysicalSymbolicCapacityEligibility', AdmissionStartedAt, Result=Proof.Status, PlanFingerprint=Assembly.Plan.PlanFingerprint)
    if Proof.Status == 'capacity-feasible':
        Context.CapacityFeasibleLocalContractFingerprints.add(LocalCapacityContractFingerprint)
        return True
    if Proof.Status != 'architectural-unsatisfiable':
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalSymbolicCapacityEligibility', Detail='the selected local capacity proof did not complete before exterior routing', Diagnostics={'SymbolicCapacityStatus': Proof.Status, 'SymbolicCapacityDiagnostics': dict(Proof.Diagnostics or {}), 'PhysicalAssemblyPlanFingerprint': Assembly.Plan.PlanFingerprint, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False, 'ImplicitForeignTransitDomainCount': 0}))
    ProofDiagnostics = dict(Proof.Diagnostics or {})
    BinaryCore = tuple(sorted(set(map(str, ProofDiagnostics.get('LocalUnsatCoreSignals', ())))))
    if len(BinaryCore) == 2 and len(Context.CompiledSymbolicCapacityBinaryCores) < Context.MaximumCompleteBinaryCoreCertificates and (BinaryCore not in Context.CompiledSymbolicCapacityBinaryCores):
        BinaryCompilationStartedAt = Context.Services.monotonic()
        BinaryCertificate = CompilePhysicalComponentSymbolicPortPairDomain(Assembly.Problem, Context.PreparedEligibility, BinaryCore, DeadlineSeconds=Context.SharedInterfacePlanningDeadline.RemainingSeconds(), WorkCheck=lambda Diagnostics: Context.SharedInterfacePlanningDeadline.RaiseIfExpired('PhysicalComponentBinarySupportCompilation', Diagnostics), NetStateCache=Context.InterfaceResources.PhysicalComponentSymbolicNetStateCache, CompletedCertificateCache=Context.InterfaceResources.PhysicalComponentSymbolicPortPairCertificateCache, CompleteCompatibilityIndexCache=Context.InterfaceResources.PhysicalComponentSymbolicPairCompatibilityIndexCache, RouteClaimsConstructionCache=Context.ComponentRouteClaimsConstructionCache)
        if not BinaryCertificate.Complete:
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalComponentBinarySupportCompilation', AffectedNets=BinaryCore, Detail='the learned binary local core did not finish complete support compilation'))
        BinaryCoreClauses, BinaryProjectionDiagnostics = ProjectCompletePhysicalPortPairCertificateToApertureClauses(Context.PreparedEligibility, BinaryCertificate)
        if not BinaryProjectionDiagnostics.get('ApertureProjectionComplete', False):
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalComponentBinarySupportCompilation', AffectedNets=BinaryCore, Detail='the learned complete binary core could not be projected to aperture clauses', Diagnostics=dict(BinaryProjectionDiagnostics)))
        Context.InterfaceResources.RejectedPhysicalComponentPortReservationSets.update(BinaryCoreClauses)
        Context.CompiledSymbolicCapacityBinaryCores.add(BinaryCore)
        RecordPhysicalComponentStageTiming(Context, 'PhysicalComponentBinarySupportCompilation', BinaryCompilationStartedAt, Result='complete', PlanFingerprint=Assembly.Plan.PlanFingerprint)
        Context.StateAttemptDiagnostics.append({'Result': 'binary-support-core-compiled', 'SignalPair': list(BinaryCore), 'PublishedBinaryClauseCount': len(BinaryCoreClauses), 'BinaryCertificateFingerprint': BinaryCertificate.ProofFingerprint, **BinaryProjectionDiagnostics, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False})
    NoGoodDiagnostics = RecordPhysicalComponentSymbolicCapacityEligibilityNoGood(Proof, Assembly.Plan, Context.InterfaceResources, FactorDomain=Context.PreparedEligibility)
    Context.CumulativeSymbolicCapacityPressureSignals.update(map(str, Proof.Diagnostics.get('LocalUnsatCoreSignals', ()) if isinstance(Proof.Diagnostics, dict) else ()))
    Context.LatestSymbolicCapacityRepairEvidence = BuildSymbolicCapacityRepairEvidence(NoGoodDiagnostics, Context.CumulativeSymbolicCapacityPressureSignals)
    Context.StateAttemptDiagnostics.append({'Result': 'symbolic-capacity-reject-before-global', 'PhysicalAssemblyPlanFingerprint': Assembly.Plan.PlanFingerprint, **NoGoodDiagnostics, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False, 'ImplicitForeignTransitDomainCount': 0})
    return False


def ReplanPhysicalAssemblyWithTiming(Context, RequiredGlobalBoundaryPorts: tuple[Any, ...] | None=None) -> Any:
    PressureSignals = tuple(sorted(Context.CumulativeSymbolicCapacityPressureSignals))
    RepairEvidence = dict(Context.LatestSymbolicCapacityRepairEvidence)
    if (
        PressureSignals
        and RepairEvidence.get('SymbolicCapacityProofComplete', False)
        and RepairEvidence.get('SymbolicCapacityProofFingerprint', '')
    ):
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
            Stage='PhysicalSymbolicCapacityPlacementFeedback',
            AffectedNets=PressureSignals,
            Detail='a complete local capacity core rejected the frozen pre-route interface; automatic component replanning is disabled and targeted placement feedback is required',
            Diagnostics={
                'PreRouteInterfaceSelection': Context.PreRouteInterfaceResult.ToDictionary(),
                'RequiredGlobalBoundaryPortCount': len(RequiredGlobalBoundaryPorts or ()),
                'SymbolicCapacityAssemblyReplanCount': Context.SymbolicCapacityAssemblyReplanCount,
                'AutomaticReplanDisabled': True,
                'SymbolicCapacityPlacementFeedback': True,
                'PlacementInterfacePressureSignals': list(PressureSignals),
                'GlobalPlanningEntered': False,
                'LocalCompilationEntered': False,
                **RepairEvidence,
            },
        ))
    raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PreRouteInterfaceSelection', AffectedNets=PressureSignals, Detail='the frozen pre-route interface contract was rejected; automatic component replanning is disabled', Diagnostics={'PreRouteInterfaceSelection': Context.PreRouteInterfaceResult.ToDictionary(), 'RequiredGlobalBoundaryPortCount': len(RequiredGlobalBoundaryPorts or ()), 'SymbolicCapacityAssemblyReplanCount': Context.SymbolicCapacityAssemblyReplanCount, 'AutomaticReplanDisabled': True, **RepairEvidence}))
    while True:
        if Context.SymbolicCapacityAssemblyReplanCount >= Context.MaximumSymbolicCapacityAssemblyReplans:
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable, Stage='PhysicalSymbolicCapacityPlacementFeedback', AffectedNets=tuple(sorted(Context.CumulativeSymbolicCapacityPressureSignals)), Detail='a complete selected local capacity core rejected the bounded assembly-replan portfolio; advancing to targeted placement feedback', Diagnostics={'SymbolicCapacityPlacementFeedback': True, 'PlacementInterfacePressureSignals': sorted(Context.CumulativeSymbolicCapacityPressureSignals), 'SymbolicCapacityAssemblyReplanCount': Context.SymbolicCapacityAssemblyReplanCount, 'MaximumSymbolicCapacityAssemblyReplans': Context.MaximumSymbolicCapacityAssemblyReplans, **Context.LatestSymbolicCapacityRepairEvidence, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False}))
        ReplanStartedAt = Context.Services.monotonic()
        try:
            Result = ReplanPhysicalComponentAssembly(Context.ComponentBasePlacement, Resources=Context.InterfaceResources, Deadline=Context.InterfaceDeadline, RequiredGlobalBoundaryPorts=RequiredGlobalBoundaryPorts)
        except RoutingStageError as Error:
            RecordPhysicalComponentStageTiming(Context, 'PhysicalAssemblyReplan', ReplanStartedAt, Result='failed')
            if not Context.CumulativeSymbolicCapacityPressureSignals:
                raise
            Diagnostics = dict(Error.Failure.Diagnostics) if isinstance(Error.Failure.Diagnostics, dict) else {}
            raise RoutingStageError(replace(Error.Failure, Diagnostics={**Diagnostics, 'PlacementInterfacePressureSignals': sorted(Context.CumulativeSymbolicCapacityPressureSignals), 'SymbolicCapacityPlacementFeedback': True, **Context.LatestSymbolicCapacityRepairEvidence})) from Error
        except Exception:
            RecordPhysicalComponentStageTiming(Context, 'PhysicalAssemblyReplan', ReplanStartedAt, Result='failed')
            raise
        RecordPhysicalComponentStageTiming(Context, 'PhysicalAssemblyReplan', ReplanStartedAt, Result='complete', PlanFingerprint=Result.Plan.PlanFingerprint)
        Context.SymbolicCapacityAssemblyReplanCount += 1
        if AdmitSymbolicLocalCapacity(Context, Result):
            return Result
        if RequiredGlobalBoundaryPorts is not None:
            RequiredGlobalBoundaryPorts = None


def ReserveAuthoritativeGlobalChannels(Context, Assembly: Any) -> tuple[Any, RoutedDesign | None]:

    def RebuildFrontierDeferrals() -> None:
        PrunedFrontier, _RejectedRetainedPlanFingerprints = PruneRetainedPhysicalGlobalPlansByRejectedApertureClauses(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier, Context.InterfaceResources.RejectedPhysicalComponentPortReservationSets)
        Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier = PrunedFrontier
        Context.InterfaceResources.DeferredPhysicalComponentPortAssignmentFingerprints = {Entry.Assembly.Plan.PortAssignmentFingerprint for Entry in Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier.values()}

    def RetainIncompleteGlobalPlan(CurrentAssembly: Any, Failure: RoutingFailure) -> dict[str, object]:
        Diagnostics = dict(Failure.Diagnostics or {})
        DependencyValues = FindPhysicalGlobalDiagnosticValues(Diagnostics, 'PhysicalGlobalCandidateRequestDependencyFingerprints')
        RequestDependencies = next((Value for Value in reversed(DependencyValues) if isinstance(Value, dict)), {})
        RemainingValues = FindPhysicalGlobalDiagnosticValues(Diagnostics, 'RemainingRequestCounts')
        RemainingCounts = next((Value for Value in reversed(RemainingValues) if isinstance(Value, dict)), {})
        CandidateContinuationValues = FindPhysicalGlobalDiagnosticValues(Diagnostics, 'PhysicalComponentGlobalCandidateContinuations')
        CandidateContinuationRecords = next((Value for Value in reversed(CandidateContinuationValues) if isinstance(Value, list) and Value), [])
        if not RemainingCounts:
            RemainingCounts = {str(Value.get('Signal', '')): max(0, int(Value.get('RemainingRequestCount', 0))) for Value in CandidateContinuationRecords if isinstance(Value, dict) and str(Value.get('Signal', ''))}
        ApertureValues = FindPhysicalGlobalDiagnosticValues(Diagnostics, 'CertifiedPhysicalComponentApertureDomain')
        ApertureDiagnostics = next((Value for Value in reversed(ApertureValues) if isinstance(Value, dict)), {})
        PortsBySignal = {Port.Signal: Port for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(CurrentAssembly.Plan)}
        CorridorBySignal = {}
        for Domain in Context.InterfaceResources.PhysicalPortCorridorDomainCache.values():
            Port = PortsBySignal.get(Domain.Signal)
            if Port is None or Domain.PortReservationFingerprint != Port.ReservationFingerprint:
                continue
            ExpectedDependency = RequestDependencies.get(Domain.Signal)
            if ExpectedDependency and Domain.RequestDependencyFingerprint != ExpectedDependency:
                continue
            CorridorBySignal[Domain.Signal] = Domain
        CertificateFingerprints = tuple(sorted({str(Value) for Key in ('CertificateFingerprint', 'AccessCertificateFingerprint') for Value in FindPhysicalGlobalDiagnosticValues(Diagnostics, Key) if isinstance(Value, str) and Value} | {str(ApertureDiagnostics.get('DomainFingerprint', ''))}))
        ApertureFingerprint = str(ApertureDiagnostics.get('DomainFingerprint', ''))
        ResumeCursor, CompletedWork = BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(CurrentAssembly.Plan.PlanFingerprint, ApertureFingerprint, Diagnostics)
        Continuation = BuildPhysicalGlobalPlanContinuationState(CurrentAssembly.Plan, RequestDependencies, RemainingCounts, CorridorBySignal.values(), CertificateFingerprints, CompletedWork=CompletedWork, ResumeCursor=ResumeCursor)
        ExistingEntry = Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier.get(CurrentAssembly.Plan.PlanFingerprint)
        RetentionAdmission = ClassifyPhysicalGlobalPlanRetentionAdmission(ApertureDiagnostics, Continuation=Continuation, ExistingEntry=ExistingEntry)
        if not RetentionAdmission['Retained']:
            return RetentionAdmission
        Sequence = int(Context.InterfaceResources.PhysicalGlobalPlanFrontierScheduleSequence)
        Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier = RetainIncompletePhysicalGlobalPlan(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier, CurrentAssembly, Continuation, EnqueuedSequence=Sequence)
        Context.InterfaceResources.PhysicalGlobalPlanFrontierScheduleSequence = Sequence + 1
        RebuildFrontierDeferrals()
        return {**RetentionAdmission, 'RetainedGlobalPlanFrontierSize': len(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier)}
    PreviousGlobalPlanWasRetained = False
    CompleteGlobalPlanRejectionCount = 0

    def SelectRetainedAssembly(*, ClearDeferredAssignments: bool) -> Any:
        nonlocal PreviousGlobalPlanWasRetained
        RebuildFrontierDeferrals()
        Frontier = Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier
        if not Frontier:
            raise ValueError('retained physical global-plan frontier is empty')
        if ClearDeferredAssignments:
            Context.InterfaceResources.DeferredPhysicalComponentPortAssignmentFingerprints.clear()
        Sequence = int(Context.InterfaceResources.PhysicalGlobalPlanFrontierScheduleSequence)
        Entry, UpdatedFrontier = SelectNextRetainedPhysicalGlobalPlan(Frontier, ScheduleSequence=Sequence)
        Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier = UpdatedFrontier
        Context.InterfaceResources.PhysicalGlobalPlanFrontierScheduleSequence = Sequence + 1
        PreviousGlobalPlanWasRetained = True
        return Entry.Assembly

    def SelectFreshOrRetainedAssembly() -> Any:
        nonlocal PreviousGlobalPlanWasRetained
        RebuildFrontierDeferrals()
        Frontier = Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier
        if ShouldScheduleRetainedPhysicalGlobalPlan(Frontier, PreviousPlanWasRetained=PreviousGlobalPlanWasRetained):
            return SelectRetainedAssembly(ClearDeferredAssignments=False)
        try:
            Assembly = ReplanPhysicalAssemblyWithTiming(Context)
            PreviousGlobalPlanWasRetained = False
            return Assembly
        except RoutingStageError as Error:
            Diagnostics = dict(Error.Failure.Diagnostics or {})
            if not Diagnostics.get('DeferredPortAssignmentDomainExhausted', False):
                raise
            Frontier = Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier
            if not Frontier:
                raise
            return SelectRetainedAssembly(ClearDeferredAssignments=True)
    CurrentAssembly = Assembly
    while True:
        if not CurrentAssembly.Plan.SelectedLocalPortSupports:
            CurrentAssembly = BindPhysicalComponentAssemblyLocalPortSupports(CurrentAssembly, RequireFrozenGlobalChannels=False)
        Context.InterfaceResources.FrozenPhysicalComponentAssemblyPlan = CurrentAssembly.Plan
        Context.InterfaceResources.PreparedComponentRoutingProblem = CurrentAssembly.Problem
        Context.InterfaceResources.FrozenPreparedPortalDomainCache = None
        Context.InterfaceResources.FrozenInterfaceGlobalCandidateCache = {}
        Context.InterfaceResources.FrozenInterfaceGlobalCandidateMetadata = {}
        Context.InterfaceResources.FrozenInterfaceGlobalCandidatePlacementIdentity = None
        GlobalPlanningAttemptStartedAt = Context.Services.monotonic()
        GlobalPlanningAttemptResult = 'failed'
        GlobalPlanningAttemptRecorded = False
        GlobalPlanningAttemptPlanFingerprint = CurrentAssembly.Plan.PlanFingerprint
        try:
            ExactGlobalSignals = SelectPhysicalComponentExactGlobalChannelSignals(CurrentAssembly.Plan)
            Context.InterfaceResources.PreparingPhysicalComponentGlobalChannels = True
            Context.InterfaceResources.PhysicalComponentExactGlobalChannelSignals = ExactGlobalSignals
            GlobalPlanningPlacement = replace(Context.ComponentBasePlacement, Placed=PreparePhysicalComponentGlobalPlanningPlacement(Context.ComponentBasePlacement.Placed, CurrentAssembly.Problem, CurrentAssembly.Plan, LocalSupportTemplate=None))
            if Context.InterfaceDeadline.IsExpired():
                raise RoutingStageError(BuildPhysicalAssemblyPlanningIncompleteFailure(Context.InterfaceStageSchedule, RemainingSeconds=Context.InterfaceDeadline.RemainingSeconds(), GlobalPlanningEntered=True))
            GlobalPlanningDeadline = BuildPhysicalGlobalPlanYieldDeadline(Context.InterfaceDeadline, len(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier), CurrentPlanWasRetained=PreviousGlobalPlanWasRetained)
            GlobalPlanningSeconds = GlobalPlanningDeadline.RemainingSeconds()
            GlobalPlanningPolicy = replace(Context.Policy, RuntimeBudgetSeconds=GlobalPlanningSeconds, AdaptiveRouting=replace(Context.Policy.AdaptiveRouting, MaximumRuntimeSeconds=min(Context.Policy.AdaptiveRouting.MaximumRuntimeSeconds, GlobalPlanningSeconds)))
            GlobalChannelDesign = Context.Services.RoutePcbDesign(GlobalPlanningPlacement, Policy=GlobalPlanningPolicy, Deadline=GlobalPlanningDeadline, Resources=Context.InterfaceResources)
            BoundAssembly = BindPhysicalComponentAssemblyGlobalChannels(CurrentAssembly, GlobalChannelDesign, Context.InterfaceResources.ResourceGraph)
            Context.InterfaceResources.PreparingPhysicalComponentGlobalChannels = False
            Context.InterfaceResources.FrozenPhysicalComponentAssemblyPlan = BoundAssembly.Plan
            Context.InterfaceResources.PreparedComponentRoutingProblem = BoundAssembly.Problem
            ForeignPortalPlacement = replace(GlobalPlanningPlacement, Placed=FreezePhysicalAssemblyGlobalChannels(GlobalPlanningPlacement.Placed, BoundAssembly.Plan, GlobalChannelDesign))
            ValidatePhysicalComponentForeignPortalSupport(ForeignPortalPlacement, Resources=Context.InterfaceResources, Policy=GlobalPlanningPolicy, Deadline=GlobalPlanningDeadline)
            BoundAssembly = BindPhysicalComponentAssemblyLocalPortSupports(BoundAssembly)
            GlobalPlanningAttemptResult = 'reserved'
        except RoutingStageError as GlobalPlanningError:
            FrozenPostClosurePortalHandoffTelemetry = BuildFrozenPostClosurePortalHandoffTelemetry(Context.InterfaceResources, Context.InterfaceResources.PreparedPhysicalComponentPortFactorDomain, CurrentAssembly.Plan)
            GlobalPlanningFailure = replace(GlobalPlanningError.Failure, Diagnostics={**dict(GlobalPlanningError.Failure.Diagnostics or {}), 'FrozenPostClosurePortalHandoff': FrozenPostClosurePortalHandoffTelemetry})
            ClassifiedFailure = ClassifyPhysicalComponentGlobalPlanningFailure(GlobalPlanningFailure, CurrentAssembly.Plan, DeadlineExpired=Context.InterfaceDeadline.IsExpired())
            if ClassifiedFailure.Reason == RoutingFailureReason.ComponentChannelCapacityUnsatisfiable and (not Context.InterfaceDeadline.IsExpired()) and ClassifiedFailure.Diagnostics.get('AssemblyPlanReassignmentAllowed', True):
                CompleteGlobalPlanRejectionCount += 1
                GlobalPlanningAttemptResult = 'complete-plan-rejected'
                Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier.pop(CurrentAssembly.Plan.PlanFingerprint, None)
                RebuildFrontierDeferrals()
                NoGoodDiagnostics = RecordPhysicalComponentGlobalPlanNoGood(ClassifiedFailure, CurrentAssembly.Plan, Context.InterfaceResources, ShouldStop=Context.InterfaceDeadline.IsExpired)
                Context.StateAttemptDiagnostics.append({'Result': 'global-planning-reject-physical-plan', 'GlobalPlanSource': 'retained' if PreviousGlobalPlanWasRetained else 'fresh', 'PhysicalAssemblyPlanFingerprint': CurrentAssembly.Plan.PlanFingerprint, 'RejectedPortAssignmentFingerprint': CurrentAssembly.Plan.PortAssignmentFingerprint, **NoGoodDiagnostics, 'UnderlyingFailure': ClassifiedFailure.ToDictionary(), 'LocalCompilationEntered': False, 'LocalTemplateReopened': False, 'ImplicitForeignTransitDomainCount': 0})
                RecordPhysicalComponentStageTiming(Context, 'AuthoritativeGlobalReserve', GlobalPlanningAttemptStartedAt, Result=GlobalPlanningAttemptResult, PlanFingerprint=GlobalPlanningAttemptPlanFingerprint)
                GlobalPlanningAttemptRecorded = True
                CurrentAssembly = SelectFreshOrRetainedAssembly()
                continue
            if ClassifiedFailure.Reason == RoutingFailureReason.PhysicalComponentAssemblyIncomplete:
                RetentionDiagnostics = RetainIncompleteGlobalPlan(CurrentAssembly, ClassifiedFailure)
                GlobalPlanningAttemptResult = 'incomplete-plan-retained' if RetentionDiagnostics['Retained'] else 'incomplete-plan-not-retained'
                Context.StateAttemptDiagnostics.append({'Result': 'global-planning-retain-plan' if RetentionDiagnostics['Retained'] else 'global-planning-incomplete-plan-not-retained', 'GlobalPlanSource': 'retained' if PreviousGlobalPlanWasRetained else 'fresh', 'PhysicalAssemblyPlanFingerprint': CurrentAssembly.Plan.PlanFingerprint, 'PortAssignmentFingerprint': CurrentAssembly.Plan.PortAssignmentFingerprint, 'RetainedGlobalPlanFrontierSize': len(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier), 'CursorResumeAvailable': bool(RetentionDiagnostics.get('CursorResumeAvailable', False)), 'CompleteAssignmentCutProof': False, 'NoGoodRecorded': False, 'RetentionAdmission': RetentionDiagnostics, 'UnderlyingFailure': ClassifiedFailure.ToDictionary(), 'ImplicitForeignTransitDomainCount': 0})
                if Context.InterfaceDeadline.IsExpired():
                    SharedPlanningExpired = Context.SharedInterfacePlanningDeadline.IsExpired()
                    raise RoutingStageError(replace(ClassifiedFailure, Diagnostics={**dict(ClassifiedFailure.Diagnostics or {}), 'PlacementWorkSliceExpired': not SharedPlanningExpired, 'PlacementInterfacePressureSignals': list(ClassifiedFailure.AffectedNets), 'SharedPlanningDeadline': Context.SharedInterfacePlanningDeadline.ToDictionary()})) from GlobalPlanningError
                if not RetentionDiagnostics['Retained']:
                    raise RoutingStageError(replace(ClassifiedFailure, Diagnostics={**dict(ClassifiedFailure.Diagnostics or {}), 'RetentionAdmission': RetentionDiagnostics, 'RetainedGlobalPlanFrontierSize': len(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier)})) from GlobalPlanningError
                RecordPhysicalComponentStageTiming(Context, 'AuthoritativeGlobalReserve', GlobalPlanningAttemptStartedAt, Result=GlobalPlanningAttemptResult, PlanFingerprint=GlobalPlanningAttemptPlanFingerprint)
                GlobalPlanningAttemptRecorded = True
                CurrentAssembly = SelectFreshOrRetainedAssembly()
                continue
            raise RoutingStageError(ClassifiedFailure) from GlobalPlanningError
        finally:
            if not GlobalPlanningAttemptRecorded:
                RecordPhysicalComponentStageTiming(Context, 'AuthoritativeGlobalReserve', GlobalPlanningAttemptStartedAt, Result=GlobalPlanningAttemptResult, PlanFingerprint=GlobalPlanningAttemptPlanFingerprint)
            Context.InterfaceResources.PreparingPhysicalComponentGlobalChannels = False
            Context.InterfaceResources.PhysicalComponentExactGlobalChannelSignals = frozenset()
        Context.InterfaceResources.PreparedPhysicalComponentAssembly = BoundAssembly
        Context.InterfaceResources.PreparedComponentRoutingProblem = BoundAssembly.Problem
        Context.InterfaceResources.FrozenPhysicalComponentAssemblyPlan = BoundAssembly.Plan
        Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier.pop(BoundAssembly.Plan.PlanFingerprint, None)
        RebuildFrontierDeferrals()
        Context.SuccessfulGlobalPlanWasRetained = PreviousGlobalPlanWasRetained
        return (BoundAssembly, GlobalChannelDesign)


def CheckCandidateValidation(Context, Diagnostics: dict[str, object]) -> None:
    Context.AttemptDeadline.RaiseIfExpired('PlacementCandidateValidation', {'CandidateId': Context.CandidateRecord.CandidateId, 'AdaptiveAttemptStartedAt': Context.AttemptStarted, 'AdaptiveAttemptExpiresAt': Context.AdaptiveAttemptExpiresAt, **Diagnostics})

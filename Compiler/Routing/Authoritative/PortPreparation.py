"""Small orchestrator for exact physical-port factor preparation."""

from __future__ import annotations

from ..Contracts.Component import ComponentCutAccessFeasibilityCertificate
from ..Contracts.PhysicalInterface import PreparedPhysicalComponentPortFactorDomain
from ..Contracts.Results import RoutingResources
from ..Failures import RoutingFailure
from ..Failures import RoutingFailureReason
from ..Failures import RoutingStageError
from ..ResourceGraph import LocalRouteClaim
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Mapping
from time import monotonic
from .PortPreparationState import PortPreparationState
from .PortPreparationInputs import (
    ValidatePhysicalPortPreparation,
    BuildPhysicalPortChannelReservations,
    BuildPhysicalPortExteriorFabrics,
    PreparePhysicalPortConnectorSearch,
)
from .PortPreparationFactors import (
    BuildPhysicalPortLaneFactors,
    CertifyPhysicalPortFactors,
    CachePhysicalPortLocalFactors,
    FinalizePhysicalPortPreparation,
)
from .PhysicalGuides import DecomposePhysicalPortLaneFactors
from ..Reliability import BuildStableFingerprint


def SelectDisjointCapacitySeams(LocalFactorsBySignal, Constraint):
    """Select the first canonical capacity-one assignment from local factors."""
    FactorsBySignal = dict(LocalFactorsBySignal)
    Domains = {
        Signal: tuple(sorted(
            FactorsBySignal.get(Signal, ()),
            key=lambda Factor: (
                str(Factor.SeamContractFingerprint),
                tuple(sorted(map(str, Factor.LocalClaims.ResourceIds))),
            ),
        ))
        for Signal in Constraint.Signals
    }
    AvailableSeamClasses = tuple(
        (Signal, tuple(
            str(Factor.SeamContractFingerprint)
            for Factor in Domains[Signal]
        ))
        for Signal in Constraint.Signals
    )
    if any(not Domains[Signal] for Signal in Constraint.Signals):
        return False, "", (), AvailableSeamClasses

    def Search(Remaining, UsedClaims, Selected):
        if not Remaining:
            return Selected
        Signal = Remaining[0]
        for Factor in Domains[Signal]:
            Claims = frozenset(Factor.LocalClaims.ResourceIds)
            if Claims.isdisjoint(UsedClaims):
                Result = Search(
                    Remaining[1:],
                    UsedClaims | Claims,
                    (*Selected, (
                        Signal,
                        str(Factor.SeamContractFingerprint),
                    )),
                )
                if Result is not None:
                    return Result
        return None

    Assignment = Search(tuple(sorted(Constraint.Signals)), frozenset(), ())
    if Assignment is None:
        return False, "", (), AvailableSeamClasses
    OrderedAssignment = tuple(sorted(Assignment))
    return (
        True,
        BuildStableFingerprint((
            Constraint.RepairDomainFingerprint,
            OrderedAssignment,
        )),
        OrderedAssignment,
        AvailableSeamClasses,
    )


def SelectPriorityDisjointCapacitySeams(Context, Constraint):
    LocalFactorsBySignal, _ApertureFactors, _Supports = (
        DecomposePhysicalPortLaneFactors(
            Context.LaneFactorsBySignal,
            Context.ChannelReservations,
            Context.ResourceGraph,
            FabricOrigin=Context.FabricOrigin,
        )
    )
    return SelectDisjointCapacitySeams(
        LocalFactorsBySignal,
        Constraint,
    )


def PreparePhysicalComponentPortFactorDomain(Placed: Any, Problem: Any, CoarsePlan: Any, Resources: RoutingResources, *, LayerCount: int | None=None, AccessCertificate: ComponentCutAccessFeasibilityCertificate | None=None, AuthoritativeRegion: Any | None=None, AuthoritativeRegionFingerprint: str='', Profiles: Mapping[str, Any] | None=None, FrozenComponentClaims: Iterable[LocalRouteClaim]=(), TechnologyFingerprint: str='', WorkCheck: Callable[[dict[str, object]], None] | None=None) -> PreparedPhysicalComponentPortFactorDomain:
    """Prepare and freeze the complete pre-assignment port factor domain."""
    Context = PortPreparationState(Placed=Placed, Problem=Problem, CoarsePlan=CoarsePlan, Resources=Resources, LayerCount=LayerCount, AccessCertificate=AccessCertificate, AuthoritativeRegion=AuthoritativeRegion, AuthoritativeRegionFingerprint=AuthoritativeRegionFingerprint, Profiles=Profiles, FrozenComponentClaims=FrozenComponentClaims, TechnologyFingerprint=TechnologyFingerprint, WorkCheck=WorkCheck)
    Context.FactorPreparationTimings: dict[str, float] = {}

    def RecordTiming(Name: str, StartedAt: float) -> None:
        Context.FactorPreparationTimings[Name] = (
            Context.FactorPreparationTimings.get(Name, 0.0)
            + monotonic()
            - StartedAt
        )

    PhaseStartedAt = monotonic()
    ValidatePhysicalPortPreparation(Context)
    BuildPhysicalPortChannelReservations(Context)
    BuildPhysicalPortExteriorFabrics(Context)
    RecordTiming("InputAndExteriorFabric", PhaseStartedAt)
    PortSignals = frozenset(
        str(Port.Signal) for Port in Context.Problem.Interface.Ports
    )
    PrioritySignals = frozenset(map(str, getattr(
        Resources,
        "PhysicalComponentBoundaryTraversalPrioritySignals",
        (),
    ))) & PortSignals
    if PrioritySignals:
        PhaseStartedAt = monotonic()
        PreparePhysicalPortConnectorSearch(
            Context,
            PrioritySignals,
        )
        RecordTiming("NativeConnectorPreparation", PhaseStartedAt)
        PhaseStartedAt = monotonic()
        BuildPhysicalPortLaneFactors(Context, PrioritySignals)
        RecordTiming("LaneFactorMaterialization", PhaseStartedAt)
        CompleteEmptyPrioritySignals = tuple(sorted(
            Signal
            for Signal in PrioritySignals
            if not Context.LaneFactorsBySignal.get(Signal)
            and Context.CertifiedPortDomainBySignal.get(Signal) is not None
            and Context.CertifiedPortDomainBySignal[Signal].Complete
            and Context.AccessCertificate.Complete
        ))
        if CompleteEmptyPrioritySignals:
            raise RoutingStageError(RoutingFailure(
                Reason=(
                    RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
                ),
                Stage="PhysicalComponentEligibility",
                AffectedNets=CompleteEmptyPrioritySignals,
                Detail=(
                    "the priority physical port slice has a certified empty "
                    "bank"
                ),
                Diagnostics={
                    "Complete": True,
                    "Feasible": False,
                    "PriorityPreparation": True,
                    "PriorityPreparationSignals": sorted(PrioritySignals),
                    "DomainDiagnosticsBySignal": {
                        Signal: Context.LaneFactorDiagnosticsBySignal[Signal]
                        for Signal in sorted(PrioritySignals)
                    },
                    "NativeConnectorBatchWorkItems": (
                        Context.NativeConnectorBatchWorkItems
                    ),
                    "NativeConnectorBatchActiveWorkerCount": (
                        Context.NativeConnectorBatchActiveWorkerCount
                    ),
                    "PriorityFactorPreparationElapsedSeconds": (
                        monotonic()
                        - Context.ExteriorFactorPreparationStartedAt
                    ),
                    "ComponentFabricConstructionComplete": True,
                    "OwnershipSearchComplete": True,
                    "ImplicitForeignTransitDomainCount": 0,
                },
            ))
        CapacityRepairConstraint = getattr(
            Resources,
            "PhysicalComponentCapacityRepairConstraint",
            None,
        )
        if (
            CapacityRepairConstraint is not None
            and CapacityRepairConstraint.RepairLevel == "local-assembly"
        ):
            CapacityWitness = SelectPriorityDisjointCapacitySeams(
                Context,
                CapacityRepairConstraint,
            )
            Resources.PreparedPhysicalComponentCapacityRepairWitness = (
                CapacityWitness
            )
            if not CapacityWitness[0]:
                raise RoutingStageError(RoutingFailure(
                    Reason=(
                        RoutingFailureReason
                        .ComponentPortAssignmentUnsatisfiable
                    ),
                    Stage="PhysicalCapacityRepairPrecheck",
                    AffectedNets=CapacityRepairConstraint.Signals,
                    Detail=(
                        "the repaired placement still has no disjoint local "
                        "seam capacity for the complete symbolic core"
                    ),
                    Diagnostics={
                        "SymbolicCapacityPlacementFeedback": True,
                        "SymbolicCapacityProofFingerprint": (
                            CapacityRepairConstraint.SourceProofFingerprint
                        ),
                        "PlacementInterfacePressureSignals": list(
                            CapacityRepairConstraint.Signals
                        ),
                        "LocalCapacityCoreClause": [
                            list(Value) for Value in
                            CapacityRepairConstraint.ForcedSeamClasses
                        ],
                        "CapacityRepairConstraint": (
                            CapacityRepairConstraint.ToDictionary()
                        ),
                        "CapacityRepairAchievedSeamFingerprint": "",
                        "AvailableSeamClassesBySignal": [
                            [Signal, list(Seams)]
                            for Signal, Seams in CapacityWitness[3]
                        ],
                        "PriorityCapacityPrecheck": True,
                        "GlobalPlanningEntered": False,
                        "LocalCompilationEntered": False,
                    },
                ))
        RemainingSignals = PortSignals - PrioritySignals
        if RemainingSignals:
            PhaseStartedAt = monotonic()
            PreparePhysicalPortConnectorSearch(
                Context,
                RemainingSignals,
                Initialize=False,
            )
            RecordTiming("NativeConnectorPreparation", PhaseStartedAt)
            PhaseStartedAt = monotonic()
            BuildPhysicalPortLaneFactors(Context, RemainingSignals)
            RecordTiming("LaneFactorMaterialization", PhaseStartedAt)
    else:
        PhaseStartedAt = monotonic()
        PreparePhysicalPortConnectorSearch(Context)
        RecordTiming("NativeConnectorPreparation", PhaseStartedAt)
        PhaseStartedAt = monotonic()
        BuildPhysicalPortLaneFactors(Context)
        RecordTiming("LaneFactorMaterialization", PhaseStartedAt)
    PhaseStartedAt = monotonic()
    CertifyPhysicalPortFactors(Context)
    RecordTiming("FactorCertification", PhaseStartedAt)
    PhaseStartedAt = monotonic()
    CachePhysicalPortLocalFactors(Context)
    RecordTiming("LocalFactorPublication", PhaseStartedAt)
    return FinalizePhysicalPortPreparation(Context)

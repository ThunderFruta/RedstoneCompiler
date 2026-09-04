"""Physical boundary planning, assembly binding, and continuation domains."""

from __future__ import annotations

from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import product
from math import prod
import multiprocessing
import os
from time import monotonic
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping
from ....Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from ....Contracts.Component import ComponentRoutingProblem, ComponentRoutingSolveResult, PhysicalComponentAssemblyPlan, PhysicalComponentChannelReservation, PhysicalComponentPortReservation, PhysicalComponentSelectedLocalPortSupport, RoutedComponentNet, RoutedComponentTemplate
from ....Contracts.Core import Position3
from ....Contracts.PhysicalInterface import PhysicalComponentLocalFactorProjection, PhysicalComponentLocalFactorProjectionComparison, PhysicalComponentLocalFactorUnsatCertificate, PhysicalLocalPortPairProofRecord, PhysicalLocalPortPairSupportCertificate, PhysicalComponentSymbolicHigherOrderCertificate, PhysicalComponentSymbolicPortPairCertificate, PhysicalPortCorridorDomain, PhysicalPortCorridorFactor, PreparedPhysicalComponentAssembly, PreparedPhysicalComponentPortFactorDomain
from ....Constraints import BoundaryRelations
from ....Constraints.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint, ProjectPhysicalComponentSignalGlobalProfile
from ....Constraints.PhysicalClaims import ComponentClaimsConflict
from ....Resources.ResourceGraph import RoutingResourceClaims
from ....Runtime.Reliability import BuildStableFingerprint
from .InterfacePlanning import (
    BuildComponentCapacityGuide,
    ComponentCapacityGuide,
    ComponentCapacityGuideOption,
    ComponentInterfaceContract,
    ComponentPlanningResult,
    ComponentPlanningStatus,
    IterClosedComponentContracts,
    PlanClosedComponent,
    SolveComponentInterfaceCsp,
)

from ..Core import BuildCompleteComponentNetPortfolioStaticContext
from ..Symbolic.SymbolicState import _BuildPreparedComponentSymbolicNetStateContextFingerprint, BuildComponentSymbolicNetStateCacheKey, PrepareComponentSymbolicNetStateContext
from ..Symbolic.SymbolicWorkers import CompilePreparedComponentPhysicalFactorStateBatch, CompilePreparedComponentSymbolicNetStates
from .Portfolios import (
    BuildCompleteOpposingNetAccessContractDomain,
    BuildCompleteOpposingNetAccessRowContext,
    CompileCompleteComponentNetVariantPortfolio,
    CompileCompleteComponentNetVariantPortfolios,
    EvaluateCompleteOpposingNetAccessContractRow,
)
from ..Solving.Solver import MaterializeRoutedComponentTemplate, SolveComponentRoutingProblem, ValidateRoutedComponentHandoff

from ..Boundaries.Reservations import FinalizePhysicalComponentChannelReservations
from ..Proofs.Validation import BuildPhysicalPortApertureContractFingerprint, BuildPhysicalPortLocalContractFingerprint, ValidatePhysicalBoundaryPortHandoff, ValidatePhysicalExteriorFabricHandoff, _Fingerprint
def SelectPhysicalAssemblyGlobalBoundaryPorts(
    Plan: PhysicalComponentAssemblyPlan,
) -> tuple[Any, ...]:
    """Return the authoritative global-only port boundary for a plan.

    New physical plans publish ``GlobalBoundaryPorts`` before local support
    is selected.  The legacy composite port tuple remains a compatibility
    input for older serialized fixtures, but global planning must not inspect
    it when the closed boundary exists.
    """
    BoundaryPorts = tuple(getattr(Plan, "GlobalBoundaryPorts", ()) or ())
    Ports = BoundaryPorts or tuple(getattr(Plan, "Ports", ()) or ())
    Signals = tuple(str(Port.Signal) for Port in Ports)
    if len(set(Signals)) != len(Signals):
        raise ValueError("physical assembly contains duplicate global ports")
    return Ports


def BuildPhysicalAssemblyGlobalReuseFingerprint(
    Plan: PhysicalComponentAssemblyPlan,
) -> str:
    """Identify every immutable input to an already reserved global route."""
    return "global-assembly-reuse-v1:" + _Fingerprint((
        str(getattr(Plan, "PlacementFingerprint", "")),
        str(getattr(Plan, "ComponentGraphFingerprint", "")),
        str(getattr(Plan, "ResourceGraphFingerprint", "")),
        str(getattr(Plan, "TechnologyFingerprint", "")),
        tuple(getattr(Plan, "EnvelopeMinimum", ())),
        tuple(getattr(Plan, "EnvelopeMaximum", ())),
        str(getattr(Plan, "GlobalKeepoutFingerprint", "")),
        tuple(sorted(
            (
                Port.Signal,
                BuildPhysicalPortGlobalContractFingerprint(Port),
            )
            for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
        )),
        tuple(sorted(
            (
                Channel.Signal,
                int(Channel.Layer),
                tuple(sorted(Channel.GuideCells)),
                int(Channel.Capacity),
                tuple(sorted(Channel.FeedthroughComponentIds)),
                str(Channel.ReservationFingerprint),
            )
            for Channel in getattr(Plan, "PlanningChannels", ())
        )),
        tuple(sorted(
            (
                Contract.Signal,
                int(Contract.Capacity),
                tuple(sorted(Contract.EndpointPairs)),
                tuple(sorted(Contract.ReservedPathNodes)),
            )
            for Contract in getattr(Plan, "Feedthroughs", ())
        )),
    ))


def PhysicalAssemblyGlobalRouteCanBeRebound(
    PreviousPlan: PhysicalComponentAssemblyPlan,
    NextPlan: PhysicalComponentAssemblyPlan,
) -> bool:
    """Return whether a local-factor change preserves the global contract."""
    return (
        BuildPhysicalAssemblyGlobalReuseFingerprint(PreviousPlan)
        == BuildPhysicalAssemblyGlobalReuseFingerprint(NextPlan)
    )

def PhysicalAssemblyPlanViolatesRejectedApertureClauses(
    Plan: PhysicalComponentAssemblyPlan,
    RejectedClauses: Iterable[frozenset[tuple[str, str]]],
) -> bool:
    """Return whether one frozen plan contains a learned exact aperture cut."""
    SelectedKeys = frozenset(
        (
            str(Port.Signal),
            BuildPhysicalPortApertureContractFingerprint(Port),
        )
        for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
    )
    return any(
        frozenset(
            (str(Signal), str(Fingerprint))
            for Signal, Fingerprint in Clause
        ) <= SelectedKeys
        for Clause in RejectedClauses
        if Clause
    )


def PruneRetainedPhysicalGlobalPlansByRejectedApertureClauses(
    Frontier: Mapping[str, Any],
    RejectedClauses: Iterable[frozenset[tuple[str, str]]],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Remove retained continuations invalidated by monotone exact clauses."""
    Clauses = tuple(RejectedClauses)
    RejectedPlanFingerprints = tuple(sorted(
        str(PlanFingerprint)
        for PlanFingerprint, Entry in Frontier.items()
        if PhysicalAssemblyPlanViolatesRejectedApertureClauses(
            Entry.Assembly.Plan,
            Clauses,
        )
    ))
    RejectedSet = frozenset(RejectedPlanFingerprints)
    return (
        {
            str(PlanFingerprint): Entry
            for PlanFingerprint, Entry in Frontier.items()
            if str(PlanFingerprint) not in RejectedSet
        },
        RejectedPlanFingerprints,
    )


def BuildPhysicalComponentAssemblyChoiceFingerprint(
    Plan: PhysicalComponentAssemblyPlan,
) -> str:
    """Identify one exact port plus explicit-feedthrough assembly choice."""
    return "assembly-choice-v1:" + BuildStableFingerprint((
        str(getattr(Plan, "PlacementFingerprint", "")),
        str(getattr(Plan, "ComponentGraphFingerprint", "")),
        str(getattr(Plan, "ResourceGraphFingerprint", "")),
        str(getattr(Plan, "TechnologyFingerprint", "")),
        tuple(sorted(
            (
                Port.Signal,
                Port.ReservationFingerprint,
                BuildPhysicalPortApertureContractFingerprint(Port),
            )
            for Port in Plan.Ports
        )),
        tuple(sorted(
            (
                Feedthrough.Signal,
                Feedthrough.ReservationFingerprint,
                str(getattr(
                    Feedthrough,
                    "EndpointDomainFingerprint",
                    "",
                )),
                str(getattr(
                    Feedthrough,
                    "EndpointCandidateFingerprint",
                    "",
                )),
            )
            for Feedthrough in getattr(Plan, "Feedthroughs", ())
        )),
    ))


def BuildPhysicalRequestAperturePortNoGood(
    Plan: PhysicalComponentAssemblyPlan,
    RequestApertureNoGood: Any,
    *,
    SignalLocalRequestFactorProofComplete: bool = False,
    PortSolverCacheKey: str = "",
) -> frozenset[tuple[str, str]]:
    """Project an exact request-support proof onto its port determinants.

    The request factor is jointly determined by every external global port
    contract and by later candidate-generation controls.  A signal-local
    sibling proof can minimize aperture blockers, but it cannot remove those
    global determinants.  Its solver-domain key prevents the projected clause
    from escaping the immutable eligibility domain that produced the plan.
    """
    ProofKeys = frozenset(
        (str(Signal), str(Fingerprint))
        for Signal, Fingerprint in RequestApertureNoGood
    )
    RequestSignals = frozenset(
        Signal for Signal, Fingerprint in ProofKeys
        if Fingerprint.startswith("request-factor:")
    )
    ApertureSignals = frozenset(
        Signal for Signal, Fingerprint in ProofKeys
        if Fingerprint.startswith("aperture-factor:")
    )
    GlobalPorts = SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
    PortsBySignal = {Port.Signal: Port for Port in GlobalPorts}
    RequiredApertureSignals = RequestSignals | ApertureSignals
    if (
        len(RequestSignals) != 1
        or not ApertureSignals
        or not RequiredApertureSignals <= PortsBySignal.keys()
    ):
        return frozenset()
    UseScopedProjection = bool(
        SignalLocalRequestFactorProofComplete and PortSolverCacheKey
    )
    RequestSignal = next(iter(RequestSignals))
    if UseScopedProjection:
        # The certified signal-local request domain is frozen by the retained
        # eligibility domain.  Its selected global contract is the remaining
        # CSP determinant; local reservation representatives do not change
        # the certified exterior request domain.  A one-blocker starvation
        # proof therefore becomes a real binary clause instead of an
        # all-port assignment no-good.
        return frozenset((
            (
                RequestSignal,
                BuildPhysicalPortGlobalContractFingerprint(
                    PortsBySignal[RequestSignal]
                ),
            ),
            *(
                (
                    Signal,
                    BuildPhysicalPortApertureContractFingerprint(
                        PortsBySignal[Signal]
                    ),
                )
                for Signal in sorted(ApertureSignals)
            ),
        ))
    # Without the signal-local certificate, retain every selected global
    # determinant and do not project the request across sibling contracts.
    GlobalContractKeys = tuple(
        (
            Port.Signal,
            BuildPhysicalPortGlobalContractFingerprint(Port),
        )
        for Port in GlobalPorts
    )
    return frozenset((
        *GlobalContractKeys,
        *(
            (
                Signal,
                BuildPhysicalPortApertureContractFingerprint(
                    PortsBySignal[Signal]
                ),
            )
            for Signal in sorted(RequiredApertureSignals)
        ),
    ))


def BuildPhysicalComponentPortSolverCacheKey(
    DomainFingerprint: str,
) -> str:
    """Scope learned port clauses to one immutable eligibility domain."""
    return BuildStableFingerprint((
        "physical-component-port-solver-cache-v2",
        str(DomainFingerprint),
    ))


def MaterializePreparedPhysicalPortOptionDomains(
    Preparation: Any,
    Resources: Any,
    Signals: Any,
) -> dict[str, tuple[PhysicalComponentPortReservation, ...]]:
    """Materialize complete local-contract representatives from frozen factors.

    The factorized eligibility solver deliberately avoids constructing the
    full physical seam-option domain.  Post-admission local proof stages vary
    only the component-owned local contract, so reconstruct exactly one
    deterministic supported reservation for each distinct local contract in
    the requested signals.  Alternative global apertures for that same local
    contract are deliberately absent: they belong to global assembly planning,
    not to closed-component compilation.
    """
    DomainFingerprint = str(getattr(
        Preparation,
        "DomainFingerprint",
        "",
    ))
    if (
        Preparation is None
        or not getattr(Preparation, "Complete", False)
        or not DomainFingerprint
    ):
        raise ValueError(
            "a complete prepared physical factor domain is required"
        )
    PortSolverCacheKey = BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Cache = getattr(
        Resources,
        "PhysicalComponentFactorPortOptionDomainCache",
        None,
    )
    if Cache is None:
        Cache = {}
        Resources.PhysicalComponentFactorPortOptionDomainCache = Cache
    RequestedSignals = tuple(sorted({str(Value) for Value in Signals}))
    if all((PortSolverCacheKey, Signal) in Cache for Signal in RequestedSignals):
        return {
            Signal: Cache[(PortSolverCacheKey, Signal)]
            for Signal in RequestedSignals
        }
    ResourceGraph = getattr(Resources, "ResourceGraph", None)
    if ResourceGraph is None:
        raise ValueError(
            "materializing uncached prepared physical factors requires a "
            "resource graph"
        )
    LocalBySignal = dict(getattr(
        Preparation,
        "LocalAccessFactorsBySignal",
        (),
    ))
    ApertureBySignal = dict(getattr(
        Preparation,
        "ApertureFactorsBySignal",
        (),
    ))
    SupportBySignal = dict(getattr(
        Preparation,
        "LocalApertureSupportBySignal",
        (),
    ))
    Result = {}
    for Signal in RequestedSignals:
        CacheKey = (PortSolverCacheKey, Signal)
        Cached = Cache.get(CacheKey)
        if Cached is not None:
            Result[Signal] = Cached
            continue
        LocalByFingerprint = {
            Value.LocalAccessFingerprint: Value
            for Value in LocalBySignal.get(Signal, ())
        }
        ApertureByFingerprint = {
            Value.ApertureOptionFingerprint: Value
            for Value in ApertureBySignal.get(Signal, ())
        }
        OptionsByLocalContract = {}
        for Support in sorted(
            SupportBySignal.get(Signal, ()),
            key=lambda Value: (
                Value.ReservationFingerprint,
                Value.SupportFingerprint,
            ),
        ):
            Local = LocalByFingerprint.get(
                Support.LocalAccessFingerprint
            )
            Aperture = ApertureByFingerprint.get(
                Support.ApertureOptionFingerprint
            )
            if Local is None or Aperture is None:
                raise ValueError(
                    "prepared physical factor support references a missing "
                    "endpoint"
                )
            Option = PhysicalComponentPortReservation(
                Signal=Local.Signal,
                Direction=Local.Direction,
                OwnedTerminals=Local.OwnedTerminals,
                OwnedTerminalFingerprints=(
                    Local.OwnedTerminalFingerprints
                ),
                OwnedCandidateFingerprints=(
                    Local.OwnedCandidateFingerprints
                ),
                FabricDomainFingerprint=(
                    Local.FabricDomainFingerprint
                ),
                FabricAttachment=Local.FabricAttachment,
                Attachment=Aperture.Attachment,
                LocalPath=Local.LocalPath,
                GlobalPath=Aperture.GlobalPath,
                Claims=ResourceGraph.BuildRouteClaims(frozenset((
                    *Local.LocalPath,
                    *Aperture.GlobalPath,
                ))),
                LocalClaims=Local.LocalClaims,
                GlobalClaims=Aperture.GlobalClaims,
                OwnedAccessCandidates=Local.OwnedAccessCandidates,
                Capacity=Local.Capacity,
                ReservationFingerprint=Support.ReservationFingerprint,
            )
            if (
                Option.Signal != Signal
                or Aperture.Signal != Signal
                or Support.Signal != Signal
                or Local.Direction != Aperture.Direction
                or Local.Capacity != Aperture.Capacity
                or BuildPhysicalPortLocalContractFingerprint(Option)
                != Local.LocalContractFingerprint
                or BuildPhysicalPortGlobalContractFingerprint(Option)
                != Aperture.GlobalContractFingerprint
                or BuildPhysicalPortApertureContractFingerprint(Option)
                != Aperture.ApertureContractFingerprint
            ):
                raise ValueError(
                    "prepared physical factor materialization identity "
                    "mismatch"
                )
            LocalContractFingerprint = (
                BuildPhysicalPortLocalContractFingerprint(Option)
            )
            OptionsByLocalContract.setdefault(
                LocalContractFingerprint,
                Option,
            )
            # ReservationFingerprint is normalized to the component origin;
            # SupportFingerprint may include absolute aperture geometry.  Use
            # the normalized identity first so translating an otherwise
            # identical component cannot select a different local witness.
            # Keeping that first witness intentionally collapses globally
            # distinct apertures that expose the same component-local contract.
        Options = tuple(
            OptionsByLocalContract[Fingerprint]
            for Fingerprint in sorted(OptionsByLocalContract)
        )
        Cache[CacheKey] = Options
        Result[Signal] = Options
    return Result


def BuildPhysicalGlobalPlanDependencyFingerprint(
    Plan: PhysicalComponentAssemblyPlan,
    DependencySignals: Any,
) -> str:
    """Identify the exact physical contracts supporting a global cut.

    Keep this separate from the whole assembly-plan identity.  A global
    proof normally depends on only a subset of component ports, and the
    planner must be able to distinguish a repeated proof over that subset
    from a genuinely different physical contract.
    """
    PortsBySignal = {
        str(Port.Signal): Port
        for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
    }
    Signals = tuple(sorted({
        str(Signal) for Signal in DependencySignals if str(Signal)
    }))
    return "global-dependency-v2:" + _Fingerprint(tuple(
        (
            Signal,
            (
                BuildPhysicalPortApertureContractFingerprint(
                    PortsBySignal[Signal]
                )
                if Signal in PortsBySignal
                else "non-component-port"
            ),
        )
        for Signal in Signals
    ))


def BuildPhysicalGlobalPlanCutFamilyFingerprint(
    ConflictGraph: dict[str, object],
) -> str:
    """Identify stable conflict support independently of route variants."""

    def Signals(Key: str) -> tuple[str, ...]:
        Value = ConflictGraph.get(Key, ())
        if isinstance(Value, str):
            return (Value,) if Value else ()
        if not isinstance(Value, (tuple, list, set, frozenset)):
            return ()
        return tuple(sorted({str(Item) for Item in Value if str(Item)}))

    PairwiseEdges = tuple(sorted({
        tuple(sorted((str(Edge[0]), str(Edge[1]))))
        for Edge in ConflictGraph.get("PairwiseIncompatibleEdges", ())
        if isinstance(Edge, (tuple, list)) and len(Edge) == 2
    }))
    return "global-cut-family-v1:" + _Fingerprint((
        str(ConflictGraph.get("Classification", "")),
        str(ConflictGraph.get("FailureNet", "")),
        Signals("ConflictSignals"),
        Signals("NativeConflictSignals"),
        Signals("CongestionCutSignals"),
        Signals("NoCandidateSignals"),
        PairwiseEdges,
    ))

def ApplyPhysicalComponentAssemblyGlobalProfiles(
    Profiles: dict[str, Any],
    Problem: ComponentRoutingProblem,
    Plan: PhysicalComponentAssemblyPlan,
) -> dict[str, Any]:
    """Project whole-design nets onto the immutable component seams.

    This is the pre-compilation counterpart of routed-template profile
    projection.  It lets the authoritative global router select complete
    physical channels while the component interior remains closed and
    unrouted.
    """
    if Problem.PhysicalAssemblyPlan not in (None, Plan):
        raise ValueError(
            "component problem and physical assembly contracts differ"
        )
    GlobalBoundaryPorts = SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
    PortsBySignal = {
        Port.Signal: Port for Port in GlobalBoundaryPorts
    }
    OwnedTerminalsBySignal: dict[str, frozenset[Position3]] = {}
    for Domain in Problem.OwnedTerminalDomains:
        OwnedTerminalsBySignal[Domain.Signal] = frozenset((
            *OwnedTerminalsBySignal.get(Domain.Signal, frozenset()),
            Domain.Terminal,
        ))

    Result = dict(Profiles)
    for Signal in sorted(Problem.ComponentSignals):
        Profile = Result.get(Signal)
        if Profile is None:
            continue
        Projected = ProjectPhysicalComponentSignalGlobalProfile(
            Profile,
            OwnedTerminalsBySignal.get(Signal, frozenset()),
            PortsBySignal.get(Signal),
        )
        if Projected is None:
            Result.pop(Signal, None)
        else:
            Result[Signal] = Projected
    return Result


def BindPhysicalComponentAssemblyGlobalChannels(
    Assembly: PreparedPhysicalComponentAssembly,
    Routed: Any,
    ResourceGraph: Any,
) -> PreparedPhysicalComponentAssembly:
    """Freeze one authoritative global assignment into an assembly plan."""
    Assignment = getattr(Routed, "RoutingAssignment", None)
    if Assignment is None:
        raise ValueError(
            "physical assembly global planning produced no route assignment"
        )
    Candidates = dict(Assignment.SelectedCandidates)
    ExactPlannedSignals = SelectPhysicalComponentExactGlobalChannelSignals(
        Assembly.Plan
    )
    SelectedSignals = frozenset(map(str, Candidates))
    if SelectedSignals != ExactPlannedSignals:
        MissingSignals = tuple(sorted(
            ExactPlannedSignals - SelectedSignals
        ))
        UnexpectedSignals = tuple(sorted(
            SelectedSignals - ExactPlannedSignals
        ))
        raise ValueError(
            "physical assembly global assignment signal identity "
            "mismatch: "
            f"missing={MissingSignals}, unexpected={UnexpectedSignals}"
        )
    GlobalBoundaryPorts = SelectPhysicalAssemblyGlobalBoundaryPorts(
        Assembly.Plan
    )
    PortsBySignal = {
        Port.Signal: Port for Port in GlobalBoundaryPorts
    }
    MissingPortSignals = tuple(sorted(
        set(PortsBySignal) - set(Candidates)
    ))
    if MissingPortSignals:
        raise ValueError(
            "physical assembly global assignment omitted component ports: "
            f"{MissingPortSignals}"
        )
    ExistingChannels = {
        Channel.Signal: Channel
        for Channel in Assembly.Plan.PlanningChannels
    }
    ExactChannels = [
        Channel
        for Channel in Assembly.Plan.Channels
        if Channel.Signal not in ExactPlannedSignals
    ]
    for Signal, Candidate in sorted(Candidates.items()):
        Existing = ExistingChannels.get(Signal)
        CandidateFingerprint = _Fingerprint((
            Candidate.CandidateId,
            int(Candidate.Layer),
            tuple(sorted(Candidate.Guide)),
            tuple(sorted(Candidate.Nodes)),
            tuple(sorted(map(str, Candidate.Claims.ResourceIds))),
            Candidate.SourcePortalId,
            tuple(sorted(Candidate.TargetPortalIds.items())),
            tuple(Candidate.RepeaterWaypoints),
        ))
        ReservationFingerprint = _Fingerprint((
            "authoritative-exact-global-channel-v1",
            Signal,
            CandidateFingerprint,
        ))
        ExactChannels.append(PhysicalComponentChannelReservation(
            Signal=Signal,
            Layer=int(Candidate.Layer),
            GuideCells=tuple(sorted(Candidate.Guide)),
            ResourceIds=tuple(map(str, sorted(
                Candidate.Claims.ResourceIds,
                key=str,
            ))),
            Claims=Candidate.Claims,
            Capacity=(Existing.Capacity if Existing is not None else 1),
            FeedthroughComponentIds=(
                Existing.FeedthroughComponentIds
                if Existing is not None
                else ()
            ),
            ReservationFingerprint=ReservationFingerprint,
            ReservedPathNodes=tuple(sorted(Candidate.Nodes)),
            RouteCandidateId=Candidate.CandidateId,
            RouteCandidateFingerprint=CandidateFingerprint,
        ))
    ValidatedChannels = FinalizePhysicalComponentChannelReservations(
        tuple(ExactChannels),
        GlobalBoundaryPorts,
        ResourceGraph,
        MinimumPlacementY=Assembly.Plan.EnvelopeMinimum[1],
        EnvelopeMinimum=Assembly.Plan.EnvelopeMinimum,
        EnvelopeMaximum=Assembly.Plan.EnvelopeMaximum,
        KeepoutClaims=Assembly.Plan.KeepoutClaims,
        GlobalKeepoutNodes=frozenset(
            Assembly.Plan.GlobalKeepoutNodes
        ),
        PreservedChannelSignals=frozenset(),
    )
    PlanFingerprint = _Fingerprint((
        "physical-component-assembly-with-exact-global-channels-v1",
        Assembly.Plan.PlanFingerprint,
        tuple(
            Channel.ReservationFingerprint
            for Channel in ValidatedChannels
        ),
    ))
    Plan = replace(
        Assembly.Plan,
        PlanFingerprint=PlanFingerprint,
        Channels=ValidatedChannels,
        Corridors=tuple(
            Channel
            for Channel in Assembly.Plan.Corridors
            if Channel.Signal not in ExactPlannedSignals
        ),
    )
    Interface = replace(
        Assembly.Problem.Interface,
        PhysicalAssemblyPlanFingerprint=PlanFingerprint,
    )
    Problem = replace(
        Assembly.Problem,
        ProblemFingerprint=_Fingerprint((
            Assembly.Problem.ProblemFingerprint,
            PlanFingerprint,
        )),
        Interface=Interface,
        PhysicalAssemblyPlan=Plan,
        ReservedGlobalClaimsBySignal=tuple(
            (Channel.Signal, Channel.Claims)
            for Channel in ValidatedChannels
        ),
    )
    return replace(
        Assembly,
        Plan=Plan,
        Problem=Problem,
    )


def BindPhysicalComponentAssemblyLocalPortSupports(
    Assembly: PreparedPhysicalComponentAssembly,
    Preparation: PreparedPhysicalComponentPortFactorDomain | None = None,
    *,
    RequireFrozenGlobalChannels: bool = True,
) -> PreparedPhysicalComponentAssembly:
    """Join frozen global apertures to exact local support witnesses.

    This is intentionally a post-global operation.  The authoritative route
    first fixes boundary ports and exact channel claims; only then may the
    closed-component input select the certified local half of each seam.
    """
    FactorDomain = Preparation or Assembly.PortFactorDomain
    if FactorDomain is None or not FactorDomain.Complete:
        raise ValueError(
            "post-global local support binding requires a complete frozen "
            "physical port factor domain"
        )
    Plan = Assembly.Plan
    ValidatePhysicalExteriorFabricHandoff(Plan, FactorDomain)
    if RequireFrozenGlobalChannels and not Plan.Channels:
        raise ValueError(
            "local port supports cannot be selected before authoritative "
            "global channels are frozen"
        )
    BoundaryBySignal = {
        Port.Signal: Port
        for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
    }
    CompositeBySignal = {Port.Signal: Port for Port in Plan.Ports}
    LocalBySignal = dict(FactorDomain.LocalAccessFactorsBySignal)
    ApertureBySignal = dict(FactorDomain.ApertureFactorsBySignal)
    SupportBySignal = dict(FactorDomain.LocalApertureSupportBySignal)
    SelectedSupports = []
    for Signal in sorted(BoundaryBySignal):
        Boundary = BoundaryBySignal[Signal]
        Composite = CompositeBySignal.get(Signal)
        if Composite is None:
            raise ValueError(
                "post-global local support binding is missing a composite "
                f"port for {Signal}"
            )
        LocalContract = BuildPhysicalPortLocalContractFingerprint(Composite)
        CertifiedSupportReservationFingerprint = str(getattr(
            Composite,
            "CertifiedSupportReservationFingerprint",
            "",
        )) or Composite.ReservationFingerprint
        LocalFactors = tuple(
            Value for Value in LocalBySignal.get(Signal, ())
            if Value.LocalContractFingerprint == LocalContract
        )
        ApertureFactors = tuple(
            Value for Value in ApertureBySignal.get(Signal, ())
            if Value.GlobalContractFingerprint
            == Boundary.GlobalContractFingerprint
            and Value.ApertureContractFingerprint
            == Boundary.ApertureContractFingerprint
        )
        Matches = tuple(sorted(
            [(
                Support,
                Local,
            )
            for Local in LocalFactors
            for Aperture in ApertureFactors
            for Support in SupportBySignal.get(Signal, ())
            if Support.LocalAccessFingerprint
            == Local.LocalAccessFingerprint
            and Support.ApertureOptionFingerprint
            == Aperture.ApertureOptionFingerprint
            and Support.ReservationFingerprint
            == CertifiedSupportReservationFingerprint
            ],
            key=lambda Value: (
            Value[0].SupportFingerprint,
            Value[1].LocalAccessFingerprint,
            ),
        ))
        if not Matches:
            raise ValueError(
                "frozen global boundary has no exact certified local "
                f"support for {Signal}"
            )
        Support, Local = Matches[0]
        SelectedSupports.append(PhysicalComponentSelectedLocalPortSupport(
            Signal=Signal,
            BoundaryReservationFingerprint=(
                Boundary.ReservationFingerprint
            ),
            LocalContractFingerprint=LocalContract,
            LocalAccessFingerprint=Local.LocalAccessFingerprint,
            SupportFingerprint=Support.SupportFingerprint,
        ))
    SelectedSupportsTuple = tuple(SelectedSupports)
    PlanFingerprint = _Fingerprint((
        "physical-component-assembly-with-local-supports-v1",
        Plan.PlanFingerprint,
        tuple(
            Support.SupportFingerprint
            for Support in SelectedSupportsTuple
        ),
    ))
    BoundPlan = replace(
        Plan,
        PlanFingerprint=PlanFingerprint,
        SelectedLocalPortSupports=SelectedSupportsTuple,
    )
    Interface = replace(
        Assembly.Problem.Interface,
        PhysicalAssemblyPlanFingerprint=PlanFingerprint,
    )
    Problem = replace(
        Assembly.Problem,
        ProblemFingerprint=_Fingerprint((
            Assembly.Problem.ProblemFingerprint,
            PlanFingerprint,
        )),
        Interface=Interface,
        PhysicalAssemblyPlan=BoundPlan,
    )
    ValidatePhysicalBoundaryPortHandoff(
        Problem,
        BoundPlan,
        RequireSelectedLocalSupports=True,
    )
    return replace(Assembly, Plan=BoundPlan, Problem=Problem)


def FinalizePreparedPhysicalComponentAssemblyCorridors(
    Assembly: PreparedPhysicalComponentAssembly,
    ResourceGraph: Any,
) -> PreparedPhysicalComponentAssembly:
    """Freeze assigned portals and capacity-aware corridors before local solve."""
    PassageSignals = SelectPhysicalComponentExactGlobalChannelSignals(
        Assembly.Plan
    )
    PlanningChannels = Assembly.Plan.PlanningChannels
    ChannelSignals = frozenset(
        Channel.Signal for Channel in PlanningChannels
    )
    FinalizedChannels = FinalizePhysicalComponentChannelReservations(
        PlanningChannels,
        SelectPhysicalAssemblyGlobalBoundaryPorts(Assembly.Plan),
        ResourceGraph,
        MinimumPlacementY=Assembly.Plan.EnvelopeMinimum[1],
        EnvelopeMinimum=Assembly.Plan.EnvelopeMinimum,
        EnvelopeMaximum=Assembly.Plan.EnvelopeMaximum,
        KeepoutClaims=Assembly.Plan.KeepoutClaims,
        GlobalKeepoutNodes=frozenset(
            Assembly.Plan.GlobalKeepoutNodes
        ),
        PreservedChannelSignals=ChannelSignals - PassageSignals,
    )
    PlanFingerprint = _Fingerprint((
        "physical-component-assembly-with-reserved-corridors-v1",
        Assembly.Plan.PlanFingerprint,
        tuple(
            Channel.ReservationFingerprint
            for Channel in FinalizedChannels
        ),
    ))
    Plan = replace(
        Assembly.Plan,
        PlanFingerprint=PlanFingerprint,
        Channels=FinalizedChannels,
        Corridors=(),
    )
    Interface = replace(
        Assembly.Problem.Interface,
        PhysicalAssemblyPlanFingerprint=PlanFingerprint,
    )
    Problem = replace(
        Assembly.Problem,
        ProblemFingerprint=_Fingerprint((
            Assembly.Problem.ProblemFingerprint,
            PlanFingerprint,
        )),
        Interface=Interface,
        PhysicalAssemblyPlan=Plan,
        ReservedGlobalClaimsBySignal=tuple(
            (Channel.Signal, Channel.Claims)
            for Channel in FinalizedChannels
        ),
    )
    return replace(Assembly, Plan=Plan, Problem=Problem)


def SelectPhysicalComponentExactGlobalChannelSignals(
    Plan: PhysicalComponentAssemblyPlan,
) -> frozenset[str]:
    """Select declared passages that require pre-local detailed ownership."""
    Channels = tuple(Plan.PlanningChannels)
    ExactSignals = {
        Port.Signal
        for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
    }
    ExactSignals.update(getattr(
        Plan,
        "DeclaredFeedthroughSignals",
        frozenset(
            Value.Signal
            for Value in getattr(Plan, "Feedthroughs", ())
        ),
    ))
    ExactSignals.update(
        Channel.Signal
        for Channel in Channels
        if Channel.FeedthroughComponentIds
    )
    return frozenset(ExactSignals)


def PreparePhysicalComponentGlobalPlanningPlacement(
    Placed: Any,
    Problem: ComponentRoutingProblem,
    Plan: PhysicalComponentAssemblyPlan,
    *,
    LocalSupportTemplate: RoutedComponentTemplate | None = None,
) -> Any:
    """Expose selected local support while global channels are selected."""
    if LocalSupportTemplate is not None:
        if (
            LocalSupportTemplate.ProblemFingerprint
            != Problem.ProblemFingerprint
            or LocalSupportTemplate.PlacementFingerprint
            != Problem.PlacementFingerprint
            or LocalSupportTemplate.FabricFingerprint
            != Problem.Fabric.FabricFingerprint
            or LocalSupportTemplate.InterfaceFingerprint
            != Plan.InterfaceFingerprint
        ):
            raise ValueError(
                "local support template and physical assembly identities differ"
            )
        Placed = MaterializeRoutedComponentTemplate(
            Placed,
            LocalSupportTemplate,
        )
    OwnedClaimKeys = frozenset(
        (Claim.Signal, frozenset(Claim.Nodes))
        for Claim in Problem.LocalClaims
    )
    RetainedClaims = tuple(
        Claim
        for Claim in (getattr(Placed, "LocalRouteClaims", ()) or ())
        if (Claim.Signal, frozenset(Claim.Nodes)) not in OwnedClaimKeys
    )
    Diagnostics = dict(
        getattr(Placed, "LocalRouteDiagnostics", {}) or {}
    )
    Diagnostics["__PhysicalComponentGlobalPlanning__"] = {
        "PlanFingerprint": Plan.PlanFingerprint,
        "RemovedLocalClaimCount": (
            len(getattr(Placed, "LocalRouteClaims", ()) or ())
            - len(RetainedClaims)
        ),
        "SelectedLocalSupportExposed": (
            LocalSupportTemplate is not None
        ),
        "SelectedLocalSupportProofFingerprint": (
            LocalSupportTemplate.ProofFingerprint
            if LocalSupportTemplate is not None
            else ""
        ),
        "SelectedLocalSupportClaimsFingerprint": (
            LocalSupportTemplate.ClaimsFingerprint
            if LocalSupportTemplate is not None
            else ""
        ),
        "StageOrder": list(Plan.StageOrder),
        "ImplicitForeignTransitDomainCount": 0,
    }
    return replace(
        Placed,
        LocalRouteClaims=RetainedClaims,
        LocalRouteDiagnostics=Diagnostics,
        ClusterBoundaryLeaseRequests=(),
        CompleteClusterInterfaceAccess=False,
        InterClusterRoutingChannel=None,
        RoutedComponentTemplates=(),
    )


def ClassifyPhysicalComponentGlobalPlanningFailure(
    Failure: RoutingFailure,
    Plan: PhysicalComponentAssemblyPlan,
    *,
    DeadlineExpired: bool,
) -> RoutingFailure:
    """Give pre-compilation global planning its own typed result."""
    Diagnostics = dict(Failure.Diagnostics or {})
    MandatoryProof = Diagnostics.get("MandatoryAccessProof", {})
    MandatoryProofKind = (
        str(MandatoryProof.get("Kind", ""))
        if isinstance(MandatoryProof, dict)
        else ""
    )
    MandatorySignalProofs = tuple(
        MandatoryProof.get("SignalProofs", ())
        if isinstance(MandatoryProof, dict)
        else ()
    )
    NetWidePortalProofIdentityMatches = bool(
        MandatoryProofKind
        != "generated-net-wide-portal-tuple-domain-exhausted"
        or (
            MandatorySignalProofs
            and all(
                isinstance(Value, dict)
                and Value.get("PortalDomainCertificateFingerprint")
                and Value.get("PhysicalAssemblyPlanFingerprint")
                == Plan.PlanFingerprint
                and Value.get("ResourceGraphFingerprint")
                == Plan.ResourceGraphFingerprint
                and Value.get("TechnologyFingerprint")
                == Plan.TechnologyFingerprint
                and Value.get("PlacementFingerprint")
                == Plan.PlacementFingerprint
                and Value.get("InterfaceFingerprint")
                == Plan.InterfaceFingerprint
                and Value.get("SeamFingerprint")
                and Value.get("PortalRequestDomainFingerprint")
                and Value.get("ExactAttachmentValidationFingerprint")
                for Value in MandatorySignalProofs
            )
        )
    )
    AmbiguousFixedPortalProof = bool(
        MandatoryProofKind == "generated-fixed-portal-domain-exhausted"
        and not (
            MandatoryProof.get("PortalTupleDomainComplete", False)
            and MandatoryProof.get("ProofScope")
            == "complete-portal-tuple-domain"
        )
    )
    CompleteCapacityProof = bool(
        not DeadlineExpired
        and not AmbiguousFixedPortalProof
        and NetWidePortalProofIdentityMatches
        and (
            Diagnostics.get("GlobalPlanDomainComplete", False)
            or
            Diagnostics.get("CompleteAssignmentCutProof", False)
            or (
                isinstance(MandatoryProof, dict)
                and MandatoryProof.get("Complete", False)
                and not MandatoryProof.get("BudgetExhausted", False)
                and not MandatoryProof.get("DeadlineExceeded", False)
            )
        )
    )
    UnderlyingConflictGraph = Diagnostics.get("ConflictGraph", {})
    if not isinstance(UnderlyingConflictGraph, dict):
        UnderlyingConflictGraph = {}
    else:
        UnderlyingConflictGraph = dict(UnderlyingConflictGraph)
    if not UnderlyingConflictGraph and Failure.AffectedNets:
        UnderlyingConflictGraph = {
            "Classification": (
                "candidate-starvation-placement-conflict"
                if Failure.Stage
                == "PhysicalComponentGlobalCandidateDomain"
                else "physical-component-global-capacity-cut"
            ),
            "ConflictSignals": list(Failure.AffectedNets),
            "NoCandidateSignals": (
                list(Failure.AffectedNets)
                if Failure.Stage
                == "PhysicalComponentGlobalCandidateDomain"
                else []
            ),
            "RelocationSignals": list(Failure.AffectedNets),
            "PriorityRelocationSignals": list(Failure.AffectedNets),
            "CompleteAssignmentCutProof": CompleteCapacityProof,
            "IndependentEmptyCandidateDomainSignals": list(
                Diagnostics.get(
                    "IndependentEmptyCandidateDomainSignals",
                    (),
                )
                or ()
            ),
        }

    def DependencySignals(Value: object) -> tuple[str, ...]:
        if isinstance(Value, str):
            return (Value,) if Value else ()
        if not isinstance(Value, (tuple, list, set, frozenset)):
            return ()
        return tuple(str(Signal) for Signal in Value if str(Signal))

    ReportedAssemblyDependencySignals = frozenset({
        *(str(Signal) for Signal in Failure.AffectedNets),
        *DependencySignals(UnderlyingConflictGraph.get(
            "ConflictSignals",
            (),
        )),
        *DependencySignals(UnderlyingConflictGraph.get(
            "CongestionCutSignals",
            (),
        )),
    })
    HigherOrderPortReservationNoGoodSignals = frozenset(
        str(Signal)
        for Signal in (
            Diagnostics.get(
                "HigherOrderPortReservationNoGoodSignals",
                (),
            )
            or ()
        )
        if str(Signal)
    )
    HasCertifiedHigherOrderDependencyCore = bool(
        Diagnostics.get(
            "HigherOrderPortReservationNoGoodProofComplete",
            False,
        )
        and len(HigherOrderPortReservationNoGoodSignals) >= 2
        and HigherOrderPortReservationNoGoodSignals
        <= ReportedAssemblyDependencySignals
    )
    AssemblyDependencySignals = tuple(sorted(
        HigherOrderPortReservationNoGoodSignals
        if HasCertifiedHigherOrderDependencyCore
        else ReportedAssemblyDependencySignals
    ))
    if AssemblyDependencySignals:
        # Preserve all signals on which the proof depends in the authoritative
        # graph.  Some exact cuts carry their full support only in
        # CongestionCutSignals, while AffectedNets names the native dead end.
        UnderlyingConflictGraph["ConflictSignals"] = list(
            AssemblyDependencySignals
        )
    AssemblyPortSignals = frozenset(
        Port.Signal
        for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
    )
    AssemblyPlanDependentPortSignals = tuple(sorted(
        frozenset(AssemblyDependencySignals) & AssemblyPortSignals
    ))
    MandatoryAccessProof = Diagnostics.get("MandatoryAccessProof", {})
    MandatoryProofKind = (
        str(MandatoryAccessProof.get("Kind", ""))
        if isinstance(MandatoryAccessProof, dict)
        else ""
    )
    MandatoryConflictClassification = str(
        UnderlyingConflictGraph.get("Classification", "")
    )
    HasMandatoryProofClassification = bool(
        MandatoryProofKind in {
            "generated-fixed-portal-domain-exhausted",
            "generated-net-wide-portal-tuple-domain-exhausted",
        }
        or MandatoryConflictClassification in {
            "mandatory-boundary-capacity-cut",
            "mandatory-access-self-conflict",
        }
    )
    PairwiseDependencySignals = frozenset(
        str(Signal)
        for Edge in UnderlyingConflictGraph.get(
            "PairwiseIncompatibleEdges",
            (),
        )
        if isinstance(Edge, (tuple, list)) and len(Edge) == 2
        for Signal in Edge
    )
    CertifiedPairwiseEdges = frozenset(
        tuple(sorted((str(Edge[0]), str(Edge[1]))))
        for Edge in (
            Diagnostics.get(
                "PairwisePortReservationNoGoodEdges",
                (),
            )
            or ()
        )
        if (
            isinstance(Edge, (tuple, list))
            and len(Edge) == 2
            and str(Edge[0]) != str(Edge[1])
        )
    )
    ReportedPairwiseEdges = frozenset(
        tuple(sorted((str(Edge[0]), str(Edge[1]))))
        for Edge in UnderlyingConflictGraph.get(
            "PairwiseIncompatibleEdges",
            (),
        )
        if (
            isinstance(Edge, (tuple, list))
            and len(Edge) == 2
            and str(Edge[0]) != str(Edge[1])
        )
    )
    MandatoryPairDependencyIdentityComplete = bool(
        MandatoryConflictClassification
        != "mandatory-boundary-capacity-cut"
        or (
            Diagnostics.get(
                "PairwisePortReservationNoGoodProofComplete",
                False,
            )
            and CertifiedPairwiseEdges
            and CertifiedPairwiseEdges == ReportedPairwiseEdges
            and frozenset(
                Signal
                for Edge in CertifiedPairwiseEdges
                for Signal in Edge
            )
            == frozenset(AssemblyDependencySignals)
        )
    )
    HigherOrderPortReservationNoGoodProofComplete = bool(
        CompleteCapacityProof
        and HasCertifiedHigherOrderDependencyCore
        and len(HigherOrderPortReservationNoGoodSignals) >= 2
        and HigherOrderPortReservationNoGoodSignals
        == frozenset(AssemblyDependencySignals)
        and HigherOrderPortReservationNoGoodSignals
        <= AssemblyPortSignals
    )
    AssemblyPlanDependencyIdentityComplete = bool(
        CompleteCapacityProof
        and (
            HigherOrderPortReservationNoGoodProofComplete
            or (
                isinstance(MandatoryAccessProof, dict)
                and MandatoryAccessProof.get("Complete", False)
                and not MandatoryAccessProof.get(
                    "BudgetExhausted",
                    False,
                )
                and not MandatoryAccessProof.get(
                    "DeadlineExceeded",
                    False,
                )
                and HasMandatoryProofClassification
                and MandatoryPairDependencyIdentityComplete
                and PairwiseDependencySignals
                and PairwiseDependencySignals
                == frozenset(AssemblyDependencySignals)
                and frozenset(AssemblyDependencySignals)
                <= AssemblyPortSignals
            )
        )
    )
    PlanIndependentMandatoryCut = bool(
        CompleteCapacityProof
        and isinstance(MandatoryAccessProof, dict)
        and HasMandatoryProofClassification
        and MandatoryAccessProof.get("Complete", False)
        and not MandatoryAccessProof.get("BudgetExhausted", False)
        and not MandatoryAccessProof.get("DeadlineExceeded", False)
        and not (
            frozenset(AssemblyDependencySignals) & AssemblyPortSignals
        )
    )
    PlanIndependentGlobalCut = bool(
        CompleteCapacityProof
        and not (
            frozenset(AssemblyDependencySignals) & AssemblyPortSignals
        )
    )
    GlobalPlanDependencyFingerprint = (
        BuildPhysicalGlobalPlanDependencyFingerprint(
            Plan,
            AssemblyDependencySignals,
        )
    )
    GlobalPlanCutFamilyFingerprint = (
        BuildPhysicalGlobalPlanCutFamilyFingerprint(
            UnderlyingConflictGraph
        )
    )
    GlobalPlanProofFingerprint = "global-proof-v1:" + _Fingerprint((
        CompleteCapacityProof,
        GlobalPlanDependencyFingerprint,
        GlobalPlanCutFamilyFingerprint,
        str(Diagnostics.get(
            "CandidateFingerprint",
            Diagnostics.get("CandidateDomainFingerprint", ""),
        )),
        str(Diagnostics.get("ConflictFingerprint", "")),
    ))
    RequestApertureFactorNoGood = tuple(
        (str(Key[0]), str(Key[1]))
        for Key in (
            Diagnostics.get("RequestApertureFactorNoGood", ()) or ()
        )
        if isinstance(Key, (tuple, list)) and len(Key) == 2
    )
    RequestApertureFactorProofComplete = bool(
        CompleteCapacityProof
        and RequestApertureFactorNoGood
        and (
            Diagnostics.get(
                "RequestApertureFactorProofComplete",
                False,
            )
            or Diagnostics.get(
                "RequestApertureFactorProofReused",
                False,
            )
        )
    )
    RequestAperturePortNoGood = tuple(
        (str(Key[0]), str(Key[1]))
        for Key in (
            Diagnostics.get("RequestAperturePortNoGood", ()) or ()
        )
        if isinstance(Key, (tuple, list)) and len(Key) == 2
    )
    return RoutingFailure(
        Reason=(
            RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
            if CompleteCapacityProof
            else RoutingFailureReason.PhysicalComponentAssemblyIncomplete
        ),
        Stage=(
            "PhysicalComponentGlobalChannelUnsatisfiable"
            if CompleteCapacityProof
            else "PhysicalComponentGlobalPlanningIncomplete"
        ),
        AffectedNets=AssemblyDependencySignals,
        Locations=Failure.Locations,
        Resources=Failure.Resources,
        Detail=(
            "the complete authoritative global channel domain is "
            "unsatisfiable for this physical assembly placement"
            if CompleteCapacityProof
            else "authoritative global channel planning did not complete"
        ),
        RepairActions=(),
        Diagnostics={
            "PhysicalAssemblyPlanFingerprint": Plan.PlanFingerprint,
            "GlobalPlanDomainComplete": CompleteCapacityProof,
            "CompleteAssignmentCutProof": CompleteCapacityProof,
            "IndependentEmptyCandidateDomainSignals": list(
                Diagnostics.get(
                    "IndependentEmptyCandidateDomainSignals",
                    (),
                )
                or ()
            ),
            "AmbiguousFixedPortalProofRejected": (
                AmbiguousFixedPortalProof
            ),
            "ConflictGraph": UnderlyingConflictGraph,
            "CandidateFingerprint": Diagnostics.get(
                "CandidateFingerprint",
                Diagnostics.get("CandidateDomainFingerprint", ""),
            ),
            "ConflictFingerprint": Diagnostics.get(
                "ConflictFingerprint",
                "",
            ),
            "UnderlyingFailure": Failure.ToDictionary(),
            "FrozenPostClosurePortalHandoff": dict(
                Diagnostics.get(
                    "FrozenPostClosurePortalHandoff",
                    {},
                )
                if isinstance(
                    Diagnostics.get(
                        "FrozenPostClosurePortalHandoff",
                        {},
                    ),
                    Mapping,
                )
                else {}
            ),
            "ComponentFabricConstructionComplete": True,
            "OwnershipSearchComplete": CompleteCapacityProof,
            "ImplicitForeignTransitDomainCount": 0,
            "BroadFallbackAllowed": False,
            "ExecutableLegacyRepairCascade": False,
            "AssemblyPlanReassignmentAllowed": (
                not PlanIndependentGlobalCut
            ),
            "PlanIndependentMandatoryCut": (
                PlanIndependentMandatoryCut
            ),
            "PlanIndependentGlobalCut": PlanIndependentGlobalCut,
            "AssemblyPlanDependencySignals": list(
                AssemblyDependencySignals
            ),
            "AssemblyPlanDependentPortSignals": list(
                AssemblyPlanDependentPortSignals
            ),
            "AssemblyPlanDependencyIdentityComplete": (
                AssemblyPlanDependencyIdentityComplete
            ),
            "MandatoryPairDependencyIdentityComplete": (
                MandatoryPairDependencyIdentityComplete
            ),
            "HigherOrderPortReservationNoGoodProofComplete": (
                HigherOrderPortReservationNoGoodProofComplete
            ),
            "HigherOrderPortReservationNoGoodSignals": sorted(
                HigherOrderPortReservationNoGoodSignals
            ),
            "HigherOrderPortReservationNoGoodCandidateCounts": dict(
                sorted(
                    (
                        str(Signal),
                        int(Count),
                    )
                    for Signal, Count in dict(
                        Diagnostics.get(
                            "HigherOrderPortReservationNoGoodCandidateCounts",
                            {},
                        )
                        or {}
                    ).items()
                    if str(Signal)
                )
            ),
            "GlobalPlanDependencyFingerprint": (
                GlobalPlanDependencyFingerprint
            ),
            "GlobalPlanCutFamilyFingerprint": (
                GlobalPlanCutFamilyFingerprint
            ),
            "GlobalPlanProofFingerprint": GlobalPlanProofFingerprint,
            "PairwisePortReservationNoGoodProofComplete": bool(
                Diagnostics.get(
                    "PairwisePortReservationNoGoodProofComplete",
                    False,
                )
            ),
            "PairwisePortReservationNoGoodEdges": list(
                Diagnostics.get(
                    "PairwisePortReservationNoGoodEdges",
                    (),
                )
                or ()
            ),
            "AssemblyPlanFeedthroughIndependentProofComplete": bool(
                CompleteCapacityProof
                and Diagnostics.get(
                    "AssemblyPlanFeedthroughIndependentProofComplete",
                    False,
                )
            ),
            "RequestApertureFactorProofComplete": (
                RequestApertureFactorProofComplete
            ),
            "RequestApertureFactorNoGood": [
                list(Key) for Key in RequestApertureFactorNoGood
            ],
            "RequestAperturePortNoGood": [
                list(Key) for Key in RequestAperturePortNoGood
            ],
            "SignalLocalRequestFactorProofComplete": bool(
                Diagnostics.get(
                    "SignalLocalRequestFactorProofComplete",
                    False,
                )
            ),
            "UnderlyingEscalationHistory": list(
                Diagnostics.get("EscalationHistory", ())
            ),
        },
    )


def AdvancePhysicalComponentBoundaryTraversal(
    Resources: Any,
    Signals: Iterable[str],
    *,
    FocusSignal: str = "",
) -> dict[str, object]:
    """Rotate a proof-neutral CSP branch hint and invalidate stale stacks."""
    OrderedSignals = tuple(sorted(frozenset(map(str, Signals))))
    if OrderedSignals:
        RequestedFocusSignal = str(FocusSignal)
        TraversalCursor = int(getattr(
            Resources,
            "PhysicalComponentBoundaryTraversalCursor",
            0,
        )) % len(OrderedSignals)
        FocusSignal = (
            RequestedFocusSignal
            if RequestedFocusSignal in OrderedSignals
            else OrderedSignals[TraversalCursor]
        )
        PrioritySignals = tuple(
            Signal for Signal in OrderedSignals if Signal != FocusSignal
        ) + (FocusSignal,)
        if not RequestedFocusSignal:
            Resources.PhysicalComponentBoundaryTraversalCursor = (
                TraversalCursor + 1
            ) % len(OrderedSignals)
    else:
        FocusSignal = ""
        PrioritySignals = ()
    Resources.PhysicalComponentBoundaryTraversalPrioritySignals = (
        PrioritySignals
    )
    Resources.PhysicalComponentBoundaryTraversalEpoch = int(getattr(
        Resources,
        "PhysicalComponentBoundaryTraversalEpoch",
        0,
    )) + 1
    BoundaryIteratorCache = getattr(
        Resources,
        "PhysicalComponentBoundaryAssignmentIteratorCache",
        None,
    )
    if BoundaryIteratorCache is not None:
        BoundaryIteratorCache.clear()
    return {
        "BoundaryTraversalPrioritySignals": list(PrioritySignals),
        "BoundaryTraversalFocusSignal": FocusSignal,
        "BoundaryTraversalEpoch": int(
            Resources.PhysicalComponentBoundaryTraversalEpoch
        ),
    }


def BuildPhysicalComponentAssemblyPlanDomainFingerprint(
    PreparedDomainFingerprint: str,
    DeferLocalCompositeSelection: bool,
) -> str:
    """Identify one immutable placed physical assembly search domain."""
    return "physical-assembly-plan-domain-v1:" + _Fingerprint((
        str(PreparedDomainFingerprint),
        bool(DeferLocalCompositeSelection),
    ))


def PreservePhysicalComponentAssemblyPlanDomainContinuation(
    Resources: Any,
) -> dict[str, object]:
    """Publish a monotonic clause epoch without replaying the live frontier.

    The boundary iterator yields each assignment once.  Its consumer checks
    every yielded aperture tuple against the current monotonic clause set, so
    retaining the suspended cursor is both sound and necessary to avoid
    revisiting rejected partial assignments.  Clause epochs remain explicit
    diagnostics; only an explicit traversal change invalidates the cursor.
    """
    DomainFingerprint = str(getattr(
        Resources,
        "PhysicalComponentAssemblyPlanDomainFingerprint",
        "",
    ))
    if not DomainFingerprint:
        Preparation = getattr(
            Resources,
            "PreparedPhysicalComponentPortFactorDomain",
            None,
        )
        DomainFingerprint = (
            BuildPhysicalComponentAssemblyPlanDomainFingerprint(
                str(getattr(Preparation, "DomainFingerprint", "")),
                bool(getattr(
                    Resources,
                    "PhysicalComponentDeferLocalCompositeSelection",
                    True,
                )),
            )
        )
        Resources.PhysicalComponentAssemblyPlanDomainFingerprint = (
            DomainFingerprint
        )
    ClauseFingerprint = _Fingerprint((
        tuple(sorted(
            tuple(sorted(
                (str(Signal), str(Fingerprint))
                for Signal, Fingerprint in Clause
            ))
            for Clause in getattr(
                Resources,
                "RejectedPhysicalComponentPortReservationSets",
                (),
            )
        )),
        tuple(sorted(
            (
                str(Signal),
                tuple(sorted(map(str, Fingerprints))),
            )
            for Signal, Fingerprints in getattr(
                Resources,
                "RejectedPhysicalComponentPortReservationsBySignal",
                {},
            ).items()
        )),
        tuple(sorted(map(str, getattr(
            Resources,
            "RejectedPhysicalComponentPortAssignmentFingerprints",
            (),
        )))),
        tuple(sorted(map(str, getattr(
            Resources,
            "RejectedPhysicalComponentAssemblyChoiceFingerprints",
            (),
        )))),
        tuple(sorted(map(str, getattr(
            Resources,
            "RejectedPhysicalComponentAssemblyPlanFingerprints",
            (),
        )))),
        tuple(sorted(
            tuple(sorted(
                (str(Signal), str(CandidateId))
                for Signal, CandidateId in CandidateSet
            ))
            for CandidateSet in getattr(
                Resources,
                "ForbiddenPhysicalComponentGlobalCandidateSets",
                (),
            )
        )),
    ))
    States = getattr(
        Resources,
        "PhysicalComponentAssemblyPlanClauseStateByDomain",
        None,
    )
    if States is None:
        States = {}
        Resources.PhysicalComponentAssemblyPlanClauseStateByDomain = States
    PriorFingerprint, PriorEpoch = States.get(
        DomainFingerprint,
        ("", 0),
    )
    ClauseEpoch = (
        PriorEpoch + 1
        if ClauseFingerprint != PriorFingerprint
        else PriorEpoch
    )
    States[DomainFingerprint] = (ClauseFingerprint, ClauseEpoch)
    ClauseChanged = bool(ClauseFingerprint != PriorFingerprint)
    return {
        "AssemblyPlanDomainFingerprint": DomainFingerprint,
        "AssemblyPlanDomainClauseFingerprint": ClauseFingerprint,
        "AssemblyPlanDomainClauseEpoch": ClauseEpoch,
        "BoundaryTraversalEpoch": int(getattr(
            Resources,
            "PhysicalComponentBoundaryTraversalEpoch",
            0,
        )),
        "BoundaryTraversalPrioritySignals": list(getattr(
            Resources,
            "PhysicalComponentBoundaryTraversalPrioritySignals",
            (),
        )),
        "BoundaryTraversalFocusSignal": "",
        "BoundaryIteratorCacheCleared": False,
        "BoundaryIteratorContinuationPreserved": True,
    }

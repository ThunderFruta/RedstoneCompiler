"""Closed-component compilation and authoritative global assembly stage."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import product
from math import prod
from time import monotonic
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping

from .ComponentRouter import (
    _BuildPreparedComponentSymbolicNetStateContextFingerprint,
    BuildComponentSymbolicNetStateCacheKey,
    BuildCompleteComponentNetPortfolioStaticContext,
    BuildCompleteOpposingNetAccessContractDomain,
    BuildCompleteOpposingNetAccessRowContext,
    CompileCompleteComponentNetVariantPortfolio,
    CompileCompleteComponentNetVariantPortfolios,
    CompilePreparedComponentSymbolicNetStates,
    CompilePreparedComponentPhysicalFactorStateBatch,
    ComponentClaimsConflict,
    EvaluateCompleteOpposingNetAccessContractRow,
    MaterializeRoutedComponentTemplate,
    PrepareComponentSymbolicNetStateContext,
    SolveComponentRoutingProblem,
    ValidateRoutedComponentHandoff,
)
from .Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from .Models import (
    ComponentRoutingProblem,
    ComponentRoutingSolveResult,
    PhysicalComponentAssemblyPlan,
    PhysicalComponentChannelReservation,
    PhysicalComponentLocalFactorProjection,
    PhysicalComponentLocalFactorProjectionComparison,
    PhysicalComponentLocalFactorUnsatCertificate,
    PhysicalLocalPortPairProofRecord,
    PhysicalLocalPortPairSupportCertificate,
    PhysicalComponentPortReservation,
    PhysicalComponentSelectedLocalPortSupport,
    PhysicalComponentSymbolicHigherOrderCertificate,
    PhysicalComponentSymbolicPortPairCertificate,
    PhysicalPortCorridorDomain,
    PhysicalPortCorridorFactor,
    PreparedPhysicalComponentAssembly,
    PreparedPhysicalComponentPortFactorDomain,
    Position3,
    RoutedComponentNet,
    RoutedComponentTemplate,
)
from .ResourceGraph import RoutingResourceClaims
from .Reliability import BuildStableFingerprint
from .ComponentPlanning import (
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


_CompletedComponentTemplateCache: dict[
    str,
    tuple[
        Position3,
        RoutedComponentTemplate,
        tuple[tuple[str, str], ...],
    ],
] = {}


def SelectPhysicalComponentGlobalContractRecommendation(
    Domains: Any,
    RequiredSignals: Any,
    *,
    RejectedSets: Any = (),
    CompatibilityCache: dict[tuple[str, str], bool] | None = None,
    ResourceGraphFingerprint: str = "",
    TechnologyFingerprint: str = "",
    PortableRequestFamilyFingerprint: str = "",
) -> dict[str, PhysicalPortCorridorFactor] | None:
    """Find a certified portable positive exact-corridor witness.

    Pairwise claim compatibility alone cannot make candidates generated under
    different keepouts, fixed portals, or ordinary-net obstacles portable.
    The caller must name a non-empty dependency-family certificate, and every
    contributing domain must carry that exact certificate. Captured plan
    domains intentionally leave it empty and therefore cannot steer another
    plan merely because their claims happen not to overlap.
    """
    Signals = tuple(sorted({str(Signal) for Signal in RequiredSignals}))
    if not Signals or not PortableRequestFamilyFingerprint:
        return None
    Rejected = tuple(
        frozenset((str(Signal), str(Fingerprint)) for Signal, Fingerprint in Set)
        for Set in RejectedSets
        if Set
    )
    FactorsBySignal: dict[str, dict[str, PhysicalPortCorridorFactor]] = {
        Signal: {} for Signal in Signals
    }
    for Domain in Domains:
        if (
            not isinstance(Domain, PhysicalPortCorridorDomain)
            or not Domain.Complete
            or Domain.Signal not in FactorsBySignal
            or Domain.PortableRequestFamilyFingerprint
            != PortableRequestFamilyFingerprint
            or (
                ResourceGraphFingerprint
                and Domain.ResourceGraphFingerprint
                != ResourceGraphFingerprint
            )
            or (
                TechnologyFingerprint
                and Domain.TechnologyFingerprint
                != TechnologyFingerprint
            )
        ):
            continue
        for Factor in Domain.Factors:
            if (
                Factor.Signal != Domain.Signal
                or Factor.PortGlobalContractFingerprint
                != Domain.PortGlobalContractFingerprint
            ):
                continue
            Identity = _Fingerprint((
                Factor.PortGlobalContractFingerprint,
                Factor.RouteCandidateFingerprint,
                tuple(sorted(Factor.Nodes)),
                tuple(sorted(map(str, Factor.Claims.ResourceIds))),
            ))
            FactorsBySignal[Domain.Signal].setdefault(Identity, Factor)
    if any(not Values for Values in FactorsBySignal.values()):
        return None

    Compatibility = CompatibilityCache if CompatibilityCache is not None else {}

    def Compatible(
        First: PhysicalPortCorridorFactor,
        Second: PhysicalPortCorridorFactor,
    ) -> bool:
        Key = tuple(sorted((
            First.RouteCandidateFingerprint,
            Second.RouteCandidateFingerprint,
        )))
        Result = Compatibility.get(Key)
        if Result is None:
            Result = not ComponentClaimsConflict(
                First.Claims,
                Second.Claims,
            )
            Compatibility[Key] = Result
        return Result

    OrderedDomains = {
        Signal: tuple(sorted(
            Values.values(),
            key=lambda Factor: (
                Factor.NormalizedIdentityFingerprint,
                Factor.PortGlobalContractFingerprint,
                Factor.RouteCandidateFingerprint,
            ),
        ))
        for Signal, Values in FactorsBySignal.items()
    }

    def Search(
        Remaining: tuple[str, ...],
        Selected: dict[str, PhysicalPortCorridorFactor],
    ) -> dict[str, PhysicalPortCorridorFactor] | None:
        if not Remaining:
            return dict(Selected)
        Signal = min(Remaining, key=lambda Value: (
            len(OrderedDomains[Value]),
            Value,
        ))
        NextRemaining = tuple(Value for Value in Remaining if Value != Signal)
        for Factor in OrderedDomains[Signal]:
            if not all(Compatible(Factor, Value) for Value in Selected.values()):
                continue
            Keys = frozenset((
                *(
                    (Name, Value.PortGlobalContractFingerprint)
                    for Name, Value in Selected.items()
                ),
                (Signal, Factor.PortGlobalContractFingerprint),
            ))
            if any(Set.issubset(Keys) for Set in Rejected):
                continue
            if any(
                not any(
                    Compatible(Factor, Candidate)
                    and all(
                        Compatible(Candidate, Value)
                        for Value in Selected.values()
                    )
                    for Candidate in OrderedDomains[OtherSignal]
                )
                for OtherSignal in NextRemaining
            ):
                continue
            Selected[Signal] = Factor
            Result = Search(NextRemaining, Selected)
            if Result is not None:
                return Result
            Selected.pop(Signal, None)
        return None

    return Search(Signals, {})


def _ClaimsContain(
    Container: RoutingResourceClaims,
    Contained: RoutingResourceClaims,
) -> bool:
    return bool(
        Contained.WireCells <= Container.WireCells
        and Contained.SupportCells <= Container.SupportCells
        and Contained.RequiredAirCells
        <= Container.RequiredAirCells
        and Contained.ElectricalCells
        <= Container.ElectricalCells
    )


def ValidatePhysicalBoundaryPortHandoff(
    Problem: ComponentRoutingProblem,
    Plan: PhysicalComponentAssemblyPlan,
    *,
    RequireSelectedLocalSupports: bool = False,
) -> None:
    """Validate the additive global-only boundary handoff contract.

    ``Ports`` remains the transitional composite local/global contract while
    the local compiler is migrated.  Whenever the authoritative planner also
    publishes ``GlobalBoundaryPorts``, require that representation to be a
    complete and exact projection of only the globally owned half.  This
    keeps the new stage boundary honest without changing the legacy local
    compilation input yet.
    """
    BoundaryPorts = tuple(Plan.GlobalBoundaryPorts)
    if not BoundaryPorts:
        return
    ExportedSignals = tuple(sorted(
        Port.Signal for Port in Problem.Interface.Ports
    ))
    BoundaryPortsBySignal = {
        Port.Signal: Port for Port in BoundaryPorts
    }
    if (
        len(set(ExportedSignals)) != len(ExportedSignals)
        or len(BoundaryPortsBySignal) != len(BoundaryPorts)
        or tuple(sorted(BoundaryPortsBySignal)) != ExportedSignals
    ):
        raise ValueError(
            "physical boundary handoff must contain exactly one global "
            "boundary port per exported signal"
        )

    CompositePortsBySignal = {
        Port.Signal: Port for Port in Plan.Ports
    }
    if (
        len(CompositePortsBySignal) != len(Plan.Ports)
        or tuple(sorted(CompositePortsBySignal)) != ExportedSignals
    ):
        raise ValueError(
            "physical boundary handoff and transitional composite ports "
            "export different signals"
        )

    ForbiddenLocalFields = (
        "Claims",
        "FabricAttachment",
        "FabricDomainFingerprint",
        "LocalClaims",
        "LocalPath",
        "OwnedAccessCandidates",
        "OwnedCandidateFingerprints",
        "OwnedTerminalFingerprints",
        "OwnedTerminals",
    )
    for Signal in ExportedSignals:
        BoundaryPort = BoundaryPortsBySignal[Signal]
        CompositePort = CompositePortsBySignal[Signal]
        if any(
            hasattr(BoundaryPort, Field)
            for Field in ForbiddenLocalFields
        ):
            raise ValueError(
                "physical global boundary port contains component-local "
                "fields"
            )
        if (
            not BoundaryPort.GlobalPath
            or BoundaryPort.GlobalPath[0] != BoundaryPort.Attachment
        ):
            raise ValueError(
                "physical global boundary path must start at its attachment"
            )
        if (
            BoundaryPort.Direction != CompositePort.Direction
            or BoundaryPort.Attachment != CompositePort.Attachment
            or BoundaryPort.GlobalPath != CompositePort.GlobalPath
            or BoundaryPort.Capacity != CompositePort.Capacity
            or BoundaryPort.GlobalClaims != CompositePort.GlobalClaims
            or BoundaryPort.GlobalContractFingerprint
            != BuildPhysicalPortGlobalContractFingerprint(CompositePort)
            or BoundaryPort.ApertureContractFingerprint
            != BuildPhysicalPortApertureContractFingerprint(CompositePort)
        ):
            raise ValueError(
                "physical global boundary contract differs from the "
                "transitional composite port external half"
            )

    if any(
        not all(
            getattr(Support, Field, "")
            for Field in (
                "Signal",
                "BoundaryReservationFingerprint",
                "LocalContractFingerprint",
                "LocalAccessFingerprint",
                "SupportFingerprint",
            )
        )
        for Support in Plan.SelectedLocalPortSupports
    ):
        raise ValueError(
            "selected local port support has an incomplete identity"
        )
    SupportsBySignal = {
        Support.Signal: Support
        for Support in Plan.SelectedLocalPortSupports
    }
    if RequireSelectedLocalSupports and (
        len(SupportsBySignal) != len(Plan.SelectedLocalPortSupports)
        or tuple(sorted(SupportsBySignal)) != ExportedSignals
    ):
        raise ValueError(
            "closed-component compilation requires exactly one post-global "
            "local support per boundary port"
        )
    for Signal, Support in SupportsBySignal.items():
        BoundaryPort = BoundaryPortsBySignal.get(Signal)
        CompositePort = CompositePortsBySignal.get(Signal)
        if BoundaryPort is None or CompositePort is None or (
            Support.BoundaryReservationFingerprint
            != BoundaryPort.ReservationFingerprint
            or Support.LocalContractFingerprint
            != BuildPhysicalPortLocalContractFingerprint(CompositePort)
        ):
            raise ValueError(
                "selected local support differs from its frozen boundary "
                "or composite local contract"
            )


def ValidatePhysicalExteriorFabricHandoff(
    Plan: PhysicalComponentAssemblyPlan,
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    *,
    CurrentResourceGraphFingerprint: str = "",
) -> None:
    """Require one exact exterior-routing identity across the stage seam.

    Older fixtures carry three empty defaults and remain valid. Once either
    side publishes any exterior identity, both sides must publish the complete
    fabric-set, detailed-region, and joint-capacity-ledger triple and match it
    exactly.
    """
    FieldNames = (
        "ExteriorFabricSetFingerprint",
        "ExteriorRegionFingerprint",
        "ExteriorCapacityLedgerFingerprint",
    )
    PlanIdentity = tuple(
        str(getattr(Plan, FieldName, "")) for FieldName in FieldNames
    )
    PreparationIdentity = tuple(
        str(getattr(Preparation, FieldName, ""))
        for FieldName in FieldNames
    )
    PlanResourceFingerprint = str(getattr(
        Plan,
        "ResourceGraphFingerprint",
        "",
    ))
    PreparationResourceFingerprint = str(getattr(
        Preparation,
        "ResourceGraphFingerprint",
        "",
    ))
    ResourceIdentities = (
        PlanResourceFingerprint,
        PreparationResourceFingerprint,
        str(CurrentResourceGraphFingerprint),
    )
    if not any((*PlanIdentity, *PreparationIdentity, *ResourceIdentities)):
        return
    if any((*PlanIdentity, *PreparationIdentity)) and (
        not all(PlanIdentity) or not all(PreparationIdentity)
    ):
        raise ValueError(
            "physical exterior fabric handoff requires the complete "
            "fabric-set, region, and capacity-ledger identity triple"
        )
    Mismatches = tuple(
        FieldName
        for FieldName, PlanValue, PreparationValue in zip(
            FieldNames,
            PlanIdentity,
            PreparationIdentity,
        )
        if PlanValue != PreparationValue
    )
    if Mismatches:
        raise ValueError(
            "physical exterior fabric handoff identity mismatch: "
            + ", ".join(Mismatches)
        )
    if any(ResourceIdentities) and (
        not PlanResourceFingerprint
        or not PreparationResourceFingerprint
        or PlanResourceFingerprint != PreparationResourceFingerprint
        or (
            CurrentResourceGraphFingerprint
            and str(CurrentResourceGraphFingerprint)
            != PreparationResourceFingerprint
        )
    ):
        raise ValueError(
            "physical exterior fabric handoff resource-graph identity "
            "mismatch"
        )


def _ValidatePhysicalProblemContract(
    Problem: ComponentRoutingProblem,
    Plan: PhysicalComponentAssemblyPlan,
) -> None:
    """Reject any porous or mutable physical component interface."""
    if Problem.Interface is None:
        raise ValueError("closed physical component interface is missing")
    if not Plan.AccessCertificateFingerprint:
        raise ValueError(
            "physical assembly is missing its access certificate identity"
        )
    if (
        not Plan.LocalAccessDomainFingerprint
        or BuildPhysicalLocalAccessDomainFingerprint(Problem)
        != Plan.LocalAccessDomainFingerprint
    ):
        raise ValueError(
            "physical component local access domain identity mismatch"
        )
    if (
        Problem.PhysicalAssemblyPlan != Plan
        or Problem.Interface.PhysicalPortReservations != Plan.Ports
        or Problem.Interface.Feedthroughs != Plan.Feedthroughs
        or Problem.PlacementFingerprint != Plan.PlacementFingerprint
    ):
        raise ValueError(
            "component problem and physical assembly contracts differ"
        )
    PortsBySignal = {
        Port.Signal: Port for Port in Plan.Ports
    }
    if len(PortsBySignal) != len(Plan.Ports):
        raise ValueError(
            "physical assembly contains duplicate signal ports"
        )
    ExpectedTerminalKeys = frozenset(
        (Port.Signal, TerminalFingerprint)
        for Port in Plan.Ports
        for TerminalFingerprint in Port.OwnedTerminalFingerprints
    )
    if any(
        not (
            len(Port.OwnedTerminals)
            == len(Port.OwnedTerminalFingerprints)
            and (
                not Port.OwnedAccessCandidates
                or frozenset(Port.OwnedCandidateFingerprints)
                == frozenset(
                    Candidate.CandidateFingerprint
                    for Candidate in Port.OwnedAccessCandidates
                )
            )
        )
        for Port in Plan.Ports
    ):
        raise ValueError(
            "physical port candidate evidence differs from its certified "
            "local access domain"
        )
    ExportedDomainKeys = frozenset(
        (Domain.Signal, Domain.TerminalFingerprint)
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal in PortsBySignal
    )
    if ExportedDomainKeys != ExpectedTerminalKeys:
        raise ValueError(
            "physical ports and exported terminal domains differ"
        )
    if any(
        not Domain.Candidates
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal in PortsBySignal
    ):
        raise ValueError("physical port has an empty local access domain")
    for Port in Plan.Ports:
        def OutsideComponentEnvelope(
            Position: Position3,
        ) -> bool:
            return bool(
                Position[0] < Plan.EnvelopeMinimum[0]
                or Position[0] > Plan.EnvelopeMaximum[0]
                or Position[2] < Plan.EnvelopeMinimum[2]
                or Position[2] > Plan.EnvelopeMaximum[2]
            )

        if (
            not Port.LocalPath
            or Port.LocalPath[0] != Port.FabricAttachment
            or Port.LocalPath[-1] != Port.Attachment
            or not Port.GlobalPath
            or Port.GlobalPath[0] != Port.Attachment
            or not OutsideComponentEnvelope(Port.Attachment)
            or any(
                not OutsideComponentEnvelope(Position)
                for Position in Port.GlobalPath
            )
        ):
            raise ValueError(
                "physical port seam ownership is malformed"
            )
        PathOwnershipOverlap = (
            frozenset(Port.LocalPath[1:])
            & frozenset(Port.GlobalPath[1:])
        )
        if PathOwnershipOverlap:
            raise ValueError(
                "local and global port paths overlap beyond the seam: "
                f"signal={Port.Signal}, "
                f"overlap={tuple(sorted(PathOwnershipOverlap))}, "
                f"local_path={tuple(Port.LocalPath)}, "
                f"global_path={tuple(Port.GlobalPath)}"
            )
        SeamClaims = Problem.ResourceGraph.BuildRouteClaims(frozenset((
            *Port.LocalPath,
            *Port.GlobalPath,
        )))
        ExpectedLocalClaims = Problem.ResourceGraph.BuildRouteClaims(
            frozenset(Port.LocalPath)
        )
        ExpectedGlobalClaims = Problem.ResourceGraph.BuildRouteClaims(
            frozenset(Port.GlobalPath)
        )
        if (
            Port.LocalClaims is None
            or not _ClaimsContain(Port.LocalClaims, ExpectedLocalClaims)
        ):
            raise ValueError(
                "physical port omits its locally owned seam claims"
            )
        if (
            Port.GlobalClaims is None
            or not _ClaimsContain(Port.GlobalClaims, ExpectedGlobalClaims)
        ):
            raise ValueError(
                "physical port omits its globally owned portal claims"
            )
        if not _ClaimsContain(Port.Claims, SeamClaims):
            raise ValueError(
                "physical port omits its immutable seam claims"
            )
    # Keep the established composite-port diagnostics authoritative during
    # migration, then cross-check the additive global-only representation.
    ValidatePhysicalBoundaryPortHandoff(
        Problem,
        Plan,
        RequireSelectedLocalSupports=bool(Plan.GlobalBoundaryPorts),
    )
    ReservedClaims = tuple(Problem.ReservedGlobalClaimsBySignal)
    ExpectedReservedClaims = tuple(
        (Channel.Signal, Channel.Claims)
        for Channel in Plan.Channels
    )
    if ReservedClaims != ExpectedReservedClaims:
        raise ValueError(
            "reserved global channel claims differ from assembly plan"
        )
    ActualTransitSignals = frozenset(
        Domain.Signal for Domain in Problem.ForeignTransitDomains
    )
    if ActualTransitSignals != Plan.DeclaredFeedthroughSignals:
        raise ValueError(
            "physical feedthrough domains differ from declarations"
        )


def _ValidatePhysicalTemplate(
    Problem: ComponentRoutingProblem,
    Template: RoutedComponentTemplate,
) -> None:
    """Enforce exact seams and immutable foreign global corridors."""
    Plan = Problem.PhysicalAssemblyPlan
    if Plan is None:
        return
    ExpectedPorts = tuple(sorted(
        (Value.Signal, Value.Attachment)
        for Value in Plan.Ports
    ))
    if tuple(sorted(Template.ExportedPorts)) != ExpectedPorts:
        raise ValueError(
            "component template changed its physical port assignment"
        )
    NetBySignal = {
        Net.Signal: Net for Net in Template.Nets
    }
    for Port in Plan.Ports:
        Net = NetBySignal.get(Port.Signal)
        if Net is None:
            raise ValueError(
                "component template omitted a physical port signal"
            )
        RequiredLocalNodes = frozenset(Port.LocalPath)
        RequiredTerminalNodes = frozenset(Port.OwnedTerminals)
        if (
            not RequiredLocalNodes <= Net.Nodes
            or not RequiredTerminalNodes <= Net.Nodes
        ):
            raise ValueError(
                "component template does not terminate at its assigned seam"
            )
        if (
            frozenset(Port.GlobalPath)
            - frozenset((Port.Attachment,))
        ) & Net.Nodes:
            raise ValueError(
                "component template entered the global side of its seam"
            )
    ChannelClaims = dict(Problem.ReservedGlobalClaimsBySignal)
    Conflicts = tuple(sorted({
        (Net.Signal, ReservedSignal)
        for Net in Template.Nets
        for ReservedSignal, Claims in ChannelClaims.items()
        if (
            ReservedSignal != Net.Signal
            and ComponentClaimsConflict(Net.Claims, Claims)
        )
    }))
    if Conflicts:
        raise ValueError(
            "component template conflicts with immutable global corridors: "
            f"{Conflicts}"
        )
    if (
        frozenset(
            Value.Signal
            for Value in Template.ForeignTransitReservations
        )
        != Plan.DeclaredFeedthroughSignals
    ):
        raise ValueError(
            "component template transit differs from declared feedthroughs"
        )
    FeedthroughsBySignal = {
        Value.Signal: Value for Value in Plan.Feedthroughs
    }
    for Signal, Contract in FeedthroughsBySignal.items():
        Nets = tuple(
            Net
            for Net in Template.ForeignTransitReservations
            if Net.Signal == Signal
        )
        if (
            len(Nets) > Contract.Capacity
            or any(
                (
                    not any(
                        Entry in Net.Nodes and Exit in Net.Nodes
                        for Entry, Exit in Contract.EndpointPairs
                    )
                    or (
                        Contract.ReservedPathNodes
                        and Net.Nodes
                        != frozenset(Contract.ReservedPathNodes)
                    )
                    or (
                        Contract.Claims is not None
                        and Net.Claims != Contract.Claims
                    )
                )
                for Net in Nets
            )
        ):
            raise ValueError(
                "component template changed a declared feedthrough"
            )
    if (
        Template.ForeignEscapeReservations
        or Template.ExternalContinuationReservations
    ):
        raise ValueError(
            "physical component template reopened an undeclared export"
        )


def _Fingerprint(Value: object) -> str:
    return sha256(repr(Value).encode("utf-8")).hexdigest()[:16]


def BuildPhysicalPortLocalContractFingerprint(
    Port: PhysicalComponentPortReservation,
) -> str:
    """Fingerprint the translation-stable immutable boundary contract.

    Match the domain consumed by the global-relaxed local proof: the fixed
    local seam/access geometry only.  Exterior channel stems and global
    claims are deliberately absent so the same proven local impossibility is
    not rediscovered under a different global route.
    """
    CertifiedFingerprint = str(getattr(
        Port,
        "CertifiedLocalContractFingerprint",
        "",
    ))
    CertifiedSeamFingerprint = str(getattr(
        Port,
        "CertifiedSeamContractFingerprint",
        "",
    ))
    if (
        CertifiedFingerprint
        and CertifiedSeamFingerprint
        and not getattr(Port, "OwnedCandidateFingerprints", ())
        and not getattr(Port, "OwnedAccessCandidates", ())
        and BuildPhysicalPortSeamContractFingerprint(Port)
        == CertifiedSeamFingerprint
    ):
        return CertifiedFingerprint
    Origin = Port.FabricAttachment

    def RelativePath(Path: Any) -> tuple[tuple[int, int, int], ...]:
        return tuple(
            tuple(
                int(Position[Index]) - int(Origin[Index])
                for Index in range(3)
            )
            for Position in Path
        )

    CandidateContracts = tuple(sorted(
        (
            RelativePath(Candidate.Path),
            int(Candidate.Layer),
        )
        for Candidate in getattr(Port, "OwnedAccessCandidates", ())
    ))
    OwnedTerminalContract = tuple(sorted(
        RelativePath(getattr(Port, "OwnedTerminals", ()))
    ))
    return "local-contract-v1:" + _Fingerprint((
        getattr(Port, "Direction", ""),
        getattr(Port, "FabricDomainFingerprint", ""),
        OwnedTerminalContract,
        RelativePath(getattr(Port, "LocalPath", ())),
        CandidateContracts,
        int(getattr(Port, "Capacity", 1)),
    ))


def BuildPhysicalPortSeamContractFingerprint(
    Port: PhysicalComponentPortReservation,
) -> str:
    """Fingerprint the local seam without internal terminal witnesses."""
    Origin = tuple(getattr(Port, "FabricAttachment", (0, 0, 0)))
    if len(Origin) != 3:
        raise ValueError("physical port seam origin must be three-dimensional")

    def RelativePath(Path: Any) -> tuple[tuple[int, int, int], ...]:
        return tuple(
            tuple(
                int(Position[Index]) - int(Origin[Index])
                for Index in range(3)
            )
            for Position in Path
        )

    return "local-seam-contract-v1:" + _Fingerprint((
        getattr(Port, "Direction", ""),
        getattr(Port, "FabricDomainFingerprint", ""),
        tuple(sorted(RelativePath(
            getattr(Port, "OwnedTerminals", ())
        ))),
        RelativePath(getattr(Port, "LocalPath", ())),
        int(getattr(Port, "Capacity", 1)),
    ))


def BuildPhysicalPortGlobalContractFingerprint(
    Port: PhysicalComponentPortReservation,
) -> str:
    """Fingerprint exactly the port geometry visible to global planning.

    The authoritative global profile terminates at ``Attachment`` and owns
    ``GlobalPath``.  Local fabric attachment, terminal access, and LocalPath
    are deliberately excluded: changing those cannot change a complete
    external candidate-domain proof for the same retained placement.
    """
    return "global-contract-v1:" + _Fingerprint((
        getattr(Port, "Direction", ""),
        tuple(getattr(Port, "Attachment", ())),
        tuple(tuple(Value) for Value in getattr(Port, "GlobalPath", ())),
        int(getattr(Port, "Capacity", 1)),
    ))


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


def BuildPhysicalPortApertureContractFingerprint(
    Port: PhysicalComponentPortReservation,
) -> str:
    """Fingerprint exact globally owned portal claims for one selected port.

    Global routing owns ``GlobalPath`` and shares only ``Attachment`` with
    local routing. ``Claims`` remains the full seam union for validation and
    local immutable obstacles, but interior ownership cannot participate in
    a global sibling-aperture no-good.
    """
    Claims = getattr(Port, "GlobalClaims", None)
    if Claims is None:
        raise ValueError(
            "physical port aperture identity requires global claims"
        )
    ClaimsIdentity = (
        tuple(sorted(map(str, getattr(Claims, "ResourceIds", ()))))
        if Claims is not None
        else ()
    )
    return "aperture-contract-v2:" + _Fingerprint((
        BuildPhysicalPortGlobalContractFingerprint(Port),
        ClaimsIdentity,
    ))


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


def BuildPhysicalLocalAccessDomainFingerprint(
    Problem: ComponentRoutingProblem,
) -> str:
    """Fingerprint the certified local choices excluded from global CSPs."""
    return _Fingerprint(tuple(
        (
            Domain.Signal,
            Domain.Terminal,
            Domain.TerminalRole,
            Domain.TerminalFingerprint,
            tuple(
                (
                    Candidate.CandidateFingerprint,
                    Candidate.Attachment,
                    tuple(Candidate.Path),
                    Candidate.Layer,
                    tuple(sorted(map(str, Candidate.Claims.ResourceIds))),
                )
                for Candidate in Domain.Candidates
            ),
        )
        for Domain in Problem.OwnedTerminalDomains
    ))


def ProjectPhysicalComponentSignalGlobalProfile(
    Profile: Any,
    CoveredTerminals: Iterable[Position3],
    Port: Any | None,
) -> Any | None:
    """Project one whole-design profile onto one immutable component seam."""
    Covered = frozenset(CoveredTerminals)
    if Port is not None and str(Port.Signal) != str(Profile.Signal):
        raise ValueError("physical port and projected profile signals differ")
    RootIsCovered = Profile.Root in Covered
    OutsideTargets = tuple(
        Target for Target in Profile.Targets if Target not in Covered
    )
    if Port is None:
        return None if RootIsCovered and not OutsideTargets else Profile
    if not Port.GlobalPath or Port.GlobalPath[0] != Port.Attachment:
        raise ValueError(
            "physical port has no immutable external access path"
        )
    if RootIsCovered:
        if not OutsideTargets:
            return None
        Root = Port.Attachment
        Targets = OutsideTargets
        SourceAccessPath = tuple(Port.GlobalPath)
        TargetAccessPaths = {
            Target: Profile.TargetAccessPaths[Target]
            for Target in Targets
        }
    else:
        Root = Profile.Root
        Targets = tuple(dict.fromkeys((
            *OutsideTargets,
            Port.Attachment,
        )))
        SourceAccessPath = Profile.SourceAccessPath
        TargetAccessPaths = {
            **{
                Target: Profile.TargetAccessPaths[Target]
                for Target in OutsideTargets
            },
            Port.Attachment: tuple(Port.GlobalPath),
        }
    return replace(
        Profile,
        Root=Root,
        Targets=Targets,
        Span=max(
            abs(Target[0] - Root[0])
            + abs(Target[2] - Root[2])
            for Target in Targets
        ),
        Fanout=len(Targets),
        SourceAccessPath=SourceAccessPath,
        TargetAccessPaths=TargetAccessPaths,
    )


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


def RecordPhysicalComponentGlobalPlanNoGood(
    Failure: RoutingFailure,
    Plan: PhysicalComponentAssemblyPlan,
    Resources: Any,
    *,
    ShouldStop: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Record the smallest port contract justified by a complete cut.

    Global planning happens before local compilation, so its complete proof
    may reject only physical choices on which that proof depends.  Explicit
    feedthroughs remain part of the exact assembly identity unless the proof
    independently certifies that they cannot affect the cut.
    """
    Diagnostics = dict(Failure.Diagnostics or {})
    if not bool(
        Diagnostics.get("GlobalPlanDomainComplete", False)
        and Diagnostics.get("CompleteAssignmentCutProof", False)
    ):
        raise ValueError(
            "physical global-plan no-good requires a complete domain proof"
        )
    DependencySignals = frozenset(str(Signal) for Signal in (
        Diagnostics.get("AssemblyPlanDependentPortSignals", ()) or ()
    ))
    AssemblyDependencySignals = frozenset(str(Signal) for Signal in (
        Diagnostics.get("AssemblyPlanDependencySignals", ()) or ()
    ))
    Feedthroughs = tuple(getattr(Plan, "Feedthroughs", ()))
    FeedthroughIndependenceProved = bool(Diagnostics.get(
        "AssemblyPlanFeedthroughIndependentProofComplete",
        False,
    ))
    RequiresExactAssemblyChoice = bool(
        Feedthroughs and not FeedthroughIndependenceProved
    )
    DependencyPorts = tuple(
        Port for Port in Plan.Ports if Port.Signal in DependencySignals
    )
    if frozenset(Port.Signal for Port in DependencyPorts) != DependencySignals:
        raise ValueError(
            "physical global-plan proof names an undeclared component port"
        )
    DeclaredDependencyFingerprint = str(
        Diagnostics.get("GlobalPlanDependencyFingerprint", "")
    )
    DependencyProjectionProofComplete = bool(
        Diagnostics.get("CompleteAssignmentCutProof", False)
        and Diagnostics.get(
            "AssemblyPlanDependencyIdentityComplete",
            False,
        )
        and AssemblyDependencySignals
        and AssemblyDependencySignals == DependencySignals
        and DeclaredDependencyFingerprint
        and DeclaredDependencyFingerprint
        == BuildPhysicalGlobalPlanDependencyFingerprint(
            Plan,
            AssemblyDependencySignals,
        )
        and Diagnostics.get("GlobalPlanCutFamilyFingerprint", "")
        and Diagnostics.get("GlobalPlanProofFingerprint", "")
    )
    RequestApertureFactorNoGood = frozenset(
        (str(Key[0]), str(Key[1]))
        for Key in (
            Diagnostics.get("RequestApertureFactorNoGood", ()) or ()
        )
        if isinstance(Key, (tuple, list)) and len(Key) == 2
    )
    RequestApertureProofSignals = frozenset(
        Signal for Signal, _FingerprintValue
        in RequestApertureFactorNoGood
    )
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
    PortSolverDomainFingerprint = str(
        getattr(Preparation, "DomainFingerprint", "")
    )
    DeclaredRequestAperturePortNoGood = frozenset(
        (str(Key[0]), str(Key[1]))
        for Key in (
            Diagnostics.get("RequestAperturePortNoGood", ()) or ()
        )
        if isinstance(Key, (tuple, list)) and len(Key) == 2
    )
    ExpectedRequestAperturePortNoGood = (
        BuildPhysicalRequestAperturePortNoGood(
            Plan,
            RequestApertureFactorNoGood,
            SignalLocalRequestFactorProofComplete=bool(
                Diagnostics.get(
                    "SignalLocalRequestFactorProofComplete",
                    False,
                )
            ),
            PortSolverCacheKey=BuildPhysicalComponentPortSolverCacheKey(
                PortSolverDomainFingerprint
            ),
        )
        if RequestApertureFactorNoGood and PortSolverDomainFingerprint
        else frozenset()
    )
    if (
        DeclaredRequestAperturePortNoGood
        and DeclaredRequestAperturePortNoGood
        != ExpectedRequestAperturePortNoGood
    ):
        raise ValueError(
            "request/aperture port no-good identity mismatch"
        )
    RequestApertureProofComplete = bool(
        Diagnostics.get("RequestApertureFactorProofComplete", False)
        and RequestApertureFactorNoGood
        and RequestApertureProofSignals == DependencySignals
        and PortSolverDomainFingerprint
    )
    IndependentEmptyDomainSignals = frozenset(
        str(Signal)
        for Signal in (
            Diagnostics.get(
                "IndependentEmptyCandidateDomainSignals",
                (),
            )
            or ()
        )
    )
    if not IndependentEmptyDomainSignals <= DependencySignals:
        raise ValueError(
            "independent empty route-domain proof names an unrelated signal"
        )
    # A complete assignment cut carries its exact dependency closure.  When
    # every dependency is an assembly port and its plan-bound identity
    # fingerprint validates, unrelated ports cannot affect that proof and
    # must not inflate the learned clause.  Request/aperture starvation keeps
    # its stronger prepared-domain scoped representation below.  Feedthroughs
    # still require exact assembly-choice identity unless their independence
    # is separately certified.
    Ports = (
        tuple(
            Port for Port in Plan.Ports
            if Port.Signal in IndependentEmptyDomainSignals
        )
        if IndependentEmptyDomainSignals
        else DependencyPorts
        if RequestApertureProofComplete
        else DependencyPorts
        if DependencyProjectionProofComplete
        else tuple(Plan.Ports)
    )
    ReservationKeys = frozenset(
        (
            Port.Signal,
            BuildPhysicalPortApertureContractFingerprint(Port),
        )
        for Port in Ports
    )
    PortSolverScopeKey = (
        (
            min(DependencySignals),
            "local-signal-domain:"
            + BuildPhysicalComponentPortSolverCacheKey(
                PortSolverDomainFingerprint
            ),
        )
        if RequestApertureProofComplete
        else None
    )
    RequestGlobalDeterminantKeys = (
        frozenset(
            (
                Port.Signal,
                BuildPhysicalPortGlobalContractFingerprint(Port),
            )
            for Port in Plan.Ports
        )
        if RequestApertureProofComplete
        else frozenset()
    )
    RejectedRequestApertureSet = frozenset((
        *(
            DeclaredRequestAperturePortNoGood
            or frozenset((
                *RequestGlobalDeterminantKeys,
                *ReservationKeys,
                *((
                    (PortSolverScopeKey,)
                    if PortSolverScopeKey is not None
                    else ()
                )),
            ))
        ),
    ))
    ReservationKeyBySignal = {
        Signal: (Signal, Fingerprint)
        for Signal, Fingerprint in ReservationKeys
    }
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    PairwiseEdges = tuple(
        tuple(map(str, Edge))
        for Edge in (
            Diagnostics.get(
                "PairwisePortReservationNoGoodEdges",
                (),
            )
            or (
                ConflictGraph.get("PairwiseIncompatibleEdges", ())
                if isinstance(ConflictGraph, dict)
                else ()
            )
        )
        if (
            isinstance(Edge, (list, tuple))
            and len(Edge) == 2
            and str(Edge[0]) in ReservationKeyBySignal
            and str(Edge[1]) in ReservationKeyBySignal
        )
    )
    PairwiseProofComplete = bool(Diagnostics.get(
        "PairwisePortReservationNoGoodProofComplete",
        False,
    ))
    PairwiseReservationSets = (
        frozenset(
            frozenset((
                ReservationKeyBySignal[First],
                ReservationKeyBySignal[Second],
            ))
            for First, Second in PairwiseEdges
            if (
                First != Second
                and First in ReservationKeyBySignal
                and Second in ReservationKeyBySignal
            )
        )
        if PairwiseProofComplete
        else frozenset()
    )
    CompiledPairRelationDiagnostics: list[dict[str, object]] = []
    PreparedFactorCache = getattr(
        Resources,
        "PhysicalBoundaryMandatoryPortalFactorDomainCache",
        {},
    )
    PairwiseEdgeSignals = frozenset(
        Signal for Edge in PairwiseEdges for Signal in Edge
    )
    PreparedPairFactorDomains = tuple(
        Value
        for Key, Value in PreparedFactorCache.items()
        if (
            isinstance(Key, tuple)
            and len(Key) == 3
            and Key[0] == PortSolverDomainFingerprint
            and str(Key[1]) in PairwiseEdgeSignals
        )
    )
    HasPreparedPairFactorArchitecture = bool(
        PreparedPairFactorDomains
        and {
            str(getattr(Value, "Signal", ""))
            for Value in PreparedPairFactorDomains
        } == PairwiseEdgeSignals
        and all(
            bool(getattr(Value, "Complete", False))
            for Value in PreparedPairFactorDomains
        )
    )
    PreparedPairFactorArchitectureDiagnostics = {
        "Available": HasPreparedPairFactorArchitecture,
        "ExpectedSignals": sorted(PairwiseEdgeSignals),
        "PreparedSignalCount": len({
            str(getattr(Value, "Signal", ""))
            for Value in PreparedPairFactorDomains
        }),
        "FactorDomainCount": len(PreparedPairFactorDomains),
        "CompleteFactorDomainCount": sum(
            int(bool(getattr(Value, "Complete", False)))
            for Value in PreparedPairFactorDomains
        ),
        "IncompleteSignals": sorted({
            str(getattr(Value, "Signal", ""))
            for Value in PreparedPairFactorDomains
            if not bool(getattr(Value, "Complete", False))
        }),
        "OtherPreparedDomainFactorCount": sum(
            1
            for Key in PreparedFactorCache
            if (
                isinstance(Key, tuple)
                and len(Key) == 3
                and Key[0] != PortSolverDomainFingerprint
            )
        ),
    }
    PreparedPairOptionCounts = {
        Signal: len({
            str(getattr(Value, "ApertureContractFingerprint", ""))
            for Value in PreparedPairFactorDomains
            if str(getattr(Value, "Signal", "")) == Signal
        })
        for Signal in PairwiseEdgeSignals
    }
    PreparedPairOptionProduct = 1
    for Signal in sorted(PreparedPairOptionCounts):
        PreparedPairOptionProduct *= PreparedPairOptionCounts[Signal]
    # The relation compiler uses a shared symbolic frontier quotient for
    # larger products, so the gate bounds published certificate size rather
    # than forcing the exterior router to rediscover each incompatible pair
    # one contract at a time.
    MaximumEagerPreparedPairOptionProduct = 65_536
    CompletePreparedPairSignals = frozenset(
        Signal
        for Signal in PairwiseEdgeSignals
        if (
            PreparedPairOptionCounts.get(Signal, 0) > 0
            and all(
                bool(getattr(Value, "Complete", False))
                for Value in PreparedPairFactorDomains
                if str(getattr(Value, "Signal", "")) == Signal
            )
        )
    )
    PreparedPairEdges = tuple(sorted({
        tuple(sorted((str(First), str(Second))))
        for First, Second in PairwiseEdges
        if str(First) != str(Second)
    }))
    EligiblePreparedPairRelations = tuple(sorted(
        (
            (
                PreparedPairOptionCounts.get(Pair[0], 0)
                * PreparedPairOptionCounts.get(Pair[1], 0)
            ),
            Pair,
        )
        for Pair in PreparedPairEdges
        if (
            frozenset(Pair) <= CompletePreparedPairSignals
            and 0
            < (
                PreparedPairOptionCounts.get(Pair[0], 0)
                * PreparedPairOptionCounts.get(Pair[1], 0)
            )
            <= MaximumEagerPreparedPairOptionProduct
        )
    ))
    ShouldCompilePreparedPairRelation = bool(
        PairwiseProofComplete
        and EligiblePreparedPairRelations
    )
    PreparedPairFactorArchitectureDiagnostics.update({
        "OptionCountsBySignal": PreparedPairOptionCounts,
        "OptionProduct": PreparedPairOptionProduct,
        "MaximumEagerOptionProduct": (
            MaximumEagerPreparedPairOptionProduct
        ),
        "EagerCompilationSelected": (
            ShouldCompilePreparedPairRelation
        ),
    })
    if ShouldCompilePreparedPairRelation:
        # Imported lazily to preserve AuthoritativePlanner's existing use of
        # this pipeline module.  The relation compiler consumes only portal
        # domains already produced by that single authoritative planner.
        from .AuthoritativePlanner import (
            CompilePhysicalBoundaryMandatoryPortalPairRelation,
        )

        CompiledPairwiseReservationSets = set(PairwiseReservationSets)
        # Compile one smallest eligible pair per exterior failure.  The exact
        # relation itself runs to completion under the shared typed deadline;
        # imposing a second certificate-count quantum here would repeatedly
        # revisit the same cached state index without ever publishing its
        # complete binary clauses.
        _SelectedPairOptionProduct, Pair = (
            EligiblePreparedPairRelations[0]
        )
        for Pair in (Pair,):
            Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
                Preparation,
                Pair,
                Resources,
                ShouldStop=ShouldStop,
                MaximumNewCertificates=None,
                PreferredApertureContractsBySignal={
                    Port.Signal: (
                        BuildPhysicalPortApertureContractFingerprint(Port)
                    )
                    for Port in Plan.Ports
                    if Port.Signal in Pair
                },
            )
            CompiledPairRelationDiagnostics.append({
                "RelationFingerprint": Relation.RelationFingerprint,
                "Signals": list(Relation.Signals),
                "ExpectedOptionPairCount": (
                    Relation.ExpectedOptionPairCount
                ),
                "CertificateCount": len(Relation.Certificates),
                "UnsatisfiableClauseCount": len(
                    Relation.UnsatisfiableApertureClauses
                ),
                "ForeignDependencyCertificateCount": (
                    Relation.ForeignDependencyCertificateCount
                ),
                "FactorCertificateCount": getattr(
                    Relation, "FactorCertificateCount", 0
                ),
                "FactorStateCount": getattr(
                    Relation, "FactorStateCount", 0
                ),
                "UniqueClaimStateCountsBySignal": dict(getattr(
                    Relation,
                    "UniqueClaimStateCountsBySignal",
                    (),
                )),
                "FactorExpansionCount": getattr(
                    Relation, "FactorExpansionCount", 0
                ),
                "CompatibilityIndexStatePairUpperBound": getattr(
                    Relation,
                    "CompatibilityIndexStatePairUpperBound",
                    0,
                ),
                "Complete": Relation.Complete,
            })
            CompiledPairwiseReservationSets.update(
                Relation.UnsatisfiableApertureClauses
            )
        PairwiseReservationSets = frozenset(
            CompiledPairwiseReservationSets
        )
    Scope = "none"
    RejectedAssemblyChoiceFingerprint = ""
    if RequiresExactAssemblyChoice:
        ComputedAssemblyChoiceFingerprint = (
            BuildPhysicalComponentAssemblyChoiceFingerprint(Plan)
        )
        DeclaredAssemblyChoiceFingerprint = str(getattr(
            Plan,
            "AssemblyChoiceFingerprint",
            "",
        ))
        if (
            DeclaredAssemblyChoiceFingerprint
            and DeclaredAssemblyChoiceFingerprint
            != ComputedAssemblyChoiceFingerprint
        ):
            raise ValueError(
                "physical assembly choice fingerprint identity mismatch"
            )
        RejectedAssemblyChoiceFingerprint = (
            DeclaredAssemblyChoiceFingerprint
            or ComputedAssemblyChoiceFingerprint
        )
        RejectedChoices = getattr(
            Resources,
            "RejectedPhysicalComponentAssemblyChoiceFingerprints",
            None,
        )
        if RejectedChoices is None:
            RejectedChoices = set()
            Resources.RejectedPhysicalComponentAssemblyChoiceFingerprints = (
                RejectedChoices
            )
        RejectedChoices.add(RejectedAssemblyChoiceFingerprint)
        Scope = "exact-assembly-port-feedthrough-choice"
    elif IndependentEmptyDomainSignals:
        for Signal, ReservationFingerprint in ReservationKeys:
            (
                Resources
                .RejectedPhysicalComponentPortReservationsBySignal
                .setdefault(Signal, set())
                .add(ReservationFingerprint)
            )
        Scope = "independent-empty-global-route-domain"
    elif RequestApertureProofComplete:
        (
            Resources
            .RejectedPhysicalComponentPortReservationSets
            .add(RejectedRequestApertureSet)
        )
        Scope = "request-aperture-factor-port-set"
    elif len(ReservationKeys) == 1:
        Signal, ReservationFingerprint = next(iter(ReservationKeys))
        (
            Resources
            .RejectedPhysicalComponentPortReservationsBySignal
            .setdefault(Signal, set())
            .add(ReservationFingerprint)
        )
        Scope = "single-port-aperture-reservation"
    elif PairwiseReservationSets:
        (
            Resources
            .RejectedPhysicalComponentPortReservationSets
            .update(PairwiseReservationSets)
        )
        Scope = "pairwise-port-aperture-reservation-sets"
    elif ReservationKeys:
        (
            Resources
            .RejectedPhysicalComponentPortReservationSets
            .add(ReservationKeys)
        )
        Scope = "exact-assembly-port-aperture-set"
    CorridorCache = getattr(
        Resources,
        "PhysicalPortCorridorDomainCache",
        {},
    )
    Recommendation = (
        SelectPhysicalComponentGlobalContractRecommendation(
            CorridorCache.values(),
            (Port.Signal for Port in Plan.Ports),
            RejectedSets=(
                Resources.RejectedPhysicalComponentPortReservationSets
            ),
            CompatibilityCache=getattr(
                Resources,
                "PhysicalGlobalAssignmentArcCompatibilityCache",
                None,
            ),
            ResourceGraphFingerprint=str(getattr(
                Plan,
                "ResourceGraphFingerprint",
                "",
            )),
            TechnologyFingerprint=str(getattr(
                Plan,
                "TechnologyFingerprint",
                "",
            )),
        )
        if CorridorCache
        else None
    )
    RecommendedContracts = (
        {
            Signal: Factor.PortGlobalContractFingerprint
            for Signal, Factor in Recommendation.items()
        }
        if Recommendation is not None
        else {}
    )
    MinimumDeltaPivotSignal = ""
    MinimumDeltaPivotDomainCounts: dict[str, int] = {}
    MinimumDeltaCertifiedExteriorDomainCounts: dict[str, int] = {}
    MinimumDeltaRetainedContracts: dict[str, str] = {}
    MinimumDeltaRetainedApertures: dict[str, str] = {}
    MinimumDeltaRetainedReservations: dict[str, str] = {}
    if (
        not RecommendedContracts
        and Scope in {
            "exact-assembly-port-aperture-set",
            "request-aperture-factor-port-set",
            "independent-empty-global-route-domain",
        }
    ):
        # A complete higher-order cut rejects the exact tuple, not each of
        # its literals.  Preserve every non-pivot global contract as a soft
        # preference so the next CSP solution changes the smallest useful
        # part of the assembly and can reuse completed exterior domains.
        # Universal conflict hubs are the strongest deterministic pivot;
        # otherwise use the reported failure net or the first dependency.
        RequestAperturePivotSignals = tuple(sorted(
            Signal
            for Signal, Fingerprint in RequestApertureFactorNoGood
            if Fingerprint.startswith("aperture-factor:")
            and Signal in DependencySignals
        ))
        UniversalConflictHubs = (
            ConflictGraph.get("UniversalConflictHubs", {})
            if isinstance(ConflictGraph, dict)
            else {}
        )
        HubSignals = tuple(sorted(
            (
                str(Signal),
                int(
                    Details.get("PairDegree", 0)
                    if isinstance(Details, dict)
                    else 0
                ),
            )
            for Signal, Details in (
                UniversalConflictHubs.items()
                if isinstance(UniversalConflictHubs, dict)
                else ()
            )
            if str(Signal) in DependencySignals
        ))
        RemainingApertureDomains = {
            str(Signal): frozenset(
                BuildPhysicalPortApertureContractFingerprint(Option)
                for Option in Options
                if BuildPhysicalPortApertureContractFingerprint(Option)
                not in (
                    Resources
                    .RejectedPhysicalComponentPortReservationsBySignal
                    .get(str(Signal), set())
                )
            )
            for Signal, Options in (
                getattr(
                    Preparation,
                    "BoundaryPortReservationsBySignal",
                    (),
                )
                if Preparation is not None
                else ()
            )
            if str(Signal) in DependencySignals
        }
        MinimumDeltaPivotDomainCounts = {
            Signal: len(Fingerprints)
            for Signal, Fingerprints
            in sorted(RemainingApertureDomains.items())
        }
        CertifiedExteriorCoreCandidateCounts = {
            str(Signal): int(Count)
            for Signal, Count in dict(
                Diagnostics.get(
                    "HigherOrderPortReservationNoGoodCandidateCounts",
                    {},
                )
                or {}
            ).items()
            if (
                str(Signal) in DependencySignals
                and int(Count) > 0
            )
        }
        MinimumDeltaCertifiedExteriorDomainCounts = dict(
            CertifiedExteriorCoreCandidateCounts
        )
        SmallestCertifiedExteriorDomainSignals = tuple(sorted(
            CertifiedExteriorCoreCandidateCounts,
            key=lambda Signal: (
                CertifiedExteriorCoreCandidateCounts[Signal],
                Signal,
            ),
        ))
        SmallestRemainingDomainSignals = tuple(sorted(
            RemainingApertureDomains,
            key=lambda Signal: (
                len(RemainingApertureDomains[Signal]),
                Signal,
                BuildPhysicalPortApertureContractFingerprint(
                    next(
                        Port for Port in Plan.Ports
                        if Port.Signal == Signal
                    )
                ),
            ),
        ))
        if IndependentEmptyDomainSignals:
            MinimumDeltaPivotSignal = min(
                IndependentEmptyDomainSignals
            )
        elif RequestAperturePivotSignals:
            MinimumDeltaPivotSignal = RequestAperturePivotSignals[0]
        elif SmallestCertifiedExteriorDomainSignals:
            MinimumDeltaPivotSignal = (
                SmallestCertifiedExteriorDomainSignals[0]
            )
        elif SmallestRemainingDomainSignals:
            # Keep the exact proof intact, but enumerate its cheapest useful
            # delta first.  This is the same MRV rule used by the interface
            # CSP and avoids retrying a wide port domain while a smaller
            # dependency domain can disprove the retained context.
            MinimumDeltaPivotSignal = SmallestRemainingDomainSignals[0]
        elif HubSignals:
            MinimumDeltaPivotSignal = max(
                HubSignals,
                key=lambda Value: (Value[1], Value[0]),
            )[0]
        else:
            ReportedFailureNet = str(
                ConflictGraph.get("FailureNet", "")
                if isinstance(ConflictGraph, dict)
                else ""
            )
            MinimumDeltaPivotSignal = (
                ReportedFailureNet
                if ReportedFailureNet in DependencySignals
                else min(DependencySignals)
                if DependencySignals
                else ""
            )
        MinimumDeltaRetainedContracts = {
            Port.Signal: BuildPhysicalPortGlobalContractFingerprint(Port)
            for Port in Plan.Ports
            if Port.Signal != MinimumDeltaPivotSignal
        }
        MinimumDeltaRetainedReservations = {
            Port.Signal: str(getattr(
                Port,
                "ReservationFingerprint",
                "",
            ))
            for Port in Plan.Ports
            if (
                Port.Signal != MinimumDeltaPivotSignal
                and str(getattr(
                    Port,
                    "ReservationFingerprint",
                    "",
                ))
            )
        }
        MinimumDeltaRetainedApertures = {
            Port.Signal: BuildPhysicalPortApertureContractFingerprint(Port)
            for Port in Plan.Ports
            if Port.Signal != MinimumDeltaPivotSignal
        }
        RecommendedContracts = dict(MinimumDeltaRetainedContracts)
    PreferredGlobalContracts = getattr(
        Resources,
        "PreferredPhysicalComponentGlobalContractsBySignal",
        None,
    )
    if PreferredGlobalContracts is None:
        PreferredGlobalContracts = {}
        Resources.PreferredPhysicalComponentGlobalContractsBySignal = (
            PreferredGlobalContracts
        )
    else:
        PreferredGlobalContracts.clear()
    PreferredGlobalContracts.update(RecommendedContracts)
    PreferredApertureContracts = getattr(
        Resources,
        "PreferredPhysicalComponentApertureContractsBySignal",
        None,
    )
    if PreferredApertureContracts is None:
        PreferredApertureContracts = {}
        Resources.PreferredPhysicalComponentApertureContractsBySignal = (
            PreferredApertureContracts
        )
    else:
        PreferredApertureContracts.clear()
    PreferredApertureContracts.update(MinimumDeltaRetainedApertures)
    PreferredPortReservations = getattr(
        Resources,
        "PreferredPhysicalComponentPortReservationsBySignal",
        None,
    )
    if PreferredPortReservations is None:
        PreferredPortReservations = {}
        Resources.PreferredPhysicalComponentPortReservationsBySignal = (
            PreferredPortReservations
        )
    else:
        PreferredPortReservations.clear()
    PreferredPortReservations.update(
        MinimumDeltaRetainedReservations
    )
    PrunedRetainedGlobalPlanCount = 0
    if CompiledPairRelationDiagnostics:
        Frontier = getattr(
            Resources,
            "RetainedPhysicalGlobalPlanFrontier",
            {},
        )
        RejectedSets = tuple(
            Resources.RejectedPhysicalComponentPortReservationSets
        )
        Retained = {}
        for Fingerprint, Entry in Frontier.items():
            EntryKeys = frozenset(
                (
                    Port.Signal,
                    BuildPhysicalPortApertureContractFingerprint(Port),
                )
                for Port in Entry.Assembly.Plan.Ports
            )
            if any(Clause <= EntryKeys for Clause in RejectedSets):
                PrunedRetainedGlobalPlanCount += 1
                continue
            Retained[Fingerprint] = Entry
        Resources.RetainedPhysicalGlobalPlanFrontier = Retained
    TraversalDiagnostics = (
        PreservePhysicalComponentAssemblyPlanDomainContinuation(
            Resources,
        )
    )
    return {
        "NoGoodScope": Scope,
        "NoGoodSignals": sorted(
            IndependentEmptyDomainSignals or DependencySignals
        ),
        "NoGoodReservationKeys": [
            [Signal, ReservationFingerprint]
            for Signal, ReservationFingerprint in sorted(
                ()
                if RequiresExactAssemblyChoice
                else RejectedRequestApertureSet
                if RequestApertureProofComplete
                else ReservationKeys
            )
        ],
        "NoGoodConstraintArity": (
            1
            if (
                RequiresExactAssemblyChoice
                or IndependentEmptyDomainSignals
                or len(ReservationKeys) == 1
            )
            else 2
            if PairwiseReservationSets
            else len(
                RejectedRequestApertureSet
                if RequestApertureProofComplete
                else ReservationKeys
            )
        ),
        "AssemblyPortCount": len(tuple(Plan.Ports)),
        "NoGoodReservationSets": [
            [list(Key) for Key in sorted(ReservationSet)]
            for ReservationSet in sorted(
                PairwiseReservationSets,
                key=lambda Value: tuple(sorted(Value)),
            )
        ],
        "CachedCorridorContractRecommendation": dict(
            sorted(RecommendedContracts.items())
        ),
        "CachedCorridorContractRecommendationComplete": bool(
            Recommendation is not None
        ),
        "MinimumDeltaReplanPivotSignal": MinimumDeltaPivotSignal,
        "MinimumDeltaPivotDomainCounts": dict(sorted(
            MinimumDeltaPivotDomainCounts.items()
        )),
        "MinimumDeltaCertifiedExteriorDomainCounts": dict(sorted(
            MinimumDeltaCertifiedExteriorDomainCounts.items()
        )),
        "MinimumDeltaRetainedGlobalContracts": dict(sorted(
            MinimumDeltaRetainedContracts.items()
        )),
        "MinimumDeltaRetainedApertureContracts": dict(sorted(
            MinimumDeltaRetainedApertures.items()
        )),
        "MinimumDeltaRetainedPortReservations": dict(sorted(
            MinimumDeltaRetainedReservations.items()
        )),
        **TraversalDiagnostics,
        "RejectedPortAssignmentFingerprint": (
            Plan.PortAssignmentFingerprint
        ),
        "GlobalPlanDependencyFingerprint": str(
            Diagnostics.get("GlobalPlanDependencyFingerprint", "")
        ),
        "GlobalPlanCutFamilyFingerprint": str(
            Diagnostics.get("GlobalPlanCutFamilyFingerprint", "")
        ),
        "GlobalPlanProofFingerprint": str(
            Diagnostics.get("GlobalPlanProofFingerprint", "")
        ),
        "PairwisePortReservationNoGoodProofComplete": (
            PairwiseProofComplete
        ),
        "CompiledMandatoryPortalPairRelations": (
            CompiledPairRelationDiagnostics
        ),
        "PreparedMandatoryPortalPairFactorStatus": (
            PreparedPairFactorArchitectureDiagnostics
        ),
        "CompiledMandatoryPortalPairClauseCount": sum(
            int(Value["UnsatisfiableClauseCount"])
            for Value in CompiledPairRelationDiagnostics
        ),
        "PrunedRetainedGlobalPlanCount": PrunedRetainedGlobalPlanCount,
        "RejectedAssemblyChoiceFingerprint": (
            RejectedAssemblyChoiceFingerprint
        ),
        "AssemblyPlanFeedthroughIndependentProofComplete": (
            FeedthroughIndependenceProved
        ),
        "AssemblyPlanDependencyProjectionProofComplete": (
            DependencyProjectionProofComplete
        ),
        "AssemblyPlanDependencyProjectionSignals": sorted(
            DependencySignals if DependencyProjectionProofComplete else ()
        ),
    }


def BuildGlobalRelaxedLocalProofDomainFingerprint(
    Problem: ComponentRoutingProblem,
) -> str:
    """Identify the bound local domain after removing global corridors."""
    Plan = Problem.PhysicalAssemblyPlan
    if Plan is None:
        raise ValueError("global-relaxed proof requires a physical plan")
    Origin = _Origin(Problem)
    SignalIdentities = dict(_SignalStructuralIdentities(Problem))

    def SignalIdentity(Signal: str) -> str:
        return SignalIdentities.get(Signal, "foreign:" + str(Signal))

    def TerminalDomainIdentity(Domain: Any) -> tuple[object, ...]:
        return (
            SignalIdentity(str(Domain.Signal)),
            Domain.TerminalRole,
            _Normalize(Domain.Terminal, Origin),
            Domain.TerminalFingerprint,
            bool(getattr(Domain, "Complete", True)),
            tuple(sorted(
                (
                    Candidate.CandidateFingerprint,
                    _Normalize(Candidate.Attachment, Origin),
                    tuple(
                        _Normalize(Value, Origin)
                        for Value in Candidate.Path
                    ),
                    _NormalizedClaimsIdentity(Candidate.Claims, Origin),
                    Candidate.Layer,
                    Candidate.Cost,
                )
                for Candidate in Domain.Candidates
            )),
        )

    def TransitDomainIdentity(Domain: Any) -> tuple[object, ...]:
        return (
            SignalIdentity(str(Domain.Signal)),
            Domain.PartitionAxis,
            Domain.PartitionFingerprint,
            Domain.Complete,
            tuple(sorted(
                (
                    Candidate.NetFingerprint,
                    tuple(sorted(
                        _Normalize(Value, Origin)
                        for Value in Candidate.Nodes
                    )),
                    tuple(sorted(
                        tuple(sorted((
                            _Normalize(First, Origin),
                            _Normalize(Second, Origin),
                        )))
                        for First, Second in Candidate.Edges
                    )),
                    _NormalizedClaimsIdentity(Candidate.Claims, Origin),
                    tuple(
                        (_Normalize(Position, Origin), Facing)
                        for Position, Facing in Candidate.Repeaters
                    ),
                    tuple(sorted(
                        _Normalize(Value, Origin)
                        for Value in Candidate.ExportedPorts
                    )),
                )
                for Candidate in Domain.Candidates
            )),
        )

    ResourceGraph = Problem.ResourceGraph
    Technology = getattr(ResourceGraph, "Technology", None)
    return _Fingerprint((
        "global-relaxed-local-proof-domain-v2",
        Problem.Fabric.FabricFingerprint,
        BuildPhysicalLocalAccessDomainFingerprint(Problem),
        tuple(sorted(SignalIdentities.items())),
        tuple(sorted(
            (
                "component"
                if Claim.Signal in Problem.ComponentSignals
                else "foreign",
                _NormalizedClaimsIdentity(Claim.Claims, Origin),
            )
            for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims)
        )),
        tuple(sorted(
            (
                Port.Signal,
                Port.Direction,
                tuple(
                    _Normalize(Value, Origin)
                    for Value in Port.OwnedTerminals
                ),
                tuple(Port.OwnedTerminalFingerprints),
                tuple(Port.OwnedCandidateFingerprints),
                _Normalize(Port.FabricAttachment, Origin),
                _Normalize(Port.Attachment, Origin),
                tuple(_Normalize(Value, Origin) for Value in Port.LocalPath),
                Port.Capacity,
            )
            for Port in Plan.Ports
        )),
        tuple(sorted(
            (
                Feedthrough.Signal,
                tuple(sorted(
                    (
                        _Normalize(Entry, Origin),
                        _Normalize(Exit, Origin),
                    )
                    for Entry, Exit in Feedthrough.EndpointPairs
                )),
                Feedthrough.Capacity,
                tuple(
                    _Normalize(Value, Origin)
                    for Value in Feedthrough.ReservedPathNodes
                ),
                (
                    _NormalizedClaimsIdentity(Feedthrough.Claims, Origin)
                    if Feedthrough.Claims is not None
                    else ()
                ),
                Feedthrough.ReservationFingerprint,
            )
            for Feedthrough in Plan.Feedthroughs
        )),
        tuple(sorted(
            TerminalDomainIdentity(Domain)
            for Domain in Problem.ExternalContinuationDomains
        )),
        tuple(sorted(
            TerminalDomainIdentity(Domain)
            for Domain in Problem.ForeignEscapeDomains
        )),
        tuple(sorted(
            TransitDomainIdentity(Domain)
            for Domain in Problem.ForeignTransitDomains
        )),
        tuple(sorted(
            (
                SignalIdentity(str(Signal)),
                _Normalize(Terminal, Origin),
                Role,
            )
            for Signal, Terminal, Role
            in Problem.ExternalContinuationTerminals
        )),
        Problem.MaximumPowerDistance,
        getattr(ResourceGraph, "GraphVersion", None),
        tuple(sorted(
            _Normalize(Value, Origin)
            for Value in getattr(ResourceGraph, "ActualBlocks", ())
        )),
        tuple(sorted(
            _Normalize(Value, Origin)
            for Value in getattr(ResourceGraph, "ElectricalBlocks", ())
        )),
        tuple(sorted(
            _Normalize(Value, Origin)
            for Value in getattr(ResourceGraph, "SolidBlocks", ())
        )),
        type(Technology).__qualname__,
        getattr(Technology, "TechnologyVersion", None),
        repr(Technology),
    ))


def BuildPhysicalLocalPairProofContextFingerprint(
    Problem: ComponentRoutingProblem,
    Preparation: PreparedPhysicalComponentPortFactorDomain,
) -> str:
    """Bind pair-support proofs to the complete global-independent context.

    Exact global channels and selected ports are intentionally removed.  All
    component-local geometry, immutable obstacles, declared feedthroughs, and
    finite terminal domains remain in the relaxed-domain identity.  Comparing
    the bound problem with the preparation problem prevents a post-planning
    mutation from being laundered into a reusable pre-assignment certificate.
    """
    if (
        Preparation is None
        or not Preparation.Complete
        or not Preparation.Feasible
        or not Preparation.DomainFingerprint
    ):
        raise ValueError(
            "local pair proof context requires a complete feasible preparation"
        )

    def CanonicalFingerprint(Value: ComponentRoutingProblem) -> str:
        Interface = Value.Interface
        if (
            Interface is None
            or not bool(getattr(Interface, "Complete", True))
            or not Value.DomainComplete
            or not bool(getattr(Value.Fabric, "Complete", True))
            or any(
                not bool(getattr(Domain, "Complete", True))
                for Domain in Value.OwnedTerminalDomains
            )
        ):
            raise ValueError("local pair proof input domain is incomplete")
        Feedthroughs = tuple(getattr(Interface, "Feedthroughs", ()))
        DeclaredFeedthroughSignals = frozenset(
            Value.Signal for Value in Feedthroughs
        )
        ForeignTransitSignals = frozenset(
            Domain.Signal for Domain in Value.ForeignTransitDomains
        )
        if not ForeignTransitSignals.issubset(DeclaredFeedthroughSignals):
            raise ValueError(
                "local pair proof contains undeclared foreign transit"
            )
        CanonicalInterface = replace(
            Interface,
            PhysicalPortReservations=(),
        )
        CanonicalProblem = replace(
            Value,
            Interface=CanonicalInterface,
            PhysicalAssemblyPlan=SimpleNamespace(
                Ports=(),
                Feedthroughs=Feedthroughs,
            ),
            ReservedGlobalClaimsBySignal=(),
        )
        return BuildGlobalRelaxedLocalProofDomainFingerprint(
            CanonicalProblem
        )

    PreparedContext = CanonicalFingerprint(Preparation.Problem)
    CurrentContext = CanonicalFingerprint(Problem)
    if PreparedContext != CurrentContext:
        raise ValueError(
            "local pair proof context differs from its prepared domain"
        )
    AccessCertificate = Preparation.AccessCertificate
    if (
        not bool(getattr(AccessCertificate, "Complete", False))
        or not bool(getattr(AccessCertificate, "Feasible", False))
        or not getattr(AccessCertificate, "CertificateFingerprint", "")
    ):
        raise ValueError(
            "local pair proof requires a complete feasible access certificate"
        )
    return _Fingerprint((
        "physical-local-pair-proof-context-v1",
        Preparation.DomainFingerprint,
        Preparation.PlacementFingerprint,
        Preparation.ComponentGraphFingerprint,
        Preparation.ResourceGraphFingerprint,
        Preparation.AccessCertificateFingerprint,
        PreparedContext,
    ))


def _BuildPhysicalComponentLocalFactorProjectionParts(
    Problem: ComponentRoutingProblem,
    LocalFactorsBySignal: Any,
) -> tuple[
    str,
    str,
    tuple[tuple[str, tuple[str, ...]], ...],
    dict[str, tuple[str, tuple[str, ...]]],
]:
    """Build name-free normalized identities shared by proofs and candidates."""
    Origin = _Origin(Problem)

    def ClaimsIdentity(Claims: Any) -> tuple[tuple[Position3, ...], ...]:
        if Claims is None:
            return ((), (), (), ())
        return tuple(
            tuple(sorted(
                _Normalize(Value, Origin)
                for Value in getattr(Claims, Attribute, ())
            ))
            for Attribute in (
                "WireCells",
                "SupportCells",
                "RequiredAirCells",
                "ElectricalCells",
            )
        )

    FactorsBySignal = {
        str(Signal): tuple(Factors)
        for Signal, Factors in LocalFactorsBySignal
    }
    Interface = Problem.Interface
    LogicalPortsBySignal = {
        str(Port.Signal): Port
        for Port in getattr(Interface, "Ports", ())
    }
    PhysicalPortsBySignal = {
        str(Port.Signal): Port
        for Port in getattr(
            Interface,
            "PhysicalPortReservations",
            (),
        )
    }
    Signals = tuple(sorted(FactorsBySignal))
    SignalRecordsByName: dict[
        str, tuple[str, tuple[str, ...]]
    ] = {}
    for Signal in Signals:
        LogicalPort = LogicalPortsBySignal.get(Signal)
        PhysicalPort = PhysicalPortsBySignal.get(Signal)
        OwnedDomainIdentity = tuple(sorted(
            (
                str(Domain.TerminalRole),
                _Normalize(Domain.Terminal, Origin),
                bool(getattr(Domain, "Complete", True)),
                tuple(sorted(
                    (
                        _Normalize(Candidate.Attachment, Origin),
                        tuple(
                            _Normalize(Value, Origin)
                            for Value in Candidate.Path
                        ),
                        ClaimsIdentity(Candidate.Claims),
                        int(Candidate.Layer),
                    )
                    for Candidate in Domain.Candidates
                )),
            )
            for Domain in Problem.OwnedTerminalDomains
            if str(Domain.Signal) == Signal
        ))
        SignalIdentity = _Fingerprint((
            "physical-component-local-factor-signal-v1",
            OwnedDomainIdentity,
            (
                str(LogicalPort.Direction),
                tuple(sorted(
                    _Normalize(Value, Origin)
                    for Value in LogicalPort.OwnedTerminals
                )),
                int(LogicalPort.ExternalTerminalCount),
                int(LogicalPort.Capacity),
            ) if LogicalPort is not None else None,
            (
                str(PhysicalPort.Direction),
                tuple(sorted(
                    _Normalize(Value, Origin)
                    for Value in PhysicalPort.OwnedTerminals
                )),
                _Normalize(PhysicalPort.FabricAttachment, Origin),
                int(PhysicalPort.Capacity),
            ) if PhysicalPort is not None else None,
        ))
        FactorIdentities = tuple(sorted(
            _Fingerprint((
                "physical-component-local-access-factor-v1",
                str(Factor.Direction),
                int(Factor.Capacity),
                tuple(sorted(
                    _Normalize(Value, Origin)
                    for Value in Factor.OwnedTerminals
                )),
                _Normalize(Factor.FabricAttachment, Origin),
                tuple(
                    _Normalize(Value, Origin)
                    for Value in Factor.LocalPath
                ),
                ClaimsIdentity(Factor.LocalClaims),
                tuple(sorted(
                    (
                        _Normalize(Candidate.Attachment, Origin),
                        tuple(
                            _Normalize(Value, Origin)
                            for Value in Candidate.Path
                        ),
                        ClaimsIdentity(Candidate.Claims),
                        int(Candidate.Layer),
                    )
                    for Candidate in Factor.OwnedAccessCandidates
                )),
            ))
            for Factor in FactorsBySignal[Signal]
        ))
        SignalRecordsByName[Signal] = (
            SignalIdentity,
            FactorIdentities,
        )

    SignalFactorIdentities = tuple(sorted(
        SignalRecordsByName.values()
    ))
    Fabric = Problem.Fabric
    ComponentTopologyFingerprint = _Fingerprint((
        "physical-component-local-topology-v1",
        tuple(sorted(
            _Normalize(Value, Origin) for Value in Fabric.Nodes
        )),
        tuple(sorted(
            tuple(sorted((
                _Normalize(First, Origin),
                _Normalize(Second, Origin),
            )))
            for First, Second in Fabric.Edges
        )),
        tuple(sorted(
            _Normalize(Value, Origin) for Value in Fabric.IngressNodes
        )),
        str(Fabric.TopologyKind),
        int(Problem.MaximumPowerDistance),
        tuple(sorted(
            len(Record[1])
            for Record in SignalFactorIdentities
        )),
    ))
    FeedthroughIdentity = tuple(sorted(
        (
            tuple(sorted(
                (
                    _Normalize(Entry, Origin),
                    _Normalize(Exit, Origin),
                )
                for Entry, Exit in Feedthrough.EndpointPairs
            )),
            int(Feedthrough.Capacity),
            tuple(
                _Normalize(Value, Origin)
                for Value in Feedthrough.ReservedPathNodes
            ),
            ClaimsIdentity(Feedthrough.Claims),
        )
        for Feedthrough in getattr(Interface, "Feedthroughs", ())
    ))
    InterfaceContractFingerprint = _Fingerprint((
        "physical-component-local-interface-v1",
        bool(getattr(Interface, "Complete", False)),
        tuple(sorted(
            SignalIdentity
            for SignalIdentity, _ in SignalFactorIdentities
        )),
        FeedthroughIdentity,
    ))
    return (
        ComponentTopologyFingerprint,
        InterfaceContractFingerprint,
        SignalFactorIdentities,
        SignalRecordsByName,
    )


def BuildPhysicalComponentLocalFactorProjection(
    Problem: ComponentRoutingProblem,
    LocalFactorsBySignal: Any,
    *,
    ResourceGraphFingerprint: str = "",
    TechnologyFingerprint: str = "",
    Complete: bool = True,
) -> PhysicalComponentLocalFactorProjection:
    """Freeze the cheap local part of a retained placement before apertures."""
    (
        ComponentTopologyFingerprint,
        InterfaceContractFingerprint,
        SignalFactorIdentities,
        _,
    ) = _BuildPhysicalComponentLocalFactorProjectionParts(
        Problem,
        LocalFactorsBySignal,
    )
    Plan = Problem.PhysicalAssemblyPlan
    ResourceIdentity = str(
        ResourceGraphFingerprint
        or getattr(Plan, "ResourceGraphFingerprint", "")
    )
    TechnologyIdentity = str(
        TechnologyFingerprint
        or getattr(Plan, "TechnologyFingerprint", "")
    )
    DomainComplete = bool(
        Complete
        and ResourceIdentity
        and TechnologyIdentity
        and Problem.DomainComplete
        and Problem.Fabric.Complete
        and Problem.Interface is not None
        and Problem.Interface.Complete
        and SignalFactorIdentities
        and all(Factors for _, Factors in SignalFactorIdentities)
        and all(
            bool(getattr(Domain, "Complete", False))
            for Domain in Problem.OwnedTerminalDomains
        )
    )
    LocalFactorDomainFingerprint = _Fingerprint((
        "physical-component-local-factor-domain-v1",
        ComponentTopologyFingerprint,
        InterfaceContractFingerprint,
        SignalFactorIdentities,
    ))
    ProjectionFingerprint = _Fingerprint((
        "physical-component-local-factor-projection-v1",
        ComponentTopologyFingerprint,
        ResourceIdentity,
        TechnologyIdentity,
        InterfaceContractFingerprint,
        LocalFactorDomainFingerprint,
        DomainComplete,
    ))
    return PhysicalComponentLocalFactorProjection(
        ProjectionFingerprint=ProjectionFingerprint,
        ComponentTopologyFingerprint=ComponentTopologyFingerprint,
        ResourceGraphFingerprint=ResourceIdentity,
        TechnologyFingerprint=TechnologyIdentity,
        InterfaceContractFingerprint=InterfaceContractFingerprint,
        LocalFactorDomainFingerprint=LocalFactorDomainFingerprint,
        SignalFactorIdentities=SignalFactorIdentities,
        Complete=DomainComplete,
    )


def BuildPhysicalComponentLocalFactorUnsatCertificate(
    Problem: ComponentRoutingProblem,
    LocalFactorsBySignal: Any,
    Proof: dict[str, object],
    *,
    ResourceGraphFingerprint: str = "",
    TechnologyFingerprint: str = "",
) -> PhysicalComponentLocalFactorUnsatCertificate:
    """Bind one complete architectural-UNSAT proof to its cheap projection."""
    Projection = BuildPhysicalComponentLocalFactorProjection(
        Problem,
        LocalFactorsBySignal,
        ResourceGraphFingerprint=ResourceGraphFingerprint,
        TechnologyFingerprint=TechnologyFingerprint,
    )
    _, _, _, SignalRecordsByName = (
        _BuildPhysicalComponentLocalFactorProjectionParts(
            Problem,
            LocalFactorsBySignal,
        )
    )
    CoreSignals = tuple(sorted({
        str(Value)
        for Value in Proof.get(
            "GlobalRelaxedLocalUnsatCoreSignals",
            (),
        ) or ()
    }))
    CoreSignalFactorIdentities = tuple(sorted(
        SignalRecordsByName[Signal]
        for Signal in CoreSignals
        if Signal in SignalRecordsByName
    ))
    SourceProofFingerprint = str(Proof.get(
        "GlobalRelaxedLocalProofFingerprint",
        "",
    ))
    ProofKind = str(Proof.get(
        "GlobalRelaxedLocalUnsatCoreKind",
        "",
    ))
    Complete = bool(
        Projection.Complete
        and Proof.get("GlobalRelaxedLocalProofComplete", False)
        and Proof.get("GlobalRelaxedLocalCoreComplete", False)
        and Proof.get("GlobalRelaxedLocalProofStatus", "")
        == "architectural-unsatisfiable"
        and SourceProofFingerprint
        and ProofKind
        and CoreSignals
        and len(CoreSignalFactorIdentities) == len(CoreSignals)
    )
    ProofFingerprint = _Fingerprint((
        "physical-component-normalized-local-factor-proof-v1",
        Projection.ProjectionFingerprint,
        CoreSignalFactorIdentities,
        ProofKind,
        Complete,
    ))
    CertificateFingerprint = _Fingerprint((
        "physical-component-local-factor-unsat-certificate-v1",
        Projection.ProjectionFingerprint,
        CoreSignalFactorIdentities,
        ProofFingerprint,
        ProofKind,
        Complete,
    ))
    return PhysicalComponentLocalFactorUnsatCertificate(
        CertificateFingerprint=CertificateFingerprint,
        ProjectionFingerprint=Projection.ProjectionFingerprint,
        ComponentTopologyFingerprint=(
            Projection.ComponentTopologyFingerprint
        ),
        ResourceGraphFingerprint=Projection.ResourceGraphFingerprint,
        TechnologyFingerprint=Projection.TechnologyFingerprint,
        InterfaceContractFingerprint=(
            Projection.InterfaceContractFingerprint
        ),
        LocalFactorDomainFingerprint=(
            Projection.LocalFactorDomainFingerprint
        ),
        CoreSignalFactorIdentities=CoreSignalFactorIdentities,
        ProofFingerprint=ProofFingerprint,
        ProofKind=ProofKind,
        Complete=Complete,
    )


def ComparePhysicalComponentLocalFactorProjection(
    Certificate: PhysicalComponentLocalFactorUnsatCertificate,
    Projection: PhysicalComponentLocalFactorProjection,
) -> PhysicalComponentLocalFactorProjectionComparison:
    """Compare a retained placement without treating similarity as proof."""
    Reason = ""
    if not Certificate.Complete:
        Reason = "incomplete-certificate"
    elif not Projection.Complete:
        Reason = "incomplete-projection"
    elif (
        Certificate.ResourceGraphFingerprint
        != Projection.ResourceGraphFingerprint
    ):
        Reason = "resource-graph-mismatch"
    elif (
        Certificate.TechnologyFingerprint
        != Projection.TechnologyFingerprint
    ):
        Reason = "technology-mismatch"
    elif (
        Certificate.ComponentTopologyFingerprint
        != Projection.ComponentTopologyFingerprint
    ):
        Reason = "component-topology-mismatch"
    elif (
        Certificate.InterfaceContractFingerprint
        != Projection.InterfaceContractFingerprint
    ):
        Reason = "interface-contract-mismatch"
    IdentityCompatible = not Reason
    CoreCounts: dict[tuple[str, tuple[str, ...]], int] = {}
    for Record in Projection.SignalFactorIdentities:
        CoreCounts[Record] = CoreCounts.get(Record, 0) + 1
    CoreFactorMatchCount = 0
    for Record in Certificate.CoreSignalFactorIdentities:
        if CoreCounts.get(Record, 0) > 0:
            CoreFactorMatchCount += 1
            CoreCounts[Record] -= 1
    ExactDomainMatch = bool(
        IdentityCompatible
        and Certificate.LocalFactorDomainFingerprint
        == Projection.LocalFactorDomainFingerprint
    )
    if IdentityCompatible and not ExactDomainMatch:
        Reason = "local-contract-domain-mismatch"
    CanPrune = bool(ExactDomainMatch and Certificate.Complete)
    ComparisonFingerprint = _Fingerprint((
        "physical-component-local-factor-comparison-v1",
        Certificate.CertificateFingerprint,
        Projection.ProjectionFingerprint,
        IdentityCompatible,
        ExactDomainMatch,
        CoreFactorMatchCount,
        len(Certificate.CoreSignalFactorIdentities),
        CanPrune,
        Reason,
    ))
    return PhysicalComponentLocalFactorProjectionComparison(
        ComparisonFingerprint=ComparisonFingerprint,
        IdentityCompatible=IdentityCompatible,
        ExactDomainMatch=ExactDomainMatch,
        CoreFactorMatchCount=CoreFactorMatchCount,
        CoreFactorCount=len(Certificate.CoreSignalFactorIdentities),
        CanPrune=CanPrune,
        RejectionReason=Reason,
    )


def ProveGlobalRelaxedLocalUnsatisfiability(
    Problem: ComponentRoutingProblem,
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    VariantPortfolioCache: dict[Any, Any] | None = None,
    NetVariantConstructionCache: dict[Any, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
) -> dict[str, object]:
    """Run one non-recursive local proof without reserved global claims."""
    DomainFingerprint = BuildGlobalRelaxedLocalProofDomainFingerprint(
        Problem
    )
    if DeadlineSeconds is not None and DeadlineSeconds <= 0:
        return {
            "GlobalRelaxedLocalProofComplete": False,
            "GlobalRelaxedLocalCoreComplete": False,
            "GlobalRelaxedLocalProofStatus": "incomplete",
            "GlobalRelaxedLocalProofFingerprint": "",
            "GlobalRelaxedLocalDomainFingerprint": DomainFingerprint,
            "GlobalRelaxedLocalUnsatCoreSignals": [],
            "GlobalRelaxedLocalUnsatCoreKind": "",
        }
    RelaxedProblem = replace(
        Problem,
        ProblemFingerprint=_Fingerprint((
            "global-relaxed-local-proof-problem-v1",
            DomainFingerprint,
        )),
        ReservedGlobalClaimsBySignal=(),
    )
    RelaxedSolve = SolveComponentRoutingProblem(
        RelaxedProblem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        DiscoveryVariantLimit=None,
        VariantPortfolioCache=VariantPortfolioCache,
        NetVariantConstructionCache=NetVariantConstructionCache,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        NetVariantDiscoveryStateCache=NetVariantDiscoveryStateCache,
    )
    Complete = RelaxedSolve.Status == "architectural-unsatisfiable"
    RelaxedDiagnostics = dict(RelaxedSolve.Diagnostics or {})
    CoreComplete = bool(
        RelaxedDiagnostics.get("LocalUnsatCoreComplete", False)
    )
    return {
        "GlobalRelaxedLocalProofComplete": Complete,
        "GlobalRelaxedLocalCoreComplete": CoreComplete,
        "GlobalRelaxedLocalProofStatus": RelaxedSolve.Status,
        "GlobalRelaxedLocalProofFingerprint": (
            RelaxedSolve.ProofFingerprint if Complete else ""
        ),
        "GlobalRelaxedLocalDomainFingerprint": DomainFingerprint,
        "GlobalRelaxedLocalUnsatCoreSignals": list(
            RelaxedDiagnostics.get("LocalUnsatCoreSignals", ()) or ()
        ),
        "GlobalRelaxedLocalUnsatCoreKind": str(
            RelaxedDiagnostics.get("LocalUnsatCoreKind", "")
        ),
        "LocalUnsatCoreProjectionFingerprint": str(
            RelaxedDiagnostics.get(
                "LocalUnsatCoreProjectionFingerprint",
                "",
            )
        ),
        "GlobalRelaxedLocalCurrentSignal": str(
            RelaxedDiagnostics.get("LocalUnsatCoreCurrentSignal", "")
        ),
        "GlobalRelaxedLocalCompleteSignal": str(
            RelaxedDiagnostics.get("LocalUnsatCoreCompleteSignal", "")
        ),
        "GlobalRelaxedLocalProofExpansionCount": (
            RelaxedSolve.ExpansionCount
        ),
    }


def ProveClosedComponentOwnedSignalFrontiers(
    Problem: ComponentRoutingProblem,
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    CompletedProofCache: dict[
        str, ComponentRoutingSolveResult
    ] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
) -> ComponentRoutingSolveResult:
    """Certify per-signal local frontier existence before global routing.

    This eligibility proof stops before the net-capacity CSP and before any
    routed component template is materialized.  It may therefore reject a
    placement whose owned signal domain is structurally empty without
    violating the port-first global-before-local compilation boundary.
    """
    Plan = Problem.PhysicalAssemblyPlan
    DomainFingerprint = (
        BuildGlobalRelaxedLocalProofDomainFingerprint(Problem)
        if Plan is not None
        else _Fingerprint((
            "closed-component-owned-frontier-domain-v1",
            Problem.ProblemFingerprint,
            Problem.Fabric.FabricFingerprint,
            getattr(Problem.Interface, "InterfaceFingerprint", ""),
            getattr(Problem.ResourceGraph, "GraphVersion", ""),
            getattr(
                getattr(Problem.ResourceGraph, "Technology", None),
                "TechnologyVersion",
                "",
            ),
            Problem.MaximumPowerDistance,
        ))
    )
    Cached = (
        CompletedProofCache.get(DomainFingerprint)
        if CompletedProofCache is not None
        else None
    )
    if Cached is not None:
        if Cached.Template is not None:
            raise ValueError(
                "cached owned-signal frontier proof materialized a template"
            )
        return replace(
            Cached,
            Diagnostics={
                **dict(Cached.Diagnostics or {}),
                "OwnedSignalFrontierProofCacheHit": True,
                "OwnedSignalFrontierProofDomainFingerprint": (
                    DomainFingerprint
                ),
            },
        )
    Result = SolveComponentRoutingProblem(
        Problem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        StopAfterOwnedSignalFrontierProof=True,
    )
    if Result.Template is not None:
        raise ValueError(
            "owned-signal frontier eligibility materialized a template"
        )
    if Result.Exhaustive and CompletedProofCache is not None:
        CompletedProofCache[DomainFingerprint] = replace(
            Result,
            Diagnostics={
                **dict(Result.Diagnostics or {}),
                "OwnedSignalFrontierProofCacheHit": False,
                "OwnedSignalFrontierProofDomainFingerprint": (
                    DomainFingerprint
                ),
            },
        )
    return Result


def _SelectPhysicalComponentSymbolicPortPairFactors(
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    Signals: tuple[str, ...],
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], str]]:
    """Return the complete supported local-access and seam relation."""
    LocalFactorsBySignal = dict(FactorDomain.LocalAccessFactorsBySignal)
    SupportedLocalAccessFingerprintsBySignal = {
        str(Signal): frozenset(
            str(Support.LocalAccessFingerprint)
            for Support in Supports
        )
        for Signal, Supports
        in FactorDomain.LocalApertureSupportBySignal
    }
    FactorsBySignal: dict[str, dict[str, Any]] = {}
    SeamFingerprintByLocalAccess: dict[tuple[str, str], str] = {}
    for Signal in Signals:
        Supported = SupportedLocalAccessFingerprintsBySignal.get(
            Signal,
            frozenset(),
        )
        ByLocalAccess = {}
        for Factor in LocalFactorsBySignal.get(Signal, ()):
            LocalAccessFingerprint = str(Factor.LocalAccessFingerprint)
            if LocalAccessFingerprint not in Supported:
                continue
            Existing = ByLocalAccess.get(LocalAccessFingerprint)
            if Existing is not None and Existing != Factor:
                raise ValueError(
                    "symbolic port pair local-access identity collision"
                )
            ByLocalAccess[LocalAccessFingerprint] = Factor
            SeamFingerprintByLocalAccess[(
                Signal,
                LocalAccessFingerprint,
            )] = (
                str(getattr(Factor, "SeamContractFingerprint", ""))
                or BuildPhysicalPortSeamContractFingerprint(Factor)
            )
        if not ByLocalAccess:
            raise ValueError(
                f"symbolic port pair has no complete seam domain for {Signal}"
            )
        FactorsBySignal[Signal] = ByLocalAccess
    return FactorsBySignal, SeamFingerprintByLocalAccess


def _BuildPhysicalComponentSymbolicPortPairContext(
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    Signals: tuple[str, ...],
    FactorsBySignal: dict[str, dict[str, Any]],
    SeamFingerprintByLocalAccess: dict[tuple[str, str], str],
) -> dict[str, str]:
    """Build every immutable identity on which a pair proof depends."""
    PlacementFingerprint = str(Problem.PlacementFingerprint)
    PreparedDomainFingerprint = str(FactorDomain.DomainFingerprint)
    ComponentGraphFingerprint = str(FactorDomain.ComponentGraphFingerprint)
    FabricFingerprint = str(Problem.Fabric.FabricFingerprint)
    ResourceGraphFingerprint = str(FactorDomain.ResourceGraphFingerprint)
    TechnologyFingerprint = str(getattr(
        FactorDomain.AccessCertificate,
        "TechnologyFingerprint",
        "",
    ))
    AccessCertificateFingerprint = str(getattr(
        FactorDomain.AccessCertificate,
        "CertificateFingerprint",
        "",
    ))
    # Pair support is compiled over the complete prepared local-access/seam
    # domain, not over one subsequently selected global aperture tuple. Bind
    # cache identity to that immutable preparation interface so equivalent
    # CSP contracts reuse the same exact signal-local state certificates.
    InterfaceFingerprint = str(getattr(
        FactorDomain.Problem.Interface,
        "InterfaceFingerprint",
        "",
    ))
    SupportIdentity = tuple(sorted(
        (
            str(Signal),
            str(Support.LocalAccessFingerprint),
            str(Support.ApertureOptionFingerprint),
            str(Support.SupportFingerprint),
        )
        for Signal, Supports
        in FactorDomain.LocalApertureSupportBySignal
        if str(Signal) in Signals
        for Support in Supports
    ))
    LocalAccessIdentity = tuple(
        (
            Signal,
            tuple(
                (
                    LocalAccessFingerprint,
                    str(Factor.LocalContractFingerprint),
                    str(Factor.FabricDomainFingerprint),
                    tuple(Factor.OwnedTerminalFingerprints),
                    tuple(Factor.OwnedCandidateFingerprints),
                    tuple(Factor.LocalPath),
                    tuple(sorted(map(str, Factor.LocalClaims.ResourceIds))),
                )
                for LocalAccessFingerprint, Factor in sorted(
                    FactorsBySignal[Signal].items()
                )
            ),
        )
        for Signal in Signals
    )
    LocalAccessDomainFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-local-access-domain-v1",
        LocalAccessIdentity,
        SupportIdentity,
    ))
    SeamDomainFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-seam-domain-v1",
        tuple(sorted(
            (
                Signal,
                LocalAccessFingerprint,
                SeamFingerprint,
            )
            for (Signal, LocalAccessFingerprint), SeamFingerprint
            in SeamFingerprintByLocalAccess.items()
        )),
    ))
    DomainFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-domain-v2",
        PreparedDomainFingerprint,
        PlacementFingerprint,
        ComponentGraphFingerprint,
        FabricFingerprint,
        ResourceGraphFingerprint,
        TechnologyFingerprint,
        AccessCertificateFingerprint,
        InterfaceFingerprint,
        LocalAccessDomainFingerprint,
        SeamDomainFingerprint,
        Signals,
    ))
    return {
        "DomainFingerprint": DomainFingerprint,
        "PreparedDomainFingerprint": PreparedDomainFingerprint,
        "PlacementFingerprint": PlacementFingerprint,
        "ComponentGraphFingerprint": ComponentGraphFingerprint,
        "FabricFingerprint": FabricFingerprint,
        "ResourceGraphFingerprint": ResourceGraphFingerprint,
        "TechnologyFingerprint": TechnologyFingerprint,
        "AccessCertificateFingerprint": AccessCertificateFingerprint,
        "InterfaceFingerprint": InterfaceFingerprint,
        "LocalAccessDomainFingerprint": LocalAccessDomainFingerprint,
        "SeamDomainFingerprint": SeamDomainFingerprint,
    }


def _BuildPhysicalComponentSymbolicPortPairVariantProblem(
    Problem: ComponentRoutingProblem,
    Signal: str,
    LocalAccessFingerprint: str,
    Factor: Any,
) -> ComponentRoutingProblem:
    """Bind one exact local access while leaving global ownership relaxed."""
    if Problem.Interface is None:
        raise ValueError("symbolic port pair requires a closed interface")
    BasePort = next((
        Port
        for Port in Problem.Interface.PhysicalPortReservations
        if str(Port.Signal) == Signal
    ), None)
    SyntheticBasePort = BasePort is None
    if BasePort is None:
        Attachment = (
            tuple(Factor.LocalPath[-1])
            if Factor.LocalPath
            else tuple(Factor.FabricAttachment)
        )
        BasePort = PhysicalComponentPortReservation(
            Signal=Signal,
            Direction=Factor.Direction,
            OwnedTerminals=Factor.OwnedTerminals,
            OwnedTerminalFingerprints=(
                Factor.OwnedTerminalFingerprints
            ),
            OwnedCandidateFingerprints=(
                Factor.OwnedCandidateFingerprints
            ),
            FabricDomainFingerprint=Factor.FabricDomainFingerprint,
            FabricAttachment=Factor.FabricAttachment,
            Attachment=Attachment,
            LocalPath=Factor.LocalPath,
            GlobalPath=(Attachment,),
            Claims=Factor.LocalClaims,
            LocalClaims=Factor.LocalClaims,
            GlobalClaims=RoutingResourceClaims(),
            OwnedAccessCandidates=Factor.OwnedAccessCandidates,
            Capacity=Factor.Capacity,
        )
    SeamFingerprint = BuildPhysicalPortSeamContractFingerprint(Factor)
    VariantPort = replace(
        BasePort,
        Direction=Factor.Direction,
        OwnedTerminals=Factor.OwnedTerminals,
        OwnedTerminalFingerprints=Factor.OwnedTerminalFingerprints,
        OwnedCandidateFingerprints=Factor.OwnedCandidateFingerprints,
        FabricDomainFingerprint=Factor.FabricDomainFingerprint,
        FabricAttachment=Factor.FabricAttachment,
        LocalPath=Factor.LocalPath,
        Claims=Factor.LocalClaims,
        LocalClaims=Factor.LocalClaims,
        OwnedAccessCandidates=Factor.OwnedAccessCandidates,
        Capacity=Factor.Capacity,
        ReservationFingerprint=_Fingerprint((
            "symbolic-port-domain-reservation-v1",
            Signal,
            SeamFingerprint,
            BasePort.Attachment,
            BasePort.GlobalPath,
        )),
        CertifiedLocalContractFingerprint=(
            Factor.LocalContractFingerprint
        ),
        CertifiedSeamContractFingerprint=SeamFingerprint,
        CertifiedSupportReservationFingerprint="",
    )
    VariantPorts = tuple(
        VariantPort if str(Port.Signal) == Signal else Port
        for Port in Problem.Interface.PhysicalPortReservations
    )
    if SyntheticBasePort:
        VariantPorts = (*VariantPorts, VariantPort)
    return replace(
        Problem,
        ProblemFingerprint=_Fingerprint((
            "symbolic-port-domain-net-state-v1",
            Problem.ProblemFingerprint,
            Signal,
            LocalAccessFingerprint,
        )),
        Interface=replace(
            Problem.Interface,
            PhysicalPortReservations=VariantPorts,
            PhysicalAssemblyPlanFingerprint="",
        ),
        ReservedGlobalClaimsBySignal=(),
    )


def _BuildPhysicalComponentSymbolicNetStateFingerprint(
    States: Iterable[Any],
) -> str:
    """Bind a certificate to exact state contents, not only its cache key."""
    return _Fingerprint((
        "physical-symbolic-net-state-domain-v1",
        tuple(sorted(
            (
                str(State.Signal),
                str(State.NetFingerprint),
                tuple(sorted(State.Nodes)),
                tuple(sorted(State.Edges)),
                tuple(sorted(map(str, State.Claims.ResourceIds))),
                tuple(State.CoveredTerminals),
                tuple(State.ExportedPorts),
                tuple(State.Repeaters),
            )
            for State in States
        )),
    ))


def ValidatePhysicalComponentSymbolicPortPairCertificate(
    Certificate: PhysicalComponentSymbolicPortPairCertificate,
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    SignalPair: Iterable[str],
    *,
    NetStateCache: dict[str, Any] | None = None,
) -> None:
    """Reject cached pair proofs unless every dependency matches exactly."""
    Signals = tuple(sorted(frozenset(map(str, SignalPair))))
    if len(Signals) != 2:
        raise ValueError("symbolic port pair validation requires two signals")
    FactorsBySignal, SeamFingerprintByLocalAccess = (
        _SelectPhysicalComponentSymbolicPortPairFactors(
            FactorDomain,
            Signals,
        )
    )
    Context = _BuildPhysicalComponentSymbolicPortPairContext(
        Problem,
        FactorDomain,
        Signals,
        FactorsBySignal,
        SeamFingerprintByLocalAccess,
    )
    FieldValues = {
        "DomainFingerprint": Certificate.DomainFingerprint,
        "PreparedDomainFingerprint": Certificate.PreparedDomainFingerprint,
        "PlacementFingerprint": Certificate.PlacementFingerprint,
        "ComponentGraphFingerprint": Certificate.ComponentGraphFingerprint,
        "FabricFingerprint": Certificate.FabricFingerprint,
        "ResourceGraphFingerprint": Certificate.ResourceGraphFingerprint,
        "TechnologyFingerprint": Certificate.TechnologyFingerprint,
        "AccessCertificateFingerprint": (
            Certificate.AccessCertificateFingerprint
        ),
        "InterfaceFingerprint": Certificate.InterfaceFingerprint,
        "LocalAccessDomainFingerprint": (
            Certificate.LocalAccessDomainFingerprint
        ),
        "SeamDomainFingerprint": Certificate.SeamDomainFingerprint,
    }
    Mismatches = tuple(
        FieldName
        for FieldName, Expected in Context.items()
        if str(FieldValues.get(FieldName, "")) != str(Expected)
    )
    if Certificate.SignalPair != Signals:
        Mismatches = (*Mismatches, "SignalPair")
    ExpectedAccesses = tuple(
        (
            Signal,
            tuple(sorted(FactorsBySignal[Signal])),
        )
        for Signal in Signals
    )
    ExpectedSeams = tuple(sorted(
        (
            Signal,
            LocalAccessFingerprint,
            SeamFingerprint,
        )
        for (Signal, LocalAccessFingerprint), SeamFingerprint
        in SeamFingerprintByLocalAccess.items()
    ))
    if Certificate.LocalAccessFingerprintsBySignal != ExpectedAccesses:
        Mismatches = (*Mismatches, "LocalAccessFingerprintsBySignal")
    if Certificate.SeamFingerprintByLocalAccess != ExpectedSeams:
        Mismatches = (*Mismatches, "SeamFingerprintByLocalAccess")
    BindingKeys = tuple(
        (Signal, LocalAccessFingerprint, CacheKey)
        for Signal, LocalAccessFingerprint, CacheKey, _StateFingerprint
        in Certificate.NetStateBindings
    )
    if BindingKeys != Certificate.NetStateCacheKeys:
        Mismatches = (*Mismatches, "NetStateCacheKeys")
    RelaxedProblem = replace(
        FactorDomain.Problem,
        ReservedGlobalClaimsBySignal=(),
    )
    ExpectedCacheKeys = []
    for Signal in Signals:
        PreparedContextFingerprint = (
            _BuildPreparedComponentSymbolicNetStateContextFingerprint(
                RelaxedProblem,
                Signal,
            )
        )
        for LocalAccessFingerprint, Factor in sorted(
            FactorsBySignal[Signal].items()
        ):
            VariantProblem = (
                _BuildPhysicalComponentSymbolicPortPairVariantProblem(
                    RelaxedProblem,
                    Signal,
                    LocalAccessFingerprint,
                    Factor,
                )
            )
            ExpectedCacheKeys.append((
                Signal,
                LocalAccessFingerprint,
                BuildComponentSymbolicNetStateCacheKey(
                    VariantProblem,
                    Signal,
                    PreparedContextFingerprint=(
                        PreparedContextFingerprint
                    ),
                ),
            ))
    ExpectedCacheKeySet = frozenset(ExpectedCacheKeys)
    ActualCacheKeySet = frozenset(Certificate.NetStateCacheKeys)
    if (
        (Certificate.Complete and ActualCacheKeySet != ExpectedCacheKeySet)
        or not ActualCacheKeySet <= ExpectedCacheKeySet
        or len(ActualCacheKeySet) != len(Certificate.NetStateCacheKeys)
    ):
        Mismatches = (*Mismatches, "NetStateCacheDomain")
    ExpectedStateDomainFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-state-bindings-v1",
        Certificate.NetStateBindings,
    ))
    if (
        Certificate.NetStateDomainFingerprint
        != ExpectedStateDomainFingerprint
    ):
        Mismatches = (*Mismatches, "NetStateDomainFingerprint")
    if NetStateCache is not None:
        for (
            _Signal,
            _LocalAccessFingerprint,
            CacheKey,
            StateFingerprint,
        ) in Certificate.NetStateBindings:
            Cached = NetStateCache.get(CacheKey)
            if Cached is None:
                continue
            States, _Diagnostics = Cached
            if (
                _BuildPhysicalComponentSymbolicNetStateFingerprint(States)
                != StateFingerprint
            ):
                Mismatches = (*Mismatches, "NetStateCacheContents")
                break
    ExpectedProofFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-proof-v2",
        Certificate.DomainFingerprint,
        Certificate.LocalAccessFingerprintsBySignal,
        Certificate.UnsupportedUnaryLocalAccess,
        Certificate.UnsupportedLocalAccessPairs,
        Certificate.UnsupportedUnarySeams,
        Certificate.UnsupportedSeamPairs,
        Certificate.NetStateDomainFingerprint,
        Certificate.Complete,
    ))
    if Certificate.ProofFingerprint != ExpectedProofFingerprint:
        Mismatches = (*Mismatches, "ProofFingerprint")
    if Mismatches:
        raise ValueError(
            "physical symbolic port-pair certificate identity mismatch: "
            + ", ".join(dict.fromkeys(Mismatches))
        )


def CompilePhysicalComponentSymbolicPortPairDomain(
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    SignalPair: Iterable[str],
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    NetStateCache: dict[str, Any] | None = None,
    CompletedCertificateCache: dict[
        str, PhysicalComponentSymbolicPortPairCertificate
    ] | None = None,
    CompleteCompatibilityIndexCache: dict[str, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
) -> PhysicalComponentSymbolicPortPairCertificate:
    """Compile exact unary/binary support across two complete seam domains."""
    if not FactorDomain.Complete:
        raise ValueError(
            "symbolic port pair compilation requires a complete factor domain"
        )
    if not FactorDomain.Feasible:
        raise ValueError(
            "symbolic port pair compilation requires a feasible factor domain"
        )
    if (
        Problem.PlacementFingerprint
        != FactorDomain.PlacementFingerprint
    ):
        raise ValueError(
            "symbolic port pair placement identity mismatch"
        )
    Signals = tuple(sorted(frozenset(map(str, SignalPair))))
    if len(Signals) != 2:
        raise ValueError("symbolic port pair compilation requires two signals")
    FactorsBySignal, SeamFingerprintByLocalAccess = (
        _SelectPhysicalComponentSymbolicPortPairFactors(
            FactorDomain,
            Signals,
        )
    )
    Context = _BuildPhysicalComponentSymbolicPortPairContext(
        Problem,
        FactorDomain,
        Signals,
        FactorsBySignal,
        SeamFingerprintByLocalAccess,
    )
    DomainFingerprint = Context["DomainFingerprint"]
    EffectiveNetStateCache = (
        NetStateCache if NetStateCache is not None else {}
    )
    Cached = (
        CompletedCertificateCache.get(DomainFingerprint)
        if CompletedCertificateCache is not None
        else None
    )
    if Cached is not None:
        ValidatePhysicalComponentSymbolicPortPairCertificate(
            Cached,
            Problem,
            FactorDomain,
            Signals,
            NetStateCache=EffectiveNetStateCache,
        )
        return Cached
    StartedAt = monotonic()
    # Signal-local tree-frontier contexts are independent of the subsequently
    # selected global boundary tuple.  Bind them to the immutable prepared
    # factor domain so unary compilation performed before CSP assignment and
    # later binary certificates share the same exact state cache.
    RelaxedProblem = replace(
        FactorDomain.Problem,
        ReservedGlobalClaimsBySignal=(),
    )
    PreparedNetStateContexts = {
        Signal: PrepareComponentSymbolicNetStateContext(
            RelaxedProblem,
            Signal,
            RouteClaimsConstructionCache=(
                RouteClaimsConstructionCache
            ),
        )
        for Signal in Signals
    }

    StatesBySignalAndLocalAccess: dict[
        tuple[str, str], tuple[Any, ...]
    ] = {}
    NetStateCacheKeys = []
    NetStateBindings = []
    Complete = True
    for Signal in Signals:
        VariantProblemsByAccess = {
            LocalAccessFingerprint: (
                _BuildPhysicalComponentSymbolicPortPairVariantProblem(
                    RelaxedProblem,
                    Signal,
                    LocalAccessFingerprint,
                    Factor,
                )
            )
            for LocalAccessFingerprint, Factor in sorted(
                FactorsBySignal[Signal].items()
            )
        }
        RemainingDeadline = (
            None
            if DeadlineSeconds is None
            else max(
                0.0,
                DeadlineSeconds - (monotonic() - StartedAt),
            )
        )
        CompilationsByAccess = (
            CompilePreparedComponentPhysicalFactorStateBatch(
                PreparedNetStateContexts[Signal],
                VariantProblemsByAccess,
                DeadlineSeconds=RemainingDeadline,
                WorkCheck=WorkCheck,
                SymbolicNetStateCache=EffectiveNetStateCache,
            )
        )
        for LocalAccessFingerprint in sorted(VariantProblemsByAccess):
            Compilation = CompilationsByAccess[LocalAccessFingerprint]
            CacheKey = Compilation.CacheKey
            if not Compilation.Complete or Compilation.States is None:
                Complete = False
                break
            States = Compilation.States
            StatesBySignalAndLocalAccess[
                (Signal, LocalAccessFingerprint)
            ] = tuple(States)
            NetStateCacheKeys.append((
                Signal,
                LocalAccessFingerprint,
                CacheKey,
            ))
            NetStateBindings.append((
                Signal,
                LocalAccessFingerprint,
                CacheKey,
                _BuildPhysicalComponentSymbolicNetStateFingerprint(States),
            ))
        if not Complete:
            break

    # A port-pair certificate is support in the complete closed component,
    # not merely pairwise non-overlap between those two nets. Compile every
    # other mandatory component-signal domain once and existentially include
    # it in each candidate pair check. Other interface signals may choose any
    # supported local access; internal signals use their unbound exact state
    # domain.
    MandatoryStateDomains: list[tuple[Any, ...]] = []
    MandatoryStateDomainsBySignal: dict[str, tuple[Any, ...]] = {}
    AllLocalFactorsBySignal = dict(
        FactorDomain.LocalAccessFactorsBySignal
    )
    SupportedAccessesBySignal = {
        str(Signal): frozenset(
            str(Support.LocalAccessFingerprint)
            for Support in Supports
        )
        for Signal, Supports
        in FactorDomain.LocalApertureSupportBySignal
    }
    if Complete:
        # Unary and binary port certificates describe the exact local
        # support relation at their stated arity.  Requiring every pair to
        # extend through all remaining component nets turns this stage into
        # repeated higher-order routing.  Complete multi-net capacity is
        # proved by ``ProveClosedComponentSymbolicCapacityEligibility`` and
        # its higher-order certificate path after CSP selection.
        for OtherSignal in (
            Signal
            for Signal in sorted(Problem.ComponentSignals)
            if str(Signal) not in Signals
        ):
            OtherFactors = {
                str(Factor.LocalAccessFingerprint): Factor
                for Factor in AllLocalFactorsBySignal.get(
                    OtherSignal,
                    (),
                )
                if str(Factor.LocalAccessFingerprint)
                in SupportedAccessesBySignal.get(
                    OtherSignal,
                    frozenset(),
                )
            }
            OtherContext = PrepareComponentSymbolicNetStateContext(
                RelaxedProblem,
                OtherSignal,
                RouteClaimsConstructionCache=(
                    RouteClaimsConstructionCache
                ),
            )
            RemainingDeadline = (
                None
                if DeadlineSeconds is None
                else max(
                    0.0,
                    DeadlineSeconds - (monotonic() - StartedAt),
                )
            )
            if OtherFactors:
                OtherProblems = {
                    LocalAccessFingerprint: (
                        _BuildPhysicalComponentSymbolicPortPairVariantProblem(
                            RelaxedProblem,
                            OtherSignal,
                            LocalAccessFingerprint,
                            Factor,
                        )
                    )
                    for LocalAccessFingerprint, Factor
                    in sorted(OtherFactors.items())
                }
                OtherCompilations = (
                    CompilePreparedComponentPhysicalFactorStateBatch(
                        OtherContext,
                        OtherProblems,
                        DeadlineSeconds=RemainingDeadline,
                        WorkCheck=WorkCheck,
                        SymbolicNetStateCache={},
                    )
                )
                OtherStates = []
                for LocalAccessFingerprint in sorted(OtherProblems):
                    Compilation = OtherCompilations[
                        LocalAccessFingerprint
                    ]
                    if (
                        not Compilation.Complete
                        or Compilation.States is None
                    ):
                        Complete = False
                        break
                    OtherStates.extend(Compilation.States)
                    # Mandatory-domain support is used only to invalidate
                    # unsupported local access pairs; it is not part of the
                    # pair-certificate cache identity.
            else:
                Compilation = CompilePreparedComponentSymbolicNetStates(
                    OtherContext,
                    RelaxedProblem,
                    DeadlineSeconds=RemainingDeadline,
                    WorkCheck=WorkCheck,
                    SymbolicNetStateCache={},
                )
                if not Compilation.Complete or Compilation.States is None:
                    Complete = False
                    break
                OtherStates = list(Compilation.States)
            if not Complete or not OtherStates:
                Complete = False
                break
            OtherStateDomain = tuple(OtherStates)
            MandatoryStateDomains.append(OtherStateDomain)
            MandatoryStateDomainsBySignal[OtherSignal] = OtherStateDomain

    UnsupportedUnaryLocalAccess = tuple(sorted(
        (Signal, LocalAccessFingerprint)
        for (Signal, LocalAccessFingerprint), States
        in StatesBySignalAndLocalAccess.items()
        if not States
    ))
    UnsupportedLocalAccessPairs = []
    CompatibilityCheckCount = 0
    ConflictIndexCache: dict[
        tuple[str, ...],
        tuple[dict[str, dict[Position3, int]], int],
    ] = {}

    def BuildConflictIndexes(
        States: tuple[Any, ...],
    ) -> tuple[dict[str, dict[Position3, int]], int]:
        Key = tuple(State.NetFingerprint for State in States)
        CachedIndexes = ConflictIndexCache.get(Key)
        if CachedIndexes is not None:
            return CachedIndexes
        Mutable = {
            "Wire": {},
            "Support": {},
            "Air": {},
            "Electrical": {},
        }
        for Index, State in enumerate(States):
            Bit = 1 << Index
            for Name, Cells in (
                ("Wire", State.Claims.WireCells),
                ("Support", State.Claims.SupportCells),
                ("Air", State.Claims.RequiredAirCells),
                ("Electrical", State.Claims.ElectricalCells),
            ):
                IndexByCell = Mutable[Name]
                for Cell in Cells:
                    IndexByCell[Cell] = IndexByCell.get(Cell, 0) | Bit
        Result = (Mutable, (1 << len(States)) - 1)
        ConflictIndexCache[Key] = Result
        return Result

    def BuildFirstStateConflictMask(
        FirstState: Any,
        SecondIndexes: dict[str, dict[Position3, int]],
        AllSecondStatesMask: int,
    ) -> int:
        ConflictMask = 0

        def Add(Cells: Iterable[Position3], Names: tuple[str, ...]) -> None:
            nonlocal ConflictMask
            for Cell in Cells:
                for Name in Names:
                    ConflictMask |= SecondIndexes[Name].get(Cell, 0)
                if ConflictMask == AllSecondStatesMask:
                    return

        Add(
            FirstState.Claims.WireCells,
            ("Wire", "Support", "Air", "Electrical"),
        )
        if ConflictMask != AllSecondStatesMask:
            Add(FirstState.Claims.SupportCells, ("Wire", "Air"))
        if ConflictMask != AllSecondStatesMask:
            Add(FirstState.Claims.RequiredAirCells, ("Wire", "Support"))
        if ConflictMask != AllSecondStatesMask:
            Add(FirstState.Claims.ElectricalCells, ("Wire",))
        return ConflictMask

    if Complete and not MandatoryStateDomains:
        FirstSignal, SecondSignal = Signals
        FlattenedSecondStates = tuple(
            State
            for SecondAccess in sorted(FactorsBySignal[SecondSignal])
            for State in StatesBySignalAndLocalAccess[
                (SecondSignal, SecondAccess)
            ]
        )
        SecondAccessMasks = {}
        SecondStateOffset = 0
        for SecondAccess in sorted(FactorsBySignal[SecondSignal]):
            StateCount = len(StatesBySignalAndLocalAccess[
                (SecondSignal, SecondAccess)
            ])
            SecondAccessMasks[SecondAccess] = (
                ((1 << StateCount) - 1) << SecondStateOffset
                if StateCount
                else 0
            )
            SecondStateOffset += StateCount
        SecondIndexes, AllSecondStatesMask = BuildConflictIndexes(
            FlattenedSecondStates
        )
        for FirstAccess in sorted(FactorsBySignal[FirstSignal]):
            FirstStates = StatesBySignalAndLocalAccess[
                (FirstSignal, FirstAccess)
            ]
            SupportedSecondStateMask = 0
            for FirstState in FirstStates:
                CompatibilityCheckCount += len(FlattenedSecondStates)
                if (
                    WorkCheck is not None
                    and CompatibilityCheckCount % 128
                    < len(FlattenedSecondStates)
                ):
                    WorkCheck({
                        "Stage": (
                            "physical-symbolic-port-pair-compatibility"
                        ),
                        "SignalPair": list(Signals),
                        "CompatibilityCheckCount": (
                            CompatibilityCheckCount
                        ),
                        "CompatibilityIndexKind": (
                            "flattened-claim-cell-bitset-v2"
                        ),
                    })
                ConflictMask = BuildFirstStateConflictMask(
                    FirstState,
                    SecondIndexes,
                    AllSecondStatesMask,
                )
                SupportedSecondStateMask |= (
                    AllSecondStatesMask & ~ConflictMask
                )
                if SupportedSecondStateMask == AllSecondStatesMask:
                    break
            for SecondAccess in sorted(FactorsBySignal[SecondSignal]):
                if (
                    SupportedSecondStateMask
                    & SecondAccessMasks[SecondAccess]
                ):
                    continue
                UnsupportedLocalAccessPairs.append((
                    (FirstSignal, FirstAccess),
                    (SecondSignal, SecondAccess),
                ))
    elif Complete:
        FirstSignal, SecondSignal = Signals
        # Normalize every signal to one immutable state domain.  Accesses are
        # masks over those domains; they must not rebuild a different target
        # tuple (and therefore a different conflict matrix) for every access
        # pair.  This is the same exact k-partite relation used by the
        # higher-order compiler, specialized to two restricted domains plus
        # the complete mandatory component domain.
        def NormalizeStateDomain(
            States: Iterable[Any],
        ) -> tuple[Any, ...]:
            StateByFingerprint = {}
            for State in States:
                Fingerprint = str(State.NetFingerprint)
                Existing = StateByFingerprint.get(Fingerprint)
                if Existing is not None and Existing != State:
                    raise ValueError(
                        "port-pair symbolic net-state identity collision"
                    )
                StateByFingerprint[Fingerprint] = State
            return tuple(
                StateByFingerprint[Fingerprint]
                for Fingerprint in sorted(StateByFingerprint)
            )

        CanonicalSignals = tuple(sorted(Problem.ComponentSignals))
        DomainsToSearch = tuple(
            NormalizeStateDomain(
                (
                    State
                    for Access in sorted(FactorsBySignal[Signal])
                    for State in StatesBySignalAndLocalAccess[
                        (Signal, Access)
                    ]
                )
                if Signal in Signals
                else MandatoryStateDomainsBySignal[Signal]
            )
            for Signal in CanonicalSignals
        )
        StateIndexByFingerprint = tuple(
            {
                str(State.NetFingerprint): Index
                for Index, State in enumerate(Domain)
            }
            for Domain in DomainsToSearch
        )
        AccessMasks = {}
        SignalIndexByName = {
            Signal: Index
            for Index, Signal in enumerate(CanonicalSignals)
        }
        for Signal in Signals:
            SignalIndex = SignalIndexByName[Signal]
            for Access in sorted(FactorsBySignal[Signal]):
                Mask = 0
                for State in StatesBySignalAndLocalAccess[(Signal, Access)]:
                    Mask |= 1 << StateIndexByFingerprint[SignalIndex][
                        str(State.NetFingerprint)
                    ]
                AccessMasks[(Signal, Access)] = Mask
        CompatibilityIndexFingerprint = _Fingerprint((
            "complete-component-compatibility-index-v2",
            Problem.PlacementFingerprint,
            FactorDomain.DomainFingerprint,
            tuple(
                (
                    Signal,
                    tuple(State.NetFingerprint for State in Domain),
                )
                for Signal, Domain in zip(
                    CanonicalSignals,
                    DomainsToSearch,
                )
            ),
        ))
        CachedCompatibilityIndex = (
            CompleteCompatibilityIndexCache.get(
                CompatibilityIndexFingerprint
            )
            if CompleteCompatibilityIndexCache is not None
            else None
        )
        if CachedCompatibilityIndex is None:
            PairCompatibleStateMasks: dict[
                tuple[int, int, int], int
            ] = {}
            for FirstDomainIndex in range(len(DomainsToSearch)):
                FirstDomain = DomainsToSearch[FirstDomainIndex]
                for SecondDomainIndex in range(
                    FirstDomainIndex + 1,
                    len(DomainsToSearch),
                ):
                    SecondDomain = DomainsToSearch[SecondDomainIndex]
                    SecondIndexes, AllSecondStatesMask = (
                        BuildConflictIndexes(SecondDomain)
                    )
                    for FirstStateIndex, State in enumerate(FirstDomain):
                        ConflictMask = BuildFirstStateConflictMask(
                            State,
                            SecondIndexes,
                            AllSecondStatesMask,
                        )
                        CompatibleMask = (
                            AllSecondStatesMask & ~ConflictMask
                        )
                        PairCompatibleStateMasks[(
                            FirstDomainIndex,
                            FirstStateIndex,
                            SecondDomainIndex,
                        )] = CompatibleMask
                        CompatibilityCheckCount += len(SecondDomain)
                        if (
                            WorkCheck is not None
                            and CompatibilityCheckCount % 16384
                            < len(SecondDomain)
                        ):
                            WorkCheck({
                                "Stage": (
                                    "physical-symbolic-port-pair-complete-"
                                    "component-compatibility"
                                ),
                                "SignalPair": list(Signals),
                                "MandatorySignalDomainCount": len(
                                    MandatoryStateDomains
                                ),
                                "CompatibilityCheckCount": (
                                    CompatibilityCheckCount
                                ),
                                "CompatibilityIndexKind": (
                                    "normalized-claim-cell-bitset-v2"
                                ),
                            })
                    # Build the reverse relation directly from the same
                    # claim-cell indexes.  Transposing the forward bitsets by
                    # walking every compatible state pair makes sparse claim
                    # domains pay the full dense Cartesian-product cost in
                    # Python.  Direct construction is exact and scales with
                    # symbolic states plus their physical claims instead.
                    FirstIndexes, AllFirstStatesMask = (
                        BuildConflictIndexes(FirstDomain)
                    )
                    for SecondStateIndex, State in enumerate(SecondDomain):
                        ConflictMask = BuildFirstStateConflictMask(
                            State,
                            FirstIndexes,
                            AllFirstStatesMask,
                        )
                        PairCompatibleStateMasks[(
                            SecondDomainIndex,
                            SecondStateIndex,
                            FirstDomainIndex,
                        )] = AllFirstStatesMask & ~ConflictMask
                        CompatibilityCheckCount += len(FirstDomain)
                        if (
                            WorkCheck is not None
                            and CompatibilityCheckCount % 16384
                            < len(FirstDomain)
                        ):
                            WorkCheck({
                                "Stage": (
                                    "physical-symbolic-port-pair-complete-"
                                    "component-compatibility"
                                ),
                                "SignalPair": list(Signals),
                                "MandatorySignalDomainCount": len(
                                    MandatoryStateDomains
                                ),
                                "CompatibilityCheckCount": (
                                    CompatibilityCheckCount
                                ),
                                "CompatibilityIndexKind": (
                                    "normalized-claim-cell-bitset-v2"
                                ),
                            })
            FailedCompatibilityResiduals: set[
                tuple[int, tuple[int, ...]]
            ] = set()
            CompleteCompatibilityWitnesses: list[
                tuple[int, ...]
            ] = []
            UnsupportedRestrictionMasks: dict[
                tuple[int, int], list[tuple[int, int]]
            ] = {}
            ArcConsistencyCache: dict[
                tuple[int, tuple[int, ...]], tuple[int, ...] | None
            ] = {}
            CachedCompatibilityIndex = {
                "PairCompatibleStateMasks": (
                    PairCompatibleStateMasks
                ),
                "FailedCompatibilityResiduals": (
                    FailedCompatibilityResiduals
                ),
                "CompleteCompatibilityWitnesses": (
                    CompleteCompatibilityWitnesses
                ),
                "UnsupportedRestrictionMasks": (
                    UnsupportedRestrictionMasks
                ),
                "ArcConsistencyCache": ArcConsistencyCache,
            }
            if CompleteCompatibilityIndexCache is not None:
                CompleteCompatibilityIndexCache[
                    CompatibilityIndexFingerprint
                ] = CachedCompatibilityIndex
            CompatibilityIndexCacheHit = False
        else:
            PairCompatibleStateMasks = CachedCompatibilityIndex[
                "PairCompatibleStateMasks"
            ]
            FailedCompatibilityResiduals = CachedCompatibilityIndex[
                "FailedCompatibilityResiduals"
            ]
            CompleteCompatibilityWitnesses = CachedCompatibilityIndex[
                "CompleteCompatibilityWitnesses"
            ]
            UnsupportedRestrictionMasks = CachedCompatibilityIndex[
                "UnsupportedRestrictionMasks"
            ]
            ArcConsistencyCache = CachedCompatibilityIndex.setdefault(
                "ArcConsistencyCache",
                {},
            )
            CompatibilityIndexCacheHit = True
        if WorkCheck is not None:
            WorkCheck({
                "Stage": (
                    "physical-symbolic-port-pair-complete-component-"
                    "compatibility-index-complete"
                ),
                "SignalPair": list(Signals),
                "MandatorySignalDomainCount": len(MandatoryStateDomains),
                "NormalizedStateDomainSizes": [
                    len(Domain) for Domain in DomainsToSearch
                ],
                "CompatibilityCheckCount": CompatibilityCheckCount,
                "CompatibilityIndexKind": (
                    "normalized-claim-cell-bitset-v2"
                ),
                "CompatibilityIndexCacheHit": (
                    CompatibilityIndexCacheHit
                ),
            })

        def HasCompleteComponentSupport(
            FirstAccess: str,
            SecondAccess: str,
        ) -> bool:
            InitialMasksList = [
                (1 << len(Domain)) - 1
                for Domain in DomainsToSearch
            ]
            InitialMasksList[
                SignalIndexByName[FirstSignal]
            ] = AccessMasks[(FirstSignal, FirstAccess)]
            InitialMasksList[
                SignalIndexByName[SecondSignal]
            ] = AccessMasks[(SecondSignal, SecondAccess)]
            InitialMasks = tuple(InitialMasksList)
            FirstSignalIndex = SignalIndexByName[FirstSignal]
            SecondSignalIndex = SignalIndexByName[SecondSignal]
            if any(
                Witness[FirstSignalIndex]
                & InitialMasks[FirstSignalIndex]
                and Witness[SecondSignalIndex]
                & InitialMasks[SecondSignalIndex]
                for Witness in CompleteCompatibilityWitnesses
            ):
                return True
            RestrictionKey = (
                FirstSignalIndex,
                SecondSignalIndex,
            )
            if any(
                not (
                    InitialMasks[FirstSignalIndex] & ~FirstMask
                    or InitialMasks[SecondSignalIndex] & ~SecondMask
                )
                for FirstMask, SecondMask
                in UnsupportedRestrictionMasks.get(
                    RestrictionKey,
                    (),
                )
            ):
                return False
            SelectedStateBits = [0] * len(DomainsToSearch)

            def PropagateArcConsistency(
                RemainingDomains: int,
                AllowedMasks: tuple[int, ...],
            ) -> tuple[int, ...] | None:
                """Remove every state lacking support in a live domain.

                Exact complete-component support is a binary-constraint CSP
                over immutable symbolic net states.  Forward checking only
                against the most recently selected state leaves large
                mutually incompatible residual products intact.  Bitset arc
                consistency reaches the required fixed point before search
                and is shared by every aperture-pair restriction.
                """
                CacheKey = (RemainingDomains, AllowedMasks)
                if CacheKey in ArcConsistencyCache:
                    return ArcConsistencyCache[CacheKey]
                MutableMasks = list(AllowedMasks)
                RemainingIndexes = tuple(
                    Index
                    for Index in range(len(DomainsToSearch))
                    if RemainingDomains & (1 << Index)
                )
                Changed = True
                while Changed:
                    Changed = False
                    for SourceIndex in RemainingIndexes:
                        SourceMask = MutableMasks[SourceIndex]
                        SupportedSourceMask = 0
                        CandidateMask = SourceMask
                        while CandidateMask:
                            CandidateBit = CandidateMask & -CandidateMask
                            CandidateMask ^= CandidateBit
                            CandidateStateIndex = (
                                CandidateBit.bit_length() - 1
                            )
                            if all(
                                TargetIndex == SourceIndex
                                or bool(
                                    PairCompatibleStateMasks[(
                                        SourceIndex,
                                        CandidateStateIndex,
                                        TargetIndex,
                                    )]
                                    & MutableMasks[TargetIndex]
                                )
                                for TargetIndex in RemainingIndexes
                            ):
                                SupportedSourceMask |= CandidateBit
                        if not SupportedSourceMask:
                            ArcConsistencyCache[CacheKey] = None
                            return None
                        if SupportedSourceMask != SourceMask:
                            MutableMasks[SourceIndex] = (
                                SupportedSourceMask
                            )
                            Changed = True
                Result = tuple(MutableMasks)
                ArcConsistencyCache[CacheKey] = Result
                return Result

            def Search(
                RemainingDomains: int,
                AllowedMasks: tuple[int, ...],
            ) -> bool:
                if not RemainingDomains:
                    return True
                PropagatedMasks = PropagateArcConsistency(
                    RemainingDomains,
                    AllowedMasks,
                )
                if PropagatedMasks is None:
                    return False
                AllowedMasks = PropagatedMasks
                ResidualKey = (RemainingDomains, AllowedMasks)
                if ResidualKey in FailedCompatibilityResiduals:
                    return False
                RemainingIndexes = tuple(
                    Index
                    for Index in range(len(DomainsToSearch))
                    if RemainingDomains & (1 << Index)
                )
                SelectedIndex = min(
                    RemainingIndexes,
                    key=lambda Index: (
                        AllowedMasks[Index].bit_count(),
                        Index,
                    ),
                )
                CandidateMask = AllowedMasks[SelectedIndex]
                NextRemainingDomains = (
                    RemainingDomains & ~(1 << SelectedIndex)
                )
                while CandidateMask:
                    CandidateBit = CandidateMask & -CandidateMask
                    CandidateMask ^= CandidateBit
                    CandidateStateIndex = (
                        CandidateBit.bit_length() - 1
                    )
                    SelectedStateBits[SelectedIndex] = CandidateBit
                    NextMasks = list(AllowedMasks)
                    NextMasks[SelectedIndex] = 0
                    Viable = True
                    for TargetIndex in RemainingIndexes:
                        if TargetIndex == SelectedIndex:
                            continue
                        NextMasks[TargetIndex] &= (
                            PairCompatibleStateMasks[(
                                SelectedIndex,
                                CandidateStateIndex,
                                TargetIndex,
                            )]
                        )
                        if not NextMasks[TargetIndex]:
                            Viable = False
                            break
                    if Viable and Search(
                        NextRemainingDomains,
                        tuple(NextMasks),
                    ):
                        return True
                    SelectedStateBits[SelectedIndex] = 0
                FailedCompatibilityResiduals.add(ResidualKey)
                return False

            if any(not Mask for Mask in InitialMasks):
                return False
            Supported = Search(
                (1 << len(DomainsToSearch)) - 1,
                InitialMasks,
            )
            if Supported:
                CompleteCompatibilityWitnesses.append(
                    tuple(SelectedStateBits)
                )
                return True
            UnsupportedRestrictionMasks.setdefault(
                RestrictionKey,
                [],
            ).append((
                InitialMasks[FirstSignalIndex],
                InitialMasks[SecondSignalIndex],
            ))
            return False

        for FirstAccess in sorted(FactorsBySignal[FirstSignal]):
            for SecondAccess in sorted(FactorsBySignal[SecondSignal]):
                if WorkCheck is not None:
                    WorkCheck({
                        "Stage": (
                            "physical-symbolic-port-pair-complete-component-"
                            "compatibility"
                        ),
                        "SignalPair": list(Signals),
                        "MandatorySignalDomainCount": len(
                            MandatoryStateDomains
                        ),
                        "CompatibilityCheckCount": (
                            CompatibilityCheckCount
                        ),
                    })
                if HasCompleteComponentSupport(
                    FirstAccess,
                    SecondAccess,
                ):
                    continue
                UnsupportedLocalAccessPairs.append((
                    (FirstSignal, FirstAccess),
                    (SecondSignal, SecondAccess),
                ))
    UnsupportedUnaryLocalAccessSet = frozenset(
        UnsupportedUnaryLocalAccess
    )
    UnsupportedLocalAccessPairSet = frozenset(
        frozenset(Value) for Value in UnsupportedLocalAccessPairs
    )
    AccessesBySignalAndSeam: dict[
        tuple[str, str], set[str]
    ] = {}
    for (Signal, LocalAccessFingerprint), SeamFingerprint in (
        SeamFingerprintByLocalAccess.items()
    ):
        AccessesBySignalAndSeam.setdefault(
            (Signal, SeamFingerprint),
            set(),
        ).add(LocalAccessFingerprint)
    UnsupportedUnarySeams = tuple(sorted(
        (Signal, SeamFingerprint)
        for (Signal, SeamFingerprint), Accesses
        in AccessesBySignalAndSeam.items()
        if all(
            (Signal, Access) in UnsupportedUnaryLocalAccessSet
            for Access in Accesses
        )
    ))
    UnsupportedSeamPairs = []
    if Complete:
        FirstSignal, SecondSignal = Signals
        FirstSeams = tuple(sorted(
            Seam
            for Signal, Seam in AccessesBySignalAndSeam
            if Signal == FirstSignal
        ))
        SecondSeams = tuple(sorted(
            Seam
            for Signal, Seam in AccessesBySignalAndSeam
            if Signal == SecondSignal
        ))
        for FirstSeam in FirstSeams:
            for SecondSeam in SecondSeams:
                if all(
                    (
                        (FirstSignal, FirstAccess)
                        in UnsupportedUnaryLocalAccessSet
                        or (SecondSignal, SecondAccess)
                        in UnsupportedUnaryLocalAccessSet
                        or frozenset((
                            (FirstSignal, FirstAccess),
                            (SecondSignal, SecondAccess),
                        )) in UnsupportedLocalAccessPairSet
                    )
                    for FirstAccess in AccessesBySignalAndSeam[
                        (FirstSignal, FirstSeam)
                    ]
                    for SecondAccess in AccessesBySignalAndSeam[
                        (SecondSignal, SecondSeam)
                    ]
                ):
                    UnsupportedSeamPairs.append((
                        (FirstSignal, FirstSeam),
                        (SecondSignal, SecondSeam),
                    ))
    NetStateBindingsTuple = tuple(sorted(NetStateBindings))
    NetStateDomainFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-state-bindings-v1",
        NetStateBindingsTuple,
    ))
    LocalAccessFingerprintsBySignal = tuple(
        (
            Signal,
            tuple(sorted(FactorsBySignal[Signal])),
        )
        for Signal in Signals
    )
    ProofFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-proof-v2",
        DomainFingerprint,
        LocalAccessFingerprintsBySignal,
        UnsupportedUnaryLocalAccess,
        tuple(UnsupportedLocalAccessPairs),
        UnsupportedUnarySeams,
        tuple(UnsupportedSeamPairs),
        NetStateDomainFingerprint,
        Complete,
    ))
    Certificate = PhysicalComponentSymbolicPortPairCertificate(
        DomainFingerprint=DomainFingerprint,
        PreparedDomainFingerprint=Context["PreparedDomainFingerprint"],
        PlacementFingerprint=Context["PlacementFingerprint"],
        ComponentGraphFingerprint=Context["ComponentGraphFingerprint"],
        FabricFingerprint=Context["FabricFingerprint"],
        ResourceGraphFingerprint=Context["ResourceGraphFingerprint"],
        TechnologyFingerprint=Context["TechnologyFingerprint"],
        AccessCertificateFingerprint=(
            Context["AccessCertificateFingerprint"]
        ),
        InterfaceFingerprint=Context["InterfaceFingerprint"],
        LocalAccessDomainFingerprint=(
            Context["LocalAccessDomainFingerprint"]
        ),
        SeamDomainFingerprint=Context["SeamDomainFingerprint"],
        SignalPair=Signals,
        LocalAccessFingerprintsBySignal=(
            LocalAccessFingerprintsBySignal
        ),
        SeamFingerprintByLocalAccess=tuple(sorted(
            (
                Signal,
                LocalAccessFingerprint,
                SeamFingerprint,
            )
            for (Signal, LocalAccessFingerprint), SeamFingerprint
            in SeamFingerprintByLocalAccess.items()
        )),
        SeamFingerprintsBySignal=tuple(
            (
                Signal,
                tuple(sorted({
                    SeamFingerprintByLocalAccess[(
                        Signal,
                        LocalAccessFingerprint,
                    )]
                    for LocalAccessFingerprint
                    in FactorsBySignal[Signal]
                })),
            )
            for Signal in Signals
        ),
        UnsupportedUnaryLocalAccess=(
            UnsupportedUnaryLocalAccess
        ),
        UnsupportedLocalAccessPairs=tuple(
            UnsupportedLocalAccessPairs
        ),
        UnsupportedUnarySeams=UnsupportedUnarySeams,
        UnsupportedSeamPairs=tuple(UnsupportedSeamPairs),
        NetStateCacheKeys=tuple(sorted(NetStateCacheKeys)),
        NetStateBindings=NetStateBindingsTuple,
        NetStateDomainFingerprint=NetStateDomainFingerprint,
        ProofFingerprint=ProofFingerprint,
        Complete=Complete,
    )
    ValidatePhysicalComponentSymbolicPortPairCertificate(
        Certificate,
        Problem,
        FactorDomain,
        Signals,
        NetStateCache=EffectiveNetStateCache,
    )
    if Complete and CompletedCertificateCache is not None:
        CompletedCertificateCache[DomainFingerprint] = Certificate
    return Certificate


def _BuildPhysicalComponentSymbolicHigherOrderContext(
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    Signals: tuple[str, ...],
    FactorsBySignal: dict[str, dict[str, Any]],
    SeamFingerprintByLocalAccess: dict[tuple[str, str], str],
) -> dict[str, str]:
    """Build immutable identities for a complete higher-order proof."""
    PairContext = _BuildPhysicalComponentSymbolicPortPairContext(
        Problem,
        FactorDomain,
        Signals,
        FactorsBySignal,
        SeamFingerprintByLocalAccess,
    )
    Result = dict(PairContext)
    Result["DomainFingerprint"] = _Fingerprint((
        "physical-symbolic-higher-order-domain-v1",
        Signals,
        tuple(
            (Name, Value)
            for Name, Value in sorted(PairContext.items())
            if Name != "DomainFingerprint"
        ),
    ))
    return Result


def ValidatePhysicalComponentSymbolicHigherOrderCertificate(
    Certificate: PhysicalComponentSymbolicHigherOrderCertificate,
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    SignalDomain: Iterable[str],
    *,
    NetStateCache: dict[str, Any] | None = None,
) -> None:
    """Reject a higher-order proof if any structural identity has drifted."""
    Signals = tuple(sorted(frozenset(map(str, SignalDomain))))
    if len(Signals) < 3:
        raise ValueError(
            "symbolic higher-order validation requires at least three signals"
        )
    FactorsBySignal, SeamFingerprintByLocalAccess = (
        _SelectPhysicalComponentSymbolicPortPairFactors(
            FactorDomain,
            Signals,
        )
    )
    Context = _BuildPhysicalComponentSymbolicHigherOrderContext(
        Problem,
        FactorDomain,
        Signals,
        FactorsBySignal,
        SeamFingerprintByLocalAccess,
    )
    FieldValues = {
        "DomainFingerprint": Certificate.DomainFingerprint,
        "PreparedDomainFingerprint": Certificate.PreparedDomainFingerprint,
        "PlacementFingerprint": Certificate.PlacementFingerprint,
        "ComponentGraphFingerprint": Certificate.ComponentGraphFingerprint,
        "FabricFingerprint": Certificate.FabricFingerprint,
        "ResourceGraphFingerprint": Certificate.ResourceGraphFingerprint,
        "TechnologyFingerprint": Certificate.TechnologyFingerprint,
        "AccessCertificateFingerprint": (
            Certificate.AccessCertificateFingerprint
        ),
        "InterfaceFingerprint": Certificate.InterfaceFingerprint,
        "LocalAccessDomainFingerprint": (
            Certificate.LocalAccessDomainFingerprint
        ),
        "SeamDomainFingerprint": Certificate.SeamDomainFingerprint,
    }
    Mismatches = [
        Name
        for Name, Expected in Context.items()
        if str(FieldValues.get(Name, "")) != str(Expected)
    ]
    ExpectedAccesses = tuple(
        (Signal, tuple(sorted(FactorsBySignal[Signal])))
        for Signal in Signals
    )
    ExpectedSeams = tuple(sorted(
        (
            Signal,
            LocalAccessFingerprint,
            SeamFingerprint,
        )
        for (Signal, LocalAccessFingerprint), SeamFingerprint
        in SeamFingerprintByLocalAccess.items()
    ))
    if Certificate.SignalDomain != Signals:
        Mismatches.append("SignalDomain")
    if Certificate.LocalAccessFingerprintsBySignal != ExpectedAccesses:
        Mismatches.append("LocalAccessFingerprintsBySignal")
    if Certificate.SeamFingerprintByLocalAccess != ExpectedSeams:
        Mismatches.append("SeamFingerprintByLocalAccess")
    BindingKeys = tuple(
        (Signal, LocalAccessFingerprint, CacheKey)
        for Signal, LocalAccessFingerprint, CacheKey, _StateFingerprint
        in Certificate.NetStateBindings
    )
    if BindingKeys != Certificate.NetStateCacheKeys:
        Mismatches.append("NetStateCacheKeys")
    ExpectedBindingFingerprint = _Fingerprint((
        "physical-symbolic-higher-order-state-bindings-v1",
        Certificate.NetStateBindings,
    ))
    if Certificate.NetStateDomainFingerprint != ExpectedBindingFingerprint:
        Mismatches.append("NetStateDomainFingerprint")
    if NetStateCache is not None:
        for (
            _Signal,
            _LocalAccessFingerprint,
            CacheKey,
            StateFingerprint,
        ) in Certificate.NetStateBindings:
            Cached = NetStateCache.get(CacheKey)
            if Cached is None:
                continue
            States, _Diagnostics = Cached
            if (
                _BuildPhysicalComponentSymbolicNetStateFingerprint(States)
                != StateFingerprint
            ):
                Mismatches.append("NetStateCacheContents")
                break
    ExpectedProofFingerprint = _Fingerprint((
        "physical-symbolic-higher-order-proof-v1",
        Certificate.DomainFingerprint,
        Certificate.LocalAccessFingerprintsBySignal,
        Certificate.SupportedLocalAccessTuples,
        Certificate.SupportedSeamTuples,
        Certificate.NetStateDomainFingerprint,
        Certificate.CompatibilityCheckCount,
        Certificate.Complete,
    ))
    if Certificate.ProofFingerprint != ExpectedProofFingerprint:
        Mismatches.append("ProofFingerprint")
    if Mismatches:
        raise ValueError(
            "physical symbolic higher-order certificate identity mismatch: "
            + ", ".join(dict.fromkeys(Mismatches))
        )


def CompilePhysicalComponentSymbolicHigherOrderDomain(
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    SignalDomain: Iterable[str],
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    NetStateCache: dict[str, Any] | None = None,
    CompletedCertificateCache: dict[
        str, PhysicalComponentSymbolicHigherOrderCertificate
    ] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
) -> PhysicalComponentSymbolicHigherOrderCertificate:
    """Compile the exact joint local-state relation for 3+ signal seams."""
    if not FactorDomain.Complete or not FactorDomain.Feasible:
        raise ValueError(
            "higher-order compilation requires a complete feasible factor domain"
        )
    if Problem.PlacementFingerprint != FactorDomain.PlacementFingerprint:
        raise ValueError("higher-order placement identity mismatch")
    Signals = tuple(sorted(frozenset(map(str, SignalDomain))))
    if len(Signals) < 3:
        raise ValueError(
            "symbolic higher-order compilation requires at least three signals"
        )
    FactorsBySignal, SeamFingerprintByLocalAccess = (
        _SelectPhysicalComponentSymbolicPortPairFactors(
            FactorDomain,
            Signals,
        )
    )
    Context = _BuildPhysicalComponentSymbolicHigherOrderContext(
        Problem,
        FactorDomain,
        Signals,
        FactorsBySignal,
        SeamFingerprintByLocalAccess,
    )
    DomainFingerprint = Context["DomainFingerprint"]
    EffectiveNetStateCache = (
        NetStateCache if NetStateCache is not None else {}
    )
    Cached = (
        CompletedCertificateCache.get(DomainFingerprint)
        if CompletedCertificateCache is not None
        else None
    )
    if Cached is not None:
        ValidatePhysicalComponentSymbolicHigherOrderCertificate(
            Cached,
            Problem,
            FactorDomain,
            Signals,
            NetStateCache=EffectiveNetStateCache,
        )
        return Cached

    StartedAt = monotonic()
    RelaxedProblem = replace(Problem, ReservedGlobalClaimsBySignal=())
    PreparedNetStateContexts = {
        Signal: PrepareComponentSymbolicNetStateContext(
            RelaxedProblem,
            Signal,
            RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        )
        for Signal in Signals
    }
    StatesBySignalAndLocalAccess: dict[
        tuple[str, str], tuple[Any, ...]
    ] = {}
    NetStateCacheKeys: list[tuple[str, str, str]] = []
    NetStateBindings: list[tuple[str, str, str, str]] = []
    Complete = True
    for Signal in Signals:
        VariantProblemsByAccess = {
            LocalAccessFingerprint: (
                _BuildPhysicalComponentSymbolicPortPairVariantProblem(
                    RelaxedProblem,
                    Signal,
                    LocalAccessFingerprint,
                    Factor,
                )
            )
            for LocalAccessFingerprint, Factor
            in sorted(FactorsBySignal[Signal].items())
        }
        RemainingDeadline = (
            None
            if DeadlineSeconds is None
            else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
        )
        CompilationsByAccess = (
            CompilePreparedComponentPhysicalFactorStateBatch(
                PreparedNetStateContexts[Signal],
                VariantProblemsByAccess,
                DeadlineSeconds=RemainingDeadline,
                WorkCheck=WorkCheck,
                SymbolicNetStateCache=EffectiveNetStateCache,
            )
        )
        for LocalAccessFingerprint in sorted(VariantProblemsByAccess):
            Compilation = CompilationsByAccess[LocalAccessFingerprint]
            if not Compilation.Complete or Compilation.States is None:
                Complete = False
                break
            States = tuple(Compilation.States)
            StatesBySignalAndLocalAccess[
                (Signal, LocalAccessFingerprint)
            ] = States
            NetStateCacheKeys.append((
                Signal,
                LocalAccessFingerprint,
                Compilation.CacheKey,
            ))
            NetStateBindings.append((
                Signal,
                LocalAccessFingerprint,
                Compilation.CacheKey,
                _BuildPhysicalComponentSymbolicNetStateFingerprint(States),
            ))
        if not Complete:
            break

    SupportedLocalAccessTuples: set[
        tuple[tuple[str, str], ...]
    ] = set()
    CompatibilityCheckCount = 0
    CompatibilitySearchStateCount = 0

    # Compile the higher-order relation once over deduplicated symbolic net
    # states.  The earlier access-tuple implementation repeated Python set
    # conflict checks for every local-factor product.  These immutable
    # bitsets turn the same exact problem into a k-partite compatibility CSP
    # whose state relation is reusable across every access and seam tuple.
    UniqueStatesBySignal: dict[str, tuple[Any, ...]] = {}
    AccessStateMasks: dict[tuple[str, str], int] = {}
    PairCompatibleStateMasks: dict[tuple[int, int, int], int] = {}
    if Complete:
        for Signal in Signals:
            StateByFingerprint: dict[str, Any] = {}
            for Access in sorted(FactorsBySignal[Signal]):
                for State in StatesBySignalAndLocalAccess[(Signal, Access)]:
                    Fingerprint = str(State.NetFingerprint)
                    Existing = StateByFingerprint.get(Fingerprint)
                    if Existing is not None and Existing != State:
                        raise ValueError(
                            "higher-order symbolic net-state identity "
                            "collision"
                        )
                    StateByFingerprint[Fingerprint] = State
            States = tuple(
                StateByFingerprint[Fingerprint]
                for Fingerprint in sorted(StateByFingerprint)
            )
            UniqueStatesBySignal[Signal] = States
            StateIndexByFingerprint = {
                str(State.NetFingerprint): Index
                for Index, State in enumerate(States)
            }
            for Access in sorted(FactorsBySignal[Signal]):
                Mask = 0
                for State in StatesBySignalAndLocalAccess[(Signal, Access)]:
                    Mask |= 1 << StateIndexByFingerprint[
                        str(State.NetFingerprint)
                    ]
                AccessStateMasks[(Signal, Access)] = Mask

        for FirstSignalIndex in range(len(Signals)):
            FirstSignal = Signals[FirstSignalIndex]
            FirstStates = UniqueStatesBySignal[FirstSignal]
            for SecondSignalIndex in range(
                FirstSignalIndex + 1,
                len(Signals),
            ):
                SecondSignal = Signals[SecondSignalIndex]
                SecondStates = UniqueStatesBySignal[SecondSignal]
                ReverseMasks = [0] * len(SecondStates)
                SecondClaimIndexes: dict[
                    str, dict[Position3, int]
                ] = {
                    "Wire": {},
                    "Support": {},
                    "Air": {},
                    "Electrical": {},
                }
                for SecondStateIndex, SecondState in enumerate(
                    SecondStates
                ):
                    StateBit = 1 << SecondStateIndex
                    for Name, Cells in (
                        ("Wire", SecondState.Claims.WireCells),
                        ("Support", SecondState.Claims.SupportCells),
                        ("Air", SecondState.Claims.RequiredAirCells),
                        (
                            "Electrical",
                            SecondState.Claims.ElectricalCells,
                        ),
                    ):
                        IndexByCell = SecondClaimIndexes[Name]
                        for Cell in Cells:
                            IndexByCell[Cell] = (
                                IndexByCell.get(Cell, 0) | StateBit
                            )
                AllSecondStatesMask = (1 << len(SecondStates)) - 1
                for FirstStateIndex, FirstState in enumerate(FirstStates):
                    PriorCheckCount = CompatibilityCheckCount
                    CompatibilityCheckCount += len(SecondStates)
                    if (
                        WorkCheck is not None
                        and PriorCheckCount // 1024
                        != CompatibilityCheckCount // 1024
                    ):
                        WorkCheck({
                            "Stage": (
                                "physical-symbolic-higher-order-"
                                "compatibility-index"
                            ),
                            "SignalDomain": list(Signals),
                            "CompatibilityCheckCount": (
                                CompatibilityCheckCount
                            ),
                        })
                    ConflictMask = 0

                    def AddConflicts(
                        Cells: Iterable[Position3],
                        Names: tuple[str, ...],
                    ) -> None:
                        nonlocal ConflictMask
                        for Cell in Cells:
                            for Name in Names:
                                ConflictMask |= (
                                    SecondClaimIndexes[Name].get(Cell, 0)
                                )
                            if ConflictMask == AllSecondStatesMask:
                                return

                    AddConflicts(
                        FirstState.Claims.WireCells,
                        ("Wire", "Support", "Air", "Electrical"),
                    )
                    if ConflictMask != AllSecondStatesMask:
                        AddConflicts(
                            FirstState.Claims.SupportCells,
                            ("Wire", "Air"),
                        )
                    if ConflictMask != AllSecondStatesMask:
                        AddConflicts(
                            FirstState.Claims.RequiredAirCells,
                            ("Wire", "Support"),
                        )
                    if ConflictMask != AllSecondStatesMask:
                        AddConflicts(
                            FirstState.Claims.ElectricalCells,
                            ("Wire",),
                        )
                    CompatibleMask = (
                        AllSecondStatesMask & ~ConflictMask
                    )
                    CompatibleStateMask = CompatibleMask
                    while CompatibleStateMask:
                        StateBit = (
                            CompatibleStateMask & -CompatibleStateMask
                        )
                        CompatibleStateMask ^= StateBit
                        SecondStateIndex = StateBit.bit_length() - 1
                        ReverseMasks[SecondStateIndex] |= (
                            1 << FirstStateIndex
                        )
                    PairCompatibleStateMasks[(
                        FirstSignalIndex,
                        FirstStateIndex,
                        SecondSignalIndex,
                    )] = CompatibleMask
                for SecondStateIndex, ReverseMask in enumerate(
                    ReverseMasks
                ):
                    PairCompatibleStateMasks[(
                        SecondSignalIndex,
                        SecondStateIndex,
                        FirstSignalIndex,
                    )] = ReverseMask

    CompatibilitySearchCache: dict[
        tuple[tuple[int, ...], tuple[int, ...]], bool
    ] = {}

    def HasCompatibleStateTuple(
        AccessTuple: tuple[tuple[str, str], ...],
    ) -> bool:
        nonlocal CompatibilitySearchStateCount
        AllowedMasks = tuple(
            AccessStateMasks[Value] for Value in AccessTuple
        )
        if any(not Mask for Mask in AllowedMasks):
            return False

        def Search(
            RemainingIndexes: tuple[int, ...],
            CurrentMasks: tuple[int, ...],
        ) -> bool:
            nonlocal CompatibilitySearchStateCount
            if not RemainingIndexes:
                return True
            CacheKey = (RemainingIndexes, CurrentMasks)
            Cached = CompatibilitySearchCache.get(CacheKey)
            if Cached is not None:
                return Cached
            CompatibilitySearchStateCount += 1
            if (
                WorkCheck is not None
                and CompatibilitySearchStateCount % 1024 == 0
            ):
                WorkCheck({
                    "Stage": (
                        "physical-symbolic-higher-order-compatibility-csp"
                    ),
                    "SignalDomain": list(Signals),
                    "CompatibilityCheckCount": CompatibilityCheckCount,
                    "CompatibilitySearchStateCount": (
                        CompatibilitySearchStateCount
                    ),
                })
            SelectedIndex = min(
                RemainingIndexes,
                key=lambda Index: (
                    CurrentMasks[Index].bit_count(),
                    Index,
                ),
            )
            NextRemaining = tuple(
                Index for Index in RemainingIndexes
                if Index != SelectedIndex
            )
            CandidateMask = CurrentMasks[SelectedIndex]
            while CandidateMask:
                StateBit = CandidateMask & -CandidateMask
                CandidateMask ^= StateBit
                StateIndex = StateBit.bit_length() - 1
                NextMasks = list(CurrentMasks)
                NextMasks[SelectedIndex] = 0
                Feasible = True
                for OtherIndex in NextRemaining:
                    NextMasks[OtherIndex] &= (
                        PairCompatibleStateMasks.get(
                            (SelectedIndex, StateIndex, OtherIndex),
                            0,
                        )
                    )
                    if not NextMasks[OtherIndex]:
                        Feasible = False
                        break
                if Feasible and Search(
                    NextRemaining,
                    tuple(NextMasks),
                ):
                    CompatibilitySearchCache[CacheKey] = True
                    return True
            CompatibilitySearchCache[CacheKey] = False
            return False

        return Search(tuple(range(len(Signals))), AllowedMasks)

    if Complete:
        AccessDomains = tuple(
            tuple(sorted(FactorsBySignal[Signal]))
            for Signal in Signals
        )
        for AccessTupleIndex, AccessValues in enumerate(
            product(*AccessDomains)
        ):
            AccessTuple = tuple(zip(Signals, AccessValues))
            if (
                WorkCheck is not None
                and AccessTupleIndex % 128 == 0
            ):
                WorkCheck({
                    "Stage": "physical-symbolic-higher-order-compatibility",
                    "SignalDomain": list(Signals),
                    "CompatibilityCheckCount": CompatibilityCheckCount,
                    "CompatibilitySearchStateCount": (
                        CompatibilitySearchStateCount
                    ),
                    "AccessTupleCount": AccessTupleIndex,
            })
            if HasCompatibleStateTuple(AccessTuple):
                SupportedLocalAccessTuples.add(AccessTuple)

    AccessesBySignalAndSeam: dict[tuple[str, str], tuple[str, ...]] = {}
    for Signal in Signals:
        for Seam in sorted({
            SeamFingerprintByLocalAccess[(Signal, Access)]
            for Access in FactorsBySignal[Signal]
        }):
            AccessesBySignalAndSeam[(Signal, Seam)] = tuple(sorted(
                Access
                for Access in FactorsBySignal[Signal]
                if SeamFingerprintByLocalAccess[(Signal, Access)] == Seam
            ))
    SupportedSeamTuples: frozenset[
        tuple[tuple[str, str], ...]
    ] = frozenset()
    if Complete:
        SupportedSeamTuples = frozenset(
            tuple(
                (
                    Signal,
                    SeamFingerprintByLocalAccess[(Signal, Access)],
                )
                for Signal, Access in AccessTuple
            )
            for AccessTuple in SupportedLocalAccessTuples
        )

    NetStateBindingsTuple = tuple(sorted(NetStateBindings))
    NetStateDomainFingerprint = _Fingerprint((
        "physical-symbolic-higher-order-state-bindings-v1",
        NetStateBindingsTuple,
    ))
    LocalAccessFingerprintsBySignal = tuple(
        (Signal, tuple(sorted(FactorsBySignal[Signal])))
        for Signal in Signals
    )
    SupportedLocalAccessTuplesTuple = tuple(sorted(
        SupportedLocalAccessTuples
    ))
    SupportedSeamTuplesTuple = tuple(sorted(
        SupportedSeamTuples
    ))
    ProofFingerprint = _Fingerprint((
        "physical-symbolic-higher-order-proof-v1",
        DomainFingerprint,
        LocalAccessFingerprintsBySignal,
        SupportedLocalAccessTuplesTuple,
        SupportedSeamTuplesTuple,
        NetStateDomainFingerprint,
        CompatibilityCheckCount,
        Complete,
    ))
    Certificate = PhysicalComponentSymbolicHigherOrderCertificate(
        DomainFingerprint=DomainFingerprint,
        PreparedDomainFingerprint=Context["PreparedDomainFingerprint"],
        PlacementFingerprint=Context["PlacementFingerprint"],
        ComponentGraphFingerprint=Context["ComponentGraphFingerprint"],
        FabricFingerprint=Context["FabricFingerprint"],
        ResourceGraphFingerprint=Context["ResourceGraphFingerprint"],
        TechnologyFingerprint=Context["TechnologyFingerprint"],
        AccessCertificateFingerprint=(
            Context["AccessCertificateFingerprint"]
        ),
        InterfaceFingerprint=Context["InterfaceFingerprint"],
        LocalAccessDomainFingerprint=(
            Context["LocalAccessDomainFingerprint"]
        ),
        SeamDomainFingerprint=Context["SeamDomainFingerprint"],
        SignalDomain=Signals,
        LocalAccessFingerprintsBySignal=LocalAccessFingerprintsBySignal,
        SeamFingerprintByLocalAccess=tuple(sorted(
            (
                Signal,
                Access,
                Seam,
            )
            for (Signal, Access), Seam
            in SeamFingerprintByLocalAccess.items()
        )),
        SeamFingerprintsBySignal=tuple(
            (
                Signal,
                tuple(sorted(
                    Seam
                    for DomainSignal, Seam in AccessesBySignalAndSeam
                    if DomainSignal == Signal
                )),
            )
            for Signal in Signals
        ),
        SupportedLocalAccessTuples=(
            SupportedLocalAccessTuplesTuple
        ),
        SupportedSeamTuples=SupportedSeamTuplesTuple,
        NetStateCacheKeys=tuple(sorted(NetStateCacheKeys)),
        NetStateBindings=NetStateBindingsTuple,
        NetStateDomainFingerprint=NetStateDomainFingerprint,
        ProofFingerprint=ProofFingerprint,
        CompatibilityCheckCount=CompatibilityCheckCount,
        Complete=Complete,
    )
    ValidatePhysicalComponentSymbolicHigherOrderCertificate(
        Certificate,
        Problem,
        FactorDomain,
        Signals,
        NetStateCache=EffectiveNetStateCache,
    )
    if Complete and CompletedCertificateCache is not None:
        CompletedCertificateCache[DomainFingerprint] = Certificate
    return Certificate


def CompilePhysicalComponentSymbolicUnaryApertureDomain(
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    SignalDomain: Iterable[str],
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    NetStateCache: dict[str, Any] | None = None,
    CompletedClauseCache: dict[
        str,
        tuple[
            frozenset[frozenset[tuple[str, str]]],
            dict[str, Any],
        ],
    ] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
) -> tuple[
    frozenset[frozenset[tuple[str, str]]],
    dict[str, Any],
]:
    """Compile a requested signal domain and project complete unary cuts."""
    if not FactorDomain.Complete or not FactorDomain.Feasible:
        raise ValueError(
            "symbolic unary compilation requires a complete feasible domain"
        )
    if Problem.PlacementFingerprint != FactorDomain.PlacementFingerprint:
        raise ValueError("symbolic unary placement identity mismatch")
    Signals = tuple(sorted(frozenset(map(str, SignalDomain))))
    AvailableSignals = frozenset(
        str(Signal)
        for Signal, _Values in FactorDomain.LocalAccessFactorsBySignal
    )
    if not Signals or not frozenset(Signals) <= AvailableSignals:
        raise ValueError(
            "symbolic unary compilation requires available signals"
        )
    CacheKey = _Fingerprint((
        "physical-symbolic-unary-aperture-domain-v7",
        FactorDomain.DomainFingerprint,
        Problem.Fabric.FabricFingerprint,
        Signals,
    ))
    Cached = (
        CompletedClauseCache.get(CacheKey)
        if CompletedClauseCache is not None
        else None
    )
    if Cached is not None:
        Clauses, Diagnostics = Cached
        return Clauses, {**Diagnostics, "UnaryCertificateCacheHit": True}

    LocalFactorsBySignal = dict(FactorDomain.LocalAccessFactorsBySignal)
    SupportedAccessesBySignal = {
        str(Signal): frozenset(
            str(Support.LocalAccessFingerprint) for Support in Supports
        )
        for Signal, Supports
        in FactorDomain.LocalApertureSupportBySignal
    }
    EffectiveNetStateCache = (
        NetStateCache if NetStateCache is not None else {}
    )
    RelaxedProblem = replace(Problem, ReservedGlobalClaimsBySignal=())
    StartedAt = monotonic()
    UnsupportedAccesses: set[tuple[str, str]] = set()
    CompiledStatesByAccess: dict[
        tuple[str, str], tuple[Any, ...]
    ] = {}
    CompiledAccessCount = 0
    for Signal in Signals:
        Factors = {
            str(Factor.LocalAccessFingerprint): Factor
            for Factor in LocalFactorsBySignal.get(Signal, ())
            if str(Factor.LocalAccessFingerprint)
            in SupportedAccessesBySignal.get(Signal, frozenset())
        }
        PreparedContext = PrepareComponentSymbolicNetStateContext(
            RelaxedProblem,
            Signal,
            RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        )
        VariantProblemsByAccess = {
            LocalAccessFingerprint: (
                _BuildPhysicalComponentSymbolicPortPairVariantProblem(
                    RelaxedProblem,
                    Signal,
                    LocalAccessFingerprint,
                    Factor,
                )
            )
            for LocalAccessFingerprint, Factor in sorted(Factors.items())
        }
        RemainingDeadline = (
            None
            if DeadlineSeconds is None
            else max(
                0.0,
                DeadlineSeconds - (monotonic() - StartedAt),
            )
        )
        CompilationsByAccess = (
            CompilePreparedComponentPhysicalFactorStateBatch(
                PreparedContext,
                VariantProblemsByAccess,
                DeadlineSeconds=RemainingDeadline,
                WorkCheck=WorkCheck,
                SymbolicNetStateCache=EffectiveNetStateCache,
            )
        )
        for LocalAccessFingerprint in sorted(VariantProblemsByAccess):
            Compilation = CompilationsByAccess[LocalAccessFingerprint]
            if not Compilation.Complete or Compilation.States is None:
                return frozenset(), {
                    "Complete": False,
                    "Signal": Signal,
                    "CompiledAccessCount": CompiledAccessCount,
                    "UnaryCertificateCacheHit": False,
                }
            CompiledAccessCount += 1
            CompiledStatesByAccess[(
                Signal,
                LocalAccessFingerprint,
            )] = tuple(Compilation.States)
            if not Compilation.States:
                UnsupportedAccesses.add((Signal, LocalAccessFingerprint))

    ApertureFactorsBySignal = dict(FactorDomain.ApertureFactorsBySignal)
    SupportsByOption = dict(FactorDomain.LocalApertureSupportsByOption)
    Clauses: set[frozenset[tuple[str, str]]] = set()
    UnsupportedLocalContracts: set[tuple[str, str]] = set()
    UnsupportedApertureOptions: set[tuple[str, str]] = set()
    UnsupportedLocalApertureSupports: set[tuple[str, str]] = set()

    for Signal in Signals:
        FactorsByAccess = {
            str(Factor.LocalAccessFingerprint): Factor
            for Factor in LocalFactorsBySignal.get(Signal, ())
        }
        for LocalAccessFingerprint in sorted(
            Access
            for CandidateSignal, Access in UnsupportedAccesses
            if CandidateSignal == Signal
        ):
            # The physical port CSP represents an option through its stable
            # local contract, seam contract, and aperture contract.  The
            # factor-local access fingerprint is a compilation cache key and
            # is deliberately not part of ``BuildPhysicalPortNoGoodKeys``.
            # Project the complete unary proof onto the solver-visible local
            # contract instead of publishing an inert cache identity.
            LocalFactor = FactorsByAccess[LocalAccessFingerprint]
            LocalContractKey = (
                Signal,
                str(LocalFactor.LocalContractFingerprint),
            )
            UnsupportedLocalContracts.add(LocalContractKey)
            Clauses.add(frozenset((LocalContractKey,)))
        AccessesBySeam: dict[str, set[str]] = {}
        for LocalAccessFingerprint, Factor in FactorsByAccess.items():
            SeamFingerprint = (
                str(getattr(Factor, "SeamContractFingerprint", ""))
                or BuildPhysicalPortSeamContractFingerprint(Factor)
            )
            AccessesBySeam.setdefault(SeamFingerprint, set()).add(
                LocalAccessFingerprint
            )
        for SeamFingerprint, LocalAccessFingerprints in (
            AccessesBySeam.items()
        ):
            if LocalAccessFingerprints and all(
                (Signal, LocalAccessFingerprint) in UnsupportedAccesses
                for LocalAccessFingerprint in LocalAccessFingerprints
            ):
                Clauses.add(frozenset(((Signal, SeamFingerprint),)))
        OptionsByContract: dict[str, list[Any]] = {}
        for Aperture in ApertureFactorsBySignal.get(Signal, ()):
            OptionsByContract.setdefault(
                str(Aperture.ApertureContractFingerprint),
                [],
            ).append(Aperture)
            OptionSupports = tuple(
                SupportsByOption.get((
                    Signal,
                    str(Aperture.ApertureOptionFingerprint),
                ), ())
            )
            ForbiddenGlobalNodes = (
                frozenset(Aperture.GlobalPath)
                - frozenset((Aperture.Attachment,))
            )
            SupportedEdgeCount = 0
            for Support in OptionSupports:
                EdgeSupported = any(
                    not (ForbiddenGlobalNodes & State.Nodes)
                    for State in CompiledStatesByAccess.get((
                        Signal,
                        str(Support.LocalAccessFingerprint),
                    ), ())
                )
                if EdgeSupported:
                    SupportedEdgeCount += 1
                    continue
                UnsupportedLocalApertureSupports.add((
                    Signal,
                    str(Support.SupportFingerprint),
                ))
                Clauses.add(frozenset(((
                    Signal,
                    str(Support.SupportFingerprint),
                ),)))
            if not SupportedEdgeCount:
                UnsupportedApertureOptions.add((
                    Signal,
                    str(Aperture.ApertureOptionFingerprint),
                ))
        for ApertureContract, Apertures in OptionsByContract.items():
            if Apertures and all(
                (
                    Signal,
                    str(Aperture.ApertureOptionFingerprint),
                ) in UnsupportedApertureOptions
                for Aperture in Apertures
            ):
                Clauses.add(frozenset(((Signal, ApertureContract),)))
    Result = frozenset(Clauses)
    Diagnostics = {
        "Complete": True,
        "SignalCount": len(Signals),
        "CompiledAccessCount": CompiledAccessCount,
        "UnsupportedLocalAccessCount": len(UnsupportedAccesses),
        "UnsupportedApertureOptionCount": len(
            UnsupportedApertureOptions
        ),
        "UnsupportedLocalApertureSupportCount": len(
            UnsupportedLocalApertureSupports
        ),
        "UnaryLocalAccessClauseCount": sum(
            1
            for Clause in Result
            if bool(Clause & UnsupportedLocalContracts)
        ),
        "UnarySeamClauseCount": sum(
            1
            for Clause in Result
            if any(
                str(Fingerprint).startswith("local-seam-contract-v1:")
                for _Signal, Fingerprint in Clause
            )
        ),
        "UnaryApertureClauseCount": len(Result),
        "UnaryCertificateCacheHit": False,
        "DomainFingerprint": CacheKey,
    }
    if CompletedClauseCache is not None:
        CompletedClauseCache[CacheKey] = (Result, Diagnostics)
    return Result, Diagnostics


def SelectPhysicalComponentResourceRelevantSignalPairs(
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
) -> tuple[tuple[str, str], ...]:
    """Select only signal pairs whose exact local claims can intersect."""
    ResourceIdsBySignal = {
        str(Signal): frozenset(
            Resource
            for Factor in Factors
            for Resource in Factor.LocalClaims.ResourceIds
        )
        for Signal, Factors in FactorDomain.LocalAccessFactorsBySignal
    }
    Signals = tuple(sorted(ResourceIdsBySignal))
    return tuple(
        (FirstSignal, SecondSignal)
        for FirstIndex, FirstSignal in enumerate(Signals)
        for SecondSignal in Signals[FirstIndex + 1:]
        if ResourceIdsBySignal[FirstSignal].intersection(
            ResourceIdsBySignal[SecondSignal]
        )
    )


def CompilePhysicalComponentForeignPortalUnaryApertureClauses(
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    RawPortalCache: Any,
    ResourceGraph: Any,
) -> tuple[
    frozenset[frozenset[tuple[str, str]]],
    dict[str, object],
]:
    """Reject apertures whose fixed claims erase a foreign terminal domain.

    The whole-design portal cache is complete before component CSP solving.
    Intersecting every portal path for one terminal recovers its immutable
    access stem; the union of those stems is mandatory for that ordinary net.
    If one component aperture conflicts with every portal alternative for a
    terminal, the resulting unary clause is exact and independent of any
    exterior route candidate later materialized for that aperture.
    """
    if RawPortalCache is None or ResourceGraph is None:
        return frozenset(), {
            "Complete": False,
            "Reason": "missing-portal-cache-or-resource-graph",
        }
    RawPortals = RawPortalCache.BuildPortalDictionary()
    CompleteKeys = frozenset(
        getattr(RawPortalCache, "CompletePortalDomainKeys", ())
    )
    LayerCount = int(getattr(RawPortalCache, "LayerCount", 0))
    ComponentSignals = frozenset(
        str(Signal)
        for Signal, _Options in (
            FactorDomain.BoundaryPortReservationsBySignal
        )
    )
    TerminalsBySignal: dict[str, set[Position3]] = {}
    for Signal, Terminal, _Layer in CompleteKeys:
        if str(Signal) in ComponentSignals:
            continue
        TerminalsBySignal.setdefault(str(Signal), set()).add(Terminal)
    ForeignDomains: list[
        tuple[str, Position3, tuple[RoutingResourceClaims, ...]]
    ] = []
    IncompleteTerminalCount = 0
    for Signal in sorted(TerminalsBySignal):
        PortalsByTerminal: dict[
            Position3, tuple[Any, ...]
        ] = {}
        MandatoryNodes: set[Position3] = set()
        for Terminal in sorted(TerminalsBySignal[Signal]):
            Keys = tuple(
                (Signal, Terminal, Layer)
                for Layer in range(LayerCount)
            )
            if not all(Key in CompleteKeys for Key in Keys):
                IncompleteTerminalCount += 1
                continue
            PortalsById = {
                Portal.PortalId: Portal
                for Key in Keys
                for Portal in RawPortals.get(Key, ())
            }
            Portals = tuple(sorted(
                PortalsById.values(),
                key=lambda Value: Value.PortalId,
            ))
            if not Portals:
                continue
            PortalsByTerminal[Terminal] = Portals
            CommonNodes = set(Portals[0].Path)
            for Portal in Portals[1:]:
                CommonNodes.intersection_update(Portal.Path)
            MandatoryNodes.update(CommonNodes)
        FrozenMandatoryNodes = frozenset(MandatoryNodes)
        for Terminal, Portals in sorted(PortalsByTerminal.items()):
            ForeignDomains.append((
                Signal,
                Terminal,
                tuple(
                    ResourceGraph.BuildRouteClaims(
                        FrozenMandatoryNodes | frozenset(Portal.Path)
                    )
                    for Portal in Portals
                ),
            ))
    Clauses: set[frozenset[tuple[str, str]]] = set()
    RejectedCountsBySignal: dict[str, int] = {}
    AperturePortalSlackBySignal: dict[
        str, dict[str, tuple[int, int]]
    ] = {}
    CompatibilityCheckCount = 0
    for Signal, Options in (
        FactorDomain.BoundaryPortReservationsBySignal
    ):
        for Option in Options:
            Unsupported = False
            MinimumRemainingAlternativeCount: int | None = None
            TotalRemainingAlternativeCount = 0
            for _ForeignSignal, _Terminal, ClaimsDomain in ForeignDomains:
                CompatibilityCheckCount += len(ClaimsDomain)
                ConflictCount = sum(
                    ComponentClaimsConflict(
                        Option.GlobalClaims,
                        Claims,
                    )
                    for Claims in ClaimsDomain
                )
                RemainingAlternativeCount = (
                    len(ClaimsDomain) - ConflictCount
                )
                MinimumRemainingAlternativeCount = min(
                    RemainingAlternativeCount,
                    (
                        MinimumRemainingAlternativeCount
                        if MinimumRemainingAlternativeCount is not None
                        else RemainingAlternativeCount
                    ),
                )
                TotalRemainingAlternativeCount += (
                    RemainingAlternativeCount
                )
                if ClaimsDomain and RemainingAlternativeCount == 0:
                    Unsupported = True
                    break
            StoredFingerprint = str(
                Option.ApertureContractFingerprint
            )
            CanonicalFingerprint = (
                BuildPhysicalPortApertureContractFingerprint(Option)
            )
            if not Unsupported:
                for Fingerprint in {
                    StoredFingerprint,
                    CanonicalFingerprint,
                }:
                    if Fingerprint:
                        AperturePortalSlackBySignal.setdefault(
                            str(Signal),
                            {},
                        )[Fingerprint] = (
                            int(MinimumRemainingAlternativeCount or 0),
                            int(TotalRemainingAlternativeCount),
                        )
                continue
            for Fingerprint in {
                StoredFingerprint,
                CanonicalFingerprint,
            }:
                if Fingerprint:
                    Clauses.add(frozenset(((
                        str(Signal),
                        Fingerprint,
                    ),)))
            RejectedCountsBySignal[str(Signal)] = (
                RejectedCountsBySignal.get(str(Signal), 0) + 1
            )
    return frozenset(Clauses), {
        "Complete": IncompleteTerminalCount == 0,
        "ForeignTerminalDomainCount": len(ForeignDomains),
        "IncompleteForeignTerminalDomainCount": IncompleteTerminalCount,
        "ComponentSignalCount": len(ComponentSignals),
        "ApertureOptionCount": sum(
            len(Options)
            for _Signal, Options in (
                FactorDomain.BoundaryPortReservationsBySignal
            )
        ),
        "RejectedApertureCount": len(Clauses),
        "RejectedApertureCountsBySignal": dict(sorted(
            RejectedCountsBySignal.items()
        )),
        "AperturePortalSlackBySignal": {
            Signal: dict(sorted(Values.items()))
            for Signal, Values in sorted(
                AperturePortalSlackBySignal.items()
            )
        },
        "CompatibilityCheckCount": CompatibilityCheckCount,
    }


def ProveClosedComponentSymbolicCapacityEligibility(
    Problem: ComponentRoutingProblem,
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    CompletedProofCache: dict[
        str, ComponentRoutingSolveResult
    ] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    SymbolicNetStateCache: dict[str, Any] | None = None,
) -> ComponentRoutingSolveResult:
    """Certify one selected local port tuple before routing its corridors.

    The proof deliberately removes reserved global-route claims.  It is a
    necessary local-capacity admission certificate for an already selected
    physical boundary, not local template compilation and not authority to
    choose a global boundary.
    """
    DomainFingerprint = BuildGlobalRelaxedLocalProofDomainFingerprint(
        Problem
    )
    Cached = (
        CompletedProofCache.get(DomainFingerprint)
        if CompletedProofCache is not None
        else None
    )
    if Cached is not None:
        return replace(
            Cached,
            Diagnostics={
                **dict(Cached.Diagnostics or {}),
                "SymbolicCapacityAdmissionDomainFingerprint": (
                    DomainFingerprint
                ),
                "SymbolicCapacityAdmissionCacheHit": True,
            },
        )
    RelaxedProblem = replace(
        Problem,
        ProblemFingerprint=_Fingerprint((
            "pre-global-symbolic-capacity-admission-v1",
            DomainFingerprint,
        )),
        ReservedGlobalClaimsBySignal=(),
    )
    Result = SolveComponentRoutingProblem(
        RelaxedProblem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        SymbolicNetStateCache=SymbolicNetStateCache,
        StopAfterSymbolicCapacityProof=True,
    )
    if Result.Template is not None:
        raise ValueError(
            "symbolic capacity eligibility materialized a template"
        )
    Result = replace(
        Result,
        Diagnostics={
            **dict(Result.Diagnostics or {}),
            "SymbolicCapacityAdmissionDomainFingerprint": (
                DomainFingerprint
            ),
            "SymbolicCapacityAdmissionCacheHit": False,
            "ReservedGlobalClaimsRemoved": True,
        },
    )
    Complete = bool(
        Result.Status == "capacity-feasible"
        or (
            Result.Status == "architectural-unsatisfiable"
            and Result.Diagnostics.get(
                "SymbolicCapacityProofComplete",
                False,
            )
        )
    )
    if CompletedProofCache is not None and Complete:
        CompletedProofCache[DomainFingerprint] = Result
    return Result


def ProjectCompletePhysicalPortPairCertificateToApertureClauses(
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    Certificate: PhysicalComponentSymbolicPortPairCertificate,
) -> tuple[
    frozenset[frozenset[tuple[str, str]]],
    dict[str, object],
]:
    """Project one complete local seam relation onto absolute apertures.

    Portable reservation fingerprints deliberately do not participate in this
    projection: they are translation-normalized and may alias distinct
    physical apertures.  A cut is sound only when every local-access factor in
    both certificate domains maps through the prepared support relation to an
    authoritative ``ApertureContractFingerprint``.  Any missing or ambiguous
    edge therefore suppresses the entire projection rather than publishing a
    partial global no-good.
    """
    Signals = tuple(map(str, Certificate.SignalPair))
    Diagnostics: dict[str, object] = {
        "PortPairCompatibilityComplete": bool(Certificate.Complete),
        "ApertureProjectionComplete": False,
        "ApertureProjectionSignals": list(Signals),
        "ApertureProjectionFailureReason": "",
    }
    if not Certificate.Complete or len(Signals) != 2 or len(set(Signals)) != 2:
        Diagnostics["ApertureProjectionFailureReason"] = (
            "pair-certificate-incomplete-or-invalid"
        )
        return frozenset(), Diagnostics

    ExpectedLocalAccessBySignal = {
        str(Signal): frozenset(map(str, LocalAccessFingerprints))
        for Signal, LocalAccessFingerprints
        in Certificate.LocalAccessFingerprintsBySignal
        if str(Signal) in Signals
    }
    if (
        set(ExpectedLocalAccessBySignal) != set(Signals)
        or any(
            not ExpectedLocalAccessBySignal.get(Signal)
            for Signal in Signals
        )
    ):
        Diagnostics["ApertureProjectionFailureReason"] = (
            "certificate-local-access-domain-incomplete"
        )
        return frozenset(), Diagnostics

    SeamByLocalAccess: dict[tuple[str, str], str] = {}
    for Signal, LocalAccessFingerprint, SeamFingerprint in (
        Certificate.SeamFingerprintByLocalAccess
    ):
        Key = (str(Signal), str(LocalAccessFingerprint))
        Seam = str(SeamFingerprint)
        Existing = SeamByLocalAccess.get(Key)
        if (
            Key[0] not in Signals
            or not Seam
            or (Existing is not None and Existing != Seam)
        ):
            Diagnostics["ApertureProjectionFailureReason"] = (
                "certificate-seam-map-incomplete-or-ambiguous"
            )
            return frozenset(), Diagnostics
        SeamByLocalAccess[Key] = Seam
    if any(
        set(
            LocalAccess
            for (CandidateSignal, LocalAccess) in SeamByLocalAccess
            if CandidateSignal == Signal
        ) != set(ExpectedLocalAccessBySignal[Signal])
        for Signal in Signals
    ):
        Diagnostics["ApertureProjectionFailureReason"] = (
            "certificate-seam-map-does-not-cover-local-domain"
        )
        return frozenset(), Diagnostics

    ApertureContractByOption: dict[tuple[str, str], str] = {}
    for Signal, Factors in FactorDomain.ApertureFactorsBySignal:
        Signal = str(Signal)
        if Signal not in Signals:
            continue
        for Factor in Factors:
            Key = (
                Signal,
                str(Factor.ApertureOptionFingerprint),
            )
            Contract = str(Factor.ApertureContractFingerprint)
            Existing = ApertureContractByOption.get(Key)
            if (
                not Key[1]
                or not Contract
                or (Existing is not None and Existing != Contract)
            ):
                Diagnostics["ApertureProjectionFailureReason"] = (
                    "aperture-option-contract-incomplete-or-ambiguous"
                )
                return frozenset(), Diagnostics
            ApertureContractByOption[Key] = Contract
    if any(
        not any(Key[0] == Signal for Key in ApertureContractByOption)
        for Signal in Signals
    ):
        Diagnostics["ApertureProjectionFailureReason"] = (
            "aperture-option-domain-incomplete"
        )
        return frozenset(), Diagnostics

    SupportsByOption: dict[tuple[str, str], list[str]] = {
        Key: [] for Key in ApertureContractByOption
    }
    MappedLocalAccessBySignal: dict[str, set[str]] = {
        Signal: set() for Signal in Signals
    }
    for Signal, Supports in FactorDomain.LocalApertureSupportBySignal:
        Signal = str(Signal)
        if Signal not in Signals:
            continue
        for Support in Supports:
            Key = (
                Signal,
                str(Support.ApertureOptionFingerprint),
            )
            LocalAccess = str(Support.LocalAccessFingerprint)
            if (
                Key not in ApertureContractByOption
                or LocalAccess not in ExpectedLocalAccessBySignal[Signal]
                or (Signal, LocalAccess) not in SeamByLocalAccess
            ):
                Diagnostics["ApertureProjectionFailureReason"] = (
                    "prepared-support-edge-unresolved"
                )
                return frozenset(), Diagnostics
            SupportsByOption[Key].append(LocalAccess)
            MappedLocalAccessBySignal[Signal].add(LocalAccess)
    if (
        any(not Values for Values in SupportsByOption.values())
        or any(
            MappedLocalAccessBySignal[Signal]
            != set(ExpectedLocalAccessBySignal[Signal])
            for Signal in Signals
        )
    ):
        Diagnostics["ApertureProjectionFailureReason"] = (
            "prepared-support-domain-incomplete"
        )
        return frozenset(), Diagnostics

    SeamsByApertureContract: dict[tuple[str, str], set[str]] = {}
    for OptionKey, LocalAccessFingerprints in SupportsByOption.items():
        ApertureKey = (
            OptionKey[0],
            ApertureContractByOption[OptionKey],
        )
        SeamsByApertureContract.setdefault(ApertureKey, set()).update(
            SeamByLocalAccess[(OptionKey[0], LocalAccessFingerprint)]
            for LocalAccessFingerprint in LocalAccessFingerprints
        )

    UnsupportedUnarySeams = frozenset(
        (str(Signal), str(Seam))
        for Signal, Seam in Certificate.UnsupportedUnarySeams
    )
    UnsupportedSeamPairs = frozenset(
        frozenset((
            (str(First[0]), str(First[1])),
            (str(Second[0]), str(Second[1])),
        ))
        for First, Second in Certificate.UnsupportedSeamPairs
    )
    Clauses: set[frozenset[tuple[str, str]]] = set()
    for ApertureKey, Seams in SeamsByApertureContract.items():
        if Seams and all(
            (ApertureKey[0], Seam) in UnsupportedUnarySeams
            for Seam in Seams
        ):
            Clauses.add(frozenset((ApertureKey,)))

    FirstSignal, SecondSignal = Signals
    FirstApertures = tuple(
        (Key, Seams)
        for Key, Seams in SeamsByApertureContract.items()
        if Key[0] == FirstSignal
    )
    SecondApertures = tuple(
        (Key, Seams)
        for Key, Seams in SeamsByApertureContract.items()
        if Key[0] == SecondSignal
    )
    for FirstKey, FirstSeams in FirstApertures:
        for SecondKey, SecondSeams in SecondApertures:
            if FirstSeams and SecondSeams and all(
                (FirstSignal, FirstSeam) in UnsupportedUnarySeams
                or (SecondSignal, SecondSeam) in UnsupportedUnarySeams
                or frozenset((
                    (FirstSignal, FirstSeam),
                    (SecondSignal, SecondSeam),
                )) in UnsupportedSeamPairs
                for FirstSeam in FirstSeams
                for SecondSeam in SecondSeams
            ):
                Clauses.add(frozenset((FirstKey, SecondKey)))

    Diagnostics.update({
        "ApertureProjectionComplete": True,
        "ApertureProjectionFailureReason": "",
        "ApertureProjectionOptionCount": len(
            ApertureContractByOption
        ),
        "ApertureProjectionClauseCount": len(Clauses),
    })
    return frozenset(Clauses), Diagnostics


def ProjectCompletePhysicalHigherOrderCertificateToApertureClauses(
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    Certificate: PhysicalComponentSymbolicHigherOrderCertificate,
    *,
    RestrictedApertureContractsBySignal: (
        Mapping[str, str | frozenset[str]] | None
    ) = None,
) -> tuple[
    frozenset[frozenset[tuple[str, str]]],
    dict[str, object],
]:
    """Project one complete 3+ signal seam relation onto exact apertures.

    The projection is universal: an aperture tuple is rejected only when
    every local seam tuple supported behind it is disproven by the complete
    certificate.  Production callers may restrict the absolute contracts to
    the current physical plan so proof compilation stays core-driven instead
    of eagerly enumerating the whole global aperture product.
    """
    Signals = tuple(map(str, Certificate.SignalDomain))
    Diagnostics: dict[str, object] = {
        "HigherOrderCompatibilityComplete": bool(Certificate.Complete),
        "HigherOrderApertureProjectionComplete": False,
        "HigherOrderApertureProjectionSignals": list(Signals),
        "HigherOrderApertureProjectionFailureReason": "",
    }

    def Incomplete(Reason: str) -> tuple[
        frozenset[frozenset[tuple[str, str]]],
        dict[str, object],
    ]:
        Diagnostics["HigherOrderApertureProjectionFailureReason"] = Reason
        return frozenset(), Diagnostics

    if (
        not Certificate.Complete
        or len(Signals) < 3
        or len(set(Signals)) != len(Signals)
    ):
        return Incomplete("higher-order-certificate-incomplete-or-invalid")
    if (
        not FactorDomain.Complete
        or not FactorDomain.Feasible
        or str(Certificate.PreparedDomainFingerprint)
        != str(FactorDomain.DomainFingerprint)
    ):
        return Incomplete("prepared-domain-identity-mismatch")

    ExpectedAccessesBySignal: dict[str, frozenset[str]] = {}
    for Signal, Accesses in Certificate.LocalAccessFingerprintsBySignal:
        Signal = str(Signal)
        Values = frozenset(map(str, Accesses))
        if Signal in ExpectedAccessesBySignal:
            return Incomplete(
                "certificate-local-access-domain-incomplete-or-ambiguous"
            )
        ExpectedAccessesBySignal[Signal] = Values
    if (
        set(ExpectedAccessesBySignal) != set(Signals)
        or any(not ExpectedAccessesBySignal[Signal] for Signal in Signals)
    ):
        return Incomplete("certificate-local-access-domain-incomplete")

    SeamByAccess: dict[tuple[str, str], str] = {}
    for Signal, Access, Seam in Certificate.SeamFingerprintByLocalAccess:
        Key = (str(Signal), str(Access))
        Seam = str(Seam)
        Existing = SeamByAccess.get(Key)
        if (
            Key[0] not in Signals
            or not Seam
            or (Existing is not None and Existing != Seam)
        ):
            return Incomplete(
                "certificate-access-seam-map-incomplete-or-ambiguous"
            )
        SeamByAccess[Key] = Seam
    if any(
        frozenset(
            Access
            for CandidateSignal, Access in SeamByAccess
            if CandidateSignal == Signal
        ) != ExpectedAccessesBySignal[Signal]
        for Signal in Signals
    ):
        return Incomplete("certificate-access-seam-map-does-not-cover-domain")

    PreparedSeamByAccess: dict[tuple[str, str], str] = {}
    for Signal, Factors in FactorDomain.LocalAccessFactorsBySignal:
        Signal = str(Signal)
        if Signal not in Signals:
            continue
        for Factor in Factors:
            Access = str(Factor.LocalAccessFingerprint)
            Key = (Signal, Access)
            Seam = (
                str(getattr(Factor, "SeamContractFingerprint", ""))
                or BuildPhysicalPortSeamContractFingerprint(Factor)
            )
            Existing = PreparedSeamByAccess.get(Key)
            if Existing is not None and Existing != Seam:
                return Incomplete("prepared-access-seam-map-ambiguous")
            PreparedSeamByAccess[Key] = Seam
    if any(
        PreparedSeamByAccess.get((Signal, Access))
        != SeamByAccess.get((Signal, Access))
        for Signal in Signals
        for Access in ExpectedAccessesBySignal[Signal]
    ):
        return Incomplete("prepared-access-seam-identity-mismatch")

    ContractByOption: dict[tuple[str, str], str] = {}
    for Signal, Apertures in FactorDomain.ApertureFactorsBySignal:
        Signal = str(Signal)
        if Signal not in Signals:
            continue
        for Aperture in Apertures:
            Key = (Signal, str(Aperture.ApertureOptionFingerprint))
            Contract = str(Aperture.ApertureContractFingerprint)
            Existing = ContractByOption.get(Key)
            if (
                not Key[1]
                or not Contract
                or (Existing is not None and Existing != Contract)
            ):
                return Incomplete(
                    "aperture-option-contract-incomplete-or-ambiguous"
                )
            ContractByOption[Key] = Contract

    SupportsByOption = {
        (str(Key[0]), str(Key[1])): tuple(Supports)
        for Key, Supports in FactorDomain.LocalApertureSupportsByOption
        if str(Key[0]) in Signals
    }
    if any(Key not in ContractByOption for Key in SupportsByOption):
        return Incomplete("prepared-support-option-unresolved")
    SeamsByContract: dict[tuple[str, str], set[str]] = {}
    MappedAccessesBySignal: dict[str, set[str]] = {
        Signal: set() for Signal in Signals
    }
    for OptionKey, Contract in ContractByOption.items():
        Supports = SupportsByOption.get(OptionKey)
        if not Supports:
            return Incomplete("aperture-option-has-no-local-support")
        for Support in Supports:
            Access = str(Support.LocalAccessFingerprint)
            if (
                str(Support.ApertureOptionFingerprint) != OptionKey[1]
                or Access not in ExpectedAccessesBySignal[OptionKey[0]]
            ):
                return Incomplete("prepared-support-access-unresolved")
            Seam = SeamByAccess.get((OptionKey[0], Access))
            if Seam is None:
                return Incomplete("prepared-support-seam-unresolved")
            MappedAccessesBySignal[OptionKey[0]].add(Access)
            SeamsByContract.setdefault(
                (OptionKey[0], Contract), set()
            ).add(Seam)
    if any(
        MappedAccessesBySignal[Signal]
        != set(ExpectedAccessesBySignal[Signal])
        for Signal in Signals
    ):
        return Incomplete("prepared-support-domain-incomplete")

    Restriction = {}
    for Signal, Contracts in (
        RestrictedApertureContractsBySignal or {}
    ).items():
        Restriction[str(Signal)] = (
            frozenset((str(Contracts),))
            if isinstance(Contracts, str)
            else frozenset(map(str, Contracts))
        )
    if Restriction and not set(Signals) <= set(Restriction):
        return Incomplete("restricted-aperture-contract-domain-incomplete")
    ContractDomains = []
    for Signal in Signals:
        Contracts = tuple(sorted(
            Contract
            for CandidateSignal, Contract in SeamsByContract
            if CandidateSignal == Signal
            and (
                Signal not in Restriction
                or Contract in Restriction[Signal]
            )
        ))
        if not Contracts:
            return Incomplete("aperture-contract-domain-empty")
        if Signal in Restriction and frozenset(Contracts) != Restriction[Signal]:
            return Incomplete("restricted-aperture-contract-unresolved")
        ContractDomains.append(Contracts)

    CertifiedSeamsBySignal = {
        str(Signal): frozenset(map(str, Seams))
        for Signal, Seams in Certificate.SeamFingerprintsBySignal
    }
    if (
        set(CertifiedSeamsBySignal) != set(Signals)
        or any(
            not CertifiedSeamsBySignal[Signal]
            or CertifiedSeamsBySignal[Signal] != frozenset(
                Seam
                for (CandidateSignal, _Access), Seam
                in SeamByAccess.items()
                if CandidateSignal == Signal
            )
            for Signal in Signals
        )
    ):
        return Incomplete("certificate-seam-domain-incomplete-or-ambiguous")
    MutableSupportedSeamTuples = set()
    for TupleValue in Certificate.SupportedSeamTuples:
        BySignal = {
            str(Signal): str(Seam) for Signal, Seam in TupleValue
        }
        if (
            len(BySignal) != len(TupleValue)
            or set(BySignal) != set(Signals)
            or any(
                BySignal[Signal] not in CertifiedSeamsBySignal[Signal]
                for Signal in Signals
            )
        ):
            return Incomplete("certificate-supported-seam-tuple-invalid")
        MutableSupportedSeamTuples.add(tuple(
            (Signal, BySignal[Signal]) for Signal in Signals
        ))
    SupportedSeamTuples = frozenset(MutableSupportedSeamTuples)
    Clauses: set[frozenset[tuple[str, str]]] = set()
    SeamTupleCheckCount = 0
    for ContractValues in product(*ContractDomains):
        ContractTuple = tuple(zip(Signals, ContractValues))
        SeamDomains = tuple(
            tuple(sorted(SeamsByContract[(Signal, Contract)]))
            for Signal, Contract in ContractTuple
        )
        if any(not Seams for Seams in SeamDomains):
            return Incomplete("aperture-contract-seam-domain-empty")
        UniversallyUnsupported = True
        SeamDomainsBySignal = {
            Signal: frozenset(Seams)
            for Signal, Seams in zip(Signals, SeamDomains)
        }
        for SupportedTuple in SupportedSeamTuples:
            SeamTupleCheckCount += 1
            if all(
                Seam in SeamDomainsBySignal[Signal]
                for Signal, Seam in SupportedTuple
            ):
                UniversallyUnsupported = False
                break
        if UniversallyUnsupported:
            Clauses.add(frozenset(ContractTuple))

    Diagnostics.update({
        "HigherOrderApertureProjectionComplete": True,
        "HigherOrderApertureProjectionFailureReason": "",
        "HigherOrderApertureProjectionRestricted": bool(Restriction),
        "HigherOrderApertureProjectionContractTupleCount": int(
            prod(map(len, ContractDomains))
        ),
        "HigherOrderApertureProjectionSeamTupleCheckCount": (
            SeamTupleCheckCount
        ),
        "HigherOrderApertureProjectionClauseCount": len(Clauses),
    })
    return frozenset(Clauses), Diagnostics


def RecordPhysicalComponentSymbolicCapacityEligibilityNoGood(
    Proof: ComponentRoutingSolveResult,
    Plan: PhysicalComponentAssemblyPlan,
    Resources: Any,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain | None = None,
) -> dict[str, object]:
    """Reject one exact port tuple disproven before global reservation."""
    Diagnostics = dict(Proof.Diagnostics or {})
    if (
        Proof.Status != "architectural-unsatisfiable"
        or not Diagnostics.get("SymbolicCapacityProofComplete", False)
    ):
        raise ValueError(
            "symbolic capacity no-good requires a complete local proof"
        )
    Resources.RejectedPhysicalComponentPortAssignmentFingerprints.add(
        Plan.PortAssignmentFingerprint
    )
    Resources.RejectedPhysicalComponentAssemblyPlanFingerprints.add(
        Plan.PlanFingerprint
    )
    AssemblyChoiceFingerprint = str(getattr(
        Plan,
        "AssemblyChoiceFingerprint",
        "",
    ))
    if AssemblyChoiceFingerprint:
        Resources.RejectedPhysicalComponentAssemblyChoiceFingerprints.add(
            AssemblyChoiceFingerprint
        )
    CoreSignals = tuple(sorted({
        str(Signal)
        for Signal in Diagnostics.get("LocalUnsatCoreSignals", ())
        if str(Signal)
    }))
    PortsBySignal = {
        str(Port.Signal): Port for Port in Plan.Ports
    }
    PortCoreSignals = tuple(
        Signal for Signal in CoreSignals if Signal in PortsBySignal
    )
    LocalSeamNoGoodClauses = getattr(
        Resources,
        "RejectedPhysicalComponentLocalSeamReservationSets",
        None,
    )
    if LocalSeamNoGoodClauses is None:
        LocalSeamNoGoodClauses = set()
        setattr(
            Resources,
            "RejectedPhysicalComponentLocalSeamReservationSets",
            LocalSeamNoGoodClauses,
        )
    LocalCoreClause = frozenset()
    if (
        Diagnostics.get("LocalUnsatCoreComplete", False)
        and PortCoreSignals
    ):
        LocalCoreClause = frozenset(
            (
                Signal,
                BuildPhysicalPortSeamContractFingerprint(
                    PortsBySignal[Signal]
                ),
            )
            for Signal in PortCoreSignals
        )
        LocalSeamNoGoodClauses.add(LocalCoreClause)
        # Publish complete seam clauses to the staged CSP's canonical live
        # no-good set. Keep the legacy seam set mirrored until its remaining
        # consumers are removed after physical parity.
        Resources.RejectedPhysicalComponentPortReservationSets.add(
            LocalCoreClause
        )
    PromotedApertureClauses: set[
        frozenset[tuple[str, str]]
    ] = set()
    PromotedApertureSignals: set[str] = set()
    CoreSeamDomainSizes: dict[str, int] = {}
    if FactorDomain is not None and LocalCoreClause:
        BoundaryBySignal = {
            str(Port.Signal): Port
            for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
        }
        LocalFactorsBySignal = dict(
            FactorDomain.LocalAccessFactorsBySignal
        )
        ApertureFactorsBySignal = dict(
            FactorDomain.ApertureFactorsBySignal
        )
        SupportsByOption = dict(
            FactorDomain.LocalApertureSupportsByOption
        )
        SeamFingerprintsBySignal: dict[str, frozenset[str]] = {}
        for Signal in PortCoreSignals:
            Boundary = BoundaryBySignal.get(Signal)
            if Boundary is None:
                continue
            ApertureOptionFingerprints = frozenset(
                str(Aperture.ApertureOptionFingerprint)
                for Aperture in ApertureFactorsBySignal.get(Signal, ())
                if Aperture.GlobalContractFingerprint
                == Boundary.GlobalContractFingerprint
                and Aperture.ApertureContractFingerprint
                == Boundary.ApertureContractFingerprint
            )
            SupportedLocalAccessFingerprints = frozenset(
                str(Support.LocalAccessFingerprint)
                for ApertureOptionFingerprint
                in ApertureOptionFingerprints
                for Support in SupportsByOption.get(
                    (Signal, ApertureOptionFingerprint),
                    (),
                )
            )
            SeamFingerprints = frozenset(
                BuildPhysicalPortSeamContractFingerprint(LocalFactor)
                for LocalFactor in LocalFactorsBySignal.get(Signal, ())
                if str(LocalFactor.LocalAccessFingerprint)
                in SupportedLocalAccessFingerprints
            )
            if SeamFingerprints:
                SeamFingerprintsBySignal[Signal] = SeamFingerprints
                CoreSeamDomainSizes[Signal] = len(SeamFingerprints)

        RejectedLocalSeamClauses = tuple(
            Clause
            for Clause in LocalSeamNoGoodClauses
            if Clause and all(
                str(Fingerprint).startswith(
                    "local-seam-contract-v1:"
                )
                for _Signal, Fingerprint in Clause
            )
        )
        RejectedUnarySeamKeys = frozenset(
            next(iter(Clause))
            for Clause in RejectedLocalSeamClauses
            if len(Clause) == 1
        )
        MutableRejectedSeamPartners: dict[
            tuple[str, str], set[tuple[str, str]]
        ] = {}
        RejectedHigherOrderSeamClauses = []
        for Clause in RejectedLocalSeamClauses:
            if len(Clause) == 2:
                First, Second = tuple(Clause)
                MutableRejectedSeamPartners.setdefault(
                    First, set()
                ).add(Second)
                MutableRejectedSeamPartners.setdefault(
                    Second, set()
                ).add(First)
            elif len(Clause) > 2:
                RejectedHigherOrderSeamClauses.append(Clause)
        RejectedSeamPartners = {
            Key: frozenset(Values)
            for Key, Values in MutableRejectedSeamPartners.items()
        }

        def SeamTupleIsRejected(
            Keys: frozenset[tuple[str, str]],
        ) -> bool:
            return bool(
                Keys & RejectedUnarySeamKeys
                or any(
                    RejectedSeamPartners.get(Key, frozenset()) & Keys
                    for Key in Keys
                )
                or any(
                    Clause <= Keys
                    for Clause in RejectedHigherOrderSeamClauses
                )
            )

        def HasSupportedSeamTuple(
            RemainingSignals: tuple[str, ...],
            Keys: frozenset[tuple[str, str]],
        ) -> bool:
            if SeamTupleIsRejected(Keys):
                return False
            if not RemainingSignals:
                return True
            Signal = min(
                RemainingSignals,
                key=lambda Value: (
                    len(SeamFingerprintsBySignal[Value]),
                    Value,
                ),
            )
            NextRemaining = tuple(
                Value for Value in RemainingSignals if Value != Signal
            )
            return any(
                HasSupportedSeamTuple(
                    NextRemaining,
                    Keys | frozenset(((Signal, Seam),)),
                )
                for Seam in sorted(SeamFingerprintsBySignal[Signal])
            )

        for Signal, SeamFingerprints in (
            SeamFingerprintsBySignal.items()
        ):
            if all(
                SeamTupleIsRejected(frozenset(((Signal, Seam),)))
                for Seam in SeamFingerprints
            ):
                Boundary = BoundaryBySignal[Signal]
                (
                    Resources
                    .RejectedPhysicalComponentPortReservationsBySignal
                    .setdefault(Signal, set())
                    .add(Boundary.ApertureContractFingerprint)
                )
                PromotedApertureSignals.add(Signal)
        if len(PortCoreSignals) >= 2 and all(
            Signal in SeamFingerprintsBySignal
            and Signal in BoundaryBySignal
            for Signal in PortCoreSignals
        ):
            if not HasSupportedSeamTuple(PortCoreSignals, frozenset()):
                ApertureClause = frozenset(
                    (
                        Signal,
                        BoundaryBySignal[Signal]
                        .ApertureContractFingerprint,
                    )
                    for Signal in PortCoreSignals
                )
                Resources.RejectedPhysicalComponentPortReservationSets.add(
                    ApertureClause
                )
                PromotedApertureClauses.add(ApertureClause)
    # The boundary generator reads the learned-clause set dynamically.  Keep
    # its suspended DFS cursor and invocation-local support memo alive so the
    # next plan is the next member of the same complete domain, rather than a
    # replay of the domain under a rotated branch order.  A global/detailed
    # failure may still request an explicit traversal change; a complete
    # pre-global local core only shrinks this retained frontier.
    TraversalDiagnostics = {
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
    return {
        "NoGoodScope": "pre-global-symbolic-capacity-port-assignment",
        "RejectedPortAssignmentFingerprint": (
            Plan.PortAssignmentFingerprint
        ),
        "RejectedPhysicalAssemblyPlanFingerprint": Plan.PlanFingerprint,
        "RejectedAssemblyChoiceFingerprint": AssemblyChoiceFingerprint,
        "LocalCapacityCoreSignals": list(CoreSignals),
        "LocalCapacityProjectedInterfaceCoreSignals": list(
            PortCoreSignals
        ),
        "LocalCapacityCoreClause": [
            list(Value) for Value in sorted(LocalCoreClause)
        ],
        "LocalCapacityCorePromoted": bool(LocalCoreClause),
        "LocalCapacityApertureSignalsPromoted": sorted(
            PromotedApertureSignals
        ),
        "LocalCapacityApertureClausesPromoted": [
            [list(Key) for Key in sorted(Clause)]
            for Clause in sorted(
                PromotedApertureClauses,
                key=lambda Value: tuple(sorted(Value)),
            )
        ],
        "LocalCapacityCoreSeamDomainSizes": dict(sorted(
            CoreSeamDomainSizes.items()
        )),
        "SymbolicNetStateCacheHitCount": int(Diagnostics.get(
            "SymbolicNetStateCacheHitCount",
            0,
        )),
        "SymbolicNetStateCacheStoreCount": int(Diagnostics.get(
            "SymbolicNetStateCacheStoreCount",
            0,
        )),
        "SymbolicCapacityProofComplete": True,
        "SymbolicCapacityProofFingerprint": Proof.ProofFingerprint,
        "LocalCompilationEntered": False,
        "GlobalPlanningEntered": False,
        "PreferredRetainedGlobalContracts": dict(sorted(
            Resources.PreferredPhysicalComponentGlobalContractsBySignal.items()
        )),
        **TraversalDiagnostics,
        "ImplicitForeignTransitDomainCount": 0,
    }


def SelectContractIndependentOwnedSignalFrontierUnsatCore(
    Problem: ComponentRoutingProblem,
    Result: ComponentRoutingSolveResult,
) -> tuple[str, ...]:
    """Select only an unbound, port-independent owned-frontier proof.

    This is deliberately one-way: a feasible unbound frontier does not prove
    that any later fixed port contract is feasible.  It only lets placement
    eligibility reject a terminal-domain contradiction that binding cannot
    repair.
    """
    Interface = getattr(Problem, "Interface", None)
    if (
        tuple(getattr(Interface, "PhysicalPortReservations", ()))
        or bool(getattr(Problem, "ReservedGlobalClaimsBySignal", {}))
        or Result.Template is not None
        or Result.Status != "architectural-unsatisfiable"
    ):
        return ()
    Diagnostics = dict(Result.Diagnostics or {})
    CoreSignals = tuple(sorted(map(str, (
        Diagnostics.get("LocalUnsatCoreSignals", ()) or ()
    ))))
    SignalDiagnostics = Diagnostics.get("SignalDiagnostics", {})
    if (
        not CoreSignals
        or not Diagnostics.get("LocalUnsatCoreComplete", False)
        or Diagnostics.get("LocalUnsatCoreKind", "")
        != "tree-frontier-empty-owned-signal-domain"
        or not Diagnostics.get(
            "LocalUnsatCoreProjectionFingerprint",
            "",
        )
        or not isinstance(SignalDiagnostics, dict)
    ):
        return ()
    # Every signal admitted below is independently empty before a port is
    # assigned.  Return one deterministic singleton rather than the union of
    # all empty signals: the singleton is the deletion-minimal placement core
    # and can therefore drive a proof-qualified geometry change without
    # pretending that unrelated signals participated in the contradiction.
    for Signal in CoreSignals:
        SignalProof = SignalDiagnostics.get(Signal, {})
        if not isinstance(SignalProof, dict) or not (
            SignalProof.get("Complete", False)
            and SignalProof.get("EmptyPhase", "")
            == "owned-terminal-frontier"
            and SignalProof.get(
                "OwnedSignalDomainContractIndependent",
                False,
            )
            and int(SignalProof.get(
                "CertifiedRejectedCandidateCount",
                -1,
            )) == 0
        ):
            return ()
    return (CoreSignals[0],)


def PromoteCoveredLocalContractNoGoods(
    Plan: PhysicalComponentAssemblyPlan,
    CoreSignals: Any,
    Resources: Any,
) -> tuple[frozenset[tuple[str, str]], ...]:
    """Lift a fully covered exact local pair to its fabric-domain pair.

    The port option cache contains a complete finite domain.  Promotion is
    allowed only when proof-qualified local-contract no-goods cover the full
    Cartesian product for the current two fabric domains.  This adds no
    speculative local solve before global planning; it is clause resolution
    over completed proofs learned after authoritative local compilation.
    """
    Signals = tuple(sorted({str(Signal) for Signal in CoreSignals}))
    if len(Signals) != 2:
        return ()
    PortsBySignal = {Port.Signal: Port for Port in Plan.Ports}
    if any(Signal not in PortsBySignal for Signal in Signals):
        return ()
    FirstSignal, SecondSignal = Signals
    FirstFabric = PortsBySignal[FirstSignal].FabricDomainFingerprint
    SecondFabric = PortsBySignal[SecondSignal].FabricDomainFingerprint
    RejectedSets = (
        Resources.RejectedPhysicalComponentPortReservationSets
    )
    CurrentFirstContract = BuildPhysicalPortLocalContractFingerprint(
        PortsBySignal[FirstSignal]
    )
    CurrentSecondContract = BuildPhysicalPortLocalContractFingerprint(
        PortsBySignal[SecondSignal]
    )
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
    try:
        Domains = MaterializePreparedPhysicalPortOptionDomains(
            Preparation,
            Resources,
            Signals,
        )
    except ValueError:
        return ()
    DomainCacheKey = BuildPhysicalComponentPortSolverCacheKey(
        str(getattr(Preparation, "DomainFingerprint", ""))
    )
    FirstContracts = frozenset(
        BuildPhysicalPortLocalContractFingerprint(Option)
        for Option in Domains.get(FirstSignal, ())
        if Option.FabricDomainFingerprint == FirstFabric
    )
    SecondContracts = frozenset(
        BuildPhysicalPortLocalContractFingerprint(Option)
        for Option in Domains.get(SecondSignal, ())
        if Option.FabricDomainFingerprint == SecondFabric
    )
    if not FirstContracts or not SecondContracts:
        return ()
    if (
        CurrentFirstContract not in FirstContracts
        or CurrentSecondContract not in SecondContracts
    ):
        return ()
    Promotion = frozenset((
            (
                FirstSignal,
                "local-factor-domain:"
                + str(DomainCacheKey)
                + ":"
                + FirstFabric,
            ),
            (
                SecondSignal,
                "local-factor-domain:"
                + str(DomainCacheKey)
                + ":"
                + SecondFabric,
            ),
        ))
    if Promotion in RejectedSets:
        return (Promotion,)

    def PairIsRejected(
        FirstContract: str,
        SecondContract: str,
    ) -> bool:
        PairKeys = frozenset((
                (FirstSignal, FirstContract),
                (SecondSignal, SecondContract),
                (
                    FirstSignal,
                    "local-signal-domain:" + str(DomainCacheKey),
                ),
                (
                    SecondSignal,
                    "local-signal-domain:" + str(DomainCacheKey),
                ),
            ))
        return any(
            RejectedSet.issubset(PairKeys)
            for RejectedSet in RejectedSets
        )

    if all(
        PairIsRejected(FirstContract, SecondContract)
        for FirstContract in FirstContracts
        for SecondContract in SecondContracts
    ):
        RejectedSets.add(Promotion)
        RejectedSets.difference_update({
                frozenset((
                    (FirstSignal, FirstContract),
                    (SecondSignal, SecondContract),
                ))
                for FirstContract in FirstContracts
                for SecondContract in SecondContracts
        })
        return (Promotion,)
    return ()


def BuildDirectionalLocalFactorNoGoods(
    Plan: PhysicalComponentAssemblyPlan,
    CurrentSignal: str,
    CompleteSignal: str,
    Resources: Any,
) -> tuple[frozenset[tuple[str, str]], ...]:
    """Resolve covered exact pairs over one complete current factor domain.

    A ``complete-opposing-net-access-pair`` proof is bound to both selected
    local port contracts.  It does not by itself cover alternative seams for
    ``CurrentSignal``.  Promote the current side to its prepared-domain key
    only after proof-qualified exact no-goods cover every current contract
    against the same completed-side contract.
    """
    CurrentSignal = str(CurrentSignal)
    CompleteSignal = str(CompleteSignal)
    if not CurrentSignal or not CompleteSignal or CurrentSignal == CompleteSignal:
        return ()
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
    DomainFingerprint = str(
        getattr(Preparation, "DomainFingerprint", "")
    )
    if not DomainFingerprint:
        return ()
    PortSolverCacheKey = BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    try:
        Domains = MaterializePreparedPhysicalPortOptionDomains(
            Preparation,
            Resources,
            (CurrentSignal, CompleteSignal),
        )
    except ValueError:
        return ()
    if (
        CurrentSignal not in Domains
        or CompleteSignal not in Domains
        or not Domains[CurrentSignal]
        or not Domains[CompleteSignal]
    ):
        return ()
    PortsBySignal = {Port.Signal: Port for Port in Plan.Ports}
    if CurrentSignal not in PortsBySignal or CompleteSignal not in PortsBySignal:
        return ()
    CurrentContract = BuildPhysicalPortLocalContractFingerprint(
        PortsBySignal[CurrentSignal]
    )
    CompleteContract = BuildPhysicalPortLocalContractFingerprint(
        PortsBySignal[CompleteSignal]
    )
    CurrentDomainContracts = frozenset(
        BuildPhysicalPortLocalContractFingerprint(Option)
        for Option in Domains[CurrentSignal]
    )
    CompleteDomainContracts = frozenset(
        BuildPhysicalPortLocalContractFingerprint(Option)
        for Option in Domains[CompleteSignal]
    )
    if (
        CurrentContract not in CurrentDomainContracts
        or CompleteContract not in CompleteDomainContracts
    ):
        return ()
    RejectedSets = getattr(
        Resources,
        "RejectedPhysicalComponentPortReservationSets",
        set(),
    )

    def ExactPairIsRejected(CurrentValue: str) -> bool:
        PairKeys = frozenset((
            (CurrentSignal, CurrentValue),
            (CompleteSignal, CompleteContract),
        ))
        return any(
            RejectedSet.issubset(PairKeys)
            for RejectedSet in RejectedSets
        )

    if not all(
        ExactPairIsRejected(CurrentValue)
        for CurrentValue in CurrentDomainContracts
    ):
        return ()
    return (frozenset((
        (
            CurrentSignal,
            "local-signal-domain:" + PortSolverCacheKey,
        ),
        (CompleteSignal, CompleteContract),
    )),)


def BuildPhysicalLocalPortPairSupportCertificate(
    Preparation: Any,
    PortSolverCacheKey: str,
    RowSignal: str,
    RowContract: str,
    ColumnSignal: str,
    ColumnContracts: tuple[str, ...],
    LocalProofContextFingerprint: str,
    PairProofRecords: tuple[PhysicalLocalPortPairProofRecord, ...],
) -> PhysicalLocalPortPairSupportCertificate:
    """Freeze one completely disproven local-support row for port AC-3."""
    if (
        Preparation is None
        or not bool(getattr(Preparation, "Complete", False))
        or not bool(getattr(Preparation, "Feasible", False))
    ):
        raise ValueError(
            "local pair support requires complete feasible preparation"
        )
    PreparedDomainFingerprint = str(getattr(
        Preparation,
        "DomainFingerprint",
        "",
    ))
    Problem = getattr(Preparation, "Problem", None)
    Fabric = getattr(Problem, "Fabric", None)
    ComponentGraphFingerprint = str(getattr(
        Preparation,
        "ComponentGraphFingerprint",
        "",
    ))
    FabricFingerprint = str(getattr(Fabric, "FabricFingerprint", ""))
    ResourceGraphFingerprint = str(getattr(
        Preparation,
        "ResourceGraphFingerprint",
        "",
    ))
    TechnologyFingerprint = str(getattr(
        getattr(Preparation, "AccessCertificate", None),
        "TechnologyFingerprint",
        "",
    ))
    RowSignal = str(RowSignal)
    RowContract = str(RowContract)
    ColumnSignal = str(ColumnSignal)
    ColumnContracts = tuple(sorted({
        str(Value) for Value in ColumnContracts if str(Value)
    }))
    LocalProofContextFingerprint = str(LocalProofContextFingerprint)
    if any(
        not isinstance(Value, PhysicalLocalPortPairProofRecord)
        for Value in PairProofRecords
    ):
        raise ValueError("local pair support row is incomplete")
    PairProofRecords = tuple(sorted(
        PairProofRecords,
        key=lambda Value: (
            Value.CurrentContract,
            Value.ProofDomainFingerprint,
            Value.ProofFingerprint,
        ),
    ))
    ExpectedRecordKeys = frozenset(
        (ColumnContract, RowContract)
        for ColumnContract in ColumnContracts
    )
    ActualRecordKeys = frozenset(
        (Value.CurrentContract, Value.CompleteContract)
        for Value in PairProofRecords
    )
    RecordsAreExact = bool(
        len(PairProofRecords) == len(ColumnContracts)
        and ActualRecordKeys == ExpectedRecordKeys
        and all(
            isinstance(Value, PhysicalLocalPortPairProofRecord)
            and Value.CurrentSignal == ColumnSignal
            and Value.CompleteSignal == RowSignal
            and Value.CompleteContract == RowContract
            and Value.CurrentContract in ColumnContracts
            and Value.ProofDomainFingerprint
            and Value.ProofFingerprint
            and Value.Status == "architectural-unsatisfiable"
            and Value.Complete
            and Value.Feasible is False
            for Value in PairProofRecords
        )
    )
    if (
        not PreparedDomainFingerprint
        or not PortSolverCacheKey
        or not ComponentGraphFingerprint
        or not FabricFingerprint
        or not ResourceGraphFingerprint
        or not TechnologyFingerprint
        or not RowSignal
        or not RowContract
        or not ColumnSignal
        or RowSignal == ColumnSignal
        or not ColumnContracts
        or not LocalProofContextFingerprint
        or not RecordsAreExact
    ):
        raise ValueError("local pair support row is incomplete")
    CertificateFingerprint = _Fingerprint((
        "physical-local-port-pair-support-row-v1",
        PreparedDomainFingerprint,
        PortSolverCacheKey,
        ComponentGraphFingerprint,
        FabricFingerprint,
        ResourceGraphFingerprint,
        TechnologyFingerprint,
        RowSignal,
        RowContract,
        ColumnSignal,
        ColumnContracts,
        LocalProofContextFingerprint,
        PairProofRecords,
    ))
    return PhysicalLocalPortPairSupportCertificate(
        CertificateFingerprint=CertificateFingerprint,
        PreparedDomainFingerprint=PreparedDomainFingerprint,
        PortSolverCacheKey=str(PortSolverCacheKey),
        ComponentGraphFingerprint=ComponentGraphFingerprint,
        FabricFingerprint=FabricFingerprint,
        ResourceGraphFingerprint=ResourceGraphFingerprint,
        TechnologyFingerprint=TechnologyFingerprint,
        RowSignal=RowSignal,
        RowContract=RowContract,
        ColumnSignal=ColumnSignal,
        ColumnContracts=ColumnContracts,
        LocalProofContextFingerprint=LocalProofContextFingerprint,
        PairProofRecords=PairProofRecords,
        Complete=True,
    )


def ValidatePhysicalLocalPortPairSupportCertificate(
    Certificate: PhysicalLocalPortPairSupportCertificate,
    Preparation: Any,
    PortSolverCacheKey: str,
) -> bool:
    """Require a certificate to be the canonical row for this preparation."""
    if (
        not isinstance(
            Certificate,
            PhysicalLocalPortPairSupportCertificate,
        )
        or not Certificate.Complete
        or Certificate.PortSolverCacheKey != PortSolverCacheKey
    ):
        return False
    try:
        ExpectedProofContextFingerprint = (
            BuildPhysicalLocalPairProofContextFingerprint(
                Preparation.Problem,
                Preparation,
            )
        )
    except (AttributeError, TypeError, ValueError):
        return False
    if (
        Certificate.LocalProofContextFingerprint
        != ExpectedProofContextFingerprint
    ):
        return False
    LocalFactorsBySignal = dict(getattr(
        Preparation,
        "LocalAccessFactorsBySignal",
        (),
    ))
    RowContracts = frozenset(
        str(Value.LocalContractFingerprint)
        for Value in LocalFactorsBySignal.get(Certificate.RowSignal, ())
    )
    ColumnContracts = frozenset(
        str(Value.LocalContractFingerprint)
        for Value in LocalFactorsBySignal.get(Certificate.ColumnSignal, ())
    )
    if (
        not RowContracts
        or not ColumnContracts
        or Certificate.RowContract not in RowContracts
        or frozenset(Certificate.ColumnContracts) != ColumnContracts
    ):
        return False
    try:
        Rebuilt = BuildPhysicalLocalPortPairSupportCertificate(
            Preparation,
            PortSolverCacheKey,
            Certificate.RowSignal,
            Certificate.RowContract,
            Certificate.ColumnSignal,
            Certificate.ColumnContracts,
            Certificate.LocalProofContextFingerprint,
            Certificate.PairProofRecords,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return Rebuilt == Certificate


def CertifyDirectionalLocalContractPortfolio(
    Plan: PhysicalComponentAssemblyPlan,
    CurrentSignal: str,
    CompleteSignal: str,
    Resources: Any,
    *,
    BuildProofDomainFingerprint: Callable[[Any, Any], str],
    EvaluatePair: Callable[[Any, Any, str], dict[str, object]],
    LocalProofContextFingerprint: str,
    MaximumCompletedRows: int | None = None,
) -> dict[str, object]:
    """Certify exact local-contract pairs before resolving factor clauses.

    The selected fixed-plan proof admits this stage but covers only its exact
    pair. Every other pair must either already have a proof-qualified no-good
    or complete the same global-relaxed local feasibility proof here. A
    directional clause is emitted only after all current-side contracts are
    covered for one exact complete-side contract.
    """
    CurrentSignal = str(CurrentSignal)
    CompleteSignal = str(CompleteSignal)
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
    DomainFingerprint = str(
        getattr(Preparation, "DomainFingerprint", "")
    )
    if (
        not CurrentSignal
        or not CompleteSignal
        or CurrentSignal == CompleteSignal
        or not DomainFingerprint
        or not LocalProofContextFingerprint
    ):
        return {
            "Complete": False,
            "Reason": "missing-directional-portfolio-domain",
        }
    PortSolverCacheKey = BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    try:
        Domains = MaterializePreparedPhysicalPortOptionDomains(
            Preparation,
            Resources,
            (CurrentSignal, CompleteSignal),
        )
    except ValueError:
        return {
            "Complete": False,
            "Reason": "missing-complete-port-factor-domain",
            "PortSolverCacheKey": PortSolverCacheKey,
        }
    if any(
        Signal not in Domains or not Domains[Signal]
        for Signal in (CurrentSignal, CompleteSignal)
    ):
        return {
            "Complete": False,
            "Reason": "empty-directional-port-option-domain",
            "PortSolverCacheKey": PortSolverCacheKey,
        }

    def UniqueOptions(Signal: str) -> dict[str, Any]:
        Result = {}
        for Option in Domains[Signal]:
            Contract = BuildPhysicalPortLocalContractFingerprint(Option)
            Result.setdefault(Contract, Option)
        return Result

    CurrentOptions = UniqueOptions(CurrentSignal)
    CompleteOptions = UniqueOptions(CompleteSignal)
    RejectedSets = (
        Resources.RejectedPhysicalComponentPortReservationSets
    )
    ProofCache = getattr(
        Resources,
        "PhysicalComponentLocalInterfaceFactorProofCache",
        None,
    )
    if ProofCache is None:
        ProofCache = {}
        Resources.PhysicalComponentLocalInterfaceFactorProofCache = ProofCache
    SupportCertificateCache = getattr(
        Resources,
        "PhysicalLocalPortPairSupportCertificateCache",
        None,
    )
    if SupportCertificateCache is None:
        SupportCertificateCache = {}
        Resources.PhysicalLocalPortPairSupportCertificateCache = (
            SupportCertificateCache
        )
    EvaluatedPairCount = 0
    CachedPairCount = 0
    PreviouslyCoveredPairCount = 0
    CertifiedPairCount = 0
    IncompletePairs = []
    DeferredIncompleteRows = []
    ProofDomainFingerprints = set()
    DirectionalNoGoods = set()
    SupportCertificates = []
    FeasibleWitness = None
    FeasibleWitnessOptions = None
    ProcessedCompleteContractCount = 0
    CompletedRowCount = 0
    CompletedRowLimitReached = False

    def ExactPairNoGood(
        CurrentContract: str,
        CompleteContract: str,
    ) -> frozenset[tuple[str, str]]:
        return frozenset((
            (CurrentSignal, CurrentContract),
            (CompleteSignal, CompleteContract),
        ))

    def PairCovered(NoGood: frozenset[tuple[str, str]]) -> bool:
        ProofKeys = set(NoGood)
        for Signal, Contract in NoGood:
            Option = (
                CurrentOptions.get(Contract)
                if Signal == CurrentSignal
                else CompleteOptions.get(Contract)
                if Signal == CompleteSignal
                else None
            )
            if Option is None:
                continue
            ProofKeys.add((
                Signal,
                "local-signal-domain:" + PortSolverCacheKey,
            ))
            ProofKeys.add((
                Signal,
                "local-factor-domain:"
                + PortSolverCacheKey
                + ":"
                + str(Option.FabricDomainFingerprint),
            ))
        ExpandedNoGood = frozenset(ProofKeys)
        return any(
            RejectedSet.issubset(ExpandedNoGood)
            for RejectedSet in RejectedSets
        )

    SelectedCompleteOption = next(
        Port for Port in Plan.Ports if Port.Signal == CompleteSignal
    )
    SelectedCompleteContract = BuildPhysicalPortLocalContractFingerprint(
        SelectedCompleteOption
    )
    OrderedCompleteOptions = tuple(sorted(
        CompleteOptions.items(),
        key=lambda Value: (
            Value[0] != SelectedCompleteContract,
            Value[0],
        ),
    ))
    for CompleteContract, CompleteOption in OrderedCompleteOptions:
        ProcessedCompleteContractCount += 1
        ResolvedRowClause = frozenset((
            (
                CurrentSignal,
                "local-signal-domain:" + PortSolverCacheKey,
            ),
            (CompleteSignal, CompleteContract),
        ))
        if ResolvedRowClause in RejectedSets:
            PreviouslyCoveredPairCount += len(CurrentOptions)
            continue
        CompleteContractCovered = True
        RowProofRecords = []
        for CurrentContract, CurrentOption in sorted(CurrentOptions.items()):
            ExactNoGood = ExactPairNoGood(
                CurrentContract,
                CompleteContract,
            )
            if PairCovered(ExactNoGood):
                PreviouslyCoveredPairCount += 1
                continue
            ProofDomainFingerprint = str(BuildProofDomainFingerprint(
                CurrentOption,
                CompleteOption,
            ))
            if not ProofDomainFingerprint:
                CompleteContractCovered = False
                IncompletePairs.append((CurrentContract, CompleteContract))
                DeferredIncompleteRows.append(CompleteContract)
                break
            ProofDomainFingerprints.add(ProofDomainFingerprint)
            CacheKey = (
                PortSolverCacheKey,
                CurrentSignal,
                CompleteContract,
                ProofDomainFingerprint,
            )
            Proof = ProofCache.get(CacheKey)
            if Proof is None:
                Proof = dict(EvaluatePair(
                    CurrentOption,
                    CompleteOption,
                    ProofDomainFingerprint,
                ))
                EvaluatedPairCount += 1
            else:
                Proof = dict(Proof)
                CachedPairCount += 1
            ProofSignals = frozenset(map(str, (
                Proof.get("GlobalRelaxedLocalUnsatCoreSignals", ()) or ()
            )))
            Certified = bool(
                Proof.get("GlobalRelaxedLocalProofComplete", False)
                and Proof.get("GlobalRelaxedLocalCoreComplete", False)
                and Proof.get("GlobalRelaxedLocalUnsatCoreKind", "")
                == "complete-opposing-net-access-pair"
                and Proof.get("GlobalRelaxedLocalCurrentSignal", "")
                == CurrentSignal
                and Proof.get("GlobalRelaxedLocalCompleteSignal", "")
                == CompleteSignal
                and ProofSignals
                == frozenset((CurrentSignal, CompleteSignal))
                and Proof.get("GlobalRelaxedLocalDomainFingerprint", "")
                == ProofDomainFingerprint
                and bool(Proof.get(
                    "GlobalRelaxedLocalProofFingerprint",
                    "",
                ))
            )
            if Certified:
                # Only complete proof-qualified UNSAT results may populate
                # the reusable proof cache. Deadline/incomplete results carry
                # no monotonic information and must be recomputed.
                ProofCache[CacheKey] = Proof
                RejectedSets.add(ExactNoGood)
                CertifiedPairCount += 1
                RowProofRecords.append(
                    PhysicalLocalPortPairProofRecord(
                        CurrentSignal=CurrentSignal,
                        CurrentContract=CurrentContract,
                        CompleteSignal=CompleteSignal,
                        CompleteContract=CompleteContract,
                        ProofDomainFingerprint=ProofDomainFingerprint,
                        ProofFingerprint=str(Proof[
                            "GlobalRelaxedLocalProofFingerprint"
                        ]),
                        Status="architectural-unsatisfiable",
                        Complete=True,
                        Feasible=False,
                    )
                )
            else:
                CompleteContractCovered = False
                if (
                    Proof.get("GlobalRelaxedLocalProofStatus") == "feasible"
                    and Proof.get(
                        "GlobalRelaxedLocalFeasibleWitnessComplete",
                        False,
                    )
                ):
                    # A concrete route is a complete positive witness for this
                    # exact pair and is safe to retain.
                    ProofCache[CacheKey] = Proof
                    FeasibleWitness = (CurrentContract, CompleteContract)
                    FeasibleWitnessOptions = (CurrentOption, CompleteOption)
                    break
                IncompletePairs.append((CurrentContract, CompleteContract))
                # Incompleteness carries no authoritative support fact.  Stop
                # this row after its first unknown pair so later rows receive
                # a fair opportunity within the same admitted proof domain.
                # A future call may resume the underlying portfolio discovery
                # and reevaluate this exact pair without caching speculation.
                DeferredIncompleteRows.append(CompleteContract)
                break
        if FeasibleWitness is not None:
            break
        if CompleteContractCovered:
            CompletedRowCount += 1
            if (
                bool(getattr(Preparation, "Complete", False))
                and len(RowProofRecords) == len(CurrentOptions)
            ):
                Certificate = BuildPhysicalLocalPortPairSupportCertificate(
                    Preparation,
                    PortSolverCacheKey,
                    CompleteSignal,
                    CompleteContract,
                    CurrentSignal,
                    tuple(sorted(CurrentOptions)),
                    LocalProofContextFingerprint,
                    tuple(RowProofRecords),
                )
                SupportCertificateCache[
                    Certificate.CertificateFingerprint
                ] = Certificate
                SupportCertificates.append(Certificate)
            PortsBySignal = {Port.Signal: Port for Port in Plan.Ports}
            PortsBySignal[CompleteSignal] = CompleteOption
            PortfolioPorts = tuple(
                PortsBySignal.get(Port.Signal, Port)
                for Port in Plan.Ports
            )
            try:
                PortfolioPlan = replace(Plan, Ports=PortfolioPorts)
            except TypeError:
                PortfolioPlan = type("PortfolioPlan", (), {
                    **vars(Plan),
                    "Ports": PortfolioPorts,
                })()
            Clauses = BuildDirectionalLocalFactorNoGoods(
                PortfolioPlan,
                CurrentSignal,
                CompleteSignal,
                Resources,
            )
            RejectedSets.update(Clauses)
            DirectionalNoGoods.update(Clauses)
            if Clauses:
                # Clause resolution has replaced the full exact row. Keeping
                # every antecedent makes the persistent CSP epoch and binary
                # propagation grow quadratically without adding information.
                for CurrentContract in CurrentOptions:
                    RejectedSets.discard(ExactPairNoGood(
                        CurrentContract,
                        CompleteContract,
                    ))
            if (
                MaximumCompletedRows is not None
                and CompletedRowCount >= MaximumCompletedRows
            ):
                CompletedRowLimitReached = True
                break

    if FeasibleWitnessOptions is not None:
        CurrentOption, CompleteOption = FeasibleWitnessOptions
        PreferredReservations = getattr(
            Resources,
            "PreferredPhysicalComponentPortReservationsBySignal",
            None,
        )
        if PreferredReservations is None:
            PreferredReservations = {}
            Resources.PreferredPhysicalComponentPortReservationsBySignal = (
                PreferredReservations
            )
        PreferredGlobalContracts = getattr(
            Resources,
            "PreferredPhysicalComponentGlobalContractsBySignal",
            None,
        )
        if PreferredGlobalContracts is None:
            PreferredGlobalContracts = {}
            Resources.PreferredPhysicalComponentGlobalContractsBySignal = (
                PreferredGlobalContracts
            )
        if all(
            getattr(Option, "ReservationFingerprint", "")
            and hasattr(Option, "GlobalPath")
            for Option in (CurrentOption, CompleteOption)
        ):
            PreferredReservations.update({
                CurrentSignal: CurrentOption.ReservationFingerprint,
                CompleteSignal: CompleteOption.ReservationFingerprint,
            })
            PreferredGlobalContracts.update({
                CurrentSignal: BuildPhysicalPortGlobalContractFingerprint(
                    CurrentOption
                ),
                CompleteSignal: BuildPhysicalPortGlobalContractFingerprint(
                    CompleteOption
                ),
            })

    DeferredRowCount = max(
        0,
        len(OrderedCompleteOptions) - ProcessedCompleteContractCount,
    )
    Complete = bool(
        not IncompletePairs
        and FeasibleWitness is None
        and DeferredRowCount == 0
    )
    PromotedFabricNoGoods = (
        PromoteCoveredLocalContractNoGoods(
            Plan,
            (CurrentSignal, CompleteSignal),
            Resources,
        )
        if Complete
        else ()
    )
    return {
        "Complete": Complete,
        "Status": (
            "complete-unsatisfiable-portfolio"
            if Complete
            else "partial-complete-rows"
            if CompletedRowLimitReached
            else "feasible-witness"
            if FeasibleWitness is not None
            else "incomplete"
        ),
        "Reason": (
            "complete-cartesian-local-contract-coverage"
            if Complete
            else "completed-row-frontier-yield"
            if CompletedRowLimitReached
            else "exact-local-contract-pair-is-feasible"
            if FeasibleWitness is not None
            else "local-interface-factor-proof-incomplete"
        ),
        "Stage": "local-interface-factor-portfolio-certification",
        "PortSolverCacheKey": PortSolverCacheKey,
        "CurrentSignal": CurrentSignal,
        "CompleteSignal": CompleteSignal,
        "CurrentContractCount": len(CurrentOptions),
        "CompleteContractCount": len(CompleteOptions),
        "PairDomainCount": len(CurrentOptions) * len(CompleteOptions),
        "EvaluatedPairCount": EvaluatedPairCount,
        "CachedPairCount": CachedPairCount,
        "PreviouslyCoveredPairCount": PreviouslyCoveredPairCount,
        "CertifiedPairCount": CertifiedPairCount,
        "IncompletePairs": [list(Value) for Value in IncompletePairs],
        "IncompletePairCount": len(IncompletePairs),
        "DeferredIncompleteRows": list(DeferredIncompleteRows),
        "DeferredIncompleteRowCount": len(DeferredIncompleteRows),
        "ProcessedCompleteContractCount": ProcessedCompleteContractCount,
        "CompletedRowCount": CompletedRowCount,
        "DeferredRowCount": DeferredRowCount,
        "CompletedRowLimitReached": CompletedRowLimitReached,
        "FeasibleWitness": (
            list(FeasibleWitness) if FeasibleWitness is not None else None
        ),
        "FeasibleWitnessCount": int(FeasibleWitness is not None),
        "ProofDomainFingerprints": sorted(ProofDomainFingerprints),
        "DirectionalNoGoodCount": len(DirectionalNoGoods),
        "PromotedFabricNoGoodCount": len(PromotedFabricNoGoods),
        "LocalPairSupportCertificateCount": len(SupportCertificates),
        "LocalPairSupportCertificateFingerprints": [
            Value.CertificateFingerprint for Value in SupportCertificates
        ],
        "DirectionalNoGoodKeys": [
            [list(Key) for Key in sorted(NoGood)]
            for NoGood in sorted(
                DirectionalNoGoods,
                key=lambda Value: tuple(sorted(Value)),
            )
        ],
        "PromotedFabricNoGoodKeys": [
            [list(Key) for Key in sorted(NoGood)]
            for NoGood in PromotedFabricNoGoods
        ],
    }


def CertifyLocalInterfaceFactorPortfolio(
    Problem: ComponentRoutingProblem,
    Plan: PhysicalComponentAssemblyPlan,
    CurrentSignal: str,
    CompleteSignal: str,
    Resources: Any,
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    VariantPortfolioCache: dict[Any, Any] | None = None,
    NetVariantConstructionCache: dict[Any, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
) -> dict[str, object]:
    """Compile a certified local interface-factor portfolio after admission."""
    StartedAt = monotonic()
    Variants: dict[tuple[str, str], ComponentRoutingProblem] = {}
    if VariantPortfolioCache is None:
        VariantPortfolioCache = {}
    if NetVariantConstructionCache is None:
        NetVariantConstructionCache = {}
    if RouteClaimsConstructionCache is None:
        RouteClaimsConstructionCache = {}
    if NetVariantDiscoveryStateCache is None:
        NetVariantDiscoveryStateCache = {}
    # The full closed local domain is invariant across this Cartesian port
    # portfolio. Hash it once, then compose each pair from the exact normalized
    # replacement-port identities. Re-hashing every terminal candidate and
    # claim for every pair made identity construction dominate the solve.
    PortfolioBaseDomainFingerprint = (
        BuildGlobalRelaxedLocalProofDomainFingerprint(Problem)
    )
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
    PreparedDomainFingerprint = str(getattr(
        Preparation,
        "DomainFingerprint",
        "",
    ))
    LocalProofContextFingerprint = ""
    if (
        isinstance(
            Preparation,
            PreparedPhysicalComponentPortFactorDomain,
        )
        and isinstance(Preparation.Problem, ComponentRoutingProblem)
        and Preparation.Complete
        and Preparation.Feasible
    ):
        LocalProofContextFingerprint = (
            BuildPhysicalLocalPairProofContextFingerprint(
                Problem,
                Preparation,
            )
        )
    PortSolverCacheKey = (
        BuildPhysicalComponentPortSolverCacheKey(
            PreparedDomainFingerprint
        )
        if PreparedDomainFingerprint
        else ""
    )
    ContextCache = getattr(
        Resources,
        "PhysicalComponentLocalPortfolioContextCache",
        None,
    )
    if ContextCache is None:
        ContextCache = {}
        Resources.PhysicalComponentLocalPortfolioContextCache = ContextCache
    ContextKey = (
        PortSolverCacheKey,
        PortfolioBaseDomainFingerprint,
        str(CurrentSignal),
        str(CompleteSignal),
    )
    PersistentContextReused = ContextKey in ContextCache
    PersistentContext = ContextCache.setdefault(ContextKey, {
        "CompletePortfolioStaticContext": None,
        "CompletePortfoliosByContract": {},
        "OpposingRowContextsByContract": {},
        "CurrentAccessContractDomain": None,
    })
    PersistentContext.setdefault("CurrentAccessContractDomain", None)
    CompletePortfolioStaticContext = PersistentContext[
        "CompletePortfolioStaticContext"
    ]
    CompletePortfoliosByContract = PersistentContext[
        "CompletePortfoliosByContract"
    ]
    OpposingRowContextsByContract = PersistentContext[
        "OpposingRowContextsByContract"
    ]
    BulkPairResultsByCompleteContract = {}
    BulkAccessSignatureCount = 0
    BulkVariantScanCount = 0
    BulkSignaturePairCheckCount = 0
    CurrentAccessContractDomain = PersistentContext[
        "CurrentAccessContractDomain"
    ]
    CurrentAccessContractDomainReused = (
        CurrentAccessContractDomain is not None
    )
    PortfolioCompilationSeconds = 0.0
    RowContextConstructionSeconds = 0.0
    BulkAccessPreparationSeconds = 0.0
    BulkVariantScanSeconds = 0.0
    RejectedReservationSets = getattr(
        Resources,
        "RejectedPhysicalComponentPortReservationSets",
        None,
    )
    if RejectedReservationSets is None:
        RejectedReservationSets = set()
        Resources.RejectedPhysicalComponentPortReservationSets = (
            RejectedReservationSets
        )
    PortfolioOrigin = _Origin(Problem)

    def PortProofIdentity(Port: Any) -> tuple[object, ...]:
        return (
            Port.Signal,
            Port.Direction,
            tuple(
                _Normalize(Value, PortfolioOrigin)
                for Value in Port.OwnedTerminals
            ),
            tuple(Port.OwnedTerminalFingerprints),
            tuple(Port.OwnedCandidateFingerprints),
            _Normalize(Port.FabricAttachment, PortfolioOrigin),
            _Normalize(Port.Attachment, PortfolioOrigin),
            tuple(
                _Normalize(Value, PortfolioOrigin)
                for Value in Port.LocalPath
            ),
            Port.Capacity,
        )

    def BuildVariant(CurrentOption: Any, CompleteOption: Any):
        Key = (
            BuildPhysicalPortLocalContractFingerprint(CurrentOption),
            BuildPhysicalPortLocalContractFingerprint(CompleteOption),
        )
        Cached = Variants.get(Key)
        if Cached is not None:
            return Cached
        Ports = tuple(
            CurrentOption if Port.Signal == CurrentSignal
            else CompleteOption if Port.Signal == CompleteSignal
            else Port
            for Port in Plan.Ports
        )
        PlanFingerprint = _Fingerprint((
            "local-interface-factor-portfolio-plan-v1",
            Plan.PlanFingerprint,
            Key,
        ))
        VariantPlan = replace(
            Plan,
            Ports=Ports,
            PlanFingerprint=PlanFingerprint,
            PortAssignmentFingerprint=_Fingerprint(Key),
        )
        if Problem.Interface is None:
            raise ValueError("portfolio certification requires closed interface")
        VariantInterface = replace(
            Problem.Interface,
            PhysicalPortReservations=Ports,
            PhysicalAssemblyPlanFingerprint=PlanFingerprint,
        )
        VariantProblem = replace(
            Problem,
            ProblemFingerprint=_Fingerprint((
                "local-interface-factor-portfolio-problem-v1",
                Problem.ProblemFingerprint,
                PlanFingerprint,
            )),
            Interface=VariantInterface,
            PhysicalAssemblyPlan=VariantPlan,
            ReservedGlobalClaimsBySignal=(),
        )
        Variants[Key] = VariantProblem
        return VariantProblem

    def ProofDomain(CurrentOption: Any, CompleteOption: Any) -> str:
        return _Fingerprint((
            "global-relaxed-local-interface-portfolio-domain-v1",
            PortfolioBaseDomainFingerprint,
            PortProofIdentity(CurrentOption),
            PortProofIdentity(CompleteOption),
        ))

    try:
        CachedPortDomains = MaterializePreparedPhysicalPortOptionDomains(
            Preparation,
            Resources,
            (CurrentSignal, CompleteSignal),
        )
    except ValueError:
        CachedPortDomains = {}
    CompleteOptionsByContract = {}
    for Option in CachedPortDomains.get(CompleteSignal, ()):
        CompleteOptionsByContract.setdefault(
            BuildPhysicalPortLocalContractFingerprint(Option),
            Option,
        )
    MultiPortfolioResult = None
    if CompleteOptionsByContract:
        if CompletePortfolioStaticContext is None:
            CompletePortfolioStaticContext = (
                BuildCompleteComponentNetPortfolioStaticContext(
                    Problem,
                    CompleteSignal,
                )
            )
            PersistentContext["CompletePortfolioStaticContext"] = (
                CompletePortfolioStaticContext
            )
        Remaining = (
            None
            if DeadlineSeconds is None
            else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
        )
        PortfolioCompilationStartedAt = monotonic()
        MultiPortfolioResult = CompileCompleteComponentNetVariantPortfolios(
            Problem,
            CompleteSignal,
            CompleteOptionsByContract,
            DeadlineSeconds=Remaining,
            WorkCheck=WorkCheck,
            VariantPortfolioCache=VariantPortfolioCache,
            NetVariantConstructionCache=NetVariantConstructionCache,
            RouteClaimsConstructionCache=RouteClaimsConstructionCache,
            NetVariantDiscoveryStateCache=NetVariantDiscoveryStateCache,
            StaticContext=CompletePortfolioStaticContext,
        )
        PortfolioCompilationSeconds += (
            monotonic() - PortfolioCompilationStartedAt
        )
        for Contract, Portfolio in MultiPortfolioResult.Portfolios.items():
            if Portfolio.Complete:
                CompletePortfoliosByContract[Contract] = Portfolio

    def Evaluate(
        CurrentOption: Any,
        CompleteOption: Any,
        ProofDomainFingerprint: str,
    ) -> dict[str, object]:
        nonlocal CompletePortfolioStaticContext
        nonlocal BulkAccessSignatureCount
        nonlocal BulkVariantScanCount
        nonlocal BulkSignaturePairCheckCount
        nonlocal CurrentAccessContractDomain
        nonlocal PortfolioCompilationSeconds
        nonlocal RowContextConstructionSeconds
        nonlocal BulkAccessPreparationSeconds
        nonlocal BulkVariantScanSeconds
        Remaining = (
            None
            if DeadlineSeconds is None
            else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
        )
        CurrentContract = BuildPhysicalPortLocalContractFingerprint(
            CurrentOption
        )
        CompleteContract = BuildPhysicalPortLocalContractFingerprint(
            CompleteOption
        )
        VariantProblem = BuildVariant(CurrentOption, CompleteOption)
        Portfolio = CompletePortfoliosByContract.get(CompleteContract)
        if Portfolio is None:
            Portfolio = (
                MultiPortfolioResult.Portfolios.get(CompleteContract)
                if MultiPortfolioResult is not None
                else None
            )
        if Portfolio is None:
            raise ValueError(
                "shared complete-net portfolio omitted an exact contract"
            )
        RowContext = OpposingRowContextsByContract.get(CompleteContract)
        if RowContext is None and Portfolio.Complete:
            RowContextConstructionStartedAt = monotonic()
            RowContext = BuildCompleteOpposingNetAccessRowContext(
                VariantProblem,
                Portfolio.Variants,
                CurrentSignal=CurrentSignal,
                CompleteSignal=CompleteSignal,
            )
            RowContextConstructionSeconds += (
                monotonic() - RowContextConstructionStartedAt
            )
            OpposingRowContextsByContract[CompleteContract] = RowContext
        Remaining = (
            None
            if DeadlineSeconds is None
            else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
        )
        RowResults = BulkPairResultsByCompleteContract.get(CompleteContract)
        if RowResults is None:
            CurrentOptionsByContract = {
                BuildPhysicalPortLocalContractFingerprint(Option): Option
                for Option in CachedPortDomains.get(CurrentSignal, ())
            }
            if (
                not CurrentOptionsByContract
                or CurrentContract not in CurrentOptionsByContract
            ):
                raise ValueError(
                    "bulk opposing-net access is missing the exact current "
                    "option domain"
                )
            if CurrentAccessContractDomain is None:
                CurrentAccessContractDomain = (
                    BuildCompleteOpposingNetAccessContractDomain(
                        Problem,
                        CurrentSignal,
                        CurrentOptionsByContract,
                    )
                )
                PersistentContext["CurrentAccessContractDomain"] = (
                    CurrentAccessContractDomain
                )
            Bulk = EvaluateCompleteOpposingNetAccessContractRow(
                VariantProblem,
                CurrentSignal=CurrentSignal,
                CompleteSignal=CompleteSignal,
                CurrentPortsByContract=CurrentOptionsByContract,
                CompleteLocalContractFingerprint=CompleteContract,
                CompleteVariants=Portfolio.Variants,
                CompleteVariantDomainComplete=Portfolio.Complete,
                DeadlineSeconds=Remaining,
                DomainFingerprintsByCurrentContract={
                    Contract: ProofDomain(Option, CompleteOption)
                    for Contract, Option
                    in CurrentOptionsByContract.items()
                },
                ContractDomain=CurrentAccessContractDomain,
                RowContext=RowContext,
                WorkCheck=WorkCheck,
            )
            RowResults = Bulk.Results
            BulkAccessSignatureCount += Bulk.AccessSignatureCount
            BulkVariantScanCount += Bulk.VariantScanCount
            BulkSignaturePairCheckCount += Bulk.SignaturePairCheckCount
            BulkAccessPreparationSeconds += float(getattr(
                Bulk,
                "AccessPreparationSeconds",
                0.0,
            ))
            BulkVariantScanSeconds += float(getattr(
                Bulk,
                "VariantScanSeconds",
                0.0,
            ))
            if (
                Portfolio.Complete
                and all(Result.Complete for Result in RowResults.values())
            ):
                BulkPairResultsByCompleteContract[
                    CompleteContract
                ] = RowResults
        OracleResult = RowResults[CurrentContract]
        ExactIdentity = bool(
            OracleResult.CurrentSignal == CurrentSignal
            and OracleResult.CompleteSignal == CompleteSignal
            and OracleResult.CurrentLocalContractFingerprint
            == CurrentContract
            and OracleResult.CompleteLocalContractFingerprint
            == CompleteContract
        )
        CertifiedUnsatisfiable = bool(
            ExactIdentity
            and OracleResult.Complete
            and OracleResult.Status == "architectural-unsatisfiable"
            and OracleResult.Feasible is False
        )
        CertifiedFeasible = bool(
            ExactIdentity
            and OracleResult.Complete
            and OracleResult.Status == "feasible"
            and OracleResult.Feasible is True
        )
        return {
            "GlobalRelaxedLocalProofComplete": CertifiedUnsatisfiable,
            "GlobalRelaxedLocalCoreComplete": CertifiedUnsatisfiable,
            "GlobalRelaxedLocalProofStatus": (
                "architectural-unsatisfiable"
                if CertifiedUnsatisfiable
                else "feasible"
                if CertifiedFeasible
                else "incomplete"
            ),
            "GlobalRelaxedLocalProofFingerprint": (
                OracleResult.ProofFingerprint
                if CertifiedUnsatisfiable or CertifiedFeasible
                else ""
            ),
            "GlobalRelaxedLocalFeasibleWitnessComplete": (
                CertifiedFeasible
            ),
            "GlobalRelaxedLocalDomainFingerprint": ProofDomainFingerprint,
            "GlobalRelaxedLocalUnsatCoreSignals": (
                [CurrentSignal, CompleteSignal]
                if CertifiedUnsatisfiable
                else []
            ),
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
                if CertifiedUnsatisfiable
                else ""
            ),
            "GlobalRelaxedLocalCurrentSignal": (
                CurrentSignal if CertifiedUnsatisfiable else ""
            ),
            "GlobalRelaxedLocalCompleteSignal": (
                CompleteSignal if CertifiedUnsatisfiable else ""
            ),
            "PairAccessOracleStatus": OracleResult.Status,
            "PairAccessOracleComplete": OracleResult.Complete,
            "PairAccessOracleFeasible": OracleResult.Feasible,
            "PairAccessOracleDomainFingerprint": (
                OracleResult.DomainFingerprint
            ),
            "PairAccessOracleProofFingerprint": (
                OracleResult.ProofFingerprint
            ),
            "PairAccessOracleExpansionCount": OracleResult.ExpansionCount,
            "PairAccessOracleDetail": OracleResult.Detail,
            "CompleteNetPortfolioStatus": Portfolio.Status,
            "CompleteNetPortfolioComplete": Portfolio.Complete,
            "CompleteNetPortfolioVariantCount": len(Portfolio.Variants),
            "CompleteNetPortfolioDomainFingerprint": (
                Portfolio.DomainFingerprint
            ),
            "CompleteNetPortfolioExpansionCount": Portfolio.ExpansionCount,
            "CompleteNetPortfolioDiagnostics": dict(
                Portfolio.Diagnostics or {}
            ),
        }

    CertificationPasses = []
    while True:
        ProofFrontierBefore = (
            len(getattr(
                Resources,
                "PhysicalComponentLocalInterfaceFactorProofCache",
                {},
            )),
            len(CompletePortfoliosByContract),
            len(OpposingRowContextsByContract),
        )
        Diagnostics = CertifyDirectionalLocalContractPortfolio(
            Plan,
            CurrentSignal,
            CompleteSignal,
            Resources,
            BuildProofDomainFingerprint=ProofDomain,
            EvaluatePair=Evaluate,
            LocalProofContextFingerprint=(
                LocalProofContextFingerprint
            ),
            MaximumCompletedRows=None,
        )
        CertificationPasses.append({
            "Complete": bool(Diagnostics.get("Complete", False)),
            "Status": str(Diagnostics.get("Status", "")),
            "CertifiedPairCount": int(
                Diagnostics.get("CertifiedPairCount", 0)
            ),
            "IncompletePairCount": int(
                Diagnostics.get("IncompletePairCount", 0)
            ),
            "CompletedRowCount": int(
                Diagnostics.get("CompletedRowCount", 0)
            ),
        })
        ProofFrontierAfter = (
            len(getattr(
                Resources,
                "PhysicalComponentLocalInterfaceFactorProofCache",
                {},
            )),
            len(CompletePortfoliosByContract),
            len(OpposingRowContextsByContract),
        )
        if (
            Diagnostics.get("Complete", False)
            or Diagnostics.get("FeasibleWitnessCount", 0)
            or ProofFrontierAfter <= ProofFrontierBefore
        ):
            break
    return {
        **Diagnostics,
        "CertificationPassCount": len(CertificationPasses),
        "CertificationPasses": CertificationPasses,
        "PersistentPortfolioContextReused": PersistentContextReused,
        "CurrentAccessContractDomainReused": (
            CurrentAccessContractDomainReused
        ),
        "CurrentAccessContractDomainFingerprint": str(getattr(
            CurrentAccessContractDomain,
            "DomainIndexFingerprint",
            "",
        )),
        "CachedCompletePortfolioCount": len(
            CompletePortfoliosByContract
        ),
        "CachedOpposingRowContextCount": len(
            OpposingRowContextsByContract
        ),
        "BulkEvaluatedRowCount": len(BulkPairResultsByCompleteContract),
        "BulkAccessSignatureCount": BulkAccessSignatureCount,
        "BulkVariantScanCount": BulkVariantScanCount,
        "BulkSignaturePairCheckCount": BulkSignaturePairCheckCount,
        "PortfolioCompilationSeconds": PortfolioCompilationSeconds,
        "RowContextConstructionSeconds": RowContextConstructionSeconds,
        "BulkAccessPreparationSeconds": BulkAccessPreparationSeconds,
        "BulkVariantScanSeconds": BulkVariantScanSeconds,
        "MultiContractPortfolioComplete": bool(
            MultiPortfolioResult is not None
            and MultiPortfolioResult.Complete
        ),
        "MultiContractCanonicalStateCount": (
            0 if MultiPortfolioResult is None
            else MultiPortfolioResult.CanonicalStateCount
        ),
        "MultiContractNetVariantBuildCount": (
            0 if MultiPortfolioResult is None
            else MultiPortfolioResult.NetVariantBuildCount
        ),
        "MultiContractPortfolioDiagnostics": (
            {} if MultiPortfolioResult is None
            else dict(MultiPortfolioResult.Diagnostics or {})
        ),
    }


def BuildUniversalPromotedFabricPortAssignmentFailure(
    Plan: PhysicalComponentAssemblyPlan,
    Resources: Any,
    PortfolioDiagnostics: dict[str, object] | None,
) -> RoutingFailure | None:
    """Build direct UNSAT only when a promoted clause covers full domains."""
    if (
        not PortfolioDiagnostics
        or not PortfolioDiagnostics.get("Complete", False)
        or int(PortfolioDiagnostics.get("PromotedFabricNoGoodCount", 0)) <= 0
    ):
        return None
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
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
        return None
    PortSolverCacheKey = BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    if str(PortfolioDiagnostics.get("PortSolverCacheKey", "")) != (
        PortSolverCacheKey
    ):
        return None
    try:
        Domains = MaterializePreparedPhysicalPortOptionDomains(
            Preparation,
            Resources,
            tuple(Port.Signal for Port in Plan.Ports),
        )
    except ValueError:
        return None
    PromotedClauses = tuple(
        frozenset(
            (str(Signal), str(Fingerprint))
            for Signal, Fingerprint in Clause
        )
        for Clause in PortfolioDiagnostics.get(
            "PromotedFabricNoGoodKeys",
            (),
        )
        if Clause
    )
    RejectedSets = getattr(
        Resources,
        "RejectedPhysicalComponentPortReservationSets",
        set(),
    )
    PortsBySignal = {str(Port.Signal): Port for Port in Plan.Ports}
    Candidates = []
    for Clause in PromotedClauses:
        Signals = tuple(sorted({Signal for Signal, _ in Clause}))
        if (
            Clause not in RejectedSets
            or not Signals
            or any(Signal not in PortsBySignal for Signal in Signals)
            or any(
                Signal not in Domains or not Domains[Signal]
                for Signal in Signals
            )
        ):
            continue
        UniversalKeysBySignal = {}
        for Signal in Signals:
            KeySets = tuple(
                frozenset(((
                    Signal,
                    "local-factor-domain:"
                    + PortSolverCacheKey
                    + ":"
                    + str(Option.FabricDomainFingerprint),
                ),))
                for Option in Domains[Signal]
            )
            UniversalKeysBySignal[Signal] = frozenset.intersection(*KeySets)
        if all(
            Literal in UniversalKeysBySignal.get(
                Literal[0],
                frozenset(),
            )
            for Literal in Clause
        ):
            Candidates.append((Signals, Clause))
    if not Candidates:
        return None
    CoreSignals, CoreClause = min(
        Candidates,
        key=lambda Value: (
            len(Value[0]),
            Value[0],
            tuple(sorted(Value[1])),
        ),
    )
    return RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="LocalInterfaceFactorPortfolioUnsatisfiable",
        AffectedNets=CoreSignals,
        Detail=(
            "a complete local interface portfolio promoted a fabric clause "
            "that is universal over the active complete port domains"
        ),
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "CompleteAssignmentCutProof": True,
            "OwnershipSearchComplete": True,
            "PortAssignmentUnsatProofBasis": (
                "complete-local-interface-factor-domain-no-good"
            ),
            "PortAssignmentUnsatCoreSignals": list(CoreSignals),
            "PortAssignmentUnsatCoreClause": [
                list(Value) for Value in sorted(CoreClause)
            ],
            "PortSolverCacheKey": PortSolverCacheKey,
            "CompletePortDomainSizes": {
                Signal: len(Domains[Signal]) for Signal in CoreSignals
            },
            "LocalInterfaceFactorPortfolio": PortfolioDiagnostics,
            "PhysicalAssemblyPlanFingerprint": Plan.PlanFingerprint,
            "GlobalReplanEntered": False,
            "LocalTemplateReopened": False,
            "BroadFallbackAllowed": False,
            "ExecutableLegacyRepairCascade": False,
            "ImplicitForeignTransitDomainCount": 0,
        },
    )


def RecordPhysicalComponentDetailedRoutingNoGood(
    Plan: PhysicalComponentAssemblyPlan,
    GlobalChannelDesign: Any,
    Resources: Any,
) -> dict[str, object]:
    """Reject only the exact bound channels after detailed-route failure."""
    Assignment = getattr(GlobalChannelDesign, "RoutingAssignment", None)
    if Assignment is None:
        raise ValueError(
            "detailed routing no-good requires a bound global assignment"
        )
    CandidateSet = frozenset(
        (str(Signal), str(Candidate.CandidateId))
        for Signal, Candidate in Assignment.SelectedCandidates.items()
    )
    BoundCandidateSet = frozenset(
        (str(Channel.Signal), str(Channel.RouteCandidateId))
        for Channel in Plan.Channels
    )
    if not CandidateSet or CandidateSet != BoundCandidateSet:
        raise ValueError(
            "detailed routing no-good global assignment identity mismatch"
        )
    Resources.ForbiddenPhysicalComponentGlobalCandidateSets.add(
        CandidateSet
    )
    Resources.RejectedPhysicalComponentAssemblyPlanFingerprints.add(
        Plan.PlanFingerprint
    )
    return {
        "NoGoodScope": "exact-physical-global-candidate-set",
        "ForbiddenGlobalCandidateSet": [
            [Signal, CandidateId]
            for Signal, CandidateId in sorted(CandidateSet)
        ],
        "RejectedPhysicalAssemblyPlanFingerprint": (
            Plan.PlanFingerprint
        ),
        "PortAssignmentRejected": False,
    }


def RecordPhysicalComponentLocalCompilationNoGood(
    Solve: ComponentRoutingSolveResult,
    Plan: PhysicalComponentAssemblyPlan,
    GlobalChannelDesign: Any,
    Resources: Any,
    *,
    Problem: ComponentRoutingProblem | None = None,
) -> dict[str, object]:
    """Record the narrowest proof-qualified local compilation no-good."""
    Diagnostics = dict(Solve.Diagnostics or {})
    if (
        Solve.Status != "architectural-unsatisfiable"
        or not bool(Diagnostics.get("LocalUnsatCoreComplete", False))
    ):
        raise ValueError(
            "local component no-good requires a complete local proof"
        )
    CoreSignals = frozenset(map(str, (
        Diagnostics.get("LocalUnsatCoreSignals", ()) or ()
    )))
    if not CoreSignals:
        raise ValueError("local component proof has an empty core")
    GlobalRelaxedProofComplete = bool(
        Diagnostics.get("GlobalRelaxedLocalProofComplete", False)
    )
    RelaxedProofFingerprint = str(
        Diagnostics.get("GlobalRelaxedLocalProofFingerprint", "")
    )
    RelaxedDomainFingerprint = str(
        Diagnostics.get("GlobalRelaxedLocalDomainFingerprint", "")
    )
    RelaxedCoreKind = str(
        Diagnostics.get("GlobalRelaxedLocalUnsatCoreKind", "")
    )
    CertifiedCoreKinds = frozenset((
        "complete-opposing-net-access-pair",
        "complete-symbolic-capacity-pair",
        "complete-symbolic-capacity-core",
        "complete-symbolic-empty-capacity-domain",
        "tree-frontier-empty-owned-signal-domain",
        "tree-frontier-empty-signal",
    ))
    if GlobalRelaxedProofComplete:
        if not RelaxedProofFingerprint or not RelaxedDomainFingerprint:
            raise ValueError(
                "global-relaxed local proof is missing identity fingerprints"
            )
        if (
            Problem is None
            or Problem.PhysicalAssemblyPlan is None
            or Problem.PhysicalAssemblyPlan.PlanFingerprint
            != Plan.PlanFingerprint
        ):
            raise ValueError(
                "global-relaxed local proof problem identity mismatch"
            )
        if (
            BuildGlobalRelaxedLocalProofDomainFingerprint(Problem)
            != RelaxedDomainFingerprint
        ):
            raise ValueError(
                "global-relaxed local proof domain fingerprint mismatch"
            )
    RelaxedCoreSignals = frozenset(map(str, (
        Diagnostics.get("GlobalRelaxedLocalUnsatCoreSignals", ()) or ()
    )))
    RelaxedCurrentSignal = str(
        Diagnostics.get("GlobalRelaxedLocalCurrentSignal", "")
    )
    RelaxedCompleteSignal = str(
        Diagnostics.get("GlobalRelaxedLocalCompleteSignal", "")
    )
    if GlobalRelaxedProofComplete:
        PortsBySignal = {Port.Signal: Port for Port in Plan.Ports}
        if (
            Diagnostics.get("GlobalRelaxedLocalCoreComplete", False)
            and RelaxedCoreKind in CertifiedCoreKinds
            and RelaxedCoreSignals
            and RelaxedCoreSignals <= PortsBySignal.keys()
        ):
            PreparedPortDomain = getattr(
                Resources,
                "PreparedPhysicalComponentPortFactorDomain",
                None,
            )
            OwnedSignalFamilyProof = bool(
                RelaxedCoreKind
                == "tree-frontier-empty-owned-signal-domain"
                and len(RelaxedCoreSignals) == 1
                and Diagnostics.get(
                    "LocalUnsatCoreProjectionFingerprint",
                    "",
                )
                and PreparedPortDomain is not None
                and bool(getattr(PreparedPortDomain, "Complete", False))
                and bool(getattr(PreparedPortDomain, "Feasible", False))
                and getattr(PreparedPortDomain, "DomainFingerprint", "")
            )
            if OwnedSignalFamilyProof:
                PortSolverCacheKey = (
                    BuildPhysicalComponentPortSolverCacheKey(str(
                        PreparedPortDomain.DomainFingerprint
                    ))
                )
                CoreReservationKeys = frozenset(
                    (
                        Signal,
                        "local-signal-domain:" + PortSolverCacheKey,
                    )
                    for Signal in RelaxedCoreSignals
                )
            else:
                CoreReservationKeys = frozenset(
                    (
                        Signal,
                        BuildPhysicalPortLocalContractFingerprint(
                            PortsBySignal[Signal]
                        ),
                    )
                    for Signal in RelaxedCoreSignals
                )
            Resources.RejectedPhysicalComponentPortReservationSets.add(
                CoreReservationKeys
            )
            DirectionalLocalFactorNoGoods = ()
            if not OwnedSignalFamilyProof and RelaxedCoreSignals == frozenset((
                RelaxedCurrentSignal,
                RelaxedCompleteSignal,
            )):
                DirectionalLocalFactorNoGoods = (
                    BuildDirectionalLocalFactorNoGoods(
                        Plan,
                        RelaxedCurrentSignal,
                        RelaxedCompleteSignal,
                        Resources,
                    )
                )
                Resources.RejectedPhysicalComponentPortReservationSets.update(
                    DirectionalLocalFactorNoGoods
                )
            PromotedFabricNoGoods = (
                ()
                if OwnedSignalFamilyProof
                else PromoteCoveredLocalContractNoGoods(
                    Plan,
                    RelaxedCoreSignals,
                    Resources,
                )
            )
            Scope = (
                "global-relaxed-owned-signal-domain"
                if OwnedSignalFamilyProof
                else "global-relaxed-local-port-core"
            )
        else:
            Resources.RejectedPhysicalComponentPortAssignmentFingerprints.add(
                Plan.PortAssignmentFingerprint
            )
            CoreReservationKeys = frozenset()
            PromotedFabricNoGoods = ()
            Scope = "global-relaxed-port-assignment"
        Resources.RejectedPhysicalComponentAssemblyPlanFingerprints.add(
            Plan.PlanFingerprint
        )
        # The relaxed proof removed exterior corridors, so the completed
        # global contracts are known-good context rather than part of the
        # local failure.  Retain them as deterministic CSP preferences while
        # selecting a different local contract; this avoids needlessly
        # reopening unrelated global geometry without forbidding alternatives.
        Resources.PreferredPhysicalComponentGlobalContractsBySignal = {
            Port.Signal: BuildPhysicalPortGlobalContractFingerprint(Port)
            for Port in Plan.Ports
        }
        TraversalDiagnostics = (
            PreservePhysicalComponentAssemblyPlanDomainContinuation(
                Resources,
            )
        )
        Result = {
            "NoGoodScope": Scope,
            "NoGoodSignals": sorted(RelaxedCoreSignals),
            "GlobalRelaxedLocalUnsatCoreSignals": sorted(
                RelaxedCoreSignals
            ),
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": bool(
                Diagnostics.get(
                    "GlobalRelaxedLocalCoreComplete",
                    False,
                )
            ),
            "GlobalRelaxedLocalProofFingerprint": str(
                RelaxedProofFingerprint
            ),
            "GlobalRelaxedLocalDomainFingerprint": str(
                RelaxedDomainFingerprint
            ),
            "GlobalRelaxedLocalUnsatCoreKind": RelaxedCoreKind,
            "RejectedPhysicalAssemblyPlanFingerprint": (
                Plan.PlanFingerprint
            ),
            "PreferredRetainedGlobalContracts": dict(sorted(
                Resources
                .PreferredPhysicalComponentGlobalContractsBySignal.items()
            )),
            **TraversalDiagnostics,
        }
        if CoreReservationKeys:
            Result["NoGoodReservationKeys"] = [
                [Signal, Fingerprint]
                for Signal, Fingerprint in sorted(CoreReservationKeys)
            ]
            Result["PromotedFabricNoGoodKeys"] = [
                [list(Key) for Key in sorted(Promotion)]
                for Promotion in PromotedFabricNoGoods
            ]
            Result["DirectionalLocalFactorNoGoodKeys"] = [
                [list(Key) for Key in sorted(NoGood)]
                for NoGood in DirectionalLocalFactorNoGoods
            ]
        else:
            Result["RejectedPortAssignmentFingerprint"] = (
                Plan.PortAssignmentFingerprint
            )
        return Result
    Assignment = getattr(GlobalChannelDesign, "RoutingAssignment", None)
    if Assignment is None:
        raise ValueError(
            "local component no-good requires a bound global assignment"
        )
    CandidateSet = frozenset(
        (str(Signal), str(Candidate.CandidateId))
        for Signal, Candidate in Assignment.SelectedCandidates.items()
    )
    BoundCandidateSet = frozenset(
        (str(Channel.Signal), str(Channel.RouteCandidateId))
        for Channel in Plan.Channels
    )
    if not CandidateSet or CandidateSet != BoundCandidateSet:
        raise ValueError(
            "local component no-good global assignment identity mismatch"
        )
    SignalDiagnostics = Diagnostics.get("SignalDiagnostics", {})
    ProvenExteriorCoreSignals: set[str] = set()
    ExteriorCoreComplete = bool(
        isinstance(SignalDiagnostics, dict)
        and CoreSignals
    )
    for Signal in sorted(CoreSignals):
        PerSignalDiagnostics = SignalDiagnostics.get(Signal, {})
        if not (
            isinstance(PerSignalDiagnostics, dict)
            and PerSignalDiagnostics.get(
                "ReservedGlobalRouteUnsatCoreComplete",
                False,
            )
        ):
            ExteriorCoreComplete = False
            break
        ProvenExteriorCoreSignals.update(map(str, (
            PerSignalDiagnostics.get(
                "ReservedGlobalRouteUnsatCoreSignals",
                (),
            )
        )))
    CandidateNoGood = frozenset(
        (Signal, CandidateId)
        for Signal, CandidateId in CandidateSet
        if Signal in ProvenExteriorCoreSignals
    )
    if not (
        ExteriorCoreComplete
        and CandidateNoGood
        and len(CandidateNoGood) <= 2
        and len(CandidateNoGood) == len(ProvenExteriorCoreSignals)
    ):
        CandidateNoGood = CandidateSet
        ExteriorCoreComplete = False
    Resources.ForbiddenPhysicalComponentGlobalCandidateSets.add(
        CandidateNoGood
    )
    Resources.RejectedPhysicalComponentAssemblyPlanFingerprints.add(
        Plan.PlanFingerprint
    )
    TraversalDiagnostics = (
        PreservePhysicalComponentAssemblyPlanDomainContinuation(
            Resources,
        )
    )
    return {
        "NoGoodScope": "exact-physical-global-candidate-set",
        "NoGoodSignals": sorted(CoreSignals),
        "ForbiddenGlobalCandidateSet": [
            [Signal, CandidateId]
            for Signal, CandidateId in sorted(CandidateNoGood)
        ],
        "ExteriorCandidateCoreSignals": sorted(
            ProvenExteriorCoreSignals
        ),
        "ExteriorCandidateCoreComplete": ExteriorCoreComplete,
        "NoGoodConstraintArity": len(CandidateNoGood),
        "LocalUnsatCoreFingerprint": str(
            Diagnostics.get("LocalUnsatCoreFingerprint", "")
        ),
        "RejectedPhysicalAssemblyPlanFingerprint": Plan.PlanFingerprint,
        "GlobalRelaxedLocalProofComplete": False,
        "GlobalRelaxedLocalCoreComplete": False,
        **TraversalDiagnostics,
        "GlobalRelaxedLocalProofStatus": str(
            (
                "uncertified-core-kind"
                if GlobalRelaxedProofComplete
                else Diagnostics.get(
                    "GlobalRelaxedLocalProofStatus",
                    "not-run",
                )
            )
        ),
    }


def FinalizePhysicalComponentPortReservations(
    Ports: tuple[PhysicalComponentPortReservation, ...],
    Channels: tuple[PhysicalComponentChannelReservation, ...],
    ResourceGraph: Any,
    *,
    MinimumPlacementY: int,
    KeepoutClaims: RoutingResourceClaims,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[PhysicalComponentPortReservation, ...]:
    """Connect only the selected physical seams to their frozen guides."""
    ChannelsBySignal = {Channel.Signal: Channel for Channel in Channels}
    OrdinaryClaims = {
        Channel.Signal: Channel.Claims
        for Channel in Channels
        if Channel.Signal not in {Port.Signal for Port in Ports}
    }
    FinalizedClaims: dict[str, RoutingResourceClaims] = {}
    Result = []
    for PortIndex, Port in enumerate(sorted(
        Ports,
        key=lambda Value: Value.Signal,
    ), start=1):
        Channel = ChannelsBySignal.get(Port.Signal)
        if Channel is None:
            raise RoutingStageError(RoutingFailure(
                Reason=(
                    RoutingFailureReason
                    .ComponentChannelCapacityUnsatisfiable
                ),
                Stage="PhysicalComponentAssemblyPlanning",
                AffectedNets=(Port.Signal,),
                Detail="selected component port has no global channel",
            ))
        RoutingY = ResourceGraph.Technology.RoutingY(
            MinimumPlacementY,
            int(Channel.Layer),
        )
        Targets = frozenset(
            (int(X), RoutingY, int(Z))
            for X, Z in Channel.GuideCells
        )
        ExistingPath = tuple(Port.GlobalPath)
        if not ExistingPath or not Targets:
            raise RoutingStageError(RoutingFailure(
                Reason=(
                    RoutingFailureReason
                    .ComponentChannelCapacityUnsatisfiable
                ),
                Stage="PhysicalComponentAssemblyPlanning",
                AffectedNets=(Port.Signal,),
                Detail="selected component port has no physical guide stem",
            ))
        Start = ExistingPath[-1]
        MinimumX = min(Start[0], *(Value[0] for Value in Targets)) - 4
        MaximumX = max(Start[0], *(Value[0] for Value in Targets)) + 4
        MinimumZ = min(Start[2], *(Value[2] for Value in Targets)) - 4
        MaximumZ = max(Start[2], *(Value[2] for Value in Targets)) + 4
        Obstacles = {
            **OrdinaryClaims,
            **FinalizedClaims,
        }
        BlockedLocalNodes = frozenset((
            *Port.LocalPath[1:],
            *ExistingPath[:-1],
        )) - frozenset((Start,))
        Pending = deque((Start,))
        Previous: dict[Position3, Position3 | None] = {Start: None}
        Reached = Start if Start in Targets else None
        while Pending and Reached is None:
            Current = Pending.popleft()
            if WorkCheck is not None and len(Previous) % 256 == 0:
                WorkCheck({
                    "Stage": "physical-selected-port-connector",
                    "Signal": Port.Signal,
                    "ProcessedPortCount": PortIndex - 1,
                    "PortCount": len(Ports),
                    "VisitedNodeCount": len(Previous),
                })
            X, Y, Z = Current
            for Neighbor in (
                (X - 1, Y, Z),
                (X + 1, Y, Z),
                (X, Y, Z - 1),
                (X, Y, Z + 1),
            ):
                if (
                    Neighbor in Previous
                    or Neighbor in BlockedLocalNodes
                    or not (MinimumX <= Neighbor[0] <= MaximumX)
                    or not (MinimumZ <= Neighbor[2] <= MaximumZ)
                    or ResourceGraph.BuildPrimitive(Current, Neighbor)
                    is None
                ):
                    continue
                EdgeClaims = ResourceGraph.BuildRouteClaims((
                    Current,
                    Neighbor,
                ))
                if ComponentClaimsConflict(EdgeClaims, KeepoutClaims):
                    continue
                if any(
                    ComponentClaimsConflict(EdgeClaims, Claims)
                    for Claims in Obstacles.values()
                ):
                    continue
                Previous[Neighbor] = Current
                if Neighbor in Targets:
                    Reached = Neighbor
                    break
                Pending.append(Neighbor)
        if Reached is None:
            raise RoutingStageError(RoutingFailure(
                Reason=(
                    RoutingFailureReason
                    .ComponentChannelCapacityUnsatisfiable
                ),
                Stage="PhysicalComponentAssemblyPlanning",
                AffectedNets=(Port.Signal,),
                Detail=(
                    "selected component port cannot reach its reserved "
                    "global guide"
                ),
                Diagnostics={
                    "Signal": Port.Signal,
                    "VisitedNodeCount": len(Previous),
                    "ImplicitForeignTransitDomainCount": 0,
                },
            ))
        Connector = [Reached]
        while Previous[Connector[-1]] is not None:
            Parent = Previous[Connector[-1]]
            assert Parent is not None
            Connector.append(Parent)
        Connector.reverse()
        GlobalPath = (*ExistingPath[:-1], *Connector)
        GlobalClaims = ResourceGraph.BuildRouteClaims(
            frozenset(GlobalPath)
        )
        Claims = ResourceGraph.BuildRouteClaims(frozenset((
            *Port.LocalPath,
            *GlobalPath,
        )))
        if any(
            ComponentClaimsConflict(GlobalClaims, Value)
            for Value in Obstacles.values()
        ):
            raise RoutingStageError(RoutingFailure(
                Reason=(
                    RoutingFailureReason
                    .ComponentChannelCapacityUnsatisfiable
                ),
                Stage="PhysicalComponentAssemblyPlanning",
                AffectedNets=(Port.Signal,),
                Detail="selected component port connector violates capacity",
            ))
        Finalized = replace(
            Port,
            GlobalPath=GlobalPath,
            Claims=Claims,
            LocalClaims=ResourceGraph.BuildRouteClaims(
                frozenset(Port.LocalPath)
            ),
            GlobalClaims=GlobalClaims,
        )
        # The exterior portal/channel is globally owned. Interior terminal
        # access is compiled later and is not part of this global contract.
        FinalizedClaims[Port.Signal] = GlobalClaims
        Result.append(Finalized)
    return tuple(Result)


def FinalizePhysicalComponentChannelReservations(
    Channels: tuple[PhysicalComponentChannelReservation, ...],
    Ports: tuple[PhysicalComponentPortReservation, ...],
    ResourceGraph: Any,
    *,
    MinimumPlacementY: int,
    EnvelopeMinimum: Position3,
    EnvelopeMaximum: Position3,
    KeepoutClaims: RoutingResourceClaims | None = None,
    GlobalKeepoutNodes: frozenset[Position3] = frozenset(),
    PreservedChannelSignals: frozenset[str] = frozenset(),
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[PhysicalComponentChannelReservation, ...]:
    """Freeze connected global claims from each seam into its guide.

    Guide cells inside the component envelope are not globally owned.  A
    component channel is complete only when its selected global portal path
    physically intersects the remaining exterior guide on the same routing
    layer.  The returned claims cover that connected union; local compilation
    therefore sees exact frozen global ownership instead of a disconnected
    guide label or a portal stub alone.
    """
    PortsBySignal = {Port.Signal: Port for Port in Ports}
    if len(PortsBySignal) != len(Ports):
        raise ValueError("physical assembly contains duplicate signal ports")

    # Validate the already-planned whole-design channels before attributing
    # any failure to a component seam.  A disconnected export must not mask
    # an unrelated global capacity conflict.
    OrdinaryChannels = tuple(
        Channel
        for Channel in Channels
        if Channel.Signal not in PortsBySignal
    )
    OrdinaryConflictPairs = tuple(sorted(
        (First.Signal, Second.Signal)
        for FirstIndex, First in enumerate(OrdinaryChannels)
        for Second in OrdinaryChannels[FirstIndex + 1:]
        if not (
            First.Signal in PreservedChannelSignals
            and Second.Signal in PreservedChannelSignals
        )
        and ComponentClaimsConflict(First.Claims, Second.Claims)
    ))
    if OrdinaryConflictPairs:
        AffectedSignals = tuple(sorted({
            Signal
            for Pair in OrdinaryConflictPairs
            for Signal in Pair
        }))
        raise RoutingStageError(RoutingFailure(
            Reason=(
                RoutingFailureReason
                .ComponentChannelCapacityUnsatisfiable
            ),
            Stage="PhysicalComponentAssemblyPlanning",
            AffectedNets=AffectedSignals,
            Detail=(
                "whole-design global channels exceed shared resource "
                "capacity before component seam finalization"
            ),
            Diagnostics={
                "ConflictPairs": [
                    list(Value) for Value in OrdinaryConflictPairs
                ],
                "ChannelReservationFingerprints": {
                    Value.Signal: Value.ReservationFingerprint
                    for Value in OrdinaryChannels
                    if Value.Signal in AffectedSignals
                },
                "PortReservationFingerprints": {},
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))

    def InsideEnvelope(Position: Position3) -> bool:
        if GlobalKeepoutNodes:
            return Position in GlobalKeepoutNodes
        return bool(
            EnvelopeMinimum[0] <= Position[0] <= EnvelopeMaximum[0]
            and EnvelopeMinimum[2] <= Position[2] <= EnvelopeMaximum[2]
        )

    def IsConnected(Nodes: frozenset[Position3]) -> bool:
        if not Nodes:
            return False
        Start = min(Nodes)
        Pending = deque((Start,))
        Reached = {Start}
        while Pending:
            Current = Pending.popleft()
            for Neighbor in (
                ResourceGraph.Technology.NeighborPositions(Current)
            ):
                if (
                    Neighbor not in Nodes
                    or Neighbor in Reached
                    or ResourceGraph.BuildPrimitive(Current, Neighbor)
                    is None
                ):
                    continue
                Reached.add(Neighbor)
                Pending.append(Neighbor)
        return len(Reached) == len(Nodes)

    Result = []
    for ChannelIndex, Channel in enumerate(Channels, start=1):
        if WorkCheck is not None:
            WorkCheck({
                "Stage": "physical-channel-finalization",
                "Signal": Channel.Signal,
                "ProcessedChannelCount": ChannelIndex - 1,
                "ChannelCount": len(Channels),
            })
        RoutingY = ResourceGraph.Technology.RoutingY(
            MinimumPlacementY,
            int(Channel.Layer),
        )
        ExteriorGuideNodes = frozenset(
            (int(X), RoutingY, int(Z))
            for X, Z in Channel.GuideCells
            if not InsideEnvelope((int(X), RoutingY, int(Z)))
        )
        Port = PortsBySignal.get(Channel.Signal)
        PortalNodes = frozenset(
            Port.GlobalPath if Port is not None else ()
        )

        def RaiseCapacityFailure(Detail: str) -> None:
            raise RoutingStageError(RoutingFailure(
                Reason=(
                    RoutingFailureReason
                    .ComponentChannelCapacityUnsatisfiable
                ),
                Stage="PhysicalComponentAssemblyPlanning",
                AffectedNets=(Channel.Signal,),
                Detail=Detail,
                Diagnostics={
                    "Signal": Channel.Signal,
                    "ChannelReservationFingerprint": (
                        Channel.ReservationFingerprint
                    ),
                    "PortReservationFingerprint": (
                        Port.ReservationFingerprint
                        if Port is not None
                        else ""
                    ),
                    "GuideDomainFingerprint": _Fingerprint((
                        Channel.Layer,
                        tuple(sorted(Channel.GuideCells)),
                    )),
                    "GuideCellCount": len(Channel.GuideCells),
                    "ExteriorGuideNodeCount": len(
                        ExteriorGuideNodes
                    ),
                    "PortalNodeCount": len(PortalNodes),
                    "ImplicitForeignTransitDomainCount": 0,
                },
            ))

        # Coarse guide cells remain useful planning metadata, but an exact
        # candidate reservation is authoritative.  Never reconstruct or
        # mutate its claims from the guide during component handoff.
        ExactReservedNodes = frozenset(Channel.ReservedPathNodes)
        if ExactReservedNodes:
            if (
                not Channel.RouteCandidateId
                or not Channel.RouteCandidateFingerprint
            ):
                raise RoutingStageError(RoutingFailure(
                    Reason=(
                        RoutingFailureReason
                        .ComponentAssemblyIdentityMismatch
                    ),
                    Stage="PhysicalComponentAssemblyPlanning",
                    AffectedNets=(Channel.Signal,),
                    Detail=(
                        "exact physical channel is missing its route "
                        "candidate identity"
                    ),
                ))
            ExactClaims = ResourceGraph.BuildRouteClaims(
                ExactReservedNodes
            )
            if ExactClaims != Channel.Claims:
                raise RoutingStageError(RoutingFailure(
                    Reason=(
                        RoutingFailureReason
                        .ComponentAssemblyIdentityMismatch
                    ),
                    Stage="PhysicalComponentAssemblyPlanning",
                    AffectedNets=(Channel.Signal,),
                    Detail=(
                        "exact physical channel claims do not match its "
                        "reserved path nodes"
                    ),
                    Diagnostics={
                        "Signal": Channel.Signal,
                        "RouteCandidateId": Channel.RouteCandidateId,
                        "RouteCandidateFingerprint": (
                            Channel.RouteCandidateFingerprint
                        ),
                        "ReservedPathNodeCount": len(
                            ExactReservedNodes
                        ),
                        "ImplicitForeignTransitDomainCount": 0,
                    },
                ))
            ExactResourceIds = tuple(map(str, sorted(
                ExactClaims.ResourceIds,
                key=str,
            )))
            if ExactResourceIds != Channel.ResourceIds:
                raise RoutingStageError(RoutingFailure(
                    Reason=(
                        RoutingFailureReason
                        .ComponentAssemblyIdentityMismatch
                    ),
                    Stage="PhysicalComponentAssemblyPlanning",
                    AffectedNets=(Channel.Signal,),
                    Detail=(
                        "exact physical channel resource identities do not "
                        "match its reserved path claims"
                    ),
                    Diagnostics={
                        "Signal": Channel.Signal,
                        "RouteCandidateId": Channel.RouteCandidateId,
                        "RouteCandidateFingerprint": (
                            Channel.RouteCandidateFingerprint
                        ),
                        "ImplicitForeignTransitDomainCount": 0,
                    },
                ))
            if not IsConnected(ExactReservedNodes):
                RaiseCapacityFailure(
                    "exact physical channel reservation is disconnected: "
                    f"{Channel.Signal}"
                )
            if Port is not None and not (
                PortalNodes <= ExactReservedNodes
            ):
                RaiseCapacityFailure(
                    "exact physical channel does not contain its reserved "
                    f"component portal: {Channel.Signal}"
                )
            UndeclaredKeepoutNodes = (
                ExactReservedNodes - PortalNodes
            ) & GlobalKeepoutNodes
            if UndeclaredKeepoutNodes:
                RaiseCapacityFailure(
                    "exact physical channel enters the component keepout "
                    f"outside its declared passage: {Channel.Signal}"
                )
            Result.append(Channel)
            continue

        if Channel.Signal in PreservedChannelSignals:
            Result.append(Channel)
            continue

        # Whole-design guide preparation already proved unchanged ordinary
        # channels connected and materialized their exact claims.  Preserve
        # those immutable reservations verbatim; only component ports and
        # guides changed by keepout detouring require physical re-finalization.
        if (
            Port is None
            and Channel.Claims.WireCells == ExteriorGuideNodes
        ):
            Result.append(Channel)
            continue

        if Port is not None and not (
            PortalNodes & ExteriorGuideNodes
        ):
            RaiseCapacityFailure(
                "component port global path does not intersect its "
                f"reserved exterior guide: {Channel.Signal}"
            )
        ChannelNodes = ExteriorGuideNodes | PortalNodes
        if not IsConnected(ChannelNodes):
            RaiseCapacityFailure(
                "physical global channel is disconnected after component "
                f"keepout enforcement: {Channel.Signal}"
            )
        # A component port owns its concrete seam/portal path now. The
        # exterior guide is a capacity-aware corridor contract for the later
        # detailed router, not a pre-routed wire tree; claiming every guide
        # cell here would manufacture conflicts between overlapping routing
        # preferences before detailed assignment exists.
        ClaimedNodes = PortalNodes if Port is not None else ChannelNodes
        Claims = ResourceGraph.BuildRouteClaims(ClaimedNodes)
        ResourceIds = tuple(map(str, sorted(
            Claims.ResourceIds,
            key=str,
        )))
        # Keep a port's public guide domain intact.  Envelope filtering above
        # is an ownership rule for its concrete claims, not permission to
        # rewrite the capacity-aware corridor presented to detailed routing.
        # Ordinary channels still publish their finalized exterior detour.
        FinalGuideCells = (
            tuple(Channel.GuideCells)
            if Port is not None
            else tuple(sorted({
                (Position[0], Position[2])
                for Position in ExteriorGuideNodes
            }))
        )
        Result.append(replace(
            Channel,
            GuideCells=FinalGuideCells,
            ResourceIds=ResourceIds,
            Claims=Claims,
            ReservationFingerprint=_Fingerprint((
                "connected-physical-component-channel-v1",
                Channel.Signal,
                Channel.Layer,
                FinalGuideCells,
                tuple(sorted(PortalNodes)),
                ResourceIds,
                Channel.Capacity,
                Channel.FeedthroughComponentIds,
            )),
        ))
    Finalized = tuple(Result)
    ConflictPairs = tuple(sorted(
        (
            First.Signal,
            Second.Signal,
        )
        for FirstIndex, First in enumerate(Finalized)
        for Second in Finalized[FirstIndex + 1:]
        if not (
            First.Signal in PreservedChannelSignals
            and Second.Signal in PreservedChannelSignals
        )
        and ComponentClaimsConflict(First.Claims, Second.Claims)
    ))
    if ConflictPairs:
        AffectedSignals = tuple(sorted({
            Signal
            for Pair in ConflictPairs
            for Signal in Pair
        }))
        raise RoutingStageError(RoutingFailure(
            Reason=(
                RoutingFailureReason
                .ComponentChannelCapacityUnsatisfiable
            ),
            Stage="PhysicalComponentAssemblyPlanning",
            AffectedNets=AffectedSignals,
            Detail=(
                "finalized physical channels exceed shared resource "
                "capacity"
            ),
            Diagnostics={
                "ConflictPairs": [list(Value) for Value in ConflictPairs],
                "ChannelReservationFingerprints": {
                    Value.Signal: Value.ReservationFingerprint
                    for Value in Finalized
                    if Value.Signal in AffectedSignals
                },
                "PortReservationFingerprints": {
                    Value.Signal: Value.ReservationFingerprint
                    for Value in Ports
                    if Value.Signal in AffectedSignals
                },
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    return Finalized


def _Origin(Problem: ComponentRoutingProblem) -> Position3:
    Values = tuple(Problem.Fabric.Nodes)
    if not Values:
        return (0, 0, 0)
    return (
        min(Value[0] for Value in Values),
        min(Value[1] for Value in Values),
        min(Value[2] for Value in Values),
    )


def _Move(Position: Position3, Delta: Position3) -> Position3:
    return tuple(
        Position[Index] + Delta[Index]
        for Index in range(3)
    )


def _Normalize(Position: Position3, Origin: Position3) -> Position3:
    return tuple(
        Position[Index] - Origin[Index]
        for Index in range(3)
    )


def _NormalizedClaimsIdentity(
    Claims: RoutingResourceClaims,
    Origin: Position3,
) -> tuple[tuple[Position3, ...], ...]:
    return (
        tuple(sorted(
            _Normalize(Value, Origin)
            for Value in Claims.WireCells
        )),
        tuple(sorted(
            _Normalize(Value, Origin)
            for Value in Claims.SupportCells
        )),
        tuple(sorted(
            _Normalize(Value, Origin)
            for Value in Claims.RequiredAirCells
        )),
        tuple(sorted(
            _Normalize(Value, Origin)
            for Value in Claims.ElectricalCells
        )),
    )


def _SignalStructuralIdentities(
    Problem: ComponentRoutingProblem,
) -> tuple[tuple[str, str], ...]:
    """Map names to translation-normalized physical net roles."""
    Origin = _Origin(Problem)
    Interface = Problem.Interface
    PortsBySignal = {
        Port.Signal: Port
        for Port in (
            Interface.PhysicalPortReservations
            if Interface is not None
            else ()
        )
    }
    FeedthroughsBySignal = {
        Value.Signal: Value
        for Value in (
            Interface.Feedthroughs
            if Interface is not None
            else ()
        )
    }
    Signals = frozenset((
        *Problem.ComponentSignals,
        *FeedthroughsBySignal,
    ))
    Result = []
    for Signal in Signals:
        Port = PortsBySignal.get(Signal)
        Feedthrough = FeedthroughsBySignal.get(Signal)
        Identity = (
            tuple(sorted(
                (
                    Domain.TerminalRole,
                    _Normalize(Domain.Terminal, Origin),
                    tuple(sorted(
                        (
                            _Normalize(
                                Candidate.Attachment,
                                Origin,
                            ),
                            tuple(
                                _Normalize(Value, Origin)
                                for Value in Candidate.Path
                            ),
                            _NormalizedClaimsIdentity(
                                Candidate.Claims,
                                Origin,
                            ),
                            Candidate.Layer,
                        )
                        for Candidate in Domain.Candidates
                    )),
                )
                for Domain in Problem.OwnedTerminalDomains
                if Domain.Signal == Signal
            )),
            (
                (
                    Port.Direction,
                    tuple(
                        _Normalize(Value, Origin)
                        for Value in Port.OwnedTerminals
                    ),
                    _Normalize(Port.FabricAttachment, Origin),
                    _Normalize(Port.Attachment, Origin),
                    tuple(
                        _Normalize(Value, Origin)
                        for Value in Port.LocalPath
                    ),
                    tuple(
                        _Normalize(Value, Origin)
                        for Value in Port.GlobalPath
                    ),
                    _NormalizedClaimsIdentity(
                        Port.Claims,
                        Origin,
                    ),
                    Port.Capacity,
                )
                if Port is not None
                else None
            ),
            (
                (
                    tuple(sorted(
                        (
                            _Normalize(Entry, Origin),
                            _Normalize(Exit, Origin),
                        )
                        for Entry, Exit
                        in Feedthrough.EndpointPairs
                    )),
                    Feedthrough.Capacity,
                )
                if Feedthrough is not None
                else None
            ),
        )
        Result.append((Signal, _Fingerprint(Identity)))
    return tuple(sorted(Result))


def _BuildSignalTranslation(
    CachedIdentities: tuple[tuple[str, str], ...],
    CurrentIdentities: tuple[tuple[str, str], ...],
) -> dict[str, str] | None:
    CachedByIdentity: dict[str, list[str]] = {}
    CurrentByIdentity: dict[str, list[str]] = {}
    for Signal, Identity in CachedIdentities:
        CachedByIdentity.setdefault(Identity, []).append(Signal)
    for Signal, Identity in CurrentIdentities:
        CurrentByIdentity.setdefault(Identity, []).append(Signal)
    if (
        CachedByIdentity.keys() != CurrentByIdentity.keys()
        or any(
            len(CachedByIdentity[Identity])
            != len(CurrentByIdentity[Identity])
            for Identity in CachedByIdentity
        )
    ):
        return None
    return {
        CachedSignal: CurrentSignal
        for Identity in sorted(CachedByIdentity)
        for CachedSignal, CurrentSignal in zip(
            sorted(CachedByIdentity[Identity]),
            sorted(CurrentByIdentity[Identity]),
        )
    }


def _MoveClaims(
    Claims: RoutingResourceClaims,
    Delta: Position3,
) -> RoutingResourceClaims:
    return RoutingResourceClaims(
        WireCells=frozenset(
            _Move(Value, Delta) for Value in Claims.WireCells
        ),
        SupportCells=frozenset(
            _Move(Value, Delta) for Value in Claims.SupportCells
        ),
        RequiredAirCells=frozenset(
            _Move(Value, Delta)
            for Value in Claims.RequiredAirCells
        ),
        ElectricalCells=frozenset(
            _Move(Value, Delta)
            for Value in Claims.ElectricalCells
        ),
    )


def BuildCompletedComponentTemplateCacheFingerprint(
    Problem: ComponentRoutingProblem,
) -> str:
    """Identify topology/port/technology-equivalent component compiles."""
    if Problem.Interface is None:
        raise ValueError("closed interface required for template caching")
    Origin = _Origin(Problem)

    def NormalizeValues(
        Values: Any,
    ) -> tuple[Position3, ...]:
        return tuple(sorted(
            _Normalize(Value, Origin) for Value in Values
        ))

    DomainIdentity = tuple(sorted(
        (
            Domain.TerminalRole,
            _Normalize(Domain.Terminal, Origin),
            tuple(sorted(
                (
                    _Normalize(Candidate.Attachment, Origin),
                    NormalizeValues(Candidate.Path),
                    NormalizeValues(Candidate.Claims.WireCells),
                    NormalizeValues(Candidate.Claims.SupportCells),
                    NormalizeValues(
                        Candidate.Claims.RequiredAirCells
                    ),
                    NormalizeValues(
                        Candidate.Claims.ElectricalCells
                    ),
                    Candidate.Layer,
                )
                for Candidate in Domain.Candidates
            )),
        )
        for Domain in Problem.OwnedTerminalDomains
    ))
    ClaimIdentity = tuple(sorted(
        (
            "component"
            if Claim.Signal in Problem.ComponentSignals
            else "foreign",
            NormalizeValues(Claim.Claims.WireCells),
            NormalizeValues(Claim.Claims.SupportCells),
            NormalizeValues(Claim.Claims.RequiredAirCells),
            NormalizeValues(Claim.Claims.ElectricalCells),
        )
        for Claim in (
            *Problem.LocalClaims,
            *Problem.ImmutableClaims,
        )
    ))
    SignalIdentityByName = dict(_SignalStructuralIdentities(Problem))

    def AssemblySignalIdentity(Signal: str) -> str:
        return SignalIdentityByName.get(
            Signal,
            "foreign-global-channel",
        )

    LogicalInterfaceIdentity = (
        Problem.Interface.Complete,
        tuple(sorted(
            (
                AssemblySignalIdentity(Port.Signal),
                Port.Direction,
                tuple(sorted(
                    _Normalize(Value, Origin)
                    for Value in Port.OwnedTerminals
                )),
                Port.ExternalTerminalCount,
                Port.Capacity,
            )
            for Port in Problem.Interface.Ports
        )),
        tuple(sorted(
            AssemblySignalIdentity(Signal)
            for Signal
            in Problem.Interface.DeclaredFeedthroughSignals
        )),
    )

    Plan = Problem.PhysicalAssemblyPlan
    PhysicalContractIdentity = (
        (
            tuple(sorted(
                (
                    AssemblySignalIdentity(Port.Signal),
                    Port.Direction,
                    tuple(
                        _Normalize(Value, Origin)
                        for Value in Port.OwnedTerminals
                    ),
                    _Normalize(Port.FabricAttachment, Origin),
                    _Normalize(Port.Attachment, Origin),
                    tuple(
                        _Normalize(Value, Origin)
                        for Value in Port.LocalPath
                    ),
                    tuple(
                        _Normalize(Value, Origin)
                        for Value in Port.GlobalPath
                    ),
                    _NormalizedClaimsIdentity(
                        Port.Claims,
                        Origin,
                    ),
                    Port.Capacity,
                )
                for Port in Plan.Ports
            )),
            tuple(sorted(
                (
                    AssemblySignalIdentity(Channel.Signal),
                    Channel.Layer,
                    tuple(sorted(
                        (
                            X - Origin[0],
                            Z - Origin[2],
                        )
                        for X, Z in Channel.GuideCells
                    )),
                    _NormalizedClaimsIdentity(
                        Channel.Claims,
                        Origin,
                    ),
                    Channel.Capacity,
                    len(Channel.FeedthroughComponentIds),
                )
                for Channel in Plan.Channels
            )),
            tuple(sorted(
                (
                    AssemblySignalIdentity(Feedthrough.Signal),
                    tuple(sorted(
                        (
                            _Normalize(Entry, Origin),
                            _Normalize(Exit, Origin),
                        )
                        for Entry, Exit
                        in Feedthrough.EndpointPairs
                    )),
                    Feedthrough.Capacity,
                )
                for Feedthrough in Plan.Feedthroughs
            )),
        )
        if Plan is not None
        else ()
    )
    Technology = getattr(Problem.ResourceGraph, "Technology", None)
    return _Fingerprint((
        "completed-component-template-v3",
        LogicalInterfaceIdentity,
        Problem.Fabric.FabricFingerprint,
        DomainIdentity,
        ClaimIdentity,
        tuple(sorted(SignalIdentityByName.values())),
        PhysicalContractIdentity,
        Problem.MaximumPowerDistance,
        repr(Technology),
    ))


def _MoveNet(
    Value: RoutedComponentNet,
    Delta: Position3,
    Signal: str | None = None,
) -> RoutedComponentNet:
    Claims = _MoveClaims(Value.Claims, Delta)
    Nodes = frozenset(_Move(Position, Delta) for Position in Value.Nodes)
    Edges = frozenset(
        tuple(sorted((_Move(First, Delta), _Move(Second, Delta))))
        for First, Second in Value.Edges
    )
    Repeaters = tuple(
        (_Move(Position, Delta), Facing)
        for Position, Facing in Value.Repeaters
    )
    ExportedPorts = tuple(
        _Move(Position, Delta) for Position in Value.ExportedPorts
    )
    CoveredTerminals = tuple(
        _Move(Position, Delta) for Position in Value.CoveredTerminals
    )
    return replace(
        Value,
        Signal=Signal or Value.Signal,
        Root=_Move(Value.Root, Delta),
        Nodes=Nodes,
        Edges=Edges,
        WireCells=Claims.WireCells - frozenset(
            Position for Position, _Facing in Repeaters
        ),
        SupportCells=Claims.SupportCells,
        Repeaters=Repeaters,
        Claims=Claims,
        CoveredTerminals=CoveredTerminals,
        ExportedPorts=ExportedPorts,
        NetFingerprint=_Fingerprint((
            tuple(sorted(Nodes)),
            tuple(sorted(Edges)),
            Repeaters,
            ExportedPorts,
        )),
    )


def _InstantiateCachedTemplate(
    Problem: ComponentRoutingProblem,
    CachedOrigin: Position3,
    Cached: RoutedComponentTemplate,
    CachedSignalIdentities: tuple[tuple[str, str], ...],
    CacheFingerprint: str,
) -> RoutedComponentTemplate | None:
    if (
        Cached.ForeignEscapeReservations
        or Cached.ExternalContinuationReservations
    ):
        return None
    TargetOrigin = _Origin(Problem)
    Delta = tuple(
        TargetOrigin[Index] - CachedOrigin[Index]
        for Index in range(3)
    )
    SignalTranslation = _BuildSignalTranslation(
        CachedSignalIdentities,
        _SignalStructuralIdentities(Problem),
    )
    if SignalTranslation is None:
        return None
    Nets = tuple(
        _MoveNet(
            Value,
            Delta,
            SignalTranslation.get(Value.Signal),
        )
        for Value in Cached.Nets
    )
    ExpectedTerminalsBySignal = {
        Signal: tuple(sorted(
            Domain.Terminal
            for Domain in Problem.OwnedTerminalDomains
            if Domain.Signal == Signal
        ))
        for Signal in Problem.ComponentSignals
    }
    if any(
        tuple(sorted(Net.CoveredTerminals))
        != ExpectedTerminalsBySignal.get(Net.Signal, ())
        for Net in Nets
    ):
        return None
    ForeignTransits = tuple(
        _MoveNet(
            Value,
            Delta,
            SignalTranslation.get(Value.Signal),
        )
        for Value in Cached.ForeignTransitReservations
    )
    Claims = RoutingResourceClaims(
        WireCells=frozenset().union(*(
            Value.Claims.WireCells
            for Value in (*Nets, *ForeignTransits)
        )),
        SupportCells=frozenset().union(*(
            Value.Claims.SupportCells
            for Value in (*Nets, *ForeignTransits)
        )),
        RequiredAirCells=frozenset().union(*(
            Value.Claims.RequiredAirCells
            for Value in (*Nets, *ForeignTransits)
        )),
        ElectricalCells=frozenset().union(*(
            Value.Claims.ElectricalCells
            for Value in (*Nets, *ForeignTransits)
        )),
    )
    if Problem.ResourceGraph is not None and any(
        Problem.ResourceGraph.BuildRouteClaims(Value.Nodes)
        != Value.Claims
        for Value in (*Nets, *ForeignTransits)
    ):
        return None
    ExportedPorts = tuple(sorted(
        (Net.Signal, Position)
        for Net in Nets
        for Position in Net.ExportedPorts
    ))
    Diagnostics = {
        **Cached.Diagnostics,
        "CompletedTemplateCacheHit": True,
        "CompletedTemplateCacheFingerprint": CacheFingerprint,
        "CompletedTemplateTranslationDelta": list(Delta),
    }
    RoutedFingerprint = _Fingerprint((
        Problem.ProblemFingerprint,
        tuple(Value.NetFingerprint for Value in Nets),
        tuple(Value.NetFingerprint for Value in ForeignTransits),
        ExportedPorts,
    ))
    return replace(
        Cached,
        ProblemFingerprint=Problem.ProblemFingerprint,
        PlacementFingerprint=Problem.PlacementFingerprint,
        LocalTemplateFingerprint=Problem.LocalTemplateFingerprint,
        FabricFingerprint=Problem.Fabric.FabricFingerprint,
        RoutedTemplateFingerprint=RoutedFingerprint,
        Nets=Nets,
        ExportedPorts=ExportedPorts,
        Claims=Claims,
        ProofFingerprint=_Fingerprint((
            RoutedFingerprint,
            "completed-template-cache",
        )),
        ExpansionCount=0,
        Diagnostics=Diagnostics,
        ForeignTransitReservations=ForeignTransits,
        InterfaceFingerprint=Problem.Interface.InterfaceFingerprint,
    )


@dataclass(frozen=True)
class ComponentAssemblyResult:
    """Frozen component claims and their validated global handoff."""

    Placed: Any
    Template: RoutedComponentTemplate
    HandoffDiagnostics: dict[str, object]
    PhysicalAssemblyPlan: PhysicalComponentAssemblyPlan
def CompileClosedComponent(
    Problem: ComponentRoutingProblem,
    *,
    AssemblyPlan: PhysicalComponentAssemblyPlan | None = None,
    DeadlineSeconds: float | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ForbiddenAssignmentFingerprints: frozenset[str] = frozenset(),
    ForbiddenExportPortsBySignal: dict[
        str, tuple[Position3, ...]
    ] | None = None,
    ForbiddenForeignCandidateFingerprintsBySignal: dict[
        str, frozenset[str]
    ] | None = None,
    ForbiddenForeignAssignmentPairs: tuple[
        frozenset[tuple[str, Position3, str]], ...
    ] = (),
    VariantPortfolioCache: dict[Any, Any] | None = None,
    NetVariantConstructionCache: dict[Any, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
    DiscoveryVariantLimit: int | None = 8,
    DiscoveryVariantLimitsBySignal: dict[
        str, int | None
    ] | None = None,
    RequiredForeignTransitSignals: frozenset[str] = frozenset(),
) -> ComponentRoutingSolveResult:
    """Compile one closed local component without invoking global retries."""
    if Problem.Interface is None:
        raise ValueError(
            "production component compilation requires a closed interface"
        )
    Declared = Problem.Interface.DeclaredFeedthroughSignals
    Actual = frozenset(
        Value.Signal for Value in Problem.ForeignTransitDomains
    )
    if Actual - Declared:
        raise ValueError(
            "component problem contains implicit foreign transit domains"
        )
    EffectiveAssemblyPlan = (
        AssemblyPlan or Problem.PhysicalAssemblyPlan
    )
    if EffectiveAssemblyPlan is not None:
        if not EffectiveAssemblyPlan.Complete:
            raise ValueError(
                "component compilation requires a complete physical "
                "assembly plan"
            )
        if (
            Problem.PhysicalAssemblyPlan is None
            or Problem.PhysicalAssemblyPlan.PlanFingerprint
            != EffectiveAssemblyPlan.PlanFingerprint
            or Problem.Interface.PhysicalAssemblyPlanFingerprint
            != EffectiveAssemblyPlan.PlanFingerprint
            or Problem.Interface.InterfaceFingerprint
            != EffectiveAssemblyPlan.InterfaceFingerprint
        ):
            raise ValueError(
                "component problem and physical assembly identities differ"
            )
        _ValidatePhysicalProblemContract(
            Problem,
            EffectiveAssemblyPlan,
        )
        if (
            ForbiddenAssignmentFingerprints
            or (ForbiddenExportPortsBySignal or {})
            or (
                ForbiddenForeignCandidateFingerprintsBySignal
                or {}
            )
            or ForbiddenForeignAssignmentPairs
            or RequiredForeignTransitSignals
        ):
            raise ValueError(
                "physical component compilation cannot reopen its "
                "immutable assembly plan"
            )
    CacheEligible = bool(
        not ForbiddenAssignmentFingerprints
        and not (ForbiddenExportPortsBySignal or {})
        and not (
            ForbiddenForeignCandidateFingerprintsBySignal or {}
        )
        and not ForbiddenForeignAssignmentPairs
        and not RequiredForeignTransitSignals
    )
    CacheFingerprint = (
        BuildCompletedComponentTemplateCacheFingerprint(Problem)
        if CacheEligible
        else ""
    )
    CacheKey = CacheFingerprint
    Cached = (
        _CompletedComponentTemplateCache.get(CacheKey)
        if CacheEligible
        else None
    )
    if Cached is not None:
        (
            CachedOrigin,
            CachedTemplate,
            CachedSignalIdentities,
        ) = Cached
        Instantiated = _InstantiateCachedTemplate(
            Problem,
            CachedOrigin,
            CachedTemplate,
            CachedSignalIdentities,
            CacheFingerprint,
        )
        if Instantiated is not None:
            _ValidatePhysicalTemplate(Problem, Instantiated)
            return ComponentRoutingSolveResult(
                Status="feasible",
                Template=Instantiated,
                ProofFingerprint=Instantiated.ProofFingerprint,
                ExpansionCount=0,
                Diagnostics=Instantiated.Diagnostics,
            )
    Result = SolveComponentRoutingProblem(
        Problem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        ForbiddenAssignmentFingerprints=(
            ForbiddenAssignmentFingerprints
        ),
        ForbiddenExportPortsBySignal=ForbiddenExportPortsBySignal,
        ForbiddenForeignCandidateFingerprintsBySignal=(
            ForbiddenForeignCandidateFingerprintsBySignal
        ),
        ForbiddenForeignAssignmentPairs=(
            ForbiddenForeignAssignmentPairs
        ),
        VariantPortfolioCache=VariantPortfolioCache,
        NetVariantConstructionCache=NetVariantConstructionCache,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        NetVariantDiscoveryStateCache=NetVariantDiscoveryStateCache,
        DiscoveryVariantLimit=DiscoveryVariantLimit,
        DiscoveryVariantLimitsBySignal=(
            DiscoveryVariantLimitsBySignal
        ),
        RequiredForeignTransitSignals=RequiredForeignTransitSignals,
    )
    if Result.Feasible and Result.Template is not None:
        _ValidatePhysicalTemplate(Problem, Result.Template)
    if (
        CacheEligible
        and Result.Feasible
        and Result.Template is not None
    ):
        TemplateDiagnostics = {
            **Result.Template.Diagnostics,
            "CompletedTemplateCacheHit": False,
            "CompletedTemplateCacheFingerprint": CacheFingerprint,
            "CompletedTemplateTranslationDelta": [0, 0, 0],
        }
        Template = replace(
            Result.Template,
            Diagnostics=TemplateDiagnostics,
        )
        Result = replace(
            Result,
            Template=Template,
            Diagnostics=TemplateDiagnostics,
        )
        _CompletedComponentTemplateCache[CacheKey] = (
            _Origin(Problem),
            Template,
            _SignalStructuralIdentities(Problem),
        )
    return Result


def AssembleClosedComponentForGlobalRouting(
    Placed: Any,
    Template: RoutedComponentTemplate,
    *,
    PhysicalAssemblyPlan: PhysicalComponentAssemblyPlan,
    PlacementFingerprint: str,
    LocalTemplateFingerprint: str,
) -> ComponentAssemblyResult:
    """Freeze local claims against the immutable port-first assembly plan."""
    if not PhysicalAssemblyPlan.Complete:
        raise ValueError("physical component assembly plan is incomplete")
    if (
        PhysicalAssemblyPlan.PlacementFingerprint
        != PlacementFingerprint
        or Template.InterfaceFingerprint
        != PhysicalAssemblyPlan.InterfaceFingerprint
    ):
        raise ValueError(
            "physical component assembly handoff identity mismatch"
        )
    Diagnostics = dict(
        getattr(Placed, "LocalRouteDiagnostics", {}) or {}
    )
    Diagnostics["__PhysicalComponentAssemblyPlan__"] = (
        PhysicalAssemblyPlan.ToDictionary()
    )
    StagedPlaced = replace(
        Placed,
        LocalRouteDiagnostics=Diagnostics,
    )
    Materialized = MaterializeRoutedComponentTemplate(
        StagedPlaced,
        Template,
    )
    try:
        Handoff = ValidateRoutedComponentHandoff(
            Materialized,
            Template,
            PlacementFingerprint=PlacementFingerprint,
            LocalTemplateFingerprint=LocalTemplateFingerprint,
        )
    except ValueError as Error:
        raise RoutingStageError(RoutingFailure(
            Reason=(
                RoutingFailureReason
                .ComponentAssemblyIdentityMismatch
            ),
            Stage="ComponentAssemblyIdentityValidation",
            Detail=str(Error),
            Diagnostics={
                "PlacementFingerprint": PlacementFingerprint,
                "LocalTemplateFingerprint": (
                    LocalTemplateFingerprint
                ),
                "PhysicalAssemblyPlanFingerprint": (
                    PhysicalAssemblyPlan.PlanFingerprint
                ),
                "InterfaceFingerprint": (
                    PhysicalAssemblyPlan.InterfaceFingerprint
                ),
                "RoutedTemplateFingerprint": (
                    Template.RoutedTemplateFingerprint
                ),
                "FabricFingerprint": Template.FabricFingerprint,
                "ImplicitForeignTransitDomainCount": 0,
            },
        )) from Error
    return ComponentAssemblyResult(
        Placed=Materialized,
        Template=Template,
        HandoffDiagnostics=Handoff,
        PhysicalAssemblyPlan=PhysicalAssemblyPlan,
    )

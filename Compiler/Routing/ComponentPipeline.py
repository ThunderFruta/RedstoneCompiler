"""Closed-component compilation and authoritative global assembly stage."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from hashlib import sha256
from time import monotonic
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from .ComponentRouter import (
    BuildCompleteComponentNetPortfolioStaticContext,
    BuildCompleteOpposingNetAccessContractDomain,
    BuildCompleteOpposingNetAccessRowContext,
    CompileCompleteComponentNetVariantPortfolio,
    CompileCompleteComponentNetVariantPortfolios,
    ComponentClaimsConflict,
    EvaluateCompleteOpposingNetAccessContractRow,
    MaterializeRoutedComponentTemplate,
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
    # Eligibility identity fixes physical factors, not the later candidate
    # request controls (window/diversity, expansion limits, and penalties).
    # Keep every selected global contract in the clause even when the sibling
    # aperture support proof is signal-local.
    GlobalContractKeys = tuple(
        (
            Port.Signal,
            BuildPhysicalPortGlobalContractFingerprint(Port),
        )
        for Port in GlobalPorts
    )
    RequestSignal = next(iter(RequestSignals))
    SolverDomainKeys = (
        ((
            RequestSignal,
            "local-signal-domain:" + PortSolverCacheKey,
        ),)
        if UseScopedProjection
        else ()
    )
    return frozenset((
        *GlobalContractKeys,
        *SolverDomainKeys,
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
        Covered = OwnedTerminalsBySignal.get(Signal, frozenset())
        Port = PortsBySignal.get(Signal)
        RootIsCovered = Profile.Root in Covered
        OutsideTargets = tuple(
            Target for Target in Profile.Targets if Target not in Covered
        )
        if Port is None:
            if RootIsCovered and not OutsideTargets:
                Result.pop(Signal, None)
            continue
        if not Port.GlobalPath or Port.GlobalPath[0] != Port.Attachment:
            raise ValueError(
                "physical port has no immutable external access path"
            )
        if RootIsCovered:
            if not OutsideTargets:
                Result.pop(Signal, None)
                continue
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
        Result[Signal] = replace(
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
    if not Plan.Channels:
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
) -> Any:
    """Remove component-local seeds while global channels are selected."""
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

    AssemblyDependencySignals = tuple(sorted({
        *(str(Signal) for Signal in Failure.AffectedNets),
        *DependencySignals(UnderlyingConflictGraph.get(
            "ConflictSignals",
            (),
        )),
        *DependencySignals(UnderlyingConflictGraph.get(
            "CongestionCutSignals",
            (),
        )),
    }))
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


def RecordPhysicalComponentGlobalPlanNoGood(
    Failure: RoutingFailure,
    Plan: PhysicalComponentAssemblyPlan,
    Resources: Any,
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
    # A completed request/aperture starvation proof already carries its
    # dependency closure: the victim request and every exact sibling aperture
    # needed to block its finite candidate domain.  Pin that projection to the
    # current prepared port-solver domain.  Other global cuts still require
    # the complete assembly aperture tuple because their request-factor
    # support is not certified independently.
    Ports = (
        tuple(
            Port for Port in Plan.Ports
            if Port.Signal in IndependentEmptyDomainSignals
        )
        if IndependentEmptyDomainSignals
        else DependencyPorts
        if RequestApertureProofComplete
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
        *RequestGlobalDeterminantKeys,
        *ReservationKeys,
        *((PortSolverScopeKey,) if PortSolverScopeKey is not None else ()),
    ))
    ReservationKeyBySignal = {
        Signal: (Signal, Fingerprint)
        for Signal, Fingerprint in ReservationKeys
    }
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    PairwiseEdges = tuple(
        tuple(map(str, Edge))
        for Edge in (
            ConflictGraph.get("PairwiseIncompatibleEdges", ())
            if isinstance(ConflictGraph, dict)
            else ()
        )
        if isinstance(Edge, (list, tuple)) and len(Edge) == 2
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
    MinimumDeltaTraversalFocusSignal = ""
    MinimumDeltaRetainedContracts: dict[str, str] = {}
    if (
        not RecommendedContracts
        and Scope in {
            "exact-assembly-port-aperture-set",
            "request-aperture-factor-port-set",
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
        if RequestAperturePivotSignals:
            MinimumDeltaPivotSignal = RequestAperturePivotSignals[0]
            MinimumDeltaTraversalFocusSignal = MinimumDeltaPivotSignal
        elif HubSignals:
            MinimumDeltaPivotSignal = max(
                HubSignals,
                key=lambda Value: (Value[1], Value[0]),
            )[0]
            MinimumDeltaTraversalFocusSignal = MinimumDeltaPivotSignal
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
        RecommendedContracts = dict(MinimumDeltaRetainedContracts)
    Resources.PreferredPhysicalComponentGlobalContractsBySignal = (
        RecommendedContracts
    )
    TraversalDiagnostics = AdvancePhysicalComponentBoundaryTraversal(
        Resources,
        DependencySignals,
        FocusSignal=MinimumDeltaTraversalFocusSignal,
    )
    return {
        "NoGoodScope": Scope,
        "NoGoodSignals": sorted(DependencySignals),
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
            if RequiresExactAssemblyChoice
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
        "MinimumDeltaRetainedGlobalContracts": dict(sorted(
            MinimumDeltaRetainedContracts.items()
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
        "RejectedAssemblyChoiceFingerprint": (
            RejectedAssemblyChoiceFingerprint
        ),
        "AssemblyPlanFeedthroughIndependentProofComplete": (
            FeedthroughIndependenceProved
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
    return Result


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
    LocalCoreClause = frozenset()
    if (
        Diagnostics.get("LocalUnsatCoreComplete", False)
        and CoreSignals
        and all(Signal in PortsBySignal for Signal in CoreSignals)
    ):
        LocalCoreClause = frozenset(
            (
                Signal,
                BuildPhysicalPortSeamContractFingerprint(
                    PortsBySignal[Signal]
                ),
            )
            for Signal in CoreSignals
        )
        Resources.RejectedPhysicalComponentPortReservationSets.add(
            LocalCoreClause
        )
    PromotedApertureClauses: set[
        frozenset[tuple[str, str]]
    ] = set()
    PromotedApertureSignals: set[str] = set()
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
        for Signal in CoreSignals:
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

        RejectedLocalSeamClauses = tuple(
            Clause
            for Clause in (
                Resources.RejectedPhysicalComponentPortReservationSets
            )
            if Clause and all(
                str(Fingerprint).startswith(
                    "local-seam-contract-v1:"
                )
                for _Signal, Fingerprint in Clause
            )
        )

        def SeamTupleIsRejected(
            Keys: frozenset[tuple[str, str]],
        ) -> bool:
            return any(
                Clause <= Keys for Clause in RejectedLocalSeamClauses
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
        if len(CoreSignals) == 2 and all(
            Signal in SeamFingerprintsBySignal
            and Signal in BoundaryBySignal
            for Signal in CoreSignals
        ):
            FirstSignal, SecondSignal = CoreSignals
            if all(
                SeamTupleIsRejected(frozenset((
                    (FirstSignal, FirstSeam),
                    (SecondSignal, SecondSeam),
                )))
                for FirstSeam in SeamFingerprintsBySignal[FirstSignal]
                for SecondSeam in SeamFingerprintsBySignal[SecondSignal]
            ):
                ApertureClause = frozenset((
                    (
                        FirstSignal,
                        BoundaryBySignal[FirstSignal]
                        .ApertureContractFingerprint,
                    ),
                    (
                        SecondSignal,
                        BoundaryBySignal[SecondSignal]
                        .ApertureContractFingerprint,
                    ),
                ))
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
    return CoreSignals


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
        TraversalDiagnostics = AdvancePhysicalComponentBoundaryTraversal(
            Resources,
            RelaxedCoreSignals,
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
    Resources.ForbiddenPhysicalComponentGlobalCandidateSets.add(
        CandidateSet
    )
    Resources.RejectedPhysicalComponentAssemblyPlanFingerprints.add(
        Plan.PlanFingerprint
    )
    TraversalDiagnostics = AdvancePhysicalComponentBoundaryTraversal(
        Resources,
        CoreSignals,
    )
    return {
        "NoGoodScope": "exact-physical-global-candidate-set",
        "NoGoodSignals": sorted(CoreSignals),
        "ForbiddenGlobalCandidateSet": [
            [Signal, CandidateId]
            for Signal, CandidateId in sorted(CandidateSet)
        ],
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

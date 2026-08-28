"""Physical component seam validation, fingerprints, and normalized identities."""

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
from ..Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from ..Contracts.Component import (
    ComponentRoutingProblem,
    ComponentRoutingSolveResult,
    PhysicalComponentAssemblyPlan,
    PhysicalComponentChannelReservation,
    PhysicalComponentPortReservation,
    PhysicalComponentSelectedLocalPortSupport,
    RoutedComponentNet,
    RoutedComponentTemplate,
)
from ..Contracts.Core import Position3
from ..Contracts.PhysicalInterface import (
    PhysicalComponentLocalFactorProjection,
    PhysicalComponentLocalFactorProjectionComparison,
    PhysicalComponentLocalFactorUnsatCertificate,
    PhysicalLocalPortPairProofRecord,
    PhysicalLocalPortPairSupportCertificate,
    PhysicalComponentSymbolicHigherOrderCertificate,
    PhysicalComponentSymbolicPortPairCertificate,
    PhysicalPortCorridorDomain,
    PhysicalPortCorridorFactor,
    PreparedPhysicalComponentAssembly,
    PreparedPhysicalComponentPortFactorDomain,
)
from ..Interfaces import BoundaryRelations
from ..Interfaces.BoundaryRelations import (
    BuildPhysicalPortGlobalContractFingerprint,
    ProjectPhysicalComponentSignalGlobalProfile,
)
from ..Interfaces.PhysicalClaims import ComponentClaimsConflict
from ..ResourceGraph import RoutingResourceClaims
from ..Reliability import BuildStableFingerprint
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

from .Core import BuildCompleteComponentNetPortfolioStaticContext
from .SymbolicState import (
    _BuildPreparedComponentSymbolicNetStateContextFingerprint,
    BuildComponentSymbolicNetStateCacheKey,
    PrepareComponentSymbolicNetStateContext,
)
from .SymbolicWorkers import (
    CompilePreparedComponentPhysicalFactorStateBatch,
    CompilePreparedComponentSymbolicNetStates,
)
from .Portfolios import (
    BuildCompleteOpposingNetAccessContractDomain,
    BuildCompleteOpposingNetAccessRowContext,
    CompileCompleteComponentNetVariantPortfolio,
    CompileCompleteComponentNetVariantPortfolios,
    EvaluateCompleteOpposingNetAccessContractRow,
)
from .Solver import (
    MaterializeRoutedComponentTemplate,
    SolveComponentRoutingProblem,
    ValidateRoutedComponentHandoff,
)
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

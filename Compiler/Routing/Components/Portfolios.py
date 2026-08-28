"""Complete net portfolios and exact physical-port realizability proofs."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from hashlib import sha256
from itertools import combinations, islice, product
from math import prod as ProductIntegers
from time import monotonic
from typing import Any, Callable, Iterable, Mapping


from ..Contracts.Component import (
    ClosedComponentInterface,
    ComponentFeedthroughContract,
    ComponentForeignTransitDomain,
    ComponentInterfacePort,
    ComponentRoutingFabric,
    ComponentRoutingProblem,
    ComponentRoutingSolveResult,
    ComponentTerminalAccessCandidate,
    ComponentTerminalAccessDomain,
    RoutedComponentNet,
    RoutedComponentTemplate,
)
from ..Contracts.Core import Position3
from ..Interfaces.PhysicalClaims import (
    _MergeClaims,
    ComponentClaimsCompatibleForOwners,
    ComponentClaimsConflict,
)
from ..ResourceGraph import (
    FindSelfClaimConflicts,
    LocalRouteClaim,
    PinAccessPortal,
    RoutingEdge,
    RoutingReservation,
    RoutingResourceId,
    RoutingResourceKind,
    RoutingResourceClaims,
)
from ..Technology import DefaultRedstoneRoutingTechnology

try:
    from ...RustRouting import (
        BuildFabricSubtreesBatchWithTelemetry as _BuildFabricSubtreesBatchWithTelemetry,
    )
    from ...RustRouting import BuildRouteClaimsBatch as _BuildRouteClaimsBatch
    from ...RustRouting import (
        BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
    )
    from ...RustRouting import GetRoutingThreadCount as _GetRoutingThreadCount
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import (
            BuildFabricSubtreesBatchWithTelemetry as _BuildFabricSubtreesBatchWithTelemetry,
        )
        from RedstoneCompiler.RustRouting import (
            BuildRouteClaimsBatch as _BuildRouteClaimsBatch,
        )
        from RedstoneCompiler.RustRouting import (
            BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
        )
        from RedstoneCompiler.RustRouting import (
            GetRoutingThreadCount as _GetRoutingThreadCount,
        )
    except ImportError:
        _BuildFabricSubtreesBatchWithTelemetry = None
        _BuildRouteClaimsBatch = None
        _BuildRouteClaimsBatchWithTelemetry = None
        _GetRoutingThreadCount = None

from .Core import (
    BuildCompleteComponentNetPortfolioStaticContext,
    CompleteComponentNetPortfolioStaticContext,
    _ComponentNetPortfolioStructuralFingerprint,
    _ComponentOrigin,
    _NormalizeClaims,
    _NormalizePosition,
    _NormalizedEdge,
    _PhysicalPortLocalContractFingerprint,
    _StableFingerprint,
    _TranslateAndValidateNetPortfolio,
)
from .Fabric import BuildComponentFabricAdjacency, _BuildAdjacency, _UniqueFabricSubtree
from .NetPlanning import _BuildNetVariant
from .LegacySolver import _SolveComponentRoutingProblemLegacy
@dataclass(frozen=True)
class ExactComponentPortRealizabilityResult:
    """Powered single-port proof for one immutable physical contract."""

    Realizable: bool
    ContractFingerprint: str
    NetFingerprint: str = ""
    Detail: str = ""
    Diagnostics: dict[str, object] | None = None

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Realizable": self.Realizable,
            "ContractFingerprint": self.ContractFingerprint,
            "NetFingerprint": self.NetFingerprint,
            "Detail": self.Detail,
            "Diagnostics": dict(self.Diagnostics or {}),
        }


@dataclass(frozen=True)
class CompleteOpposingNetAccessPairResult:
    """Typed feasibility certificate for one exact local-contract pair."""

    Status: str
    Complete: bool
    Feasible: bool | None
    DomainFingerprint: str
    ProofFingerprint: str
    CurrentSignal: str
    CompleteSignal: str
    CurrentLocalContractFingerprint: str
    CompleteLocalContractFingerprint: str
    SupportingCompleteVariantFingerprints: tuple[str, ...] = ()
    ExpansionCount: int = 0
    Detail: str = ""
    Diagnostics: dict[str, object] | None = None

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Status": self.Status,
            "Complete": self.Complete,
            "Feasible": self.Feasible,
            "DomainFingerprint": self.DomainFingerprint,
            "ProofFingerprint": self.ProofFingerprint,
            "CurrentSignal": self.CurrentSignal,
            "CompleteSignal": self.CompleteSignal,
            "CurrentLocalContractFingerprint": (
                self.CurrentLocalContractFingerprint
            ),
            "CompleteLocalContractFingerprint": (
                self.CompleteLocalContractFingerprint
            ),
            "SupportingCompleteVariantFingerprints": list(
                self.SupportingCompleteVariantFingerprints
            ),
            "ExpansionCount": self.ExpansionCount,
            "Detail": self.Detail,
            "Diagnostics": dict(self.Diagnostics or {}),
        }


@dataclass(frozen=True)
class CompleteOpposingNetAccessRowContext:
    """Invariant fabric cuts for one complete opposing-net portfolio."""

    FabricFingerprint: str
    CompleteVariantFingerprints: tuple[str, ...]
    ComponentByNodeByVariant: tuple[
        tuple[str, tuple[tuple[Position3, int], ...]], ...
    ]
    ComponentMapByVariant: dict[str, dict[Position3, int]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    CurrentSignal: str = ""
    CompleteSignal: str = ""
    CurrentAccessDomainFingerprint: str = ""
    CompatibleComponentByCandidateFingerprintByVariant: dict[
        str, dict[str, int]
    ] = field(default_factory=dict, compare=False, repr=False)


def _OpposingRowCurrentAccessDomainFingerprint(
    Problem: ComponentRoutingProblem,
    Signal: str,
) -> str:
    Signal = str(Signal)
    if not Signal:
        return ""
    Origin = _ComponentOrigin(Problem)
    ForeignClaims = tuple(
        Claim.Claims
        for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims)
        if Claim.Signal not in Problem.ComponentSignals
    )
    return _StableFingerprint((
        "opposing-current-access-domain-v1",
        Signal,
        tuple(sorted(
            (
                Domain.TerminalRole,
                Domain.TerminalFingerprint,
                bool(getattr(Domain, "Complete", True)),
                tuple(sorted(
                    (
                        Candidate.CandidateFingerprint,
                        _NormalizePosition(Candidate.Attachment, Origin),
                        _NormalizeClaims(Candidate.Claims, Origin),
                    )
                    for Candidate in Domain.Candidates
                    if not any(
                        ComponentClaimsConflict(Candidate.Claims, Claims)
                        for Claims in ForeignClaims
                    )
                )),
            )
            for Domain in Problem.OwnedTerminalDomains
            if Domain.Signal == Signal
        )),
    ))


def BuildCompleteOpposingNetAccessRowContext(
    Problem: ComponentRoutingProblem,
    CompleteVariants: tuple[RoutedComponentNet, ...],
    *,
    CurrentSignal: str = "",
    CompleteSignal: str = "",
) -> CompleteOpposingNetAccessRowContext:
    """Precompute each opposing variant's surviving fabric components."""
    CurrentSignal = str(CurrentSignal)
    CompleteSignal = str(CompleteSignal)
    if bool(CurrentSignal) != bool(CompleteSignal) or (
        CurrentSignal and CurrentSignal == CompleteSignal
    ):
        raise ValueError("row support index requires two distinct signals")
    FabricAdjacency = BuildComponentFabricAdjacency(Problem.Fabric)
    SingletonFabricClaims = {
        Node: (
            Problem.ResourceGraph.BuildRouteClaims(frozenset((Node,)))
            if Problem.ResourceGraph is not None
            else RoutingResourceClaims(
                WireCells=frozenset((Node,)),
                SupportCells=frozenset(((Node[0], Node[1] - 1, Node[2]),)),
                ElectricalCells=frozenset(
                    DefaultRedstoneRoutingTechnology
                    .BuildElectricalExclusions({Node})
                ),
            )
        )
        for Node in Problem.Fabric.Nodes
    }
    CurrentAccessDomainFingerprint = (
        _OpposingRowCurrentAccessDomainFingerprint(
            Problem,
            CurrentSignal,
        )
    )
    ComponentMaps = []
    for Variant in sorted(
        CompleteVariants,
        key=lambda Value: Value.NetFingerprint,
    ):
        BlockedNodes = frozenset(
            Node
            for Node, Claims in SingletonFabricClaims.items()
            if ComponentClaimsConflict(Claims, Variant.Claims)
        )
        AllowedNodes = frozenset(Problem.Fabric.Nodes) - BlockedNodes
        ComponentByNode: dict[Position3, int] = {}
        for Start in sorted(AllowedNodes):
            if Start in ComponentByNode:
                continue
            ComponentIndex = len(set(ComponentByNode.values()))
            Pending = [Start]
            ComponentByNode[Start] = ComponentIndex
            while Pending:
                Node = Pending.pop()
                for Neighbor in FabricAdjacency.get(Node, ()):
                    if (
                        Neighbor in AllowedNodes
                        and Neighbor not in ComponentByNode
                    ):
                        ComponentByNode[Neighbor] = ComponentIndex
                        Pending.append(Neighbor)
        ComponentMaps.append((
            Variant.NetFingerprint,
            tuple(sorted(ComponentByNode.items())),
        ))
    return CompleteOpposingNetAccessRowContext(
        FabricFingerprint=Problem.Fabric.FabricFingerprint,
        CompleteVariantFingerprints=tuple(
            Variant.NetFingerprint
            for Variant in sorted(
                CompleteVariants,
                key=lambda Value: Value.NetFingerprint,
            )
        ),
        ComponentByNodeByVariant=tuple(ComponentMaps),
        ComponentMapByVariant={
            Fingerprint: dict(Values)
            for Fingerprint, Values in ComponentMaps
        },
        CurrentSignal=CurrentSignal,
        CompleteSignal=CompleteSignal,
        CurrentAccessDomainFingerprint=CurrentAccessDomainFingerprint,
        CompatibleComponentByCandidateFingerprintByVariant={},
    )


@dataclass(frozen=True)
class CompleteOpposingNetAccessContractRowResult:
    """Exact pair results computed by one shared opposing-variant scan."""

    ResultsByCurrentContract: tuple[
        tuple[str, CompleteOpposingNetAccessPairResult], ...
    ]
    AccessSignatureCount: int
    VariantScanCount: int
    SignaturePairCheckCount: int
    AccessPreparationSeconds: float = 0.0
    VariantScanSeconds: float = 0.0

    @property
    def Results(self) -> dict[str, CompleteOpposingNetAccessPairResult]:
        return dict(self.ResultsByCurrentContract)


@dataclass(frozen=True)
class CompleteOpposingNetAccessContractDomain:
    """Current-side access facts shared by every opposing contract row."""

    CurrentSignal: str
    FabricFingerprint: str
    ResourceIdentityFingerprint: str
    CurrentAccessDomainFingerprint: str
    CurrentContractDomainFingerprint: str
    DomainIndexFingerprint: str
    CurrentContractFingerprints: tuple[str, ...]
    PortObjectIdentities: tuple[tuple[str, int], ...]
    SelectionKeysBySignatureIndex: tuple[tuple[str, ...], ...]
    CanonicalAccessSignatures: tuple[
        tuple[str, tuple[object, ...]], ...
    ]
    SignatureIndexByCurrentContract: tuple[
        tuple[str, int], ...
    ]
    CandidateDomainsByCurrentContract: tuple[
        tuple[
            str,
            tuple[tuple[ComponentTerminalAccessCandidate, ...], ...],
        ], ...
    ]
    ValidatedProblemIdentityObjects: list[tuple[object, ...]] = field(
        default_factory=list,
        compare=False,
        repr=False,
    )

    @property
    def SignaturesByCurrentContract(
        self,
    ) -> tuple[tuple[str, tuple[object, ...]], ...]:
        """Expand the compact index only for the row evaluator.

        The large effective-access signature is stored once per distinct
        candidate selection.  Exact local contracts retain separate entries
        and proof identities while sharing that immutable access fact.
        """
        Signatures = tuple(
            Signature for _Fingerprint, Signature
            in self.CanonicalAccessSignatures
        )
        return tuple(
            (Contract, Signatures[Index])
            for Contract, Index in self.SignatureIndexByCurrentContract
        )


def _OpposingNetResourceIdentityFingerprint(
    Problem: ComponentRoutingProblem,
) -> str:
    Origin = _ComponentOrigin(Problem)
    ResourceGraph = Problem.ResourceGraph
    Technology = getattr(ResourceGraph, "Technology", None)
    return _StableFingerprint((
        "opposing-net-resource-domain-v1",
        Problem.Fabric.FabricFingerprint,
        getattr(ResourceGraph, "GraphVersion", None),
        type(Technology).__qualname__,
        getattr(Technology, "TechnologyVersion", None),
        repr(Technology),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(ResourceGraph, "ActualBlocks", ())
        )),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(ResourceGraph, "ElectricalBlocks", ())
        )),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(ResourceGraph, "SolidBlocks", ())
        )),
    ))


def _EffectiveOpposingNetAccessCandidateDomains(
    Problem: ComponentRoutingProblem,
    Signal: str,
    Port: Any,
) -> tuple[tuple[ComponentTerminalAccessCandidate, ...], ...]:
    CandidateFingerprints = frozenset(
        getattr(Port, "OwnedCandidateFingerprints", ())
    )
    ImmutableForeignClaims = tuple(
        Claim.Claims
        for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims)
        if Claim.Signal not in Problem.ComponentSignals
    )
    return tuple(
        tuple(
            Candidate
            for Candidate in Domain.Candidates
            if (
                (
                    not CandidateFingerprints
                    or Candidate.CandidateFingerprint
                    in CandidateFingerprints
                )
                and not any(
                    ComponentClaimsConflict(Candidate.Claims, Claims)
                    for Claims in ImmutableForeignClaims
                )
            )
        )
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == Signal
    )


def BuildOpposingNetEffectiveAccessSignature(
    Problem: ComponentRoutingProblem,
    Signal: str,
    Port: Any,
) -> tuple[object, ...]:
    """Return exactly the current-side facts consumed by the pair predicate.

    A seam's LocalPath remains in its exact local-contract and proof-domain
    identity.  The relaxed predicate itself consumes only terminal identity,
    candidate attachment, and all four physical claim sets, so contracts that
    differ only outside those facts may safely share computation but not proof.
    """
    CandidateDomains = _EffectiveOpposingNetAccessCandidateDomains(
        Problem,
        str(Signal),
        Port,
    )
    return _BuildOpposingNetEffectiveAccessSignatureFromDomains(
        Problem,
        str(Signal),
        CandidateDomains,
    )


def _BuildOpposingNetEffectiveAccessSignatureFromDomains(
    Problem: ComponentRoutingProblem,
    Signal: str,
    CandidateDomains: tuple[
        tuple[ComponentTerminalAccessCandidate, ...], ...
    ],
) -> tuple[object, ...]:
    """Fingerprint one already-filtered effective access domain."""
    Origin = _ComponentOrigin(Problem)
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == str(Signal)
    )
    return tuple(
        (
            Domain.TerminalRole,
            Domain.TerminalFingerprint,
            bool(getattr(Domain, "Complete", True)),
            tuple(sorted({
                (
                    _NormalizePosition(Candidate.Attachment, Origin),
                    _NormalizeClaims(Candidate.Claims, Origin),
                )
                for Candidate in Candidates
            })),
        )
        for Domain, Candidates in zip(Domains, CandidateDomains)
    )


def _CanonicalOpposingNetAccessSignatureFingerprint(
    Signature: tuple[object, ...],
) -> str:
    """Return a full digest for a shared effective-access signature."""
    return sha256(repr((
        "opposing-effective-access-signature-v1",
        Signature,
    )).encode("utf-8")).hexdigest()


def _BuildOpposingNetCurrentContractDomainFingerprint(
    CurrentSignal: str,
    OrderedCurrentPorts: tuple[tuple[str, Any], ...],
    SelectionKeysBySignatureIndex: tuple[tuple[str, ...], ...],
    CanonicalAccessSignatures: tuple[
        tuple[str, tuple[object, ...]], ...
    ],
    SignatureIndexByCurrentContract: tuple[tuple[str, int], ...],
    Origin: Position3,
) -> str:
    """Hash each large access signature once and exact contracts separately."""
    SignatureIndexByContract = dict(SignatureIndexByCurrentContract)
    return _StableFingerprint((
        "opposing-current-contract-domain-v2",
        str(CurrentSignal),
        tuple(
            (
                Index,
                SelectionKeysBySignatureIndex[Index],
                SignatureFingerprint,
            )
            for Index, (SignatureFingerprint, _Signature)
            in enumerate(CanonicalAccessSignatures)
        ),
        tuple(
            (
                Contract,
                getattr(Port, "Direction", ""),
                getattr(Port, "FabricDomainFingerprint", ""),
                tuple(sorted(getattr(
                    Port,
                    "OwnedTerminalFingerprints",
                    (),
                ))),
                tuple(sorted(getattr(
                    Port,
                    "OwnedCandidateFingerprints",
                    (),
                ))),
                tuple(
                    _NormalizePosition(Position, Origin)
                    for Position in getattr(Port, "LocalPath", ())
                ),
                SignatureIndexByContract[Contract],
                CanonicalAccessSignatures[
                    SignatureIndexByContract[Contract]
                ][0],
            )
            for Contract, Port in OrderedCurrentPorts
        ),
    ))


def BuildCompleteOpposingNetAccessContractDomain(
    Problem: ComponentRoutingProblem,
    CurrentSignal: str,
    CurrentPortsByContract: Mapping[str, Any],
) -> CompleteOpposingNetAccessContractDomain:
    """Precompute the invariant current-side domain for a row portfolio."""
    CurrentSignal = str(CurrentSignal)
    OrderedCurrentPorts = tuple(sorted(CurrentPortsByContract.items()))
    if not CurrentSignal or not OrderedCurrentPorts:
        raise ValueError("opposing-net access contract domain is empty")
    for Contract, Port in OrderedCurrentPorts:
        if (
            str(getattr(Port, "Signal", "")) != CurrentSignal
            or _PhysicalPortLocalContractFingerprint(Port) != Contract
        ):
            raise ValueError(
                "opposing-net access contract domain identity mismatch"
            )
    CandidateDomainsBySelection = {}
    SignatureBySelection = {}
    SelectionKeyByContract = {}
    CandidateDomains = []
    for Contract, Port in OrderedCurrentPorts:
        SelectionKey = tuple(sorted(
            getattr(Port, "OwnedCandidateFingerprints", ())
        ))
        SelectionKeyByContract[Contract] = SelectionKey
        SelectedDomains = CandidateDomainsBySelection.get(SelectionKey)
        if SelectedDomains is None:
            SelectedDomains = _EffectiveOpposingNetAccessCandidateDomains(
                Problem,
                CurrentSignal,
                Port,
            )
            CandidateDomainsBySelection[SelectionKey] = SelectedDomains
        CandidateDomains.append((Contract, SelectedDomains))
        Signature = SignatureBySelection.get(SelectionKey)
        if Signature is None:
            Signature = (
                _BuildOpposingNetEffectiveAccessSignatureFromDomains(
                    Problem,
                    CurrentSignal,
                    SelectedDomains,
                )
            )
            SignatureBySelection[SelectionKey] = Signature
    CandidateDomains = tuple(CandidateDomains)
    CandidateDomainsByContract = dict(CandidateDomains)
    SelectionKeysBySignatureIndex = tuple(sorted(SignatureBySelection))
    SignatureIndexBySelection = {
        SelectionKey: Index
        for Index, SelectionKey
        in enumerate(SelectionKeysBySignatureIndex)
    }
    CanonicalAccessSignatures = tuple(
        (
            _CanonicalOpposingNetAccessSignatureFingerprint(
                SignatureBySelection[SelectionKey]
            ),
            SignatureBySelection[SelectionKey],
        )
        for SelectionKey in SelectionKeysBySignatureIndex
    )
    SignatureIndexByCurrentContract = tuple(
        (
            Contract,
            SignatureIndexBySelection[SelectionKeyByContract[Contract]],
        )
        for Contract, _Port in OrderedCurrentPorts
    )
    Origin = _ComponentOrigin(Problem)
    CurrentContractDomainFingerprint = (
        _BuildOpposingNetCurrentContractDomainFingerprint(
            CurrentSignal,
            OrderedCurrentPorts,
            SelectionKeysBySignatureIndex,
            CanonicalAccessSignatures,
            SignatureIndexByCurrentContract,
            Origin,
        )
    )
    FabricFingerprint = Problem.Fabric.FabricFingerprint
    ResourceIdentityFingerprint = _OpposingNetResourceIdentityFingerprint(
        Problem
    )
    CurrentAccessDomainFingerprint = (
        _OpposingRowCurrentAccessDomainFingerprint(
            Problem,
            CurrentSignal,
        )
    )
    DomainIndexFingerprint = _StableFingerprint((
        "opposing-current-access-domain-index-v1",
        FabricFingerprint,
        ResourceIdentityFingerprint,
        CurrentAccessDomainFingerprint,
        CurrentContractDomainFingerprint,
    ))
    return CompleteOpposingNetAccessContractDomain(
        CurrentSignal=CurrentSignal,
        FabricFingerprint=FabricFingerprint,
        ResourceIdentityFingerprint=ResourceIdentityFingerprint,
        CurrentAccessDomainFingerprint=CurrentAccessDomainFingerprint,
        CurrentContractDomainFingerprint=CurrentContractDomainFingerprint,
        DomainIndexFingerprint=DomainIndexFingerprint,
        CurrentContractFingerprints=tuple(
            Contract for Contract, _Port in OrderedCurrentPorts
        ),
        PortObjectIdentities=tuple(
            (Contract, id(Port)) for Contract, Port in OrderedCurrentPorts
        ),
        SelectionKeysBySignatureIndex=SelectionKeysBySignatureIndex,
        CanonicalAccessSignatures=CanonicalAccessSignatures,
        SignatureIndexByCurrentContract=(
            SignatureIndexByCurrentContract
        ),
        CandidateDomainsByCurrentContract=tuple(
            (
                Contract,
                CandidateDomainsByContract[Contract],
            )
            for Contract, _Port in OrderedCurrentPorts
        ),
    )


def EvaluateCompleteOpposingNetAccessContractRow(
    Problem: ComponentRoutingProblem,
    *,
    CurrentSignal: str,
    CompleteSignal: str,
    CurrentPortsByContract: Mapping[str, Any],
    CompleteLocalContractFingerprint: str,
    CompleteVariants: tuple[RoutedComponentNet, ...],
    CompleteVariantDomainComplete: bool,
    DeadlineSeconds: float | None,
    DomainFingerprintsByCurrentContract: Mapping[str, str],
    ContractDomain: CompleteOpposingNetAccessContractDomain | None = None,
    RowContext: CompleteOpposingNetAccessRowContext | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> CompleteOpposingNetAccessContractRowResult:
    """Evaluate one complete-contract row with one outer variant scan."""
    CurrentSignal = str(CurrentSignal)
    CompleteSignal = str(CompleteSignal)
    if (
        not CurrentSignal
        or not CompleteSignal
        or CurrentSignal == CompleteSignal
        or not CurrentPortsByContract
    ):
        raise ValueError(
            "opposing-net access row requires two signals and current ports"
        )
    PortsBySignal = {
        str(Port.Signal): Port
        for Port in (
            Problem.Interface.PhysicalPortReservations
            if Problem.Interface is not None
            else ()
        )
    }
    CompletePort = PortsBySignal.get(CompleteSignal)
    if (
        CompletePort is None
        or _PhysicalPortLocalContractFingerprint(CompletePort)
        != CompleteLocalContractFingerprint
    ):
        raise ValueError(
            "opposing-net access row complete contract fingerprint mismatch"
        )
    OrderedCurrentPorts = tuple(sorted(CurrentPortsByContract.items()))
    if set(DomainFingerprintsByCurrentContract) != {
        Contract for Contract, _Port in OrderedCurrentPorts
    }:
        raise ValueError("opposing-net access row proof domains are incomplete")
    if ContractDomain is None:
        for Contract, Port in OrderedCurrentPorts:
            if (
                str(getattr(Port, "Signal", "")) != CurrentSignal
                or _PhysicalPortLocalContractFingerprint(Port) != Contract
            ):
                raise ValueError(
                    "opposing-net access row current contract fingerprint "
                    "mismatch"
                )

    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == CurrentSignal
    )
    InputDomainComplete = bool(
        Problem.DomainComplete
        and Domains
        and all(getattr(Domain, "Complete", True) for Domain in Domains)
        and CompleteVariantDomainComplete
    )
    AccessPreparationStartedAt = monotonic()
    if ContractDomain is None:
        ContractDomain = BuildCompleteOpposingNetAccessContractDomain(
            Problem,
            CurrentSignal,
            dict(OrderedCurrentPorts),
        )
    ExpectedCurrentContracts = tuple(
        Contract for Contract, _Port in OrderedCurrentPorts
    )
    OrderedPortsByContract = dict(OrderedCurrentPorts)
    CandidateDomainsByContract = dict(
        ContractDomain.CandidateDomainsByCurrentContract
    )
    SignatureIndexByContract = dict(
        ContractDomain.SignatureIndexByCurrentContract
    )
    try:
        CanonicalSignaturesAreValid = (
            ContractDomain.SelectionKeysBySignatureIndex
            == tuple(sorted(
                ContractDomain.SelectionKeysBySignatureIndex
            ))
            and len(set(
                ContractDomain.SelectionKeysBySignatureIndex
            )) == len(ContractDomain.SelectionKeysBySignatureIndex)
            and len(ContractDomain.SelectionKeysBySignatureIndex)
            == len(ContractDomain.CanonicalAccessSignatures)
            and tuple(
                Contract for Contract, _Index
                in ContractDomain.SignatureIndexByCurrentContract
            ) == ExpectedCurrentContracts
            and all(
                0 <= Index
                < len(ContractDomain.CanonicalAccessSignatures)
                and tuple(sorted(getattr(
                    OrderedPortsByContract[Contract],
                    "OwnedCandidateFingerprints",
                    (),
                )))
                == ContractDomain.SelectionKeysBySignatureIndex[Index]
                for Contract, Index
                in ContractDomain.SignatureIndexByCurrentContract
            )
            and ContractDomain.CurrentContractDomainFingerprint
            == _BuildOpposingNetCurrentContractDomainFingerprint(
                CurrentSignal,
                OrderedCurrentPorts,
                ContractDomain.SelectionKeysBySignatureIndex,
                ContractDomain.CanonicalAccessSignatures,
                ContractDomain.SignatureIndexByCurrentContract,
                _ComponentOrigin(Problem),
            )
        )
    except (IndexError, KeyError, TypeError, ValueError):
        CanonicalSignaturesAreValid = False
    ProblemIdentityObjects = (
        Problem.Fabric,
        Problem.ResourceGraph,
        Problem.OwnedTerminalDomains,
        Problem.LocalClaims,
        Problem.ImmutableClaims,
        Problem.ComponentSignals,
        *(Port for _Contract, Port in OrderedCurrentPorts),
    )
    ExpensiveIdentityIsValidated = any(
        len(CachedObjects) == len(ProblemIdentityObjects)
        and all(
            Cached is Current
            for Cached, Current
            in zip(CachedObjects, ProblemIdentityObjects)
        )
        for CachedObjects
        in ContractDomain.ValidatedProblemIdentityObjects
    )
    if (
        ContractDomain.CurrentSignal != CurrentSignal
        or ContractDomain.FabricFingerprint
        != Problem.Fabric.FabricFingerprint
        or ContractDomain.CurrentContractFingerprints
        != ExpectedCurrentContracts
        or ContractDomain.PortObjectIdentities
        != tuple(
            (Contract, id(Port))
            for Contract, Port in OrderedCurrentPorts
        )
        or not CanonicalSignaturesAreValid
        or ContractDomain.DomainIndexFingerprint
        != _StableFingerprint((
            "opposing-current-access-domain-index-v1",
            ContractDomain.FabricFingerprint,
            ContractDomain.ResourceIdentityFingerprint,
            ContractDomain.CurrentAccessDomainFingerprint,
            ContractDomain.CurrentContractDomainFingerprint,
        ))
    ):
        raise ValueError(
            "opposing-net access contract domain identity mismatch"
        )
    if (
        tuple(sorted(CandidateDomainsByContract))
        != ExpectedCurrentContracts
    ):
        raise ValueError(
            "opposing-net access contract domain is incomplete"
        )
    if not ExpensiveIdentityIsValidated:
        ExpensiveIdentityIsValid = (
            ContractDomain.ResourceIdentityFingerprint
            == _OpposingNetResourceIdentityFingerprint(Problem)
            and ContractDomain.CurrentAccessDomainFingerprint
            == _OpposingRowCurrentAccessDomainFingerprint(
                Problem,
                CurrentSignal,
            )
            and all(
                SignatureFingerprint
                == _CanonicalOpposingNetAccessSignatureFingerprint(
                    Signature
                )
                for SignatureFingerprint, Signature
                in ContractDomain.CanonicalAccessSignatures
            )
        )
        for SignatureIndex in range(
            len(ContractDomain.CanonicalAccessSignatures)
        ):
            Contracts = tuple(
                Contract for Contract in ExpectedCurrentContracts
                if SignatureIndexByContract[Contract] == SignatureIndex
            )
            if not Contracts:
                ExpensiveIdentityIsValid = False
                break
            RepresentativeDomains = CandidateDomainsByContract[Contracts[0]]
            if (
                any(
                    CandidateDomainsByContract[Contract]
                    is not RepresentativeDomains
                    for Contract in Contracts[1:]
                )
                or _BuildOpposingNetEffectiveAccessSignatureFromDomains(
                    Problem,
                    CurrentSignal,
                    RepresentativeDomains,
                ) != ContractDomain.CanonicalAccessSignatures[
                    SignatureIndex
                ][1]
            ):
                ExpensiveIdentityIsValid = False
                break
        if not ExpensiveIdentityIsValid:
            raise ValueError(
                "opposing-net access contract domain identity mismatch"
            )
        ContractDomain.ValidatedProblemIdentityObjects.append(
            ProblemIdentityObjects
        )
    ContractsBySignatureIndex: dict[int, list[str]] = defaultdict(list)
    RepresentativeCandidateDomainsBySignatureIndex: dict[
        int,
        tuple[tuple[ComponentTerminalAccessCandidate, ...], ...],
    ] = {}
    for Contract in ExpectedCurrentContracts:
        SignatureIndex = SignatureIndexByContract[Contract]
        ContractsBySignatureIndex[SignatureIndex].append(Contract)
        RepresentativeCandidateDomainsBySignatureIndex.setdefault(
            SignatureIndex,
            CandidateDomainsByContract[Contract],
        )
    AccessPreparationSeconds = monotonic() - AccessPreparationStartedAt

    ExpectedVariantFingerprints = tuple(
        Variant.NetFingerprint
        for Variant in sorted(
            CompleteVariants,
            key=lambda Value: Value.NetFingerprint,
        )
    )
    if RowContext is None and CompleteVariants:
        RowContext = BuildCompleteOpposingNetAccessRowContext(
            Problem,
            CompleteVariants,
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
        )
    if RowContext is not None and (
        RowContext.FabricFingerprint != Problem.Fabric.FabricFingerprint
        or RowContext.CompleteVariantFingerprints
        != ExpectedVariantFingerprints
        or RowContext.CurrentSignal not in ("", CurrentSignal)
        or RowContext.CompleteSignal not in ("", CompleteSignal)
        or (
            RowContext.CurrentAccessDomainFingerprint
            and RowContext.CurrentAccessDomainFingerprint
            != ContractDomain.CurrentAccessDomainFingerprint
        )
    ):
        raise ValueError("opposing-net access row context identity mismatch")
    ComponentMapByVariant = (
        {}
        if RowContext is None
        else RowContext.ComponentMapByVariant
        if RowContext.ComponentMapByVariant
        else {
            Fingerprint: dict(Values)
            for Fingerprint, Values in RowContext.ComponentByNodeByVariant
        }
    )
    CompatibleByVariant = (
        {}
        if RowContext is None
        else RowContext.CompatibleComponentByCandidateFingerprintByVariant
    )

    StartedAt = monotonic()
    VariantScanCount = 0
    SignaturePairCheckCount = 0
    SupportingVariantBySignatureIndex: dict[int, str] = {}
    ExpansionCountBySignatureIndex = {
        SignatureIndex: 0
        for SignatureIndex in ContractsBySignatureIndex
    }
    EmptySignatureIndexes = {
        SignatureIndex
        for SignatureIndex, CandidateDomains
        in RepresentativeCandidateDomainsBySignatureIndex.items()
        if any(not Candidates for Candidates in CandidateDomains)
    }
    UnresolvedSignatureIndexes = (
        set(ContractsBySignatureIndex) - EmptySignatureIndexes
    )
    InitialDeadlineExpired = bool(
        CompleteVariants
        and DeadlineSeconds is not None
        and DeadlineSeconds <= 0
    )
    DeadlineExpired = InitialDeadlineExpired
    VariantScanStartedAt = monotonic()
    if InputDomainComplete and CompleteVariants and not DeadlineExpired:
        for Variant in sorted(
            CompleteVariants,
            key=lambda Value: Value.NetFingerprint,
        ):
            if (
                DeadlineSeconds is not None
                and monotonic() - StartedAt >= DeadlineSeconds
            ):
                DeadlineExpired = True
                break
            if not UnresolvedSignatureIndexes:
                break
            VariantScanCount += 1
            if WorkCheck is not None:
                WorkCheck({
                    "Stage": "complete-opposing-net-access-contract-row",
                    "VariantScanCount": VariantScanCount,
                    "CompleteVariantCount": len(CompleteVariants),
                    "UnresolvedAccessSignatureCount": len(
                        UnresolvedSignatureIndexes
                    ),
                    "CurrentSignal": CurrentSignal,
                    "CompleteSignal": CompleteSignal,
                })
            ComponentByNode = ComponentMapByVariant[Variant.NetFingerprint]
            CandidateComponentIndex = CompatibleByVariant.get(
                Variant.NetFingerprint,
                {},
            )
            for SignatureIndex in tuple(sorted(
                UnresolvedSignatureIndexes
            )):
                SignaturePairCheckCount += 1
                ExpansionCountBySignatureIndex[SignatureIndex] += 1
                CommonComponents: set[int] | None = None
                for Candidates in (
                    RepresentativeCandidateDomainsBySignatureIndex[
                        SignatureIndex
                    ]
                ):
                    CandidateComponents = {
                        (
                            CandidateComponentIndex[
                                Candidate.CandidateFingerprint
                            ]
                            if Candidate.CandidateFingerprint
                            in CandidateComponentIndex
                            else ComponentByNode[Candidate.Attachment]
                        )
                        for Candidate in Candidates
                        if (
                            Candidate.Attachment in ComponentByNode
                            and (
                                Candidate.CandidateFingerprint
                                in CandidateComponentIndex
                                or (
                                    not CompatibleByVariant
                                    and ComponentClaimsCompatibleForOwners(
                                        CurrentSignal,
                                        Candidate.Claims,
                                        CompleteSignal,
                                        Variant.Claims,
                                    )
                                )
                            )
                        )
                    }
                    if CommonComponents is None:
                        CommonComponents = set(CandidateComponents)
                    else:
                        CommonComponents.intersection_update(
                            CandidateComponents
                        )
                    if not CommonComponents:
                        break
                if CommonComponents:
                    SupportingVariantBySignatureIndex[SignatureIndex] = (
                        Variant.NetFingerprint
                    )
                    UnresolvedSignatureIndexes.remove(SignatureIndex)
    VariantScanSeconds = monotonic() - VariantScanStartedAt

    def ExactResult(
        Contract: str,
        SignatureIndex: int,
    ) -> CompleteOpposingNetAccessPairResult:
        DomainFingerprint = str(
            DomainFingerprintsByCurrentContract[Contract]
        )
        SupportingVariant = SupportingVariantBySignatureIndex.get(
            SignatureIndex
        )
        ExpansionCount = ExpansionCountBySignatureIndex[SignatureIndex]
        IncompleteDetail = (
            "pair access input domain is incomplete"
            if not InputDomainComplete
            else "pair access deadline expired"
            if (
                InitialDeadlineExpired
                or DeadlineExpired
                and SignatureIndex in UnresolvedSignatureIndexes
            )
            else ""
        )
        if IncompleteDetail:
            return CompleteOpposingNetAccessPairResult(
                Status="incomplete",
                Complete=False,
                Feasible=None,
                DomainFingerprint=DomainFingerprint,
                ProofFingerprint="",
                CurrentSignal=CurrentSignal,
                CompleteSignal=CompleteSignal,
                CurrentLocalContractFingerprint=Contract,
                CompleteLocalContractFingerprint=(
                    CompleteLocalContractFingerprint
                ),
                ExpansionCount=ExpansionCount,
                Detail=IncompleteDetail,
            )
        Feasible = SupportingVariant is not None
        SupportingVariants = (
            (SupportingVariant,) if SupportingVariant is not None else ()
        )
        EmptyCurrentDomain = SignatureIndex in EmptySignatureIndexes
        EmptyCompleteDomain = not CompleteVariants
        ProofValue: object = (
            "empty-current-access-domain"
            if EmptyCurrentDomain
            else "empty-complete-variant-domain"
            if EmptyCompleteDomain
            else SupportingVariants
        )
        return CompleteOpposingNetAccessPairResult(
            Status=("feasible" if Feasible else "architectural-unsatisfiable"),
            Complete=True,
            Feasible=Feasible,
            DomainFingerprint=DomainFingerprint,
            ProofFingerprint=_StableFingerprint((
                "complete-opposing-net-access-pair-proof-v1",
                DomainFingerprint,
                ProofValue,
            )),
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
            CurrentLocalContractFingerprint=Contract,
            CompleteLocalContractFingerprint=CompleteLocalContractFingerprint,
            SupportingCompleteVariantFingerprints=SupportingVariants,
            ExpansionCount=ExpansionCount,
            Detail=(
                "one exact current access domain has no legal candidate"
                if EmptyCurrentDomain
                else "complete opposing-net variant domain is empty"
                if EmptyCompleteDomain
                else "a complete opposing-net variant supports every access domain"
                if Feasible
                else "no complete opposing-net variant supports every access domain"
            ),
            Diagnostics={
                "CompleteVariantDomainComplete": True,
                "ReservedGlobalClaimsIgnored": True,
                "BulkAccessSignatureShared": bool(
                    len(ContractsBySignatureIndex[SignatureIndex]) > 1
                ),
            },
        )

    return CompleteOpposingNetAccessContractRowResult(
        ResultsByCurrentContract=tuple(
            (
                Contract,
                ExactResult(
                    Contract,
                    SignatureIndexByContract[Contract],
                ),
            )
            for Contract, _Port in OrderedCurrentPorts
        ),
        AccessSignatureCount=len(ContractsBySignatureIndex),
        VariantScanCount=VariantScanCount,
        SignaturePairCheckCount=SignaturePairCheckCount,
        AccessPreparationSeconds=AccessPreparationSeconds,
        VariantScanSeconds=VariantScanSeconds,
    )


@dataclass(frozen=True)
class CompleteComponentNetVariantPortfolioResult:
    """A cache-backed complete per-net portfolio, or typed incompleteness."""

    Status: str
    Complete: bool
    Variants: tuple[RoutedComponentNet, ...]
    DomainFingerprint: str
    Detail: str = ""
    ExpansionCount: int = 0
    Diagnostics: dict[str, object] | None = None

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Status": self.Status,
            "Complete": self.Complete,
            "VariantCount": len(self.Variants),
            "VariantFingerprints": [
                Variant.NetFingerprint for Variant in self.Variants
            ],
            "DomainFingerprint": self.DomainFingerprint,
            "Detail": self.Detail,
            "ExpansionCount": self.ExpansionCount,
            "Diagnostics": dict(self.Diagnostics or {}),
        }


@dataclass(frozen=True)
class CompleteComponentNetMultiPortfolioResult:
    """One finite complete-net discovery projected onto exact port contracts."""

    Complete: bool
    PortfoliosByContract: tuple[
        tuple[str, CompleteComponentNetVariantPortfolioResult], ...
    ]
    DomainFingerprint: str
    CanonicalStateCount: int
    NetVariantBuildCount: int
    Detail: str = ""
    Diagnostics: dict[str, object] | None = None

    @property
    def Portfolios(self) -> dict[
        str, CompleteComponentNetVariantPortfolioResult
    ]:
        return dict(self.PortfoliosByContract)

def _BuildLocalOnlyCompleteNetProblem(
    Problem: ComponentRoutingProblem,
    Signal: str,
    Port: Any,
    StructuralFingerprint: str,
) -> ComponentRoutingProblem:
    """Close one signal around one exact local port, excluding global claims."""
    OriginalComponentSignals = frozenset(Problem.ComponentSignals)
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == Signal
    )
    LocalInterface = Problem.Interface
    if LocalInterface is not None:
        LocalInterface = replace(
            LocalInterface,
            OwnedSignals=(Signal,),
            Ports=tuple(
                Value for Value in LocalInterface.Ports
                if Value.Signal == Signal
            ),
            Feedthroughs=(),
            PhysicalPortReservations=(Port,),
        )
    LocalAssemblyPlan = Problem.PhysicalAssemblyPlan
    if LocalAssemblyPlan is not None:
        LocalAssemblyPlan = replace(
            LocalAssemblyPlan,
            Ports=(Port,),
            Feedthroughs=(),
        )
    return replace(
        Problem,
        ProblemFingerprint=_StableFingerprint((
            "local-only-complete-net-portfolio-problem-v1",
            StructuralFingerprint,
            Signal,
        )),
        ComponentSignals=(Signal,),
        LocalClaims=tuple(
            Claim for Claim in Problem.LocalClaims
            if Claim.Signal == Signal
        ),
        ImmutableClaims=tuple(
            Claim for Claim in Problem.ImmutableClaims
            if Claim.Signal not in OriginalComponentSignals
        ),
        OwnedTerminalDomains=Domains,
        ExternalContinuationTerminals=tuple(
            Value for Value in Problem.ExternalContinuationTerminals
            if Value[0] == Signal
        ),
        ExternalContinuationDomains=tuple(
            Domain for Domain in Problem.ExternalContinuationDomains
            if Domain.Signal == Signal
        ),
        ForeignEscapeDomains=(),
        ForeignTransitDomains=(),
        Interface=LocalInterface,
        PhysicalAssemblyPlan=LocalAssemblyPlan,
        ReservedGlobalClaimsBySignal=(),
    )


def GetCachedCompleteComponentNetVariantPortfolio(
    Problem: ComponentRoutingProblem,
    Signal: str,
    VariantPortfolioCache: dict[Any, Any],
    *,
    StaticContext: CompleteComponentNetPortfolioStaticContext | None = None,
) -> CompleteComponentNetVariantPortfolioResult:
    """Read only portfolios written after exhaustive net discovery.

    The component solver never writes ``VariantPortfolioCache`` when variant
    discovery stops at a limit.  Cache presence is therefore a completeness
    certificate, but structural reuse is still translated and revalidated
    against the exact local problem before being returned.
    """
    Signal = str(Signal)
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == Signal
    )
    Origin = _ComponentOrigin(Problem)
    StructuralFingerprint = _ComponentNetPortfolioStructuralFingerprint(
        Problem,
        Signal,
        Domains,
        Origin,
        StaticContext,
    )
    ExactKey = (Problem.ProblemFingerprint, Signal)
    StructuralKey = (
        "component-net-translation-v1",
        StructuralFingerprint,
    )
    Cached = VariantPortfolioCache.get(ExactKey)
    CacheKind = "exact"
    if Cached is None:
        Cached = VariantPortfolioCache.get(StructuralKey)
        CacheKind = "structural"
    DomainFingerprint = _StableFingerprint((
        "complete-component-net-variant-portfolio-v1",
        StructuralFingerprint,
        Signal,
    ))
    if Cached is None:
        return CompleteComponentNetVariantPortfolioResult(
            Status="incomplete",
            Complete=False,
            Variants=(),
            DomainFingerprint=DomainFingerprint,
            Detail="complete net variant portfolio is not cached",
        )
    (
        CachedVariants,
        _CombinationCount,
        _CachedRejections,
        CachedImmutableConflicts,
        CachedOrigin,
    ) = Cached
    if CacheKind == "structural" and CachedImmutableConflicts:
        return CompleteComponentNetVariantPortfolioResult(
            Status="incomplete",
            Complete=False,
            Variants=(),
            DomainFingerprint=DomainFingerprint,
            Detail="structural portfolio has context-specific conflicts",
        )
    Variants = _TranslateAndValidateNetPortfolio(
        tuple(CachedVariants),
        SourceOrigin=CachedOrigin,
        TargetOrigin=Origin,
        Signal=Signal,
        Domains=Domains,
        Problem=Problem,
    )
    if Variants is None:
        return CompleteComponentNetVariantPortfolioResult(
            Status="incomplete",
            Complete=False,
            Variants=(),
            DomainFingerprint=DomainFingerprint,
            Detail="cached net portfolio failed exact local validation",
        )
    return CompleteComponentNetVariantPortfolioResult(
        Status="complete",
        Complete=True,
        Variants=Variants,
        DomainFingerprint=DomainFingerprint,
        Detail="complete net variant portfolio reused",
    )


def CompileCompleteComponentNetVariantPortfolio(
    Problem: ComponentRoutingProblem,
    Signal: str,
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    VariantPortfolioCache: dict[Any, Any] | None = None,
    NetVariantConstructionCache: dict[Any, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
    StaticContext: CompleteComponentNetPortfolioStaticContext | None = None,
) -> CompleteComponentNetVariantPortfolioResult:
    """Exhaust one exact local net domain without solving a component template.

    Partial enumeration is retained only in ``NetVariantDiscoveryStateCache``
    so a later call can resume it.  ``VariantPortfolioCache`` is populated by
    the shared discovery implementation only after the finite domain has been
    exhausted; incomplete work is therefore never admitted as a proof cache.
    Reserved global routes are deliberately removed from this local-only
    compilation stage.
    """
    Signal = str(Signal)
    if Signal not in Problem.ComponentSignals:
        raise ValueError("net portfolio signal is not owned by the component")
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == Signal
    )
    if not Domains:
        raise ValueError("net portfolio signal has no owned terminal domain")
    if VariantPortfolioCache is None:
        VariantPortfolioCache = {}
    if NetVariantConstructionCache is None:
        NetVariantConstructionCache = {}
    if RouteClaimsConstructionCache is None:
        RouteClaimsConstructionCache = {}
    if NetVariantDiscoveryStateCache is None:
        NetVariantDiscoveryStateCache = {}

    Origin = _ComponentOrigin(Problem)
    StructuralFingerprint = _ComponentNetPortfolioStructuralFingerprint(
        Problem,
        Signal,
        Domains,
        Origin,
        StaticContext,
    )
    OriginalComponentSignals = frozenset(Problem.ComponentSignals)
    PhysicalPortReservations = tuple(
        Port
        for Port in (
            Problem.Interface.PhysicalPortReservations
            if Problem.Interface is not None
            else ()
        )
        if Port.Signal == Signal
    )
    LocalInterface = Problem.Interface
    if LocalInterface is not None:
        LocalInterface = replace(
            LocalInterface,
            OwnedSignals=(Signal,),
            Ports=tuple(
                Port for Port in LocalInterface.Ports
                if Port.Signal == Signal
            ),
            Feedthroughs=(),
            PhysicalPortReservations=PhysicalPortReservations,
        )
    LocalAssemblyPlan = Problem.PhysicalAssemblyPlan
    if LocalAssemblyPlan is not None:
        LocalAssemblyPlan = replace(
            LocalAssemblyPlan,
            Ports=PhysicalPortReservations,
            Feedthroughs=(),
        )
    LocalProblem = replace(
        Problem,
        ProblemFingerprint=_StableFingerprint((
            "local-only-complete-net-portfolio-problem-v1",
            StructuralFingerprint,
            Signal,
        )),
        ComponentSignals=(Signal,),
        LocalClaims=tuple(
            Claim for Claim in Problem.LocalClaims
            if Claim.Signal == Signal
        ),
        ImmutableClaims=tuple(
            Claim for Claim in Problem.ImmutableClaims
            if Claim.Signal not in OriginalComponentSignals
        ),
        OwnedTerminalDomains=Domains,
        ExternalContinuationTerminals=tuple(
            Value for Value in Problem.ExternalContinuationTerminals
            if Value[0] == Signal
        ),
        ExternalContinuationDomains=tuple(
            Domain for Domain in Problem.ExternalContinuationDomains
            if Domain.Signal == Signal
        ),
        ForeignEscapeDomains=(),
        ForeignTransitDomains=(),
        Interface=LocalInterface,
        PhysicalAssemblyPlan=LocalAssemblyPlan,
        ReservedGlobalClaimsBySignal=(),
    )
    Cached = GetCachedCompleteComponentNetVariantPortfolio(
        LocalProblem,
        Signal,
        VariantPortfolioCache,
        StaticContext=StaticContext,
    )
    if Cached.Complete:
        return replace(
            Cached,
            Status="complete-cached",
            Diagnostics={
                "PortfolioCacheHit": True,
                "LocalOnly": True,
                "TemplateSearchEntered": False,
            },
        )

    Solve = _SolveComponentRoutingProblemLegacy(
        LocalProblem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        VariantPortfolioCache=VariantPortfolioCache,
        NetVariantConstructionCache=NetVariantConstructionCache,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        NetVariantDiscoveryStateCache=NetVariantDiscoveryStateCache,
        DiscoveryVariantLimit=None,
        StopAfterCompleteNetVariantPortfolioSignal=Signal,
        StaticPortfolioContextsBySignal=(
            {Signal: StaticContext}
            if StaticContext is not None
            else None
        ),
    )
    Portfolio = GetCachedCompleteComponentNetVariantPortfolio(
        LocalProblem,
        Signal,
        VariantPortfolioCache,
        StaticContext=StaticContext,
    )
    if Solve.Status == "complete-net-variant-portfolio" and Portfolio.Complete:
        return replace(
            Portfolio,
            Status="complete",
            Detail="complete local-only net variant portfolio compiled",
            ExpansionCount=Solve.ExpansionCount,
            Diagnostics={
                **dict(Solve.Diagnostics or {}),
                "PortfolioCacheHit": False,
                "LocalOnly": True,
                "TemplateSearchEntered": False,
            },
        )
    return CompleteComponentNetVariantPortfolioResult(
        Status="incomplete",
        Complete=False,
        Variants=(),
        DomainFingerprint=Portfolio.DomainFingerprint,
        Detail=Solve.Detail or "local-only net portfolio compilation incomplete",
        ExpansionCount=Solve.ExpansionCount,
        Diagnostics={
            **dict(Solve.Diagnostics or {}),
            "UnderlyingStatus": Solve.Status,
            "PortfolioCacheHit": False,
            "LocalOnly": True,
            "TemplateSearchEntered": False,
        },
    )


def CompileCompleteComponentNetVariantPortfolios(
    Problem: ComponentRoutingProblem,
    Signal: str,
    PortsByContract: Mapping[str, Any],
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    VariantPortfolioCache: dict[Any, Any] | None = None,
    NetVariantConstructionCache: dict[Any, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
    StaticContext: CompleteComponentNetPortfolioStaticContext | None = None,
) -> CompleteComponentNetMultiPortfolioResult:
    """Exhaust one shared net domain and project it onto exact local ports.

    Candidate tuples are admitted to a contract only when every selected
    terminal candidate belongs to that contract.  External egress is partitioned
    by the exact ``LocalPath``.  Consequently the projected finite domain for
    each contract is identical to an independent exact-port compilation, while
    common fabric/tree construction is performed once.
    """
    StartedAt = monotonic()
    Signal = str(Signal)
    OrderedPorts = tuple(sorted(
        (str(Contract), Port)
        for Contract, Port in PortsByContract.items()
    ))
    if Signal not in Problem.ComponentSignals or not OrderedPorts:
        raise ValueError("multi-portfolio requires an owned signal and ports")
    if any(
        str(getattr(Port, "Signal", "")) != Signal
        or _PhysicalPortLocalContractFingerprint(Port) != Contract
        for Contract, Port in OrderedPorts
    ):
        raise ValueError("multi-portfolio local contract identity mismatch")
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == Signal
    )
    if not Domains:
        raise ValueError("multi-portfolio signal has no owned terminal domain")
    if StaticContext is None:
        StaticContext = BuildCompleteComponentNetPortfolioStaticContext(
            Problem,
            Signal,
        )
    if VariantPortfolioCache is None:
        VariantPortfolioCache = {}
    if NetVariantConstructionCache is None:
        NetVariantConstructionCache = {}
    if RouteClaimsConstructionCache is None:
        RouteClaimsConstructionCache = {}
    if NetVariantDiscoveryStateCache is None:
        NetVariantDiscoveryStateCache = {}

    Origin = _ComponentOrigin(Problem)

    def RelativePath(Path: Iterable[Position3]) -> tuple[Position3, ...]:
        return tuple(_NormalizePosition(Value, Origin) for Value in Path)

    CandidateByFingerprint = {
        Candidate.CandidateFingerprint: Candidate
        for Domain in Domains
        for Candidate in Domain.Candidates
    }
    ContractIdentity = tuple(
        (
            Contract,
            RelativePath(getattr(Port, "LocalPath", ())),
            tuple(sorted(
                (
                    Fingerprint,
                    _NormalizePosition(Candidate.Attachment, Origin),
                    RelativePath(Candidate.Path),
                    _NormalizeClaims(Candidate.Claims, Origin),
                    Candidate.Layer,
                    Candidate.Cost,
                )
                for Fingerprint in getattr(
                    Port,
                    "OwnedCandidateFingerprints",
                    (),
                )
                for Candidate in (CandidateByFingerprint.get(Fingerprint),)
                if Candidate is not None
            )),
        )
        for Contract, Port in OrderedPorts
    )
    DomainFingerprint = _StableFingerprint((
        "complete-net-multi-contract-domain-v1",
        StaticContext.StaticStructuralFingerprint,
        ContractIdentity,
    ))
    LocalProblems = {}
    StructuralFingerprints = {}
    CompletedPortfolios = {}
    MissingContracts = []
    for Contract, Port in OrderedPorts:
        StructuralFingerprint = _ComponentNetPortfolioStructuralFingerprint(
            replace(
                Problem,
                Interface=replace(
                    Problem.Interface,
                    PhysicalPortReservations=(Port,),
                ) if Problem.Interface is not None else None,
            ),
            Signal,
            Domains,
            Origin,
            StaticContext,
        )
        LocalProblem = _BuildLocalOnlyCompleteNetProblem(
            Problem,
            Signal,
            Port,
            StructuralFingerprint,
        )
        StructuralFingerprints[Contract] = StructuralFingerprint
        LocalProblems[Contract] = LocalProblem
        Cached = GetCachedCompleteComponentNetVariantPortfolio(
            LocalProblem,
            Signal,
            VariantPortfolioCache,
            StaticContext=StaticContext,
        )
        if Cached.Complete:
            CompletedPortfolios[Contract] = replace(
                Cached,
                Status="complete-cached",
                Diagnostics={
                    "PortfolioCacheHit": True,
                    "LocalOnly": True,
                    "MultiContract": True,
                    "TemplateSearchEntered": False,
                },
            )
        else:
            MissingContracts.append(Contract)

    StateKey = (
        "complete-net-multi-contract-discovery-v1",
        DomainFingerprint,
    )
    PriorState = NetVariantDiscoveryStateCache.get(StateKey, {})
    VariantsByContract = {
        Contract: dict(Values)
        for Contract, Values in PriorState.get(
            "VariantsByContract",
            {},
        ).items()
        if Contract in MissingContracts
    }
    for Contract in MissingContracts:
        VariantsByContract.setdefault(Contract, {})
    RejectionsByContract = {
        Contract: dict(Values)
        for Contract, Values in PriorState.get(
            "RejectionsByContract",
            {},
        ).items()
        if Contract in MissingContracts
    }
    for Contract in MissingContracts:
        RejectionsByContract.setdefault(Contract, {})
    ImmutableConflictsByContract = {
        Contract: set(Values)
        for Contract, Values in PriorState.get(
            "ImmutableConflictsByContract",
            {},
        ).items()
        if Contract in MissingContracts
    }
    for Contract in MissingContracts:
        ImmutableConflictsByContract.setdefault(Contract, set())
    ProcessedStates = set(PriorState.get("ProcessedStates", ()))
    CombinationKeysByContract = {
        Contract: set(Values)
        for Contract, Values in PriorState.get(
            "CombinationKeysByContract",
            {},
        ).items()
        if Contract in MissingContracts
    }
    for Contract in MissingContracts:
        CombinationKeysByContract.setdefault(Contract, set())

    CandidateContracts = {}
    for Domain in Domains:
        for Candidate in Domain.Candidates:
            CandidateContracts.setdefault(
                Candidate.CandidateFingerprint,
                set(),
            ).update(
                Contract
                for Contract, Port in OrderedPorts
                if Contract in MissingContracts
                and (
                    not getattr(Port, "OwnedCandidateFingerprints", ())
                    or Candidate.CandidateFingerprint in frozenset(
                        Port.OwnedCandidateFingerprints
                    )
                )
            )
    HasExternalContinuation = any(
        Value[0] == Signal
        for Value in Problem.ExternalContinuationTerminals
    )
    ContractsByEgress = defaultdict(set)
    for Contract, Port in OrderedPorts:
        if Contract in MissingContracts:
            ContractsByEgress[
                tuple(Port.LocalPath) if HasExternalContinuation else ()
            ].add(Contract)

    FabricAdjacency = _BuildAdjacency(Problem.Fabric.Edges)
    FabricParentCache = {}
    FabricComponentByNode = {}
    ComponentIndex = 0
    for Start in sorted(Problem.Fabric.Nodes):
        if Start in FabricComponentByNode:
            continue
        Pending = [Start]
        FabricComponentByNode[Start] = ComponentIndex
        while Pending:
            Node = Pending.pop()
            for Neighbor in FabricAdjacency.get(Node, ()):
                if Neighbor not in FabricComponentByNode:
                    FabricComponentByNode[Neighbor] = ComponentIndex
                    Pending.append(Neighbor)
        ComponentIndex += 1
    CandidatesByDomainByComponent = tuple(
        {
            Index: tuple(
                Candidate
                for Candidate in Domain.Candidates
                if FabricComponentByNode.get(Candidate.Attachment) == Index
            )
            for Index in set(
                FabricComponentByNode.get(Candidate.Attachment)
                for Candidate in Domain.Candidates
                if Candidate.Attachment in FabricComponentByNode
            )
        }
        for Domain in Domains
    )
    CommonComponents = (
        set(CandidatesByDomainByComponent[0])
        if CandidatesByDomainByComponent else set()
    )
    for Values in CandidatesByDomainByComponent[1:]:
        CommonComponents.intersection_update(Values)

    CanonicalStateCount = len(ProcessedStates)
    NetVariantBuildCount = 0
    ImmutableAccessConflictCache = {}
    LocalClaimsBySignal = {
        Signal: tuple(
            Claim for Claim in Problem.LocalClaims
            if Claim.Signal == Signal
        )
    }
    TreeRepeaterSubproblemCache = {}
    TreeRepeaterCacheStatistics = {}

    def SavePartial() -> None:
        NetVariantDiscoveryStateCache[StateKey] = {
            "VariantsByContract": {
                Contract: dict(Values)
                for Contract, Values in VariantsByContract.items()
            },
            "RejectionsByContract": {
                Contract: dict(Values)
                for Contract, Values in RejectionsByContract.items()
            },
            "ImmutableConflictsByContract": {
                Contract: frozenset(Values)
                for Contract, Values in ImmutableConflictsByContract.items()
            },
            "ProcessedStates": frozenset(ProcessedStates),
            "CombinationKeysByContract": {
                Contract: frozenset(Values)
                for Contract, Values in CombinationKeysByContract.items()
            },
        }

    def IncompleteResult(Detail: str) -> CompleteComponentNetMultiPortfolioResult:
        SavePartial()
        Results = dict(CompletedPortfolios)
        for Contract in MissingContracts:
            Results[Contract] = CompleteComponentNetVariantPortfolioResult(
                Status="incomplete",
                Complete=False,
                Variants=(),
                DomainFingerprint=_StableFingerprint((
                    "complete-component-net-variant-portfolio-v1",
                    StructuralFingerprints[Contract],
                    Signal,
                )),
                Detail=Detail,
                Diagnostics={
                    "MultiContract": True,
                    "SharedDomainComplete": False,
                    "TemplateSearchEntered": False,
                },
            )
        return CompleteComponentNetMultiPortfolioResult(
            Complete=False,
            PortfoliosByContract=tuple(sorted(Results.items())),
            DomainFingerprint=DomainFingerprint,
            CanonicalStateCount=CanonicalStateCount,
            NetVariantBuildCount=NetVariantBuildCount,
            Detail=Detail,
            Diagnostics={
                "SolverCallCount": 1,
                "MissingContractCount": len(MissingContracts),
                "ResumedCanonicalStateCount": len(
                    PriorState.get("ProcessedStates", ())
                ),
            },
        )

    if MissingContracts:
        for FabricComponentIndex in sorted(CommonComponents):
            CandidateDomains = tuple(
                Values[FabricComponentIndex]
                for Values in CandidatesByDomainByComponent
            )
            for Candidates in product(*CandidateDomains):
                CombinationKey = tuple(
                    Candidate.CandidateFingerprint for Candidate in Candidates
                )
                AdmittedContracts = set(MissingContracts)
                for Candidate in Candidates:
                    AdmittedContracts.intersection_update(
                        CandidateContracts.get(
                            Candidate.CandidateFingerprint,
                            (),
                        )
                    )
                if not AdmittedContracts:
                    continue
                for EgressPath, PathContracts in sorted(
                    ContractsByEgress.items()
                ):
                    StateContracts = AdmittedContracts & PathContracts
                    if not StateContracts:
                        continue
                    StateKeyValue = (
                        FabricComponentIndex,
                        CombinationKey,
                        tuple(EgressPath),
                    )
                    if StateKeyValue in ProcessedStates:
                        continue
                    if (
                        DeadlineSeconds is not None
                        and monotonic() - StartedAt >= DeadlineSeconds
                    ):
                        return IncompleteResult(
                            "multi-contract portfolio deadline expired"
                        )
                    if WorkCheck is not None:
                        try:
                            WorkCheck({
                                "Stage": "complete-net-multi-contract-portfolio",
                                "CanonicalStateCount": CanonicalStateCount,
                                "ContractCount": len(OrderedPorts),
                            })
                        except BaseException:
                            SavePartial()
                            raise
                    FabricSubtree = _UniqueFabricSubtree(
                        Problem.Fabric,
                        (
                            *(Candidate.Attachment for Candidate in Candidates),
                            *((EgressPath[0],) if EgressPath else ()),
                        ),
                        Adjacency=FabricAdjacency,
                        ParentCache=FabricParentCache,
                    )
                    TemporaryRejections = {}
                    TemporaryConflicts = set()
                    RepresentativeContract = min(StateContracts)
                    Variant = _BuildNetVariant(
                        LocalProblems[RepresentativeContract],
                        Signal,
                        Domains,
                        tuple(Candidates),
                        tuple(EgressPath),
                        TemporaryRejections,
                        TemporaryConflicts,
                        FabricAdjacency,
                        FabricParentCache,
                        ImmutableAccessConflictCache,
                        LocalClaimsBySignal,
                        NetVariantConstructionCache,
                        RouteClaimsConstructionCache,
                        TreeRepeaterSubproblemCache,
                        TreeRepeaterCacheStatistics,
                        PrecomputedFabricSubtree=FabricSubtree,
                    )
                    NetVariantBuildCount += 1
                    CanonicalStateCount += 1
                    ProcessedStates.add(StateKeyValue)
                    for Contract in StateContracts:
                        CombinationKeysByContract[Contract].add(CombinationKey)
                        for Reason, Count in TemporaryRejections.items():
                            RejectionsByContract[Contract][Reason] = (
                                RejectionsByContract[Contract].get(Reason, 0)
                                + Count
                            )
                        ImmutableConflictsByContract[Contract].update(
                            TemporaryConflicts
                        )
                        if Variant is not None:
                            VariantsByContract[Contract].setdefault(
                                Variant.NetFingerprint,
                                Variant,
                            )

    NetVariantDiscoveryStateCache.pop(StateKey, None)
    Results = dict(CompletedPortfolios)
    for Contract in MissingContracts:
        LocalProblem = LocalProblems[Contract]
        EnumeratedVariants = tuple(
            VariantsByContract[Contract][Fingerprint]
            for Fingerprint in sorted(VariantsByContract[Contract])
        )
        CachedValue = (
            EnumeratedVariants,
            len(CombinationKeysByContract[Contract]),
            dict(RejectionsByContract[Contract]),
            frozenset(ImmutableConflictsByContract[Contract]),
            Origin,
        )
        VariantPortfolioCache[(LocalProblem.ProblemFingerprint, Signal)] = (
            CachedValue
        )
        if not ImmutableConflictsByContract[Contract]:
            VariantPortfolioCache[(
                "component-net-translation-v1",
                StructuralFingerprints[Contract],
            )] = CachedValue
        Results[Contract] = CompleteComponentNetVariantPortfolioResult(
            Status="complete",
            Complete=True,
            Variants=EnumeratedVariants,
            DomainFingerprint=_StableFingerprint((
                "complete-component-net-variant-portfolio-v1",
                StructuralFingerprints[Contract],
                Signal,
            )),
            Detail="complete shared multi-contract net portfolio compiled",
            Diagnostics={
                "PortfolioCacheHit": False,
                "LocalOnly": True,
                "MultiContract": True,
                "SharedDomainComplete": True,
                "AccessCombinationCount": len(
                    CombinationKeysByContract[Contract]
                ),
                "TemplateSearchEntered": False,
            },
        )
    return CompleteComponentNetMultiPortfolioResult(
        Complete=True,
        PortfoliosByContract=tuple(sorted(Results.items())),
        DomainFingerprint=DomainFingerprint,
        CanonicalStateCount=CanonicalStateCount,
        NetVariantBuildCount=NetVariantBuildCount,
        Detail="complete shared multi-contract portfolio domain exhausted",
        Diagnostics={
            "SolverCallCount": 1,
            "ContractCount": len(OrderedPorts),
            "PreviouslyCachedContractCount": len(CompletedPortfolios),
            "SharedDomainComplete": True,
            "ResumedCanonicalStateCount": len(
                PriorState.get("ProcessedStates", ())
            ),
        },
    )


def EvaluateCompleteOpposingNetAccessPair(
    Problem: ComponentRoutingProblem,
    *,
    CurrentSignal: str,
    CompleteSignal: str,
    CurrentLocalContractFingerprint: str,
    CompleteLocalContractFingerprint: str,
    CompleteVariants: tuple[RoutedComponentNet, ...],
    CompleteVariantDomainComplete: bool,
    DeadlineSeconds: float | None,
    DomainFingerprint: str | None = None,
    RowContext: CompleteOpposingNetAccessRowContext | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ProofCache: dict[str, CompleteOpposingNetAccessPairResult] | None = None,
) -> CompleteOpposingNetAccessPairResult:
    """Decide the access predicate without global routing or template search.

    ``CompleteVariants`` must be the complete net portfolio for the selected
    ``CompleteSignal`` local contract.  The oracle checks whether at least one
    such net leaves mutually connected, claim-compatible terminal access for
    the exact ``CurrentSignal`` contract.  Reserved global corridors are
    intentionally absent from both the predicate and its identity.
    """
    CurrentSignal = str(CurrentSignal)
    CompleteSignal = str(CompleteSignal)
    if (
        not CurrentSignal
        or not CompleteSignal
        or CurrentSignal == CompleteSignal
    ):
        raise ValueError("opposing-net access requires two distinct signals")
    PortsBySignal = {
        str(Port.Signal): Port
        for Port in (
            Problem.Interface.PhysicalPortReservations
            if Problem.Interface is not None
            else ()
        )
    }
    CurrentPort = PortsBySignal.get(CurrentSignal)
    CompletePort = PortsBySignal.get(CompleteSignal)
    if CurrentPort is None or CompletePort is None:
        raise ValueError("opposing-net access requires exact physical ports")
    ExpectedCurrentContractFingerprint = (
        _PhysicalPortLocalContractFingerprint(CurrentPort)
    )
    ExpectedCompleteContractFingerprint = (
        _PhysicalPortLocalContractFingerprint(CompletePort)
    )
    if (
        CurrentLocalContractFingerprint
        != ExpectedCurrentContractFingerprint
        or CompleteLocalContractFingerprint
        != ExpectedCompleteContractFingerprint
    ):
        raise ValueError(
            "opposing-net access local contract fingerprint mismatch"
        )
    CurrentLocalContractFingerprint = ExpectedCurrentContractFingerprint
    CompleteLocalContractFingerprint = ExpectedCompleteContractFingerprint
    if (
        _PhysicalPortLocalContractFingerprint(CurrentPort)
        != CurrentLocalContractFingerprint
        or _PhysicalPortLocalContractFingerprint(CompletePort)
        != CompleteLocalContractFingerprint
    ):
        raise ValueError(
            "opposing-net access local contract identity mismatch"
        )
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == CurrentSignal
    )
    Origin = _ComponentOrigin(Problem)
    CurrentCandidateFingerprints = frozenset(
        getattr(CurrentPort, "OwnedCandidateFingerprints", ())
    )
    ImmutableForeignClaims = tuple(
        (str(Claim.Signal), Claim.Claims)
        for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims)
        if Claim.Signal not in Problem.ComponentSignals
    )

    def CandidateIdentity(
        Candidate: ComponentTerminalAccessCandidate,
    ) -> tuple[object, ...]:
        return (
            Candidate.CandidateFingerprint,
            _NormalizePosition(Candidate.Attachment, Origin),
            tuple(
                _NormalizePosition(Position, Origin)
                for Position in Candidate.Path
            ),
            _NormalizeClaims(Candidate.Claims, Origin),
            Candidate.Layer,
            Candidate.Cost,
        )

    DomainFingerprint = str(DomainFingerprint or "") or _StableFingerprint((
        "complete-opposing-net-access-pair-domain-v1",
        Problem.Fabric.FabricFingerprint,
        tuple(sorted(
            _NormalizePosition(Node, Origin)
            for Node in Problem.Fabric.Nodes
        )),
        tuple(sorted(
            _NormalizedEdge(
                _NormalizePosition(First, Origin),
                _NormalizePosition(Second, Origin),
            )
            for First, Second in Problem.Fabric.Edges
        )),
        CurrentSignal,
        CurrentLocalContractFingerprint,
        CompleteSignal,
        CompleteLocalContractFingerprint,
        tuple(sorted(
            (
                Domain.TerminalRole,
                Domain.TerminalFingerprint,
                bool(getattr(Domain, "Complete", True)),
                tuple(sorted(
                    CandidateIdentity(Candidate)
                    for Candidate in Domain.Candidates
                    if (
                        not CurrentCandidateFingerprints
                        or Candidate.CandidateFingerprint
                        in CurrentCandidateFingerprints
                    )
                )),
            )
            for Domain in Domains
        )),
        tuple(sorted(
            (
                Signal,
                _NormalizeClaims(Claims, Origin),
            )
            for Signal, Claims in ImmutableForeignClaims
        )),
        tuple(
            (
                Variant.NetFingerprint,
                tuple(sorted(
                    _NormalizePosition(Node, Origin)
                    for Node in Variant.Nodes
                )),
                _NormalizeClaims(Variant.Claims, Origin),
            )
            for Variant in sorted(
                CompleteVariants,
                key=lambda Value: Value.NetFingerprint,
            )
        ),
        bool(CompleteVariantDomainComplete),
        Problem.MaximumPowerDistance,
        getattr(Problem.ResourceGraph, "GraphVersion", None),
        type(getattr(Problem.ResourceGraph, "Technology", None)).__qualname__,
        getattr(
            getattr(Problem.ResourceGraph, "Technology", None),
            "TechnologyVersion",
            None,
        ),
        repr(getattr(Problem.ResourceGraph, "Technology", None)),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(Problem.ResourceGraph, "ActualBlocks", ())
        )),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(
                Problem.ResourceGraph,
                "ElectricalBlocks",
                (),
            )
        )),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(Problem.ResourceGraph, "SolidBlocks", ())
        )),
    ))
    if ProofCache is not None and DomainFingerprint in ProofCache:
        return ProofCache[DomainFingerprint]

    StartedAt = monotonic()
    ExpansionCount = 0

    def Incomplete(Detail: str) -> CompleteOpposingNetAccessPairResult:
        return CompleteOpposingNetAccessPairResult(
            Status="incomplete",
            Complete=False,
            Feasible=None,
            DomainFingerprint=DomainFingerprint,
            ProofFingerprint="",
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
            CurrentLocalContractFingerprint=(
                CurrentLocalContractFingerprint
            ),
            CompleteLocalContractFingerprint=(
                CompleteLocalContractFingerprint
            ),
            ExpansionCount=ExpansionCount,
            Detail=Detail,
        )

    if (
        not Problem.DomainComplete
        or not Domains
        or any(not getattr(Domain, "Complete", True) for Domain in Domains)
        or not CompleteVariantDomainComplete
    ):
        return Incomplete("pair access input domain is incomplete")
    if not CompleteVariants:
        Result = CompleteOpposingNetAccessPairResult(
            Status="architectural-unsatisfiable",
            Complete=True,
            Feasible=False,
            DomainFingerprint=DomainFingerprint,
            ProofFingerprint=_StableFingerprint((
                "complete-opposing-net-access-pair-proof-v1",
                DomainFingerprint,
                "empty-complete-variant-domain",
            )),
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
            CurrentLocalContractFingerprint=(
                CurrentLocalContractFingerprint
            ),
            CompleteLocalContractFingerprint=(
                CompleteLocalContractFingerprint
            ),
            ExpansionCount=ExpansionCount,
            Detail="complete opposing-net variant domain is empty",
        )
        if ProofCache is not None:
            ProofCache[DomainFingerprint] = Result
        return Result
    if DeadlineSeconds is not None and DeadlineSeconds <= 0:
        return Incomplete("pair access deadline expired")

    CandidateDomains = tuple(
        tuple(
            Candidate
            for Candidate in Domain.Candidates
            if (
                (
                    not CurrentCandidateFingerprints
                    or Candidate.CandidateFingerprint
                    in CurrentCandidateFingerprints
                )
                and not any(
                    ComponentClaimsConflict(Candidate.Claims, Claims)
                    for _Signal, Claims in ImmutableForeignClaims
                )
            )
        )
        for Domain in Domains
    )
    if any(not Candidates for Candidates in CandidateDomains):
        Result = CompleteOpposingNetAccessPairResult(
            Status="architectural-unsatisfiable",
            Complete=True,
            Feasible=False,
            DomainFingerprint=DomainFingerprint,
            ProofFingerprint=_StableFingerprint((
                "complete-opposing-net-access-pair-proof-v1",
                DomainFingerprint,
                "empty-current-access-domain",
            )),
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
            CurrentLocalContractFingerprint=(
                CurrentLocalContractFingerprint
            ),
            CompleteLocalContractFingerprint=(
                CompleteLocalContractFingerprint
            ),
            ExpansionCount=ExpansionCount,
            Detail="one exact current access domain has no legal candidate",
        )
        if ProofCache is not None:
            ProofCache[DomainFingerprint] = Result
        return Result

    ExpectedVariantFingerprints = tuple(
        Variant.NetFingerprint
        for Variant in sorted(
            CompleteVariants,
            key=lambda Value: Value.NetFingerprint,
        )
    )
    if RowContext is None:
        RowContext = BuildCompleteOpposingNetAccessRowContext(
            Problem,
            CompleteVariants,
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
        )
    if (
        RowContext.FabricFingerprint != Problem.Fabric.FabricFingerprint
        or RowContext.CompleteVariantFingerprints
        != ExpectedVariantFingerprints
        or (
            RowContext.CurrentSignal
            and RowContext.CurrentSignal != CurrentSignal
        )
        or (
            RowContext.CompleteSignal
            and RowContext.CompleteSignal != CompleteSignal
        )
        or (
            RowContext.CurrentAccessDomainFingerprint
            and RowContext.CurrentAccessDomainFingerprint
            != _OpposingRowCurrentAccessDomainFingerprint(
                Problem,
                CurrentSignal,
            )
        )
    ):
        raise ValueError("opposing-net access row context identity mismatch")
    ComponentMapByVariant = (
        RowContext.ComponentMapByVariant
        if RowContext.ComponentMapByVariant
        else {
            Fingerprint: dict(Values)
            for Fingerprint, Values in RowContext.ComponentByNodeByVariant
        }
    )
    CompatibleComponentsByVariant = (
        RowContext.CompatibleComponentByCandidateFingerprintByVariant
        if (
            RowContext.CurrentSignal == CurrentSignal
            and RowContext.CompleteSignal == CompleteSignal
        )
        else {}
    )
    SupportingVariants = []
    for Variant in sorted(
        CompleteVariants,
        key=lambda Value: Value.NetFingerprint,
    ):
        if (
            DeadlineSeconds is not None
            and monotonic() - StartedAt >= DeadlineSeconds
        ):
            return Incomplete("pair access deadline expired")
        ExpansionCount += 1
        if WorkCheck is not None:
            WorkCheck({
                "Stage": "complete-opposing-net-access-pair",
                "ExpansionCount": ExpansionCount,
                "CompleteVariantCount": len(CompleteVariants),
                "CurrentSignal": CurrentSignal,
                "CompleteSignal": CompleteSignal,
            })
        ComponentByNode = ComponentMapByVariant[Variant.NetFingerprint]
        CompatibleComponents = CompatibleComponentsByVariant.get(
            Variant.NetFingerprint
        )
        CommonComponents: set[int] | None = None
        for Candidates in CandidateDomains:
            CandidateComponents = (
                {
                    CompatibleComponents[
                        Candidate.CandidateFingerprint
                    ]
                    for Candidate in Candidates
                    if Candidate.CandidateFingerprint
                    in CompatibleComponents
                }
                if CompatibleComponents is not None
                else {
                    ComponentByNode[Candidate.Attachment]
                    for Candidate in Candidates
                    if (
                        Candidate.Attachment in ComponentByNode
                        and ComponentClaimsCompatibleForOwners(
                            CurrentSignal,
                            Candidate.Claims,
                            CompleteSignal,
                            Variant.Claims,
                        )
                    )
                }
            )
            if CommonComponents is None:
                CommonComponents = set(CandidateComponents)
            else:
                CommonComponents.intersection_update(CandidateComponents)
            if not CommonComponents:
                break
        if CommonComponents:
            SupportingVariants.append(Variant.NetFingerprint)
            break

    Feasible = bool(SupportingVariants)
    Result = CompleteOpposingNetAccessPairResult(
        Status="feasible" if Feasible else "architectural-unsatisfiable",
        Complete=True,
        Feasible=Feasible,
        DomainFingerprint=DomainFingerprint,
        ProofFingerprint=_StableFingerprint((
            "complete-opposing-net-access-pair-proof-v1",
            DomainFingerprint,
            tuple(SupportingVariants),
        )),
        CurrentSignal=CurrentSignal,
        CompleteSignal=CompleteSignal,
        CurrentLocalContractFingerprint=CurrentLocalContractFingerprint,
        CompleteLocalContractFingerprint=CompleteLocalContractFingerprint,
        SupportingCompleteVariantFingerprints=tuple(SupportingVariants),
        ExpansionCount=ExpansionCount,
        Detail=(
            "a complete opposing-net variant supports every access domain"
            if Feasible
            else "no complete opposing-net variant supports every access domain"
        ),
        Diagnostics={
            "CurrentAccessDomainSizes": [
                len(Values) for Values in CandidateDomains
            ],
            "CompleteVariantDomainComplete": True,
            "ReservedGlobalClaimsIgnored": True,
        },
    )
    if ProofCache is not None:
        ProofCache[DomainFingerprint] = Result
    return Result


def EvaluateCachedCompleteOpposingNetAccessPair(
    Problem: ComponentRoutingProblem,
    *,
    CurrentSignal: str,
    CompleteSignal: str,
    CurrentLocalContractFingerprint: str,
    CompleteLocalContractFingerprint: str,
    VariantPortfolioCache: dict[Any, Any],
    DeadlineSeconds: float | None,
    DomainFingerprint: str | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ProofCache: dict[str, CompleteOpposingNetAccessPairResult] | None = None,
) -> CompleteOpposingNetAccessPairResult:
    """Evaluate a pair using only an exhaustively cached complete portfolio."""
    Portfolio = GetCachedCompleteComponentNetVariantPortfolio(
        Problem,
        CompleteSignal,
        VariantPortfolioCache,
    )
    return EvaluateCompleteOpposingNetAccessPair(
        Problem,
        CurrentSignal=CurrentSignal,
        CompleteSignal=CompleteSignal,
        CurrentLocalContractFingerprint=CurrentLocalContractFingerprint,
        CompleteLocalContractFingerprint=CompleteLocalContractFingerprint,
        CompleteVariants=Portfolio.Variants,
        CompleteVariantDomainComplete=Portfolio.Complete,
        DeadlineSeconds=DeadlineSeconds,
        DomainFingerprint=DomainFingerprint,
        WorkCheck=WorkCheck,
        ProofCache=ProofCache,
    )


@dataclass(frozen=True)
class ExactComponentPortRealizabilityContext:
    """Signal-static identity and blockers shared by exact port probes."""

    Origin: Position3
    FabricStructuralFingerprint: str
    ImmutableIdentity: tuple[Any, ...]
    ImmutableClaims: tuple[tuple[str, RoutingResourceClaims], ...]
    StaticContractFingerprint: str
    CandidateIdentityCache: dict[
        tuple[
            tuple[
                ComponentTerminalAccessDomain,
                ComponentTerminalAccessCandidate,
            ],
            ...,
        ],
        str,
    ] = field(default_factory=dict, compare=False, repr=False)
    LocalPathIdentityCache: dict[
        tuple[Position3, ...], str
    ] = field(default_factory=dict, compare=False, repr=False)


MaximumStructuralPortRealizabilityCacheEntries = 65_536
_StructuralPortRealizabilityCache: dict[
    str, ExactComponentPortRealizabilityResult
] = {}


def ClearStructuralPortRealizabilityCache() -> None:
    """Clear translation-normalized exact port predicates for tests."""
    _StructuralPortRealizabilityCache.clear()


def BuildExactComponentPortRealizabilityContext(
    Problem: ComponentRoutingProblem,
    *,
    Signal: str,
    ReservedClaimsBySignal: tuple[
        tuple[str, RoutingResourceClaims], ...
    ] = (),
) -> ExactComponentPortRealizabilityContext:
    """Precompute the immutable half of a signal's port proof domain."""
    Origin = _ComponentOrigin(Problem)
    FabricStructuralFingerprint = _StableFingerprint((
        getattr(Problem.Fabric, "TopologyKind", ""),
        tuple(sorted(
            _NormalizePosition(Node, Origin)
            for Node in Problem.Fabric.Nodes
        )),
        tuple(sorted(
            tuple(sorted((
                _NormalizePosition(First, Origin),
                _NormalizePosition(Second, Origin),
            )))
            for First, Second in Problem.Fabric.Edges
        )),
    ))
    ComponentSignals = frozenset(Problem.ComponentSignals)
    RelevantClaimInputs = (
        *((str(Claim.Signal), Claim.Claims) for Claim in Problem.LocalClaims),
        *(
            (str(Claim.Signal), Claim.Claims)
            for Claim in Problem.ImmutableClaims
            if str(Claim.Signal) != Signal
        ),
        *(
            (Owner, Claims)
            for Owner, Claims in (
                *Problem.ReservedGlobalClaimsBySignal,
                *ReservedClaimsBySignal,
            )
            if Owner != Signal
        ),
    )
    ImmutableIdentity = tuple(sorted(
        (
            (
                "self"
                if Owner == Signal
                else "component-peer"
                if Owner in ComponentSignals
                else "foreign"
            ),
            _NormalizeClaims(Claims, Origin),
        )
        for Owner, Claims in RelevantClaimInputs
    ))
    ImmutableClaims = tuple(
        (str(Claim.Signal), Claim.Claims)
        for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims)
        if str(Claim.Signal) != Signal
    ) + tuple(
        (Owner, Claims)
        for Owner, Claims in (
            *Problem.ReservedGlobalClaimsBySignal,
            *ReservedClaimsBySignal,
        )
        if Owner != Signal
    )
    return ExactComponentPortRealizabilityContext(
        Origin=Origin,
        FabricStructuralFingerprint=FabricStructuralFingerprint,
        ImmutableIdentity=ImmutableIdentity,
        ImmutableClaims=ImmutableClaims,
        StaticContractFingerprint=_StableFingerprint((
            "exact-component-port-static-v2",
            FabricStructuralFingerprint,
            ImmutableIdentity,
            Problem.MaximumPowerDistance,
            getattr(Problem.ResourceGraph, "GraphVersion", None),
            type(getattr(
                Problem.ResourceGraph,
                "Technology",
                None,
            )).__qualname__,
        )),
    )


def BuildExactComponentPortRealizabilityFingerprint(
    Problem: ComponentRoutingProblem,
    *,
    Signal: str,
    Domains: tuple[ComponentTerminalAccessDomain, ...],
    Candidates: tuple[ComponentTerminalAccessCandidate, ...],
    LocalPath: tuple[Position3, ...],
    ReservedClaimsBySignal: tuple[
        tuple[str, RoutingResourceClaims], ...
    ] = (),
    Context: ExactComponentPortRealizabilityContext | None = None,
) -> str:
    """Identify translation-equivalent exact single-port proof inputs."""
    Context = Context or BuildExactComponentPortRealizabilityContext(
        Problem,
        Signal=Signal,
        ReservedClaimsBySignal=ReservedClaimsBySignal,
    )
    Origin = Context.Origin

    def ClaimIdentity(
        Claims: RoutingResourceClaims,
    ) -> tuple[tuple[Position3, ...], ...]:
        return _NormalizeClaims(Claims, Origin)

    CandidateCacheKey = tuple(
        (Domain, Candidate)
        for Domain, Candidate in zip(Domains, Candidates)
    )
    CandidateIdentityFingerprint = Context.CandidateIdentityCache.get(
        CandidateCacheKey
    )
    if CandidateIdentityFingerprint is None:
        CandidateIdentity = tuple(
            (
                Domain.TerminalRole,
                _NormalizePosition(Domain.Terminal, Origin),
                _NormalizePosition(Candidate.Attachment, Origin),
                tuple(
                    _NormalizePosition(Value, Origin)
                    for Value in Candidate.Path
                ),
                ClaimIdentity(Candidate.Claims),
                Candidate.Layer,
            )
            for Domain, Candidate in zip(Domains, Candidates)
        )
        CandidateIdentityFingerprint = _StableFingerprint((
            "exact-component-port-candidates-v2",
            len(Domains),
            len(Candidates),
            CandidateIdentity,
        ))
        Context.CandidateIdentityCache[
            CandidateCacheKey
        ] = CandidateIdentityFingerprint
    LocalPathKey = tuple(LocalPath)
    LocalPathIdentityFingerprint = Context.LocalPathIdentityCache.get(
        LocalPathKey
    )
    if LocalPathIdentityFingerprint is None:
        LocalPathIdentity = tuple(
            _NormalizePosition(Value, Origin)
            for Value in LocalPathKey
        )
        LocalPathIdentityFingerprint = _StableFingerprint((
            "exact-component-port-local-path-v2",
            LocalPathIdentity,
        ))
        Context.LocalPathIdentityCache[
            LocalPathKey
        ] = LocalPathIdentityFingerprint
    return _StableFingerprint((
        "exact-component-port-realizability-v2",
        Context.StaticContractFingerprint,
        CandidateIdentityFingerprint,
        LocalPathIdentityFingerprint,
    ))


def EvaluateExactComponentPortRealizability(
    Problem: ComponentRoutingProblem,
    *,
    Signal: str,
    Domains: tuple[ComponentTerminalAccessDomain, ...],
    Candidates: tuple[ComponentTerminalAccessCandidate, ...],
    LocalPath: tuple[Position3, ...],
    ReservedClaimsBySignal: tuple[
        tuple[str, RoutingResourceClaims], ...
    ] = (),
    RealizabilityCache: dict[
        str, ExactComponentPortRealizabilityResult
    ] | None = None,
    FabricAdjacency: dict[
        Position3, set[Position3]
    ] | None = None,
    FabricParentCache: dict[
        Position3,
        dict[Position3, Position3 | None],
    ] | None = None,
    ImmutableAccessConflictCache: dict[
        tuple[str, str, tuple[Position3, ...]],
        frozenset[str],
    ] | None = None,
    LocalClaimsBySignal: dict[
        str, tuple[Any, ...]
    ] | None = None,
    NetVariantTopologyCache: dict[
        tuple[
            str,
            frozenset[Position3],
            frozenset[RoutingEdge],
            tuple[Position3, ...],
        ],
        RoutedComponentNet | None,
    ] | None = None,
    RouteClaimsCache: dict[
        frozenset[Position3],
        RoutingResourceClaims,
    ] | None = None,
    TreeRepeaterSubproblemCache: dict[
        tuple[int, int, str],
        tuple[tuple[Position3, str], ...] | None,
    ] | None = None,
    TreeRepeaterCacheStatistics: dict[str, int] | None = None,
    Context: ExactComponentPortRealizabilityContext | None = None,
    UseStructuralCache: bool = False,
) -> ExactComponentPortRealizabilityResult:
    """Prove one exact access/seam contract without a multi-net solve."""
    ContractFingerprint = (
        BuildExactComponentPortRealizabilityFingerprint(
            Problem,
            Signal=Signal,
            Domains=Domains,
            Candidates=Candidates,
            LocalPath=LocalPath,
            ReservedClaimsBySignal=ReservedClaimsBySignal,
            Context=Context,
        )
    )
    Cached = (
        RealizabilityCache.get(ContractFingerprint)
        if RealizabilityCache is not None
        else None
    )
    CacheScope = "local"
    if Cached is None and UseStructuralCache:
        Cached = _StructuralPortRealizabilityCache.get(
            ContractFingerprint
        )
        CacheScope = "structural"
    if Cached is not None:
        if RealizabilityCache is not None:
            RealizabilityCache[ContractFingerprint] = Cached
        return replace(
            Cached,
            Diagnostics={
                **(Cached.Diagnostics or {}),
                "CacheHit": True,
                "CacheScope": CacheScope,
            },
        )
    FabricNodes = frozenset(Problem.Fabric.Nodes)

    def CandidateMatchesDomain(
        Domain: ComponentTerminalAccessDomain,
        Candidate: ComponentTerminalAccessCandidate,
    ) -> bool:
        if Candidate in Domain.Candidates:
            return True
        if (
            Problem.ResourceGraph is None
            or not Candidate.Path
            or Candidate.Path[0] != Domain.Terminal
            or Candidate.Attachment not in FabricNodes
            or Candidate.Path[-1] != Candidate.Attachment
            or any(
                Problem.ResourceGraph.BuildPrimitive(First, Second) is None
                for First, Second in zip(
                    Candidate.Path,
                    Candidate.Path[1:],
                )
            )
        ):
            return False
        return (
            Problem.ResourceGraph.BuildRouteClaims(
                frozenset(Candidate.Path)
            )
            == Candidate.Claims
        )

    if (
        len(Domains) != len(Candidates)
        or not Domains
        or not LocalPath
        or any(
            Domain.Signal != Signal
            or not CandidateMatchesDomain(Domain, Candidate)
            for Domain, Candidate in zip(Domains, Candidates)
        )
    ):
        Result = ExactComponentPortRealizabilityResult(
            Realizable=False,
            ContractFingerprint=ContractFingerprint,
            Detail="exact port contract is incomplete or inconsistent",
            Diagnostics={
                "CacheHit": False,
                "RejectionCounts": {
                    "invalid-exact-port-contract": 1,
                },
                "ImmutableConflictSignals": [],
            },
        )
    else:
        RejectionCounts: dict[str, int] = {}
        ImmutableConflictSignals: set[str] = set()
        ProbeProblem = replace(
            Problem,
            ReservedGlobalClaimsBySignal=tuple((
                *Problem.ReservedGlobalClaimsBySignal,
                *ReservedClaimsBySignal,
            )),
        )
        Net = _BuildNetVariant(
            ProbeProblem,
            Signal,
            Domains,
            Candidates,
            LocalPath,
            RejectionCounts,
            ImmutableConflictSignals,
            FabricAdjacency=FabricAdjacency,
            FabricParentCache=FabricParentCache,
            ImmutableAccessConflictCache=(
                ImmutableAccessConflictCache
            ),
            LocalClaimsBySignal=LocalClaimsBySignal,
            NetVariantTopologyCache=NetVariantTopologyCache,
            RouteClaimsCache=RouteClaimsCache,
            TreeRepeaterSubproblemCache=(
                TreeRepeaterSubproblemCache
            ),
            TreeRepeaterCacheStatistics=(
                TreeRepeaterCacheStatistics
            ),
        )
        Context = Context or BuildExactComponentPortRealizabilityContext(
            Problem,
            Signal=Signal,
            ReservedClaimsBySignal=ReservedClaimsBySignal,
        )
        ImmutableClaims = Context.ImmutableClaims
        RouteBlockers = tuple(sorted(
            Owner
            for Owner, Claims in ImmutableClaims
            if (
                Net is not None
                and not ComponentClaimsCompatibleForOwners(
                    Signal,
                    Net.Claims,
                    Owner,
                    Claims,
                )
            )
        ))
        if Net is not None and RouteBlockers:
            RejectionCounts["immutable-route-conflict"] = (
                RejectionCounts.get(
                    "immutable-route-conflict",
                    0,
                )
                + 1
            )
            ImmutableConflictSignals.update(RouteBlockers)
            Net = None
        Result = ExactComponentPortRealizabilityResult(
            Realizable=Net is not None,
            ContractFingerprint=ContractFingerprint,
            NetFingerprint=(
                Net.NetFingerprint if Net is not None else ""
            ),
            Detail=(
                ""
                if Net is not None
                else "exact port contract has no powered legal subtree"
            ),
            Diagnostics={
                "CacheHit": False,
                "RejectionCounts": dict(sorted(
                    RejectionCounts.items()
                )),
                "ImmutableConflictSignals": sorted(
                    ImmutableConflictSignals
                ),
                "FabricFingerprint": (
                    Problem.Fabric.FabricFingerprint
                ),
                "CandidateCount": len(Candidates),
                "LocalPathLength": len(LocalPath),
            },
        )
    if RealizabilityCache is not None:
        RealizabilityCache[ContractFingerprint] = Result
    if UseStructuralCache:
        _StructuralPortRealizabilityCache[ContractFingerprint] = Result
        while (
            len(_StructuralPortRealizabilityCache)
            > MaximumStructuralPortRealizabilityCacheEntries
        ):
            _StructuralPortRealizabilityCache.pop(
                next(iter(_StructuralPortRealizabilityCache))
            )
    return Result

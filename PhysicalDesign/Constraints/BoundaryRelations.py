"""Exact physical-boundary domains and mandatory portal relations.

The functions in this module compile immutable portal-domain evidence without
owning component solving or authoritative flow control.  That neutral owner is
the dependency seam that lets both higher routing layers share certificates
without an import cycle.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import Any, Callable, Iterable, Mapping

from ..Contracts.Component import PhysicalComponentPortReservation
from ..Contracts.Core import Position2, Position3
from ..Contracts.PhysicalInterface import (
    PreparedPhysicalComponentPortFactorDomain,
)
from ..Contracts.Results import RoutingResources
from ..Runtime.Reliability import BuildStableFingerprint
from ..Resources.ResourceGraph import FindSelfClaimConflicts, IndexedRoutingResourceGraph, LocalRouteClaim, NormalizeRoutingEdge, PinAccessPortal, RoutingResourceClaims, RoutingResourceGraph
from .PhysicalClaims import (
    ClaimConflictPositions,
    ComponentClaimsConflict,
    PortalTupleConflictsWithFrozenComponentClaims,
)
from .PortalConstraints import (
    ExactPortalConstraintChoice,
    ExactPortalConstraintVariableDomain,
    ExtractSparseExactPortalConstraintFactors,
    ProjectExactPortalConstraintFactors,
)


def _CountBends(Path: tuple[Position3, ...]) -> int:
    Directions = [
        (
            Second[0] - First[0],
            Second[1] - First[1],
            Second[2] - First[2],
        )
        for First, Second in zip(Path, Path[1:])
    ]
    return sum(
        First != Second
        for First, Second in zip(Directions, Directions[1:])
    )


def _ClaimsConflict(
    FirstSignal: str,
    First: Any,
    SecondSignal: str,
    Second: Any,
) -> bool:
    """Preserve the exact historical relation-compiler call contract."""
    del FirstSignal, SecondSignal
    return ComponentClaimsConflict(First, Second)


def _Fingerprint(Value: object) -> str:
    """Preserve component-boundary fingerprint compatibility exactly."""
    return sha256(repr(Value).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class MandatoryPortalPairFeasibilityCertificate:
    """Exact feasibility proof for two fixed-access portal factor domains."""

    DomainFingerprint: str
    Signals: tuple[str, str]
    Complete: bool
    Feasible: bool | None
    WitnessPortalIds: tuple[tuple[str, tuple[str, ...]], ...] = ()
    ConflictPositions: frozenset[Position3] = frozenset()
    ConflictFingerprint: str = ""
    ExpansionCount: int = 0
    MemoizedStateHitCount: int = 0
    DependencySignals: tuple[str, ...] = ()


@dataclass(frozen=True)
class MandatoryPortalFactorClaimState:
    """One unique self-legal aggregate claim state for an aperture factor."""

    StateFingerprint: str
    PortalIds: tuple[str, ...]
    Claims: Any = field(compare=False, repr=False)


@dataclass(frozen=True)
class MandatoryPortalFactorFeasibilityCertificate:
    """Complete quotient of one aperture factor's internal portal choices."""

    FactorDomainFingerprint: str
    Signal: str
    Complete: bool
    StateDomainFingerprint: str = ""
    States: tuple[MandatoryPortalFactorClaimState, ...] = ()
    ConflictPositions: frozenset[Position3] = frozenset()
    DependencySignals: tuple[str, ...] = ()
    ExpansionCount: int = 0
    MemoizedStateHitCount: int = 0


@dataclass(frozen=True)
class PhysicalBoundaryMandatoryPortalFactorDomain:
    """Complete mandatory portal factors for one prepared aperture option."""

    DomainFingerprint: str
    PreparedDomainFingerprint: str
    PlacementFingerprint: str
    ComponentGraphFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    GuideFingerprint: str
    ExteriorFabricSetFingerprint: str
    ExteriorRegionFingerprint: str
    Signal: str
    ApertureOptionFingerprint: str
    ApertureContractFingerprint: str
    GlobalContractFingerprint: str
    ChannelContractFingerprint: str
    ChannelLayer: int
    FixedAccessNodes: frozenset[Position3]
    CommonFixedAccessNodes: frozenset[Position3]
    OptionOverlayNodes: frozenset[Position3]
    OptionOverlayPortalDomainIndex: int
    PortalDomains: tuple[tuple[PinAccessPortal, ...], ...]
    GenericPortalDomainFingerprint: str
    PortalRequestDomainFingerprint: str
    PortalGuideInputFingerprint: str
    FrozenComponentClaimsFingerprint: str
    FrozenComponentClaims: tuple[LocalRouteClaim, ...] = field(
        compare=False,
        repr=False,
    )
    Complete: bool = False


@dataclass(frozen=True)
class PhysicalBoundaryMandatoryPortalPairOptionCertificate:
    """One exact aperture-pair binding and its portal feasibility proof."""

    FirstApertureContractFingerprint: str
    SecondApertureContractFingerprint: str
    Certificate: MandatoryPortalPairFeasibilityCertificate


@dataclass(frozen=True)
class PhysicalBoundaryMandatoryPortalPairRelation:
    """Complete aperture cross-product relation for one signal pair."""

    RelationFingerprint: str
    PreparedDomainFingerprint: str
    Signals: tuple[str, str]
    OptionDomainFingerprintsBySignal: tuple[
        tuple[str, tuple[str, ...]], ...
    ]
    ExpectedOptionPairCount: int
    Certificates: tuple[
        PhysicalBoundaryMandatoryPortalPairOptionCertificate, ...
    ]
    UnsatisfiableApertureClauses: tuple[
        frozenset[tuple[str, str]], ...
    ]
    Complete: bool
    ForeignDependencyCertificateCount: int = 0
    FactorCertificateCount: int = 0
    FactorStateCount: int = 0
    UniqueClaimStateCountsBySignal: tuple[tuple[str, int], ...] = ()
    FactorExpansionCount: int = 0
    CompatibilityIndexStatePairUpperBound: int = 0


@dataclass(frozen=True)
class PhysicalBoundaryMandatoryPortalPairStateIndex:
    """Frozen exact claim compatibility shared by relation work slices."""

    IndexFingerprint: str
    RelationFingerprint: str
    FactorStateDomainFingerprints: tuple[tuple[str, str], ...]
    FactorCertificates: tuple[
        MandatoryPortalFactorFeasibilityCertificate, ...
    ] = field(
        compare=False,
        repr=False,
    )
    FirstStates: tuple[MandatoryPortalFactorClaimState, ...] = field(
        compare=False,
        repr=False,
    )
    SecondStates: tuple[MandatoryPortalFactorClaimState, ...] = field(
        compare=False,
        repr=False,
    )
    CompatibleSecondMasksByFirstState: tuple[tuple[str, int], ...] = field(
        compare=False,
        repr=False,
    )
    SecondMasksByFactorFingerprint: tuple[tuple[str, int], ...] = field(
        compare=False,
        repr=False,
    )


def BuildPhysicalBoundaryMandatoryPortalFactorDomains(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    Profiles: Mapping[str, Any],
    RawPortalCache: RawPortalGeometryCache,
    ResourceGraph: Any,
    FrozenComponentClaims: Iterable[LocalRouteClaim] = (),
) -> tuple[PhysicalBoundaryMandatoryPortalFactorDomain, ...]:
    """Bind complete raw portal factors to every prepared aperture option.

    This is a projection of the authoritative portal generator's completed
    raw domain.  It does not generate routes or infer negative reachability.
    Each component attachment is replaced by the same exact singleton portal
    that a materialized assembly plan would inject later.
    """
    RawPortals = RawPortalCache.BuildPortalDictionary()
    CompletePortalKeys = frozenset(
        RawPortalCache.CompletePortalDomainKeys
    )
    RequestFingerprints = dict(
        RawPortalCache.PortalRequestDomainFingerprints
    )
    ChannelsBySignal = {
        Value.Signal: Value for Value in Preparation.ChannelReservations
    }
    AperturesBySignal = {
        str(Signal): tuple(Values)
        for Signal, Values in Preparation.ApertureFactorsBySignal
    }
    BoundariesBySignal = {
        str(Signal): tuple(Values)
        for Signal, Values in Preparation.BoundaryPortReservationsBySignal
    }
    FrozenClaims = tuple(FrozenComponentClaims)
    FrozenClaimsFingerprint = BuildStableFingerprint((
        "physical-boundary-mandatory-frozen-claims-v1",
        tuple(sorted(
            (
                str(Claim.Signal),
                tuple(sorted(map(str, Claim.Claims.ResourceIds))),
            )
            for Claim in FrozenClaims
        )),
    ))
    TechnologyFingerprint = str(getattr(
        Preparation.AccessCertificate,
        "TechnologyFingerprint",
        "",
    )) or BuildStableFingerprint(repr(getattr(
        ResourceGraph,
        "Technology",
        None,
    )))
    OwnedTerminalsBySignal: dict[str, frozenset[Position3]] = {}
    for OwnedDomain in Preparation.Problem.OwnedTerminalDomains:
        OwnedTerminalsBySignal[OwnedDomain.Signal] = frozenset((
            *OwnedTerminalsBySignal.get(
                OwnedDomain.Signal,
                frozenset(),
            ),
            OwnedDomain.Terminal,
        ))
    GenericPortalDomainCache: dict[
        tuple[str, Position3, int],
        tuple[tuple[PinAccessPortal, ...], str],
    ] = {}

    def SelectGenericPortalDomain(
        Signal: str,
        Terminal: Position3,
        ChannelLayer: int,
    ) -> tuple[tuple[PinAccessPortal, ...], str]:
        """Reuse one immutable raw portal factor across aperture options."""
        Key = (Signal, Terminal, ChannelLayer)
        Cached = GenericPortalDomainCache.get(Key)
        if Cached is not None:
            return Cached
        ValuesById = {
            Portal.PortalId: Portal
            for Layer in range(RawPortalCache.LayerCount)
            for Portal in RawPortals.get(
                (Signal, Terminal, Layer),
                (),
            )
        }
        Values = tuple(sorted(
            ValuesById.values(),
            key=lambda Portal: (
                abs(int(Portal.Layer) - ChannelLayer),
                int(Portal.Cost),
                int(Portal.Layer),
                str(Portal.PortalId),
            ),
        ))
        Fingerprint = BuildStableFingerprint((
            "physical-boundary-generic-portal-domain-v1",
            Signal,
            Terminal,
            ChannelLayer,
            tuple(
                (
                    Portal.PortalId,
                    tuple(Portal.Path),
                    tuple(sorted(map(
                        str,
                        Portal.Claims.ResourceIds,
                    ))),
                )
                for Portal in Values
            ),
        ))
        Result = (Values, Fingerprint)
        GenericPortalDomainCache[Key] = Result
        return Result

    Domains = []
    for Signal in sorted(BoundariesBySignal):
        Profile = Profiles.get(Signal)
        Channel = ChannelsBySignal.get(Signal)
        if Profile is None or Channel is None:
            continue
        ChannelLayer = int(Channel.Layer)
        ApertureByContract = {
            str(Value.ApertureContractFingerprint): Value
            for Value in AperturesBySignal.get(Signal, ())
        }
        for Boundary in sorted(
            BoundariesBySignal[Signal],
            key=lambda Value: (
                str(Value.ApertureContractFingerprint),
                str(Value.GlobalContractFingerprint),
                tuple(Value.Attachment),
            ),
        ):
            Aperture = ApertureByContract.get(
                str(Boundary.ApertureContractFingerprint)
            )
            if Aperture is None:
                continue
            ProjectedProfile = ProjectPhysicalComponentSignalGlobalProfile(
                Profile,
                OwnedTerminalsBySignal.get(Signal, frozenset()),
                Boundary,
            )
            if ProjectedProfile is None:
                continue
            Terminals = (
                ProjectedProfile.Root,
                *ProjectedProfile.Targets,
            )
            FixedAccessNodes = frozenset(
                Position
                for Path in (
                    ProjectedProfile.SourceAccessPath,
                    *(
                        ProjectedProfile.TargetAccessPaths[Target]
                        for Target in ProjectedProfile.Targets
                    ),
                )
                for Position in Path
            )
            RootIsCovered = Profile.Root in OwnedTerminalsBySignal.get(
                Signal,
                frozenset(),
            )
            OutsideTargets = tuple(
                Target for Target in Profile.Targets
                if Target not in OwnedTerminalsBySignal.get(
                    Signal,
                    frozenset(),
                )
            )
            CommonFixedAccessNodes = frozenset(
                Position
                for Path in (
                    *(
                        ()
                        if RootIsCovered
                        else (tuple(Profile.SourceAccessPath),)
                    ),
                    *(
                        tuple(Profile.TargetAccessPaths[Target])
                        for Target in OutsideTargets
                    ),
                )
                for Position in Path
            )
            ExactPath = tuple(Boundary.GlobalPath)
            ExactPortal = PinAccessPortal(
                PortalId=BuildPhysicalComponentGlobalPortalId(
                    Boundary,
                    ChannelLayer,
                ),
                Signal=Signal,
                Terminal=Boundary.Attachment,
                Layer=ChannelLayer,
                Path=ExactPath,
                Edges=frozenset(
                    NormalizeRoutingEdge(First, Second)
                    for First, Second in zip(
                        ExactPath,
                        ExactPath[1:],
                    )
                ),
                Claims=ResourceGraph.BuildRouteClaims(ExactPath),
                Length=len(ExactPath),
                BendCount=_CountBends(ExactPath),
                ViaCount=sum(
                    First[1] != Second[1]
                    for First, Second in zip(
                        ExactPath,
                        ExactPath[1:],
                    )
                ),
                Cost=len(ExactPath),
            )
            # Replace the matching synthesized terminal with the immutable
            # component seam.  The authoritative handoff validator requires
            # every attachment to be one of the profile's root/targets; an
            # absent attachment therefore makes this proof domain incomplete.
            PortalDomains = []
            PortalDomainFingerprints = []
            OptionOverlayPortalDomainIndex = -1
            ExpectedGenericKeys = set()
            for Terminal in Terminals:
                if Terminal == Boundary.Attachment:
                    OptionOverlayPortalDomainIndex = len(PortalDomains)
                    PortalDomains.append((ExactPortal,))
                    PortalDomainFingerprints.append(
                        BuildStableFingerprint((
                            "physical-boundary-exact-portal-domain-v1",
                            ExactPortal.PortalId,
                            tuple(ExactPortal.Path),
                            tuple(sorted(map(
                                str,
                                ExactPortal.Claims.ResourceIds,
                            ))),
                        ))
                    )
                    continue
                ExpectedGenericKeys.update(
                    (Signal, Terminal, Layer)
                    for Layer in range(RawPortalCache.LayerCount)
                )
                Values, Fingerprint = SelectGenericPortalDomain(
                    Signal,
                    Terminal,
                    ChannelLayer,
                )
                PortalDomains.append(Values)
                PortalDomainFingerprints.append(Fingerprint)
            GenericPortalDomainFingerprint = BuildStableFingerprint((
                "physical-boundary-generic-portal-domains-v1",
                Signal,
                ChannelLayer,
                tuple(sorted(CommonFixedAccessNodes)),
                tuple(
                    Fingerprint
                    for Index, Fingerprint
                    in enumerate(PortalDomainFingerprints)
                    if Index != OptionOverlayPortalDomainIndex
                ),
                FrozenClaimsFingerprint,
                Preparation.ResourceGraphFingerprint,
                TechnologyFingerprint,
            ))
            RequestFingerprint = str(
                RequestFingerprints.get(Signal, "")
            )
            Complete = bool(
                not RawPortalCache.RetainedPortfolioSliceLimited
                and RequestFingerprint
                and RawPortalCache.GuideInputFingerprint
                and RawPortalCache.ExteriorRegionFingerprint
                == Preparation.ExteriorRegionFingerprint
                and RawPortalCache.AuthoritativeResourceGraphFingerprint
                == Preparation.ResourceGraphFingerprint
                and ExpectedGenericKeys <= CompletePortalKeys
                and ExactPath
                and ExactPath[0] == Boundary.Attachment
                and Boundary.Attachment in frozenset(Terminals)
                and 0 <= ChannelLayer < int(RawPortalCache.LayerCount)
            )
            DomainFingerprint = BuildStableFingerprint((
                "physical-boundary-mandatory-portal-factor-v1",
                Preparation.DomainFingerprint,
                Preparation.PlacementFingerprint,
                Preparation.ComponentGraphFingerprint,
                Preparation.ResourceGraphFingerprint,
                TechnologyFingerprint,
                Preparation.GuideFingerprint,
                Preparation.ExteriorFabricSetFingerprint,
                Preparation.ExteriorRegionFingerprint,
                Signal,
                str(Aperture.ApertureOptionFingerprint),
                str(Boundary.ApertureContractFingerprint),
                str(Boundary.GlobalContractFingerprint),
                str(Boundary.ChannelContractFingerprint),
                ChannelLayer,
                tuple(sorted(FixedAccessNodes)),
                tuple(sorted(CommonFixedAccessNodes)),
                tuple(sorted(ExactPath)),
                OptionOverlayPortalDomainIndex,
                tuple(PortalDomainFingerprints),
                GenericPortalDomainFingerprint,
                RequestFingerprint,
                str(RawPortalCache.GuideInputFingerprint),
                FrozenClaimsFingerprint,
                Complete,
            ))
            Domains.append(PhysicalBoundaryMandatoryPortalFactorDomain(
                DomainFingerprint=DomainFingerprint,
                PreparedDomainFingerprint=Preparation.DomainFingerprint,
                PlacementFingerprint=Preparation.PlacementFingerprint,
                ComponentGraphFingerprint=(
                    Preparation.ComponentGraphFingerprint
                ),
                ResourceGraphFingerprint=(
                    Preparation.ResourceGraphFingerprint
                ),
                TechnologyFingerprint=TechnologyFingerprint,
                GuideFingerprint=Preparation.GuideFingerprint,
                ExteriorFabricSetFingerprint=(
                    Preparation.ExteriorFabricSetFingerprint
                ),
                ExteriorRegionFingerprint=(
                    Preparation.ExteriorRegionFingerprint
                ),
                Signal=Signal,
                ApertureOptionFingerprint=str(
                    Aperture.ApertureOptionFingerprint
                ),
                ApertureContractFingerprint=str(
                    Boundary.ApertureContractFingerprint
                ),
                GlobalContractFingerprint=str(
                    Boundary.GlobalContractFingerprint
                ),
                ChannelContractFingerprint=str(
                    Boundary.ChannelContractFingerprint
                ),
                ChannelLayer=ChannelLayer,
                FixedAccessNodes=FixedAccessNodes,
                CommonFixedAccessNodes=CommonFixedAccessNodes,
                OptionOverlayNodes=frozenset(ExactPath),
                OptionOverlayPortalDomainIndex=(
                    OptionOverlayPortalDomainIndex
                ),
                PortalDomains=tuple(PortalDomains),
                GenericPortalDomainFingerprint=(
                    GenericPortalDomainFingerprint
                ),
                PortalRequestDomainFingerprint=RequestFingerprint,
                PortalGuideInputFingerprint=str(
                    RawPortalCache.GuideInputFingerprint
                ),
                FrozenComponentClaimsFingerprint=(
                    FrozenClaimsFingerprint
                ),
                FrozenComponentClaims=FrozenClaims,
                Complete=Complete,
            ))
    return tuple(sorted(
        Domains,
        key=lambda Value: (
            Value.Signal,
            Value.ApertureContractFingerprint,
            Value.DomainFingerprint,
        ),
    ))


def PublishPhysicalBoundaryMandatoryPortalFactorDomains(
    Resources: RoutingResources,
    Domains: Iterable[PhysicalBoundaryMandatoryPortalFactorDomain],
) -> dict[str, object]:
    """Publish exact aperture factors, retaining completeness explicitly."""
    Values = tuple(Domains)
    for Value in Values:
        Resources.PhysicalBoundaryMandatoryPortalFactorDomainCache[(
            Value.PreparedDomainFingerprint,
            Value.Signal,
            Value.ApertureContractFingerprint,
        )] = Value
    return {
        "FactorDomainCount": len(Values),
        "CompleteFactorDomainCount": sum(
            int(Value.Complete) for Value in Values
        ),
        "SignalCount": len({Value.Signal for Value in Values}),
    }


def _BuildMandatoryPortalFactorClaimStateFingerprint(
    Signal: str,
    Claims: RoutingResourceClaims,
) -> str:
    """Identify an aggregate claim state independently of its witness path."""
    return BuildStableFingerprint((
        "mandatory-portal-factor-claim-state-v1",
        Signal,
        tuple(sorted(map(str, Claims.ResourceIds))),
    ))


def CompileMandatoryPortalFactorFeasibility(
    Factor: PhysicalBoundaryMandatoryPortalFactorDomain,
    ResourceGraph: Any,
    ShouldStop: Callable[[], bool] | None = None,
    SharedCache: dict[str, Any] | None = None,
) -> MandatoryPortalFactorFeasibilityCertificate:
    """Compile a seam over one shared, dominance-compressed generic frontier.

    ``RoutingResourceGraph.BuildRouteClaims`` is monotone under node union:
    wire, support, electrical, and primitive-air sets can only grow.  Thus a
    state whose four claim sets are componentwise supersets of another state
    at the same terminal prefix can never enable an external compatibility
    that the subset state cannot.  Retaining the subset is an exact
    existential quotient, including air created by cross-portal adjacency.
    """
    Signal = str(Factor.Signal)

    def BuildStateDomainFingerprint(
        States: Iterable[MandatoryPortalFactorClaimState],
    ) -> str:
        return BuildStableFingerprint((
            "mandatory-portal-factor-state-domain-v1",
            Factor.DomainFingerprint,
            tuple(State.StateFingerprint for State in States),
        ))

    Variables = tuple(sorted(
        (
            len(Domain),
            DomainIndex,
            tuple(sorted(Domain, key=lambda Value: Value.PortalId)),
        )
        for DomainIndex, Domain in enumerate(Factor.PortalDomains)
    ))
    if not Factor.Complete or not Variables or any(
        not Domain for _Size, _Index, Domain in Variables
    ):
        return MandatoryPortalFactorFeasibilityCertificate(
            FactorDomainFingerprint=Factor.DomainFingerprint,
            Signal=Signal,
            Complete=bool(Factor.Complete),
            StateDomainFingerprint=(
                BuildStateDomainFingerprint(())
                if Factor.Complete
                else ""
            ),
            DependencySignals=(Signal,),
        )
    ExactVariables = tuple(
        Value for Value in Variables
        if Value[1] == Factor.OptionOverlayPortalDomainIndex
    )
    if len(ExactVariables) != 1:
        return MandatoryPortalFactorFeasibilityCertificate(
            FactorDomainFingerprint=Factor.DomainFingerprint,
            Signal=Signal,
            Complete=False,
            DependencySignals=(Signal,),
        )
    ExactVariable = ExactVariables[0]
    GenericVariables = tuple(
        Value for Value in Variables if Value is not ExactVariable
    )
    InitialNodes = frozenset(Factor.CommonFixedAccessNodes)
    InitialClaims = ResourceGraph.BuildRouteClaims(InitialNodes)
    InitialSelfConflicts = FindSelfClaimConflicts({Signal: InitialClaims})
    if InitialSelfConflicts:
        return MandatoryPortalFactorFeasibilityCertificate(
            FactorDomainFingerprint=Factor.DomainFingerprint,
            Signal=Signal,
            Complete=True,
            StateDomainFingerprint=BuildStateDomainFingerprint(()),
            ConflictPositions=frozenset(
                Resource.Position for Resource in InitialSelfConflicts
            ),
            DependencySignals=(Signal,),
        )
    GenericDomainFingerprint = Factor.GenericPortalDomainFingerprint
    CachedGeneric = (
        SharedCache.get("generic:" + GenericDomainFingerprint)
        if SharedCache is not None
        else None
    )
    ConflictPositions: set[Position3] = set()
    DependencySignals = {Signal}
    ExpansionCount = 0
    MemoizedStateHitCount = 0
    Incomplete = False
    if CachedGeneric is not None:
        GenericStates = CachedGeneric
    else:
        # (nodes, portal ids by original domain index, exact claims)
        Frontier = ((InitialNodes, {}, InitialClaims),)

        def ClaimsSubset(
            FirstClaims: RoutingResourceClaims,
            SecondClaims: RoutingResourceClaims,
        ) -> bool:
            return bool(
                FirstClaims.WireCells <= SecondClaims.WireCells
                and FirstClaims.SupportCells <= SecondClaims.SupportCells
                and FirstClaims.RequiredAirCells
                <= SecondClaims.RequiredAirCells
                and FirstClaims.ElectricalCells
                <= SecondClaims.ElectricalCells
            )

        UseDominance = isinstance(ResourceGraph, RoutingResourceGraph)
        for _Size, DomainIndex, Domain in GenericVariables:
            NextByNodes = {}
            for Nodes, PortalIds, _Claims in Frontier:
                for Portal in Domain:
                    if ShouldStop is not None and ShouldStop():
                        Incomplete = True
                        break
                    ExpansionCount += 1
                    CandidateNodes = frozenset((*Nodes, *Portal.Path))
                    CandidateClaims = ResourceGraph.BuildRouteClaims(
                        CandidateNodes
                    )
                    if FindSelfClaimConflicts({Signal: CandidateClaims}):
                        continue
                    FrozenBlockers = (
                        PortalTupleConflictsWithFrozenComponentClaims(
                            Signal,
                            CandidateClaims,
                            Factor.FrozenComponentClaims,
                        )
                    )
                    if FrozenBlockers:
                        DependencySignals.update(map(str, FrozenBlockers))
                        continue
                    CandidatePortalIds = dict(PortalIds)
                    CandidatePortalIds[DomainIndex] = Portal.PortalId
                    Existing = NextByNodes.get(CandidateNodes)
                    if Existing is None or tuple(sorted(
                        CandidatePortalIds.items()
                    )) < tuple(sorted(Existing[1].items())):
                        NextByNodes[CandidateNodes] = (
                            CandidateNodes,
                            CandidatePortalIds,
                            CandidateClaims,
                        )
                if Incomplete:
                    break
            if Incomplete:
                break
            Next = list(NextByNodes.values())
            if UseDominance:
                Retained = []
                for Candidate in sorted(
                    Next,
                    key=lambda Value: (
                        len(Value[2].ResourceIds),
                        tuple(sorted(Value[0])),
                    ),
                ):
                    if any(ClaimsSubset(Value[2], Candidate[2]) for Value in Retained):
                        MemoizedStateHitCount += 1
                        continue
                    Retained = [
                        Value for Value in Retained
                        if not ClaimsSubset(Candidate[2], Value[2])
                    ]
                    Retained.append(Candidate)
                Frontier = tuple(Retained)
            else:
                Frontier = tuple(Next)
        GenericStates = tuple(Frontier) if not Incomplete else ()
        if not Incomplete and SharedCache is not None:
            SharedCache["generic:" + GenericDomainFingerprint] = GenericStates

    StatesByFingerprint = {}
    _ExactSize, ExactDomainIndex, ExactDomain = ExactVariable
    ExactPortal = ExactDomain[0]
    for Nodes, PortalIds, _Claims in GenericStates:
        if ShouldStop is not None and ShouldStop():
            Incomplete = True
            break
        ExpansionCount += 1
        CandidateNodes = frozenset((*Nodes, *ExactPortal.Path))
        CandidateClaims = ResourceGraph.BuildRouteClaims(CandidateNodes)
        if FindSelfClaimConflicts({Signal: CandidateClaims}):
            continue
        FrozenBlockers = PortalTupleConflictsWithFrozenComponentClaims(
            Signal,
            CandidateClaims,
            Factor.FrozenComponentClaims,
        )
        if FrozenBlockers:
            DependencySignals.update(map(str, FrozenBlockers))
            continue
        CompletePortalIds = dict(PortalIds)
        CompletePortalIds[ExactDomainIndex] = ExactPortal.PortalId
        StateFingerprint = _BuildMandatoryPortalFactorClaimStateFingerprint(
            Signal,
            CandidateClaims,
        )
        State = MandatoryPortalFactorClaimState(
            StateFingerprint=StateFingerprint,
            PortalIds=tuple(
                CompletePortalIds[Index]
                for Index in range(len(Factor.PortalDomains))
            ),
            Claims=CandidateClaims,
        )
        Existing = StatesByFingerprint.get(StateFingerprint)
        if Existing is None or State.PortalIds < Existing.PortalIds:
            StatesByFingerprint[StateFingerprint] = State
    States = (
        tuple(
            StatesByFingerprint[Fingerprint]
            for Fingerprint in sorted(StatesByFingerprint)
        )
        if not Incomplete
        else ()
    )
    return MandatoryPortalFactorFeasibilityCertificate(
        FactorDomainFingerprint=Factor.DomainFingerprint,
        Signal=Signal,
        Complete=not Incomplete,
        StateDomainFingerprint=(
            BuildStateDomainFingerprint(States)
            if not Incomplete
            else ""
        ),
        States=States,
        ConflictPositions=frozenset(ConflictPositions),
        DependencySignals=tuple(sorted(DependencySignals)),
        ExpansionCount=ExpansionCount,
        MemoizedStateHitCount=MemoizedStateHitCount,
    )


def GetMandatoryPortalFactorFeasibilityCertificate(
    Cache: dict[str, MandatoryPortalFactorFeasibilityCertificate],
    Factor: PhysicalBoundaryMandatoryPortalFactorDomain,
    ResourceGraph: Any,
    ShouldStop: Callable[[], bool] | None = None,
) -> tuple[MandatoryPortalFactorFeasibilityCertificate, bool]:
    """Reuse only a complete certificate for the exact aperture factor."""
    Cached = Cache.get(Factor.DomainFingerprint)
    if Cached is not None and Cached.Complete:
        PortalByDomainAndId = tuple(
            {Portal.PortalId: Portal for Portal in Domain}
            for Domain in Factor.PortalDomains
        )
        StateFingerprints = tuple(
            State.StateFingerprint for State in Cached.States
        )
        StateDomainFingerprint = BuildStableFingerprint((
            "mandatory-portal-factor-state-domain-v1",
            Factor.DomainFingerprint,
            StateFingerprints,
        ))
        CachedStatesValid = bool(
            len(StateFingerprints) == len(set(StateFingerprints))
            and StateFingerprints == tuple(sorted(StateFingerprints))
            and all(
                len(State.PortalIds) == len(Factor.PortalDomains)
                and all(
                    PortalId in PortalByDomainAndId[Index]
                    for Index, PortalId in enumerate(State.PortalIds)
                )
                and State.Claims == ResourceGraph.BuildRouteClaims(
                    frozenset((
                        *Factor.FixedAccessNodes,
                        *(
                            Position
                            for Index, PortalId
                            in enumerate(State.PortalIds)
                            for Position in PortalByDomainAndId[Index][
                                PortalId
                            ].Path
                        ),
                    ))
                )
                and State.StateFingerprint
                == _BuildMandatoryPortalFactorClaimStateFingerprint(
                    Factor.Signal,
                    State.Claims,
                )
                and not FindSelfClaimConflicts({
                    Factor.Signal: State.Claims
                })
                and not PortalTupleConflictsWithFrozenComponentClaims(
                    Factor.Signal,
                    State.Claims,
                    Factor.FrozenComponentClaims,
                )
                for State in Cached.States
            )
        )
        if (
            Cached.FactorDomainFingerprint != Factor.DomainFingerprint
            or Cached.Signal != Factor.Signal
            or Cached.StateDomainFingerprint != StateDomainFingerprint
            or not CachedStatesValid
        ):
            raise ValueError(
                "cached mandatory portal factor identity mismatch"
            )
        return Cached, True
    Certificate = CompileMandatoryPortalFactorFeasibility(
        Factor,
        ResourceGraph,
        ShouldStop=ShouldStop,
        SharedCache=Cache,
    )
    if Certificate.Complete:
        Cache[Factor.DomainFingerprint] = Certificate
    return Certificate, False


def BuildMandatoryPortalClaimConflictIndexes(
    States: tuple[MandatoryPortalFactorClaimState, ...],
) -> tuple[dict[str, dict[Position3, int]], int]:
    """Index exact claim conflicts as cell-local bitsets."""
    Mutable: dict[str, dict[Position3, int]] = {
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
    return Mutable, (1 << len(States)) - 1


def BuildMandatoryPortalClaimConflictMask(
    FirstState: MandatoryPortalFactorClaimState,
    SecondIndexes: Mapping[str, Mapping[Position3, int]],
    AllSecondStatesMask: int,
) -> int:
    """Return the exact second-state mask conflicting with one first state."""
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


def ValidatePhysicalBoundaryMandatoryPortalPairRelationIdentity(
    Relation: PhysicalBoundaryMandatoryPortalPairRelation,
    *,
    ExpectedRelationFingerprint: str,
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    OrderedSignals: tuple[str, str],
    OptionDomainFingerprintsBySignal: tuple[
        tuple[str, tuple[str, ...]], ...
    ],
    ExpectedApertureContractsBySignal: Mapping[str, tuple[str, ...]],
    DomainsBySignal: Mapping[
        str, tuple[PhysicalBoundaryMandatoryPortalFactorDomain, ...]
    ],
) -> None:
    """Reject a complete cached relation whose proof identity has drifted."""
    ExpectedOptionPairs = frozenset(
        (First, Second)
        for First in ExpectedApertureContractsBySignal[OrderedSignals[0]]
        for Second in ExpectedApertureContractsBySignal[OrderedSignals[1]]
    )
    CertificatesByOptionPair = {
        (
            Value.FirstApertureContractFingerprint,
            Value.SecondApertureContractFingerprint,
        ): Value
        for Value in Relation.Certificates
    }
    DomainsByAperture = {
        Signal: {
            Value.ApertureContractFingerprint: Value
            for Value in DomainsBySignal[Signal]
        }
        for Signal in OrderedSignals
    }
    FactorIdentitiesMatch = all(
        Value.PreparedDomainFingerprint == Preparation.DomainFingerprint
        and Value.PlacementFingerprint == Preparation.PlacementFingerprint
        and Value.ComponentGraphFingerprint
        == Preparation.ComponentGraphFingerprint
        and Value.ResourceGraphFingerprint
        == Preparation.ResourceGraphFingerprint
        and Value.GuideFingerprint == Preparation.GuideFingerprint
        and Value.ExteriorFabricSetFingerprint
        == Preparation.ExteriorFabricSetFingerprint
        and Value.ExteriorRegionFingerprint
        == Preparation.ExteriorRegionFingerprint
        and Value.Signal == Signal
        for Signal in OrderedSignals
        for Value in DomainsBySignal[Signal]
    )
    IdentityMatches = bool(
        Relation.Complete
        and FactorIdentitiesMatch
        and Relation.RelationFingerprint == ExpectedRelationFingerprint
        and Relation.PreparedDomainFingerprint
        == Preparation.DomainFingerprint
        and Relation.Signals == OrderedSignals
        and Relation.OptionDomainFingerprintsBySignal
        == OptionDomainFingerprintsBySignal
        and Relation.ExpectedOptionPairCount == len(ExpectedOptionPairs)
        and len(Relation.Certificates) == len(ExpectedOptionPairs)
        and len(CertificatesByOptionPair) == len(ExpectedOptionPairs)
        and frozenset(CertificatesByOptionPair) == ExpectedOptionPairs
    )
    ExpectedClauses = set()
    ForeignDependencyCertificateCount = 0
    if IdentityMatches:
        for OptionPair, Value in CertificatesByOptionPair.items():
            First = DomainsByAperture[OrderedSignals[0]].get(OptionPair[0])
            Second = DomainsByAperture[OrderedSignals[1]].get(OptionPair[1])
            Certificate = Value.Certificate
            if First is None or Second is None:
                IdentityMatches = False
                break
            ExpectedDomainFingerprint = BuildStableFingerprint((
                "mandatory-portal-pair-factor-certificate-v2",
                ExpectedRelationFingerprint,
                First.DomainFingerprint,
                Second.DomainFingerprint,
            ))
            if (
                not Certificate.Complete
                or Certificate.DomainFingerprint
                != ExpectedDomainFingerprint
                or Certificate.Signals != OrderedSignals
            ):
                IdentityMatches = False
                break
            DependenciesAreLocal = bool(
                frozenset(Certificate.DependencySignals)
                <= frozenset(OrderedSignals)
            )
            if not DependenciesAreLocal:
                ForeignDependencyCertificateCount += 1
            if Certificate.Feasible is False and DependenciesAreLocal:
                ExpectedClauses.add(frozenset((
                    (OrderedSignals[0], OptionPair[0]),
                    (OrderedSignals[1], OptionPair[1]),
                )))
    if (
        not IdentityMatches
        or frozenset(Relation.UnsatisfiableApertureClauses)
        != frozenset(ExpectedClauses)
        or Relation.ForeignDependencyCertificateCount
        != ForeignDependencyCertificateCount
    ):
        raise ValueError(
            "cached mandatory portal pair relation identity mismatch"
        )


def _CompilePhysicalBoundaryMandatoryPortalPairSparseProjection(
    *,
    CanUseSparseProjection: bool,
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    OrderedSignals: tuple[str, str],
    DomainsBySignal: Mapping[
        str, tuple[PhysicalBoundaryMandatoryPortalFactorDomain, ...]
    ],
    Resources: RoutingResources,
    SharedFrozenClaims: tuple[LocalRouteClaim, ...],
    ExpectedOptionPairCount: int,
    RelationFingerprint: str,
    OptionDomainFingerprintsBySignal: tuple[
        tuple[str, tuple[str, ...]], ...
    ],
    ShouldStop: Callable[[], bool] | None,
) -> PhysicalBoundaryMandatoryPortalPairRelation | None:
    """Compile the exact sparse aperture-pair projection when applicable."""
    if CanUseSparseProjection:
        VariableDomains = []
        BaseNodesBySignal = {}
        ApertureVariableBySignal = {}
        FactorBySignalAndAperture = {}
        GenericVariableBindingsBySignal = {}
        SparseIdentityComplete = True
        for Signal in OrderedSignals:
            Factors = DomainsBySignal[Signal]
            GenericFingerprints = {
                Value.GenericPortalDomainFingerprint for Value in Factors
            }
            CommonNodeSets = {
                Value.CommonFixedAccessNodes for Value in Factors
            }
            if len(GenericFingerprints) != 1 or len(CommonNodeSets) != 1:
                SparseIdentityComplete = False
                break
            BaseNodesBySignal[Signal] = next(iter(CommonNodeSets))
            ApertureVariable = f"aperture:{Signal}"
            ApertureVariableBySignal[Signal] = ApertureVariable
            VariableDomains.append(ExactPortalConstraintVariableDomain(
                Variable=ApertureVariable,
                Signal=Signal,
                Choices=tuple(
                    ExactPortalConstraintChoice(
                        ChoiceId=Value.ApertureContractFingerprint,
                        Nodes=Value.OptionOverlayNodes,
                    )
                    for Value in Factors
                ),
            ))
            FactorBySignalAndAperture.update(
                {
                    (Signal, Value.ApertureContractFingerprint): Value
                    for Value in Factors
                }
            )
            Representative = Factors[0]
            RepresentativeGenericDomains = tuple(
                Domain for Index, Domain
                in enumerate(Representative.PortalDomains)
                if Index != Representative.OptionOverlayPortalDomainIndex
            )
            ExpectedGenericIdentity = tuple(
                tuple(
                    (Portal.PortalId, tuple(Portal.Path))
                    for Portal in Domain
                )
                for Domain in RepresentativeGenericDomains
            )
            if any(
                tuple(
                    tuple(
                        (Portal.PortalId, tuple(Portal.Path))
                        for Portal in Domain
                    )
                    for Index, Domain in enumerate(Value.PortalDomains)
                    if Index != Value.OptionOverlayPortalDomainIndex
                ) != ExpectedGenericIdentity
                for Value in Factors[1:]
            ):
                SparseIdentityComplete = False
                break
            Bindings = []
            for GenericIndex, Domain in enumerate(
                RepresentativeGenericDomains
            ):
                Variable = f"portal:{Signal}:{GenericIndex}"
                VariableDomains.append(
                    ExactPortalConstraintVariableDomain(
                        Variable=Variable,
                        Signal=Signal,
                        Choices=tuple(
                            ExactPortalConstraintChoice(
                                ChoiceId=Portal.PortalId,
                                Nodes=frozenset(Portal.Path),
                            )
                            for Portal in Domain
                        ),
                    )
                )
                Bindings.append(Variable)
            GenericVariableBindingsBySignal[Signal] = tuple(Bindings)
        if SparseIdentityComplete:
            Extraction = ExtractSparseExactPortalConstraintFactors(
                VariableDomains,
                Resources.ResourceGraph,
                BaseNodesBySignal=BaseNodesBySignal,
                FrozenComponentClaims=SharedFrozenClaims,
            )
            Projection = ProjectExactPortalConstraintFactors(
                Extraction,
                VariableDomains,
                (
                    ApertureVariableBySignal[OrderedSignals[0]],
                    ApertureVariableBySignal[OrderedSignals[1]],
                ),
                ShouldStop=ShouldStop,
            )
            LocalFactors = tuple(
                Value for Value in Extraction.ForbiddenTuples
                if not Value.DependencySignals
            )
            LocalExtraction = replace(
                Extraction,
                DomainFingerprint=BuildStableFingerprint((
                    "sparse-exact-portal-local-factor-projection-v1",
                    Extraction.DomainFingerprint,
                    tuple(
                        (Value.Assignments, Value.ConflictPositions)
                        for Value in LocalFactors
                    ),
                )),
                ForbiddenTuples=LocalFactors,
                MaximumForbiddenTupleArity=max(
                    (len(Value.Assignments) for Value in LocalFactors),
                    default=0,
                ),
            )
            LocalProjection = ProjectExactPortalConstraintFactors(
                LocalExtraction,
                VariableDomains,
                (
                    ApertureVariableBySignal[OrderedSignals[0]],
                    ApertureVariableBySignal[OrderedSignals[1]],
                ),
                ShouldStop=ShouldStop,
            )
            SupportedPairs = frozenset(Projection.SupportedChoicePairs)
            LocalSupportedPairs = frozenset(
                LocalProjection.SupportedChoicePairs
            )
            WitnessByPair = dict(Projection.Witnesses)
            Certificates = []
            AllConflictPositions = frozenset(
                Position
                for Value in Extraction.ForbiddenTuples
                for Position in Value.ConflictPositions
            )
            for First in DomainsBySignal[OrderedSignals[0]]:
                for Second in DomainsBySignal[OrderedSignals[1]]:
                    OptionPair = (
                        First.ApertureContractFingerprint,
                        Second.ApertureContractFingerprint,
                    )
                    Feasible = OptionPair in SupportedPairs
                    LocallyFeasible = OptionPair in LocalSupportedPairs
                    WitnessAssignment = dict(
                        WitnessByPair.get(OptionPair, ())
                    )

                    def BuildWitnessPortalIds(
                        Factor: PhysicalBoundaryMandatoryPortalFactorDomain,
                    ) -> tuple[str, ...]:
                        GenericVariables = iter(
                            GenericVariableBindingsBySignal[Factor.Signal]
                        )
                        Values = []
                        for Index, Domain in enumerate(Factor.PortalDomains):
                            if Index == Factor.OptionOverlayPortalDomainIndex:
                                Values.append(Domain[0].PortalId)
                            else:
                                Values.append(WitnessAssignment[next(
                                    GenericVariables
                                )])
                        return tuple(Values)

                    DomainFingerprint = (
                        BuildMandatoryPortalPairDomainFingerprint(
                            OrderedSignals,
                            {
                                First.Signal: First.FixedAccessNodes,
                                Second.Signal: Second.FixedAccessNodes,
                            },
                            {
                                First.Signal: First.PortalDomains,
                                Second.Signal: Second.PortalDomains,
                            },
                            (),
                            First.ResourceGraphFingerprint,
                            First.TechnologyFingerprint,
                        )
                    )
                    Certificate = MandatoryPortalPairFeasibilityCertificate(
                        DomainFingerprint=DomainFingerprint,
                        Signals=OrderedSignals,
                        Complete=(
                            Projection.Complete
                            and LocalProjection.Complete
                        ),
                        Feasible=(
                            Feasible
                            if Projection.Complete
                            and LocalProjection.Complete
                            else None
                        ),
                        WitnessPortalIds=(
                            (
                                (
                                    First.Signal,
                                    BuildWitnessPortalIds(First),
                                ),
                                (
                                    Second.Signal,
                                    BuildWitnessPortalIds(Second),
                                ),
                            )
                            if Feasible
                            and Projection.Complete
                            and LocalProjection.Complete
                            else ()
                        ),
                        ConflictPositions=(
                            frozenset()
                            if Feasible
                            else AllConflictPositions
                        ),
                        ConflictFingerprint=(
                            ""
                            if Feasible
                            or not Projection.Complete
                            or not LocalProjection.Complete
                            else BuildStableFingerprint((
                                DomainFingerprint,
                                tuple(sorted(AllConflictPositions)),
                            ))
                        ),
                        ExpansionCount=Projection.ExpansionCount,
                        MemoizedStateHitCount=(
                            Projection.FailedStateMemoHitCount
                        ),
                        DependencySignals=tuple(sorted(frozenset((
                            *OrderedSignals,
                            *(
                                ()
                                if Feasible or not LocallyFeasible
                                else (
                                    Dependency
                                    for FactorValue
                                    in Extraction.ForbiddenTuples
                                    for Dependency
                                    in FactorValue.DependencySignals
                                )
                            ),
                        )))),
                    )
                    Certificates.append(
                        PhysicalBoundaryMandatoryPortalPairOptionCertificate(
                            FirstApertureContractFingerprint=(
                                First.ApertureContractFingerprint
                            ),
                            SecondApertureContractFingerprint=(
                                Second.ApertureContractFingerprint
                            ),
                            Certificate=Certificate,
                        )
                    )
            Complete = bool(
                Extraction.Complete
                and Projection.Complete
                and LocalProjection.Complete
                and len(Certificates) == ExpectedOptionPairCount
            )
            Clauses = (
                tuple(sorted(
                    (
                        frozenset((
                            (
                                OrderedSignals[0],
                                Value.FirstApertureContractFingerprint,
                            ),
                            (
                                OrderedSignals[1],
                                Value.SecondApertureContractFingerprint,
                            ),
                        ))
                        for Value in Certificates
                        if (
                            Value.Certificate.Feasible is False
                            and frozenset(
                                Value.Certificate.DependencySignals
                            ) <= frozenset(OrderedSignals)
                        )
                    ),
                    key=lambda Value: tuple(sorted(Value)),
                ))
                if Complete
                else ()
            )
            Relation = PhysicalBoundaryMandatoryPortalPairRelation(
                RelationFingerprint=RelationFingerprint,
                PreparedDomainFingerprint=Preparation.DomainFingerprint,
                Signals=OrderedSignals,
                OptionDomainFingerprintsBySignal=(
                    OptionDomainFingerprintsBySignal
                ),
                ExpectedOptionPairCount=ExpectedOptionPairCount,
                Certificates=tuple(Certificates),
                UnsatisfiableApertureClauses=Clauses,
                Complete=Complete,
                ForeignDependencyCertificateCount=sum(
                    int(
                        not frozenset(Value.Certificate.DependencySignals)
                        <= frozenset(OrderedSignals)
                    )
                    for Value in Certificates
                ),
                FactorCertificateCount=len(VariableDomains),
                FactorStateCount=len(Extraction.ForbiddenTuples),
                UniqueClaimStateCountsBySignal=tuple(
                    (
                        Signal,
                        sum(
                            len(Domain.Choices) for Domain in VariableDomains
                            if Domain.Signal == Signal
                        ),
                    )
                    for Signal in OrderedSignals
                ),
                FactorExpansionCount=(
                    Projection.ExpansionCount
                    + LocalProjection.ExpansionCount
                ),
                CompatibilityIndexStatePairUpperBound=(
                    ExpectedOptionPairCount
                ),
            )
            if Complete:
                Resources.PhysicalBoundaryMandatoryPortalPairRelationCache[
                    RelationFingerprint
                ] = Relation
            return Relation


def CompilePhysicalBoundaryMandatoryPortalPairRelation(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    Signals: tuple[str, str],
    Resources: RoutingResources,
    ShouldStop: Callable[[], bool] | None = None,
    MaximumNewCertificates: int | None = None,
    PreferredApertureContractsBySignal: Mapping[str, str] | None = None,
) -> PhysicalBoundaryMandatoryPortalPairRelation:
    """Exhaust every prepared aperture pair for one certified signal cut."""
    if MaximumNewCertificates is not None and MaximumNewCertificates < 1:
        raise ValueError("MaximumNewCertificates must be positive")
    OrderedSignals = tuple(sorted(map(str, Signals)))
    if len(OrderedSignals) != 2 or OrderedSignals[0] == OrderedSignals[1]:
        raise ValueError("mandatory aperture relation requires two signals")
    DomainsBySignal = {
        Signal: tuple(sorted(
            (
                Value
                for (PreparedFingerprint, DomainSignal, _Aperture), Value
                in Resources
                .PhysicalBoundaryMandatoryPortalFactorDomainCache.items()
                if PreparedFingerprint == Preparation.DomainFingerprint
                and DomainSignal == Signal
            ),
            key=lambda Value: (
                Value.ApertureContractFingerprint,
                Value.DomainFingerprint,
            ),
        ))
        for Signal in OrderedSignals
    }
    PreferredApertures = dict(
        PreferredApertureContractsBySignal or {}
    )
    IterationDomainsBySignal = {
        Signal: tuple(sorted(
            DomainsBySignal[Signal],
            key=lambda Value: (
                str(Value.ApertureContractFingerprint)
                != str(PreferredApertures.get(Signal, "")),
                Value.ApertureContractFingerprint,
                Value.DomainFingerprint,
            ),
        ))
        for Signal in OrderedSignals
    }
    PreparedBoundaryDomains = {
        str(Signal): tuple(Values)
        for Signal, Values
        in Preparation.BoundaryPortReservationsBySignal
    }
    ExpectedApertureContractsBySignal = {
        Signal: tuple(sorted(
            str(Value.ApertureContractFingerprint)
            for Value in PreparedBoundaryDomains.get(Signal, ())
        ))
        for Signal in OrderedSignals
    }
    ActualApertureContractsBySignal = {
        Signal: tuple(sorted(
            Value.ApertureContractFingerprint
            for Value in DomainsBySignal[Signal]
        ))
        for Signal in OrderedSignals
    }
    ExactPreparedOptionSets = bool(all(
        ExpectedApertureContractsBySignal[Signal]
        and len(ExpectedApertureContractsBySignal[Signal])
        == len(set(ExpectedApertureContractsBySignal[Signal]))
        and ActualApertureContractsBySignal[Signal]
        == ExpectedApertureContractsBySignal[Signal]
        and len(DomainsBySignal[Signal])
        == len({
            Value.DomainFingerprint for Value in DomainsBySignal[Signal]
        })
        for Signal in OrderedSignals
    ))
    FactorIdentitiesMatchPreparation = bool(all(
        Value.PreparedDomainFingerprint == Preparation.DomainFingerprint
        and Value.PlacementFingerprint == Preparation.PlacementFingerprint
        and Value.ComponentGraphFingerprint
        == Preparation.ComponentGraphFingerprint
        and Value.ResourceGraphFingerprint
        == Preparation.ResourceGraphFingerprint
        and Value.GuideFingerprint == Preparation.GuideFingerprint
        and Value.ExteriorFabricSetFingerprint
        == Preparation.ExteriorFabricSetFingerprint
        and Value.ExteriorRegionFingerprint
        == Preparation.ExteriorRegionFingerprint
        and Value.Signal == Signal
        for Signal in OrderedSignals
        for Value in DomainsBySignal[Signal]
    ))
    OptionDomainFingerprintsBySignal = tuple(
        (
            Signal,
            tuple(Value.DomainFingerprint for Value in DomainsBySignal[Signal]),
        )
        for Signal in OrderedSignals
    )
    RelationFingerprint = BuildStableFingerprint((
        "physical-boundary-mandatory-portal-pair-relation-v1",
        Preparation.DomainFingerprint,
        Preparation.PlacementFingerprint,
        Preparation.ComponentGraphFingerprint,
        Preparation.ResourceGraphFingerprint,
        Preparation.GuideFingerprint,
        Preparation.ExteriorFabricSetFingerprint,
        Preparation.ExteriorRegionFingerprint,
        tuple(sorted(ExpectedApertureContractsBySignal.items())),
        OptionDomainFingerprintsBySignal,
    ))
    Cached = (
        Resources.PhysicalBoundaryMandatoryPortalPairRelationCache.get(
            RelationFingerprint
        )
    )
    if Cached is not None and Cached.Complete:
        ValidatePhysicalBoundaryMandatoryPortalPairRelationIdentity(
            Cached,
            ExpectedRelationFingerprint=RelationFingerprint,
            Preparation=Preparation,
            OrderedSignals=OrderedSignals,
            OptionDomainFingerprintsBySignal=(
                OptionDomainFingerprintsBySignal
            ),
            ExpectedApertureContractsBySignal=(
                ExpectedApertureContractsBySignal
            ),
            DomainsBySignal=DomainsBySignal,
        )
        return Cached
    CachedPartialCertificates: tuple[
        PhysicalBoundaryMandatoryPortalPairOptionCertificate, ...
    ] = ()
    CachedPartialForeignDependencyCertificateCount = 0
    if Cached is not None:
        ExpectedPartialPairs = frozenset(
            (First, Second)
            for First in ExpectedApertureContractsBySignal[
                OrderedSignals[0]
            ]
            for Second in ExpectedApertureContractsBySignal[
                OrderedSignals[1]
            ]
        )
        CachedPartialPairs = tuple(
            (
                Value.FirstApertureContractFingerprint,
                Value.SecondApertureContractFingerprint,
            )
            for Value in Cached.Certificates
        )
        PartialIdentityMatches = bool(
            Cached.RelationFingerprint == RelationFingerprint
            and Cached.PreparedDomainFingerprint
            == Preparation.DomainFingerprint
            and Cached.Signals == OrderedSignals
            and Cached.OptionDomainFingerprintsBySignal
            == OptionDomainFingerprintsBySignal
            and Cached.ExpectedOptionPairCount
            == len(ExpectedPartialPairs)
            and len(CachedPartialPairs) == len(set(CachedPartialPairs))
            and frozenset(CachedPartialPairs) <= ExpectedPartialPairs
            and all(
                Value.Certificate.Complete
                and Value.Certificate.Signals == OrderedSignals
                for Value in Cached.Certificates
            )
        )
        if not PartialIdentityMatches:
            raise ValueError(
                "cached mandatory portal pair relation identity mismatch"
            )
        CachedPartialCertificates = Cached.Certificates
        CachedPartialForeignDependencyCertificateCount = (
            Cached.ForeignDependencyCertificateCount
        )
    ExpectedOptionPairCount = (
        len(ExpectedApertureContractsBySignal[OrderedSignals[0]])
        * len(ExpectedApertureContractsBySignal[OrderedSignals[1]])
    )
    SharedFrozenClaims = tuple(
        DomainsBySignal[OrderedSignals[0]][0].FrozenComponentClaims
        if DomainsBySignal[OrderedSignals[0]]
        else ()
    )
    # Direct forbidden-tuple elimination is cheapest for the small relations
    # used by focused fixtures.  Large aperture products are compiled through
    # the exact per-signal frontier quotient below: generic portal states are
    # built once, shared by every aperture of the signal, and compared with a
    # bitset compatibility index.  Running one CSP elimination independently
    # for thousands of retained aperture pairs needlessly repeated the same
    # internal-variable search and could consume the whole CLA planning
    # reserve before publishing a single binary certificate.
    MaximumDirectSparseProjectionOptionPairs = 256
    CanUseSparseProjection = bool(
        ExpectedOptionPairCount
        and ExpectedOptionPairCount
        <= MaximumDirectSparseProjectionOptionPairs
        and ExactPreparedOptionSets
        and FactorIdentitiesMatchPreparation
        and isinstance(Resources.ResourceGraph, RoutingResourceGraph)
        and all(
            Value.Complete
            and Value.FrozenComponentClaims == SharedFrozenClaims
            for Signal in OrderedSignals
            for Value in DomainsBySignal[Signal]
        )
    )
    SparseRelation = _CompilePhysicalBoundaryMandatoryPortalPairSparseProjection(
        CanUseSparseProjection=CanUseSparseProjection,
        Preparation=Preparation,
        OrderedSignals=OrderedSignals,
        DomainsBySignal=DomainsBySignal,
        Resources=Resources,
        SharedFrozenClaims=SharedFrozenClaims,
        ExpectedOptionPairCount=ExpectedOptionPairCount,
        RelationFingerprint=RelationFingerprint,
        OptionDomainFingerprintsBySignal=OptionDomainFingerprintsBySignal,
        ShouldStop=ShouldStop,
    )
    if SparseRelation is not None:
        return SparseRelation
    # Large relations are usually encountered after the ordinary router has
    # rejected one exact pair.  Bootstrap the two current-option support rows
    # once before constructing the full normalized state quotient.  A cached
    # partial row must fall through to the quotient on the next call; otherwise
    # successive exterior failures would enumerate a new product row forever
    # and never publish the complete binary relation.
    PreferredFactorBySignal = {
        Signal: next((
            Value
            for Value in DomainsBySignal[Signal]
            if Value.ApertureContractFingerprint
            == str(PreferredApertures.get(Signal, ""))
        ), None)
        for Signal in OrderedSignals
    }
    CanCompilePreferredRowsDirectly = bool(
        ExpectedOptionPairCount > MaximumDirectSparseProjectionOptionPairs
        and not CachedPartialCertificates
        and ExactPreparedOptionSets
        and FactorIdentitiesMatchPreparation
        and isinstance(Resources.ResourceGraph, RoutingResourceGraph)
        and all(PreferredFactorBySignal.values())
    )
    if CanCompilePreferredRowsDirectly:
        FirstSignal, SecondSignal = OrderedSignals
        FirstPreferred = PreferredFactorBySignal[FirstSignal]
        SecondPreferred = PreferredFactorBySignal[SecondSignal]
        assert FirstPreferred is not None and SecondPreferred is not None
        RowSpecifications = [
            (
                tuple(DomainsBySignal[FirstSignal]),
                (SecondPreferred,),
            ),
            (
                (FirstPreferred,),
                tuple(DomainsBySignal[SecondSignal]),
            ),
        ]
        RowSpecifications.sort(
            key=lambda Value: (
                -len(Value[0]) * len(Value[1]),
                tuple(
                    Factor.ApertureContractFingerprint
                    for Factors in Value
                    for Factor in Factors
                ),
            )
        )
        TargetPairs = tuple(dict.fromkeys((
            (
                FirstPreferred.ApertureContractFingerprint,
                SecondPreferred.ApertureContractFingerprint,
            ),
            *(
                (
                    First.ApertureContractFingerprint,
                    Second.ApertureContractFingerprint,
                )
                for FirstFactors, SecondFactors in RowSpecifications
                for First in FirstFactors
                for Second in SecondFactors
            ),
        )))
        Certificates = list(CachedPartialCertificates)
        CompletedOptionPairs = {
            (
                Value.FirstApertureContractFingerprint,
                Value.SecondApertureContractFingerprint,
            )
            for Value in Certificates
        }
        PairCertificateCache = (
            Resources.PhysicalGlobalMandatoryPortalPairCertificateCache
        )
        NewCertificateCount = 0
        ForeignDependencyCertificateCount = (
            CachedPartialForeignDependencyCertificateCount
        )
        FactorByAperture = {
            (
                Factor.Signal,
                Factor.ApertureContractFingerprint,
            ): Factor
            for Signal in OrderedSignals
            for Factor in DomainsBySignal[Signal]
        }
        for FirstAperture, SecondAperture in TargetPairs:
            OptionPair = (FirstAperture, SecondAperture)
            if OptionPair in CompletedOptionPairs:
                continue
            if (
                (
                    MaximumNewCertificates is not None
                    and NewCertificateCount >= MaximumNewCertificates
                )
                or (ShouldStop is not None and ShouldStop())
            ):
                break
            First = FactorByAperture[(FirstSignal, FirstAperture)]
            Second = FactorByAperture[(SecondSignal, SecondAperture)]
            # The normalized factor identities already bind the complete
            # fixed-access, portal, frozen-claim, graph, and technology
            # domains.  Re-serializing both full portal products for every
            # aperture pair made publication quadratic in representation
            # size after the actual compatibility index was complete.
            DomainFingerprint = BuildStableFingerprint((
                "mandatory-portal-pair-factor-certificate-v2",
                RelationFingerprint,
                First.DomainFingerprint,
                Second.DomainFingerprint,
            ))
            Certificate, _CacheHit = (
                GetMandatoryPortalPairFeasibilityCertificate(
                    PairCertificateCache,
                    Signals=OrderedSignals,
                    FixedAccessNodesBySignal={
                        First.Signal: First.FixedAccessNodes,
                        Second.Signal: Second.FixedAccessNodes,
                    },
                    PortalDomainsBySignal={
                        First.Signal: First.PortalDomains,
                        Second.Signal: Second.PortalDomains,
                    },
                    FrozenComponentClaims=First.FrozenComponentClaims,
                    ResourceGraph=Resources.ResourceGraph,
                    DomainFingerprint=DomainFingerprint,
                    ShouldStop=ShouldStop,
                )
            )
            if not Certificate.Complete:
                break
            Certificates.append(
                PhysicalBoundaryMandatoryPortalPairOptionCertificate(
                    FirstApertureContractFingerprint=FirstAperture,
                    SecondApertureContractFingerprint=SecondAperture,
                    Certificate=Certificate,
                )
            )
            CompletedOptionPairs.add(OptionPair)
            NewCertificateCount += 1
            if not frozenset(Certificate.DependencySignals) <= frozenset(
                OrderedSignals
            ):
                ForeignDependencyCertificateCount += 1
        Clauses = tuple(sorted(
            {
                frozenset((
                    (
                        FirstSignal,
                        Value.FirstApertureContractFingerprint,
                    ),
                    (
                        SecondSignal,
                        Value.SecondApertureContractFingerprint,
                    ),
                ))
                for Value in Certificates
                if (
                    Value.Certificate.Complete
                    and Value.Certificate.Feasible is False
                    and frozenset(Value.Certificate.DependencySignals)
                    <= frozenset(OrderedSignals)
                )
            },
            key=lambda Value: tuple(sorted(Value)),
        ))
        Relation = PhysicalBoundaryMandatoryPortalPairRelation(
            RelationFingerprint=RelationFingerprint,
            PreparedDomainFingerprint=Preparation.DomainFingerprint,
            Signals=OrderedSignals,
            OptionDomainFingerprintsBySignal=(
                OptionDomainFingerprintsBySignal
            ),
            ExpectedOptionPairCount=ExpectedOptionPairCount,
            Certificates=tuple(Certificates),
            UnsatisfiableApertureClauses=Clauses,
            Complete=False,
            ForeignDependencyCertificateCount=(
                ForeignDependencyCertificateCount
            ),
            FactorCertificateCount=0,
            FactorStateCount=0,
            UniqueClaimStateCountsBySignal=(),
            FactorExpansionCount=sum(
                Value.Certificate.ExpansionCount
                for Value in Certificates
            ),
            CompatibilityIndexStatePairUpperBound=len(TargetPairs),
        )
        Resources.PhysicalBoundaryMandatoryPortalPairRelationCache[
            RelationFingerprint
        ] = Relation
        return Relation
    # Exact normalized frontier quotient.  This is also the production path
    # for large relations.  Every state is reconstructed with
    # BuildRouteClaims, frozen-claim blockers remain explicit dependency
    # signals, and only pair-local complete certificates may become clauses.
    # It is therefore not a permissive fallback: uncertainty or a foreign
    # dependency still produces no negative pruning.
    Certificates = list(CachedPartialCertificates)
    NewCertificateCount = 0
    CompletedOptionPairs = {
        (
            Value.FirstApertureContractFingerprint,
            Value.SecondApertureContractFingerprint,
        )
        for Value in Certificates
    }
    Incomplete = bool(
        not ExpectedOptionPairCount
        or not ExactPreparedOptionSets
        or not FactorIdentitiesMatchPreparation
        or any(
            not Value.Complete
            for Signal in OrderedSignals
            for Value in DomainsBySignal[Signal]
        )
    )
    ForeignDependencyCertificateCount = (
        CachedPartialForeignDependencyCertificateCount
    )
    FactorCertificateCache = getattr(
        Resources,
        "PhysicalBoundaryMandatoryPortalFactorCertificateCache",
        None,
    )
    if FactorCertificateCache is None:
        FactorCertificateCache = {}
        setattr(
            Resources,
            "PhysicalBoundaryMandatoryPortalFactorCertificateCache",
            FactorCertificateCache,
        )
    StateIndexCache = getattr(
        Resources,
        "PhysicalBoundaryMandatoryPortalPairStateIndexCache",
        None,
    )
    if StateIndexCache is None:
        StateIndexCache = {}
        setattr(
            Resources,
            "PhysicalBoundaryMandatoryPortalPairStateIndexCache",
            StateIndexCache,
        )
    CachedStateIndex = StateIndexCache.get(RelationFingerprint)
    ExpectedFactorDomainFingerprints = tuple(sorted(
        Value.DomainFingerprint
        for Signal in OrderedSignals
        for Value in DomainsBySignal[Signal]
    ))
    if CachedStateIndex is not None and (
        CachedStateIndex.RelationFingerprint != RelationFingerprint
        or tuple(
            Fingerprint for Fingerprint, _StateFingerprint
            in CachedStateIndex.FactorStateDomainFingerprints
        ) != ExpectedFactorDomainFingerprints
        or tuple(sorted(
            Value.FactorDomainFingerprint
            for Value in CachedStateIndex.FactorCertificates
        )) != ExpectedFactorDomainFingerprints
        or any(
            not Value.Complete
            for Value in CachedStateIndex.FactorCertificates
        )
        or any(
            FactorCertificateCache.get(
                Value.FactorDomainFingerprint
            ) != Value
            for Value in CachedStateIndex.FactorCertificates
        )
    ):
        raise ValueError(
            "cached mandatory portal pair state index identity mismatch"
        )
    FactorCertificatesByFingerprint = (
        {
            Value.FactorDomainFingerprint: Value
            for Value in CachedStateIndex.FactorCertificates
        }
        if CachedStateIndex is not None
        else {}
    )
    if not Incomplete and CachedStateIndex is None:
        for Signal in OrderedSignals:
            for Factor in DomainsBySignal[Signal]:
                if ShouldStop is not None and ShouldStop():
                    Incomplete = True
                    break
                FactorCertificate, _FactorCacheHit = (
                    GetMandatoryPortalFactorFeasibilityCertificate(
                        FactorCertificateCache,
                        Factor,
                        Resources.ResourceGraph,
                        ShouldStop=ShouldStop,
                    )
                )
                if not FactorCertificate.Complete:
                    Incomplete = True
                    break
                FactorCertificatesByFingerprint[
                    Factor.DomainFingerprint
                ] = FactorCertificate
            if Incomplete:
                break

    # Compile compatibility once over the normalized state quotient.  Every
    # aperture is only a mask over this immutable domain, so the relation's
    # full aperture cross-product no longer reruns portal DFS.  The completed
    # index is immutable and reused by bounded relation work slices.
    FactorStateDomainFingerprints = tuple(sorted(
        (
            Fingerprint,
            Certificate.StateDomainFingerprint,
        )
        for Fingerprint, Certificate
        in FactorCertificatesByFingerprint.items()
    ))
    StateIndexFingerprint = BuildStableFingerprint((
        "physical-boundary-mandatory-portal-pair-state-index-v1",
        RelationFingerprint,
        FactorStateDomainFingerprints,
    ))
    if CachedStateIndex is not None and (
        CachedStateIndex.RelationFingerprint != RelationFingerprint
        or CachedStateIndex.IndexFingerprint != StateIndexFingerprint
        or CachedStateIndex.FactorStateDomainFingerprints
        != FactorStateDomainFingerprints
    ):
        raise ValueError(
            "cached mandatory portal pair state index identity mismatch"
        )

    FirstStatesByFingerprint = {}
    SecondStatesByFingerprint = {}

    def AddCanonicalState(
        ValuesByFingerprint: dict[
            str, MandatoryPortalFactorClaimState
        ],
        State: MandatoryPortalFactorClaimState,
    ) -> None:
        Existing = ValuesByFingerprint.get(State.StateFingerprint)
        if Existing is not None and Existing.Claims != State.Claims:
            raise ValueError(
                "mandatory portal relation claim-state identity collision"
            )
        if Existing is None or State.PortalIds < Existing.PortalIds:
            ValuesByFingerprint[State.StateFingerprint] = State

    if CachedStateIndex is not None:
        FirstStatesByFingerprint = {
            State.StateFingerprint: State
            for State in CachedStateIndex.FirstStates
        }
        CanonicalSecondStates = CachedStateIndex.SecondStates
        CompatibleSecondMaskByFirstStateFingerprint = dict(
            CachedStateIndex.CompatibleSecondMasksByFirstState
        )
        SecondMasksByFactorFingerprint = dict(
            CachedStateIndex.SecondMasksByFactorFingerprint
        )
    elif not Incomplete:
        for Factor in DomainsBySignal[OrderedSignals[0]]:
            for State in FactorCertificatesByFingerprint[
                Factor.DomainFingerprint
            ].States:
                AddCanonicalState(FirstStatesByFingerprint, State)
        for Factor in DomainsBySignal[OrderedSignals[1]]:
            for State in FactorCertificatesByFingerprint[
                Factor.DomainFingerprint
            ].States:
                AddCanonicalState(SecondStatesByFingerprint, State)
        CanonicalSecondStates = tuple(
            SecondStatesByFingerprint[Fingerprint]
            for Fingerprint in sorted(SecondStatesByFingerprint)
        )
        SecondStateIndexByFingerprint = {
            State.StateFingerprint: Index
            for Index, State in enumerate(CanonicalSecondStates)
        }
        SecondIndexes, AllSecondStatesMask = (
            BuildMandatoryPortalClaimConflictIndexes(CanonicalSecondStates)
        )
        CompatibleSecondMaskByFirstStateFingerprint = {}
        for Fingerprint in sorted(FirstStatesByFingerprint):
            if ShouldStop is not None and ShouldStop():
                Incomplete = True
                break
            ConflictMask = BuildMandatoryPortalClaimConflictMask(
                FirstStatesByFingerprint[Fingerprint],
                SecondIndexes,
                AllSecondStatesMask,
            )
            CompatibleSecondMaskByFirstStateFingerprint[Fingerprint] = (
                AllSecondStatesMask & ~ConflictMask
            )
        SecondMasksByFactorFingerprint = {}
        if not Incomplete:
            for Factor in DomainsBySignal[OrderedSignals[1]]:
                Mask = 0
                for State in FactorCertificatesByFingerprint[
                    Factor.DomainFingerprint
                ].States:
                    Mask |= 1 << SecondStateIndexByFingerprint[
                        State.StateFingerprint
                    ]
                SecondMasksByFactorFingerprint[
                    Factor.DomainFingerprint
                ] = Mask
        if not Incomplete:
            CachedStateIndex = (
                PhysicalBoundaryMandatoryPortalPairStateIndex(
                    IndexFingerprint=StateIndexFingerprint,
                    RelationFingerprint=RelationFingerprint,
                    FactorStateDomainFingerprints=(
                        FactorStateDomainFingerprints
                    ),
                    FactorCertificates=tuple(
                        FactorCertificatesByFingerprint[Fingerprint]
                        for Fingerprint in sorted(
                            FactorCertificatesByFingerprint
                        )
                    ),
                    FirstStates=tuple(
                        FirstStatesByFingerprint[Fingerprint]
                        for Fingerprint in sorted(
                            FirstStatesByFingerprint
                        )
                    ),
                    SecondStates=CanonicalSecondStates,
                    CompatibleSecondMasksByFirstState=tuple(sorted(
                        CompatibleSecondMaskByFirstStateFingerprint.items()
                    )),
                    SecondMasksByFactorFingerprint=tuple(sorted(
                        SecondMasksByFactorFingerprint.items()
                    )),
                )
            )
            StateIndexCache[RelationFingerprint] = CachedStateIndex

    for First in IterationDomainsBySignal[OrderedSignals[0]]:
        for Second in IterationDomainsBySignal[OrderedSignals[1]]:
            OptionPair = (
                First.ApertureContractFingerprint,
                Second.ApertureContractFingerprint,
            )
            if OptionPair in CompletedOptionPairs:
                continue
            if (
                Incomplete
                or (
                    MaximumNewCertificates is not None
                    and NewCertificateCount >= MaximumNewCertificates
                )
                or (ShouldStop is not None and ShouldStop())
            ):
                Incomplete = True
                break
            if (
                First.PreparedDomainFingerprint
                != Second.PreparedDomainFingerprint
                or First.ResourceGraphFingerprint
                != Second.ResourceGraphFingerprint
                or First.TechnologyFingerprint
                != Second.TechnologyFingerprint
                or First.GuideFingerprint != Second.GuideFingerprint
                or First.ExteriorFabricSetFingerprint
                != Second.ExteriorFabricSetFingerprint
                or First.ExteriorRegionFingerprint
                != Second.ExteriorRegionFingerprint
                or First.FrozenComponentClaimsFingerprint
                != Second.FrozenComponentClaimsFingerprint
            ):
                Incomplete = True
                break
            DomainFingerprint = BuildStableFingerprint((
                "mandatory-portal-pair-factor-certificate-v2",
                RelationFingerprint,
                First.DomainFingerprint,
                Second.DomainFingerprint,
            ))
            FirstFactorCertificate = FactorCertificatesByFingerprint[
                First.DomainFingerprint
            ]
            SecondFactorCertificate = FactorCertificatesByFingerprint[
                Second.DomainFingerprint
            ]
            SecondFactorMask = SecondMasksByFactorFingerprint[
                Second.DomainFingerprint
            ]
            WitnessFirstState = None
            WitnessSecondState = None
            for FirstState in FirstFactorCertificate.States:
                SupportedMask = (
                    CompatibleSecondMaskByFirstStateFingerprint[
                        FirstState.StateFingerprint
                    ]
                    & SecondFactorMask
                )
                if not SupportedMask:
                    continue
                WitnessFirstState = FirstState
                SecondStateIndex = (
                    SupportedMask & -SupportedMask
                ).bit_length() - 1
                WitnessSecondState = CanonicalSecondStates[
                    SecondStateIndex
                ]
                break
            Feasible = WitnessFirstState is not None
            DependencySignals = tuple(sorted(frozenset((
                *FirstFactorCertificate.DependencySignals,
                *SecondFactorCertificate.DependencySignals,
            ))))
            ConflictPositions = frozenset((
                *FirstFactorCertificate.ConflictPositions,
                *SecondFactorCertificate.ConflictPositions,
            ))
            Certificate = MandatoryPortalPairFeasibilityCertificate(
                DomainFingerprint=DomainFingerprint,
                Signals=OrderedSignals,
                Complete=True,
                Feasible=Feasible,
                WitnessPortalIds=(
                    (
                        (First.Signal, WitnessFirstState.PortalIds),
                        (Second.Signal, WitnessSecondState.PortalIds),
                    )
                    if Feasible
                    else ()
                ),
                ConflictPositions=ConflictPositions,
                ConflictFingerprint=(
                    ""
                    if Feasible
                    else BuildStableFingerprint((
                        DomainFingerprint,
                        tuple(sorted(ConflictPositions)),
                    ))
                ),
                ExpansionCount=(
                    FirstFactorCertificate.ExpansionCount
                    + SecondFactorCertificate.ExpansionCount
                ),
                MemoizedStateHitCount=(
                    FirstFactorCertificate.MemoizedStateHitCount
                    + SecondFactorCertificate.MemoizedStateHitCount
                ),
                DependencySignals=DependencySignals,
            )
            Certificates.append(
                PhysicalBoundaryMandatoryPortalPairOptionCertificate(
                    FirstApertureContractFingerprint=(
                        First.ApertureContractFingerprint
                    ),
                    SecondApertureContractFingerprint=(
                        Second.ApertureContractFingerprint
                    ),
                    Certificate=Certificate,
                )
            )
            CompletedOptionPairs.add(OptionPair)
            NewCertificateCount += 1
            if not Certificate.Complete:
                Incomplete = True
                break
            if not frozenset(Certificate.DependencySignals) <= frozenset(
                OrderedSignals
            ):
                ForeignDependencyCertificateCount += 1
        if Incomplete:
            break
    Complete = bool(
        not Incomplete
        and len(Certificates) == ExpectedOptionPairCount
        and all(Value.Certificate.Complete for Value in Certificates)
    )
    # Each emitted option certificate is independently complete for one
    # fixed aperture pair.  Its pair-local negative clause is sound even when
    # the surrounding cross-product is unfinished; only relation-wide
    # unsatisfiability still requires Complete=True.
    Clauses = tuple(sorted(
        {
            frozenset((
                (
                    OrderedSignals[0],
                    Value.FirstApertureContractFingerprint,
                ),
                (
                    OrderedSignals[1],
                    Value.SecondApertureContractFingerprint,
                ),
            ))
            for Value in Certificates
            if (
                Value.Certificate.Complete
                and Value.Certificate.Feasible is False
                and frozenset(Value.Certificate.DependencySignals)
                <= frozenset(OrderedSignals)
            )
        },
        key=lambda Value: tuple(sorted(Value)),
    ))
    Relation = PhysicalBoundaryMandatoryPortalPairRelation(
        RelationFingerprint=RelationFingerprint,
        PreparedDomainFingerprint=Preparation.DomainFingerprint,
        Signals=OrderedSignals,
        OptionDomainFingerprintsBySignal=(
            OptionDomainFingerprintsBySignal
        ),
        ExpectedOptionPairCount=ExpectedOptionPairCount,
        Certificates=tuple(Certificates),
        UnsatisfiableApertureClauses=Clauses,
        Complete=Complete,
        ForeignDependencyCertificateCount=(
            ForeignDependencyCertificateCount
        ),
        FactorCertificateCount=len(FactorCertificatesByFingerprint),
        FactorStateCount=sum(
            len(Value.States)
            for Value in FactorCertificatesByFingerprint.values()
        ),
        UniqueClaimStateCountsBySignal=(
            (
                OrderedSignals[0],
                len(FirstStatesByFingerprint),
            ),
            (
                OrderedSignals[1],
                len(SecondStatesByFingerprint),
            ),
        ),
        FactorExpansionCount=sum(
            Value.ExpansionCount
            for Value in FactorCertificatesByFingerprint.values()
        ),
        CompatibilityIndexStatePairUpperBound=(
            len(FirstStatesByFingerprint)
            * len(SecondStatesByFingerprint)
        ),
    )
    if Complete or Certificates:
        Resources.PhysicalBoundaryMandatoryPortalPairRelationCache[
            RelationFingerprint
        ] = Relation
    return Relation


def BuildMandatoryPortalPairDomainFingerprint(
    Signals: tuple[str, str],
    FixedAccessNodesBySignal: Mapping[str, frozenset[Position3]],
    PortalDomainsBySignal: Mapping[
        str, tuple[tuple[PinAccessPortal, ...], ...]
    ],
    FrozenComponentClaims: Iterable[LocalRouteClaim],
    ResourceGraphFingerprint: str,
    TechnologyFingerprint: str,
) -> str:
    """Identify the complete physical factors of one mandatory portal pair."""
    OrderedSignals = tuple(sorted(map(str, Signals)))
    return BuildStableFingerprint((
        "mandatory-portal-pair-factor-domain-v1",
        ResourceGraphFingerprint,
        TechnologyFingerprint,
        tuple(
            (
                Signal,
                tuple(sorted(FixedAccessNodesBySignal[Signal])),
                tuple(
                    tuple(sorted(
                        (
                            Portal.PortalId,
                            tuple(Portal.Path),
                            tuple(sorted(map(
                                str,
                                Portal.Claims.ResourceIds,
                            ))),
                        )
                        for Portal in Domain
                    ))
                    for Domain in PortalDomainsBySignal[Signal]
                ),
            )
            for Signal in OrderedSignals
        ),
        tuple(sorted(
            (
                Claim.Signal,
                tuple(sorted(map(str, Claim.Claims.ResourceIds))),
            )
            for Claim in FrozenComponentClaims
        )),
    ))


def SolveMandatoryPortalPairFeasibility(
    Signals: tuple[str, str],
    FixedAccessNodesBySignal: Mapping[str, frozenset[Position3]],
    PortalDomainsBySignal: Mapping[
        str, tuple[tuple[PinAccessPortal, ...], ...]
    ],
    FrozenComponentClaims: tuple[LocalRouteClaim, ...],
    ResourceGraph: Any,
    DomainFingerprint: str,
    ShouldStop: Callable[[], bool] | None = None,
) -> MandatoryPortalPairFeasibilityCertificate:
    """Exhaust one pair without materializing net-wide portal products."""
    OrderedSignals = tuple(sorted(map(str, Signals)))
    if len(OrderedSignals) != 2 or OrderedSignals[0] == OrderedSignals[1]:
        raise ValueError("mandatory portal feasibility requires two signals")
    Variables = tuple(sorted(
        (
            len(Domain),
            Signal,
            DomainIndex,
            tuple(sorted(Domain, key=lambda Value: Value.PortalId)),
        )
        for Signal in OrderedSignals
        for DomainIndex, Domain in enumerate(
            PortalDomainsBySignal.get(Signal, ())
        )
    ))
    if (
        any(not PortalDomainsBySignal.get(Signal) for Signal in OrderedSignals)
        or any(not Domain for _Size, _Signal, _Index, Domain in Variables)
    ):
        return MandatoryPortalPairFeasibilityCertificate(
            DomainFingerprint=DomainFingerprint,
            Signals=OrderedSignals,
            Complete=True,
            Feasible=False,
            ConflictFingerprint=BuildStableFingerprint((
                DomainFingerprint,
                "empty-terminal-domain",
            )),
            DependencySignals=OrderedSignals,
        )

    NodesBySignal = {
        Signal: frozenset(FixedAccessNodesBySignal.get(Signal, frozenset()))
        for Signal in OrderedSignals
    }
    ClaimsBySignal = {
        Signal: ResourceGraph.BuildRouteClaims(NodesBySignal[Signal])
        for Signal in OrderedSignals
    }
    SelectedPortalIds: dict[str, dict[int, str]] = {
        Signal: {} for Signal in OrderedSignals
    }
    FailedStates: set[tuple[object, ...]] = set()
    ConflictPositions: set[Position3] = set()
    DependencySignals = set(OrderedSignals)
    ExpansionCount = 0
    MemoizedStateHitCount = 0
    Incomplete = False

    def Search(VariableIndex: int) -> bool:
        nonlocal ExpansionCount, MemoizedStateHitCount, Incomplete
        if ShouldStop is not None and ShouldStop():
            Incomplete = True
            return False
        if VariableIndex >= len(Variables):
            return True
        StateKey = (
            VariableIndex,
            tuple(
                (Signal, tuple(sorted(NodesBySignal[Signal])))
                for Signal in OrderedSignals
            ),
        )
        if StateKey in FailedStates:
            MemoizedStateHitCount += 1
            return False
        _Size, Signal, DomainIndex, Domain = Variables[VariableIndex]
        OtherSignal = next(
            Value for Value in OrderedSignals if Value != Signal
        )
        PreviousNodes = NodesBySignal[Signal]
        PreviousClaims = ClaimsBySignal[Signal]
        for Portal in Domain:
            ExpansionCount += 1
            CandidateNodes = frozenset((*PreviousNodes, *Portal.Path))
            CandidateClaims = ResourceGraph.BuildRouteClaims(CandidateNodes)
            SelfConflicts = FindSelfClaimConflicts({Signal: CandidateClaims})
            if SelfConflicts:
                ConflictPositions.update(
                    Resource.Position for Resource in SelfConflicts
                )
                continue
            FrozenBlockers = PortalTupleConflictsWithFrozenComponentClaims(
                Signal,
                CandidateClaims,
                FrozenComponentClaims,
            )
            if FrozenBlockers:
                DependencySignals.update(map(str, FrozenBlockers))
                for Claim in FrozenComponentClaims:
                    if Claim.Signal in FrozenBlockers:
                        ConflictPositions.update(ClaimConflictPositions(
                            CandidateClaims,
                            Claim.Claims,
                        ))
                continue
            if _ClaimsConflict(
                Signal,
                CandidateClaims,
                OtherSignal,
                ClaimsBySignal[OtherSignal],
            ):
                ConflictPositions.update(ClaimConflictPositions(
                    CandidateClaims,
                    ClaimsBySignal[OtherSignal],
                ))
                continue
            NodesBySignal[Signal] = CandidateNodes
            ClaimsBySignal[Signal] = CandidateClaims
            SelectedPortalIds[Signal][DomainIndex] = Portal.PortalId
            if Search(VariableIndex + 1):
                return True
            SelectedPortalIds[Signal].pop(DomainIndex, None)
            NodesBySignal[Signal] = PreviousNodes
            ClaimsBySignal[Signal] = PreviousClaims
            if Incomplete:
                return False
        FailedStates.add(StateKey)
        return False

    Feasible = Search(0)
    if Incomplete:
        return MandatoryPortalPairFeasibilityCertificate(
            DomainFingerprint=DomainFingerprint,
            Signals=OrderedSignals,
            Complete=False,
            Feasible=None,
            ExpansionCount=ExpansionCount,
            MemoizedStateHitCount=MemoizedStateHitCount,
            DependencySignals=tuple(sorted(DependencySignals)),
        )
    ConflictPositionSet = frozenset(ConflictPositions)
    return MandatoryPortalPairFeasibilityCertificate(
        DomainFingerprint=DomainFingerprint,
        Signals=OrderedSignals,
        Complete=True,
        Feasible=Feasible,
        WitnessPortalIds=(
            tuple(
                (
                    Signal,
                    tuple(
                        SelectedPortalIds[Signal][Index]
                        for Index in range(len(
                            PortalDomainsBySignal[Signal]
                        ))
                    ),
                )
                for Signal in OrderedSignals
            )
            if Feasible
            else ()
        ),
        ConflictPositions=ConflictPositionSet,
        ConflictFingerprint=(
            ""
            if Feasible
            else BuildStableFingerprint((
                DomainFingerprint,
                tuple(sorted(ConflictPositionSet)),
            ))
        ),
        ExpansionCount=ExpansionCount,
        MemoizedStateHitCount=MemoizedStateHitCount,
        DependencySignals=tuple(sorted(DependencySignals)),
    )


def GetMandatoryPortalPairFeasibilityCertificate(
    Cache: dict[str, MandatoryPortalPairFeasibilityCertificate],
    **Arguments: Any,
) -> tuple[MandatoryPortalPairFeasibilityCertificate, bool]:
    """Reuse only complete exact certificates; incomplete searches resume fresh."""
    DomainFingerprint = str(Arguments["DomainFingerprint"])
    Cached = Cache.get(DomainFingerprint)
    if Cached is not None and Cached.Complete:
        return Cached, True
    Certificate = SolveMandatoryPortalPairFeasibility(**Arguments)
    if Certificate.Complete:
        Cache[DomainFingerprint] = Certificate
    return Certificate, False


def SelectCertifiedMandatoryPortalPairCuts(
    Certificates: Iterable[MandatoryPortalPairFeasibilityCertificate],
) -> tuple[tuple[tuple[str, str], frozenset[Position3]], ...]:
    """Promote complete UNSAT pair certificates and nothing weaker."""
    return tuple(
        (Certificate.Signals, Certificate.ConflictPositions)
        for Certificate in Certificates
        if Certificate.Complete and Certificate.Feasible is False
    )


def BuildRawPortalPlacementGeometryFingerprint(Placed: Any) -> str:
    """Identify placement geometry without depending on allocator identity."""
    Gates = getattr(Placed, "PlacedGates", None)
    if Gates is None:
        return ""
    return BuildStableFingerprint(tuple(sorted(
        (
            str(getattr(Gate, "Name", "")),
            str(getattr(Gate, "Kind", "")),
            int(getattr(Gate, "X", 0)),
            int(getattr(Gate, "Y", 0)),
            int(getattr(Gate, "Z", 0)),
            int(getattr(Gate, "Rotation", 0)),
            bool(getattr(Gate, "MirrorX", False)),
            tuple(getattr(Gate, "InputPins", ())),
            getattr(Gate, "OutputPin", None),
            tuple(getattr(Gate, "InputDirections", ())),
            getattr(Gate, "OutputDirection", None),
            tuple(map(str, getattr(Gate, "Inputs", ()))),
            tuple(map(str, getattr(Gate, "Outputs", ()))),
        )
        for Gate in Gates
    )))


def BuildRawPortalResourceGeometryFingerprint(
    Resources: Any,
) -> str:
    """Identify the immutable routing technology and obstacle geometry."""
    StaticGeometry = getattr(Resources, "StaticGeometry", None)
    if StaticGeometry is None:
        return ""
    ResourceGraph = getattr(Resources, "ResourceGraph", None)
    return BuildStableFingerprint((
        tuple(sorted(getattr(StaticGeometry, "ActualBlocks", ()))),
        tuple(sorted(getattr(StaticGeometry, "ElectricalBlocks", ()))),
        tuple(sorted(getattr(StaticGeometry, "SolidBlocks", ()))),
        tuple(sorted(getattr(
            StaticGeometry,
            "TemplateElectricalBlocks",
            (),
        ))),
        getattr(ResourceGraph, "GraphVersion", ""),
        repr(getattr(ResourceGraph, "Technology", None)),
        tuple(sorted(getattr(ResourceGraph, "ActualBlocks", ()))),
        tuple(sorted(getattr(ResourceGraph, "ElectricalBlocks", ()))),
        tuple(sorted(getattr(ResourceGraph, "SolidBlocks", ()))),
    ))


@dataclass(frozen=True)
class RawPortalGeometryCache:
    """Immutable native portal work reusable across routing-control retries."""

    PlacementGeometryFingerprint: str
    ResourceGeometryFingerprint: str
    PlacedReference: Any = field(compare=False, repr=False)
    ResourcesReference: Any = field(compare=False, repr=False)
    Region: Any
    LayerCount: int
    PortalLimit: int
    PortalVariantCounts: tuple[tuple[str, int], ...]
    GuideExpansion: int
    StrictMaximumExpansions: int
    Context: Any
    AssignmentIndexed: IndexedRoutingResourceGraph
    PortalEntries: tuple[
        tuple[tuple[str, Position3, int], tuple[PinAccessPortal, ...]], ...
    ]
    RequestCount: int
    TargetCount: int
    StarvationCount: int
    AssignmentEncodingCache: dict[str, tuple[Any, ...]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    AccessGeometryFingerprint: tuple[object, ...] = ()
    AssignedColumns: frozenset[Position2] = frozenset()
    ReservedAccess: frozenset[Position3] = frozenset()
    GuidePlanPrepared: bool = False
    GuideInputFingerprint: str = ""
    GuidePlan: Any = field(
        default=None,
        compare=False,
        repr=False,
    )
    SignalRequestCounts: tuple[tuple[str, int], ...] = ()
    SignalTargetCounts: tuple[tuple[str, int], ...] = ()
    SignalStarvationCounts: tuple[tuple[str, int], ...] = ()
    RetainedPortfolioSliceLimited: bool = False
    PhysicalGlobalKeepoutFingerprint: str = ""
    CompletePortalDomainKeys: tuple[
        tuple[str, Position3, int], ...
    ] = ()
    # Domains closed by routing policy (for example the interface deck being
    # reserved for inter-component signals) have no native portal request.
    # Keep their complete-empty proof separate so post-closure regeneration
    # can replay requests without silently losing those finite domains.
    PolicyCompleteEmptyPortalDomainKeys: tuple[
        tuple[str, Position3, int], ...
    ] = ()
    PortalRequestDomainFingerprints: tuple[tuple[str, str], ...] = ()
    ExteriorRegionFingerprint: str = ""
    AuthoritativeResourceGraphFingerprint: str = ""
    ConfiguredPortalRequests: tuple[tuple[Any, ...], ...] = field(
        default=(),
        compare=False,
        repr=False,
    )
    ConfiguredPortalRequestMetadata: tuple[
        tuple[str, Position3, int], ...
    ] = ()

    def MatchesPlacementResources(
        self,
        Placed: Any,
        Resources: Any,
        PlacementFingerprint: str | None = None,
        ResourceFingerprint: str | None = None,
    ) -> bool:
        """Match stable geometry, retaining identity only for opaque fixtures."""
        if PlacementFingerprint is None:
            PlacementFingerprint = (
                BuildRawPortalPlacementGeometryFingerprint(Placed)
            )
        if ResourceFingerprint is None:
            ResourceFingerprint = (
                BuildRawPortalResourceGeometryFingerprint(Resources)
            )
        return (
            (
                bool(self.PlacementGeometryFingerprint)
                and self.PlacementGeometryFingerprint
                == PlacementFingerprint
            )
            or (
                not self.PlacementGeometryFingerprint
                and self.PlacedReference is Placed
            )
        ) and (
            (
                bool(self.ResourceGeometryFingerprint)
                and self.ResourceGeometryFingerprint
                == ResourceFingerprint
            )
            or (
                not self.ResourceGeometryFingerprint
                and self.ResourcesReference is Resources
            )
        )
    def MatchesGuidePlan(
        self,
        Placed: Any,
        Resources: RoutingResources,
        LayerCount: int,
        GuideInputFingerprint: str = "",
    ) -> bool:
        """Return whether immutable same-geometry guide work is reusable."""
        return (
            self.GuidePlanPrepared
            and self.LayerCount == LayerCount
            and (
                self.GuideInputFingerprint
                == GuideInputFingerprint
                if (
                    self.GuideInputFingerprint
                    and GuideInputFingerprint
                )
                else (
                    self.MatchesPlacementResources(Placed, Resources)
                )
            )
        )

    def Matches(
        self,
        Placed: Any,
        Resources: RoutingResources,
        Region: Any,
        LayerCount: int,
        PortalLimit: int,
        PortalVariantCounts: dict[str, int],
        GuideExpansion: int,
        StrictMaximumExpansions: int,
        AccessGeometryFingerprint: tuple[object, ...] = (),
    ) -> bool:
        RegionIsCompatible = self.Region is Region or (
            getattr(self.Region, "Bounds", None)
            == getattr(Region, "Bounds", None)
            and getattr(self.Region, "Nodes", frozenset())
            <= getattr(Region, "Nodes", frozenset())
            and getattr(self.Region, "Edges", frozenset())
            <= getattr(Region, "Edges", frozenset())
        )
        return (
            self.MatchesPlacementResources(Placed, Resources)
            and (
                self.AccessGeometryFingerprint == AccessGeometryFingerprint
                if self.AccessGeometryFingerprint
                else True
            )
            and RegionIsCompatible
            and self.LayerCount == LayerCount
            and self.PortalLimit == PortalLimit
            and self.PortalVariantCounts
            == tuple(sorted(PortalVariantCounts.items()))
            and self.GuideExpansion == GuideExpansion
            and self.StrictMaximumExpansions == StrictMaximumExpansions
        )

    def BuildPortalDictionary(
        self,
    ) -> dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]]:
        return dict(self.PortalEntries)


def BuildPhysicalComponentGlobalPortalId(
    Port: PhysicalComponentPortReservation,
    Layer: int,
) -> str:
    """Identify the globally owned side of one component seam.

    A port reservation fingerprint also identifies its component-local
    access path.  Local interface-factor replanning is allowed to change
    that path while retaining an already routed external contract, so it
    must not change the authoritative portal identity carried by the global
    route candidate.
    """
    return (
        f"physical-global:{Port.Signal}:L{int(Layer)}:"
        f"{BuildPhysicalPortGlobalContractFingerprint(Port)}"
    )


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

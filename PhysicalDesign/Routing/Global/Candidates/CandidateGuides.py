"""Physical candidate guides and retained route domains."""

from __future__ import annotations

from ...Regions.Planning.PhysicalPlanning import SelectPhysicalAssemblyGlobalBoundaryPorts

from ....Contracts.Component import PhysicalComponentAssemblyPlan

from ....Contracts.Component import PhysicalComponentBoundaryPortReservation

from ....Contracts.Component import PhysicalComponentPortReservation

from ....Contracts.Core import Position2

from ....Contracts.Core import Position3

from ....Contracts.PhysicalInterface import CertifiedPhysicalComponentApertureDomain

from ....Contracts.PhysicalInterface import PhysicalComponentApertureFactor

from ....Contracts.PhysicalInterface import PhysicalGlobalPlanContinuationState

from ....Contracts.PhysicalInterface import PhysicalGlobalPlanResumeCursor

from ....Contracts.PhysicalInterface import PhysicalPortCorridorDomain

from ....Contracts.PhysicalInterface import PhysicalPortCorridorFactor

from ....Contracts.PhysicalInterface import PhysicalSignalApertureCandidateDomainIdentity

from ....Contracts.PhysicalInterface import PreparedPhysicalComponentAssembly

from ....Contracts.PhysicalInterface import PreparedPhysicalComponentPortFactorDomain

from ....Contracts.PhysicalInterface import RetainedPhysicalGlobalPlanFrontierEntry

from ....Contracts.Results import RoutingResources

from ....Contracts.Failures import RoutingFailure

from ....Contracts.Failures import RoutingFailureReason

from ....Constraints.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint

from ....Constraints.PhysicalClaims import ComponentClaimsConflict

from ....Runtime.Reliability import BuildStableFingerprint

from ....Runtime.Reliability import RoutingDeadline

from ....Resources.ResourceGraph import BuildRoutingEnvelope

from ....Resources.ResourceGraph import FindClaimConflicts

from ....Resources.ResourceGraph import NetRouteCandidate

from ....Resources.ResourceGraph import NormalizeRoutingEdge

from ....Resources.ResourceGraph import PinAccessPortal

from ....Resources.ResourceGraph import RoutingReservation

from ....Resources.ResourceGraph import RoutingResourceClaims

from ....Resources.ResourceGraph import RoutingResourceId

from collections import defaultdict

from dataclasses import dataclass

from dataclasses import field

from dataclasses import replace

from time import monotonic

from typing import Any

from typing import Callable

from typing import Iterable

from typing import Mapping

from ..Assignment.AssignmentState import CandidateRequestShapeDescriptor

from .CandidateCache import (
    TransformPlanarRoutingPosition,
)

from ..Ports.ExteriorConnectors import InvertPlanarRoutingTransform, PhysicalGlobalAperturePlanarTransforms

def BuildPortalAccessGeometryFingerprint(
    Profiles: dict[str, Any],
) -> tuple[object, ...]:
    """Fingerprint only geometry that determines native portal candidates."""
    return tuple(
        (
            Signal,
            Profile.Root,
            tuple(Profile.SourceAccessPath),
            tuple(
                (Target, tuple(Profile.TargetAccessPaths[Target]))
                for Target in Profile.Targets
            ),
        )
        for Signal, Profile in sorted(Profiles.items())
    )

def BuildCapacityAwareGuideInputFingerprint(
    Profiles: dict[str, Any],
    LayerCount: int,
    MinimumX: int,
    MinimumZ: int,
    Policy: Any,
    Technology: Any,
    LocalFanoutDistance: int,
) -> str:
    """Identify every deterministic input to the joint coarse-guide solve."""
    return BuildStableFingerprint((
        "capacity-aware-guide-input-v1",
        LayerCount,
        MinimumX,
        MinimumZ,
        LocalFanoutDistance,
        repr(Policy),
        repr(Technology),
        tuple(
            (
                Signal,
                tuple(sorted(
                    (
                        Path[-1][0],
                        Path[-1][2],
                    )
                    for Path in (
                        Profile.SourceAccessPath,
                        *Profile.TargetAccessPaths.values(),
                    )
                )),
                Profile.Span,
                Profile.Criticality,
                Profile.Fanout,
            )
            for Signal, Profile in sorted(Profiles.items())
        ),
    ))

def BuildPhysicalAssemblyGuideContractFingerprint(
    Plan: PhysicalComponentAssemblyPlan,
) -> str:
    """Identify only physical assembly state visible to global guides.

    Local terminal choices and component-template bookkeeping do not affect
    the whole-design guide assignment.  Keeping them out of this identity
    lets a replan reuse guide work when its external port and channel
    contracts are unchanged, while any changed keepout, attachment, portal
    path, guide, layer, or capacity still invalidates the joint plan.
    """
    PortsBySignal = {
        str(Port.Signal): Port
        for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
    }
    ChannelsBySignal = {
        str(Channel.Signal): Channel
        for Channel in getattr(Plan, "PlanningChannels", ())
    }
    Signals = tuple(sorted(set(PortsBySignal) | set(ChannelsBySignal)))
    return BuildStableFingerprint((
        "physical-assembly-guide-contract-v1",
        str(getattr(Plan, "GlobalKeepoutFingerprint", "")),
        tuple(
            (
                Signal,
                (
                    BuildPhysicalPortGlobalContractFingerprint(
                        PortsBySignal[Signal]
                    )
                    if Signal in PortsBySignal
                    else ""
                ),
                (
                    str(getattr(
                        ChannelsBySignal[Signal],
                        "ReservationFingerprint",
                        "",
                    ))
                    if Signal in ChannelsBySignal
                    else ""
                ),
            )
            for Signal in Signals
        ),
    ))

@dataclass(frozen=True)
class PhysicalSignalGuideFactor:
    """One signal-local guide choice separated from joint capacity state."""

    Signal: str
    LocalInputFingerprint: str
    GuideCells: frozenset[Position2]
    Layer: int
    Axis: str
    Lane: int
    OptionFingerprint: str

@dataclass(frozen=True)
class FactorizedPhysicalGuideIdentity:
    """Selected local guide factors plus their joint capacity assignment."""

    Factors: tuple[PhysicalSignalGuideFactor, ...]
    JointCapacityAssignmentFingerprint: str

    def FactorFingerprintBySignal(self) -> dict[str, str]:
        return {
            Factor.Signal: Factor.OptionFingerprint
            for Factor in self.Factors
        }

def BuildFactorizedPhysicalGuideIdentity(
    CoarsePlan: Any,
    LocalInputFingerprintsBySignal: Mapping[str, str],
) -> FactorizedPhysicalGuideIdentity:
    """Factor an existing authoritative guide plan without rerouting it.

    The local option identity contains only one signal's deterministic inputs
    and selected guide geometry.  Shared overflow/capacity state is retained
    exclusively in the joint fingerprint.  This allows later stages to prove
    that an unchanged signal factor is reusable while still treating joint
    guide feasibility as one authoritative global assignment.
    """
    Signals = tuple(sorted(getattr(CoarsePlan, "Guides", {})))
    Factors = tuple(
        PhysicalSignalGuideFactor(
            Signal=Signal,
            LocalInputFingerprint=str(
                LocalInputFingerprintsBySignal[Signal]
            ),
            GuideCells=frozenset(CoarsePlan.Guides[Signal]),
            Layer=int(CoarsePlan.Layers[Signal]),
            Axis=str(CoarsePlan.Axes[Signal]),
            Lane=int(CoarsePlan.Lanes[Signal]),
            OptionFingerprint=BuildStableFingerprint((
                "physical-signal-guide-factor-v1",
                str(LocalInputFingerprintsBySignal[Signal]),
                tuple(sorted(CoarsePlan.Guides[Signal])),
                int(CoarsePlan.Layers[Signal]),
                str(CoarsePlan.Axes[Signal]),
                int(CoarsePlan.Lanes[Signal]),
            )),
        )
        for Signal in Signals
    )
    return FactorizedPhysicalGuideIdentity(
        Factors=Factors,
        JointCapacityAssignmentFingerprint=BuildStableFingerprint((
            "physical-joint-guide-capacity-assignment-v1",
            tuple(
                (Factor.Signal, Factor.OptionFingerprint)
                for Factor in Factors
            ),
            tuple(sorted(
                (str(Key), int(Value))
                for Key, Value in dict(
                    getattr(CoarsePlan, "Overflow", {}) or {}
                ).items()
            )),
        )),
    )

def _BuildApertureClaimsFingerprint(Claims: Any) -> str:
    """Identify exact aperture ownership without relying on object identity."""
    return BuildStableFingerprint((
        tuple(sorted(getattr(Claims, "WireCells", ()))),
        tuple(sorted(getattr(Claims, "SupportCells", ()))),
        tuple(sorted(getattr(Claims, "RequiredAirCells", ()))),
        tuple(sorted(getattr(Claims, "ElectricalCells", ()))),
        tuple(sorted(map(str, getattr(Claims, "ResourceIds", ())))),
    ))

def BuildCertifiedPhysicalComponentApertureDomain(
    Plan: PhysicalComponentAssemblyPlan,
    *,
    Complete: bool,
) -> CertifiedPhysicalComponentApertureDomain:
    """Freeze every selected crossing over the unmodified keepout core.

    The core deliberately retains its exact node identity.  Apertures are
    signal-local exemptions from that core; sibling aperture claims are not
    subtracted here and must still be checked by the assembly-specific exact
    filter.
    """
    Ports = tuple(sorted(Plan.Ports, key=lambda Value: Value.Signal))
    CrossingSignals = tuple(Port.Signal for Port in Ports)
    if len(set(CrossingSignals)) != len(CrossingSignals):
        raise ValueError("component aperture domain has duplicate port signals")
    ChannelsBySignal = {
        Channel.Signal: Channel for Channel in Plan.PlanningChannels
    }
    if len(ChannelsBySignal) != len(Plan.PlanningChannels):
        raise ValueError("component aperture domain has duplicate channels")
    MissingChannels = tuple(
        Signal for Signal in CrossingSignals if Signal not in ChannelsBySignal
    )
    if Complete and MissingChannels:
        raise ValueError(
            "complete component aperture domain is missing channels: "
            + ", ".join(MissingChannels)
        )
    StableKeepoutCoreNodes = frozenset(Plan.GlobalKeepoutNodes)
    StableKeepoutCoreFingerprint = BuildStableFingerprint((
        "physical-component-stable-keepout-core-v1",
        str(Plan.ComponentGraphFingerprint),
        str(Plan.ResourceGraphFingerprint),
        str(Plan.TechnologyFingerprint),
        tuple(sorted(StableKeepoutCoreNodes)),
    ))
    Factors = []
    for Port in Ports:
        Channel = ChannelsBySignal.get(Port.Signal)
        if Channel is None:
            continue
        PassageNodes = frozenset(Port.GlobalPath)
        GlobalClaims = getattr(Port, "GlobalClaims", None)
        if GlobalClaims is None:
            raise ValueError(
                "component aperture domain requires global port claims"
            )
        ClaimsFingerprint = _BuildApertureClaimsFingerprint(GlobalClaims)
        PortGlobalContractFingerprint = (
            BuildPhysicalPortGlobalContractFingerprint(Port)
        )
        ApertureFingerprint = BuildStableFingerprint((
            "physical-component-aperture-v2",
            Port.Signal,
            PortGlobalContractFingerprint,
            Channel.ReservationFingerprint,
            tuple(sorted(PassageNodes)),
            ClaimsFingerprint,
        ))
        Factors.append(PhysicalComponentApertureFactor(
            Signal=Port.Signal,
            PortReservationFingerprint=Port.ReservationFingerprint,
            PortGlobalContractFingerprint=PortGlobalContractFingerprint,
            ChannelReservationFingerprint=Channel.ReservationFingerprint,
            PassageNodes=PassageNodes,
            ClaimsFingerprint=ClaimsFingerprint,
            ApertureFingerprint=ApertureFingerprint,
        ))
    EffectiveComplete = bool(
        Complete
        and len(Factors) == len(CrossingSignals)
        and tuple(Factor.Signal for Factor in Factors) == CrossingSignals
    )
    DomainFingerprint = BuildStableFingerprint((
        "certified-physical-component-aperture-domain-v2",
        str(Plan.ComponentGraphFingerprint),
        StableKeepoutCoreFingerprint,
        str(Plan.ResourceGraphFingerprint),
        str(Plan.TechnologyFingerprint),
        CrossingSignals,
        tuple(Factor.ApertureFingerprint for Factor in Factors),
        EffectiveComplete,
    ))
    return CertifiedPhysicalComponentApertureDomain(
        DomainFingerprint=DomainFingerprint,
        ComponentDomainFingerprint=str(Plan.ComponentGraphFingerprint),
        StableKeepoutCoreFingerprint=StableKeepoutCoreFingerprint,
        StableKeepoutCoreNodes=StableKeepoutCoreNodes,
        ResourceGraphFingerprint=str(Plan.ResourceGraphFingerprint),
        TechnologyFingerprint=str(Plan.TechnologyFingerprint),
        CrossingSignals=CrossingSignals,
        Factors=tuple(Factors),
        Complete=EffectiveComplete,
    )

def BuildPhysicalSignalApertureCandidateDomainIdentity(
    ApertureDomain: CertifiedPhysicalComponentApertureDomain,
    Signal: str,
    RequestDependencyFingerprint: str,
    BlockedNodes: Iterable[Position3],
    *,
    CoverageCursor: int,
    Complete: bool,
) -> PhysicalSignalApertureCandidateDomainIdentity:
    """Bind one candidate domain to an aperture and exact blocked core."""
    if not ApertureDomain.Complete:
        raise ValueError("candidate identity requires a complete aperture domain")
    MatchingFactors = tuple(
        Factor for Factor in ApertureDomain.Factors
        if Factor.Signal == Signal
    )
    if len(MatchingFactors) != 1:
        raise ValueError("candidate identity requires exactly one signal aperture")
    if CoverageCursor < 0:
        raise ValueError("CoverageCursor must be nonnegative")
    Factor = MatchingFactors[0]
    SortedBlockedNodes = tuple(sorted(frozenset(BlockedNodes)))
    BlockedNodesFingerprint = BuildStableFingerprint((
        "physical-signal-aperture-blocked-nodes-v1",
        SortedBlockedNodes,
    ))
    StableDomainFingerprint = BuildStableFingerprint((
        "physical-signal-exterior-route-domain-v1",
        Signal,
        Factor.PortGlobalContractFingerprint,
        RequestDependencyFingerprint,
        ApertureDomain.StableKeepoutCoreFingerprint,
        BlockedNodesFingerprint,
        ApertureDomain.ResourceGraphFingerprint,
        ApertureDomain.TechnologyFingerprint,
    ))
    DomainFingerprint = BuildStableFingerprint((
        "physical-signal-aperture-candidate-domain-v2",
        StableDomainFingerprint,
        Factor.ApertureFingerprint,
        Factor.ChannelReservationFingerprint,
        int(CoverageCursor),
        bool(Complete),
    ))
    return PhysicalSignalApertureCandidateDomainIdentity(
        DomainFingerprint=DomainFingerprint,
        StableDomainFingerprint=StableDomainFingerprint,
        Signal=Signal,
        ApertureFingerprint=Factor.ApertureFingerprint,
        PortGlobalContractFingerprint=(
            Factor.PortGlobalContractFingerprint
        ),
        ChannelReservationFingerprint=(
            Factor.ChannelReservationFingerprint
        ),
        RequestDependencyFingerprint=RequestDependencyFingerprint,
        StableKeepoutCoreFingerprint=(
            ApertureDomain.StableKeepoutCoreFingerprint
        ),
        BlockedNodesFingerprint=BlockedNodesFingerprint,
        ResourceGraphFingerprint=ApertureDomain.ResourceGraphFingerprint,
        TechnologyFingerprint=ApertureDomain.TechnologyFingerprint,
        CoverageCursor=int(CoverageCursor),
        Complete=bool(Complete),
    )

def FilterPhysicalCandidatesAgainstSiblingApertures(
    Candidates: Iterable[NetRouteCandidate],
    SiblingApertures: Iterable[tuple[str, RoutingResourceClaims]],
    *,
    ConflictClassifier: Callable[
        [RoutingResourceClaims], tuple[str, ...]
    ] | None = None,
) -> tuple[NetRouteCandidate, ...]:
    """Project candidates through sibling ownership with proof witnesses.

    Exact exterior domains are cached before sibling filtering.  Replaying
    one must classify every candidate through the same observer used during
    fresh materialization; otherwise an empty projection loses the blockers
    needed for a request/aperture factor proof and degrades into a broad
    whole-assembly no-good.
    """
    Siblings = tuple(SiblingApertures)
    Retained: list[NetRouteCandidate] = []
    for Candidate in Candidates:
        ConflictSignals = (
            tuple(ConflictClassifier(Candidate.Claims))
            if ConflictClassifier is not None
            else tuple(sorted(
                SiblingSignal
                for SiblingSignal, SiblingClaims in Siblings
                if ComponentClaimsConflict(
                    Candidate.Claims,
                    SiblingClaims,
                )
            ))
        )
        if not ConflictSignals:
            Retained.append(Candidate)
    return tuple(Retained)

def CompletePhysicalCandidatePairDomainsHaveNoSupport(
    FirstCandidates: Iterable[NetRouteCandidate],
    SecondCandidates: Iterable[NetRouteCandidate],
) -> bool:
    """Prove two complete pre-sibling route domains have no compatible pair."""
    FirstValues = tuple(FirstCandidates)
    SecondValues = tuple(SecondCandidates)
    return bool(
        FirstValues
        and SecondValues
        and all(
            FindClaimConflicts({
                First.Signal: First.Claims,
                Second.Signal: Second.Claims,
            })
            for First in FirstValues
            for Second in SecondValues
        )
    )

def ClassifySiblingApertureSeamOwnershipConflicts(
    Claims: RoutingResourceClaims,
    FullSiblingApertures: Iterable[tuple[str, RoutingResourceClaims]],
    GlobalPathSiblingApertures: Iterable[
        tuple[str, RoutingResourceClaims]
    ],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Compare full reserved seams with their globally owned path subset."""
    FullConflictSignals = tuple(sorted(
        Signal
        for Signal, SiblingClaims in FullSiblingApertures
        if ComponentClaimsConflict(Claims, SiblingClaims)
    ))
    GlobalPathConflictSignals = tuple(sorted(
        Signal
        for Signal, SiblingClaims in GlobalPathSiblingApertures
        if ComponentClaimsConflict(Claims, SiblingClaims)
    ))
    LocalInteriorOnlyConflictSignals = tuple(sorted(
        frozenset(FullConflictSignals)
        - frozenset(GlobalPathConflictSignals)
    ))
    return (
        FullConflictSignals,
        GlobalPathConflictSignals,
        LocalInteriorOnlyConflictSignals,
    )

def BuildMinimalPhysicalRequestApertureNoGood(
    Signal: str,
    RequestFactorFingerprint: str,
    CandidateConflictSignals: Iterable[Iterable[str]],
    ApertureFingerprintBySignal: Mapping[str, str],
) -> frozenset[tuple[str, str]]:
    """Build a deletion-minimal exact request/aperture starvation clause."""
    Signal = str(Signal)
    RequestFactorFingerprint = str(RequestFactorFingerprint)
    ConflictSets = tuple(
        frozenset(map(str, Values))
        for Values in CandidateConflictSignals
        if Values
    )
    if not Signal or not RequestFactorFingerprint or not ConflictSets:
        return frozenset()
    Blockers = set().union(*ConflictSets)
    if not Blockers or any(
        Blocker not in ApertureFingerprintBySignal
        or not ApertureFingerprintBySignal[Blocker]
        for Blocker in Blockers
    ):
        return frozenset()
    for Blocker in sorted(tuple(Blockers)):
        CandidateBlockers = Blockers - {Blocker}
        if CandidateBlockers and all(
            ConflictSet & CandidateBlockers
            for ConflictSet in ConflictSets
        ):
            Blockers = CandidateBlockers
    if not all(ConflictSet & Blockers for ConflictSet in ConflictSets):
        return frozenset()
    return frozenset((
        (Signal, "request-factor:" + RequestFactorFingerprint),
        *(
            (
                Blocker,
                "aperture-factor:"
                + str(ApertureFingerprintBySignal[Blocker]),
            )
            for Blocker in sorted(Blockers)
        ),
    ))

def BuildCompletePhysicalRequestAlternativeApertureNoGoods(
    Signal: str,
    PortGlobalContractFingerprint: str,
    CompletePreSiblingCandidates: Iterable[RouteCandidate],
    BoundaryPortReservationsBySignal: Mapping[
        str, Iterable[PhysicalComponentBoundaryPortReservation]
    ],
) -> tuple[frozenset[tuple[str, str]], ...]:
    """Certify every alternative aperture that starves one fixed request.

    A complete pre-sibling route domain depends on the victim's frozen global
    contract, guide, and keepout, but not on a sibling aperture.  Comparing
    that finite domain with every prepared sibling aperture therefore turns
    one exact exterior run into a complete family of binary CSP clauses.
    """
    Signal = str(Signal)
    PortGlobalContractFingerprint = str(
        PortGlobalContractFingerprint
    )
    Candidates = tuple(CompletePreSiblingCandidates)
    if not Signal or not PortGlobalContractFingerprint or not Candidates:
        return ()
    Clauses = set()
    for BlockerSignal, Ports in sorted(
        BoundaryPortReservationsBySignal.items()
    ):
        BlockerSignal = str(BlockerSignal)
        if BlockerSignal == Signal:
            continue
        for Port in Ports:
            ApertureContractFingerprint = str(
                Port.ApertureContractFingerprint
            )
            if not ApertureContractFingerprint:
                continue
            if all(
                ComponentClaimsConflict(
                    Candidate.Claims,
                    Port.GlobalClaims,
                )
                for Candidate in Candidates
            ):
                Clauses.add(frozenset((
                    (Signal, PortGlobalContractFingerprint),
                    (BlockerSignal, ApertureContractFingerprint),
                )))
    return tuple(sorted(
        Clauses,
        key=lambda Clause: tuple(sorted(Clause)),
    ))

def PhysicalSignalLocalCandidateRequestFactorProofComplete(
    Signal: str,
    DependencyComponents: Mapping[str, object],
    ExactGlobalSignals: Iterable[str],
    PhysicalPortGuidesBySignal: Mapping[str, frozenset[Position2]],
    ApertureDomain: CertifiedPhysicalComponentApertureDomain | None,
) -> bool:
    """Certify that one exterior candidate domain has local determinants."""
    Signal = str(Signal)
    ExactSignals = frozenset(map(str, ExactGlobalSignals))
    if (
        not Signal
        or Signal not in ExactSignals
        or Signal not in PhysicalPortGuidesBySignal
        or not PhysicalPortGuidesBySignal[Signal]
        or ApertureDomain is None
        or not ApertureDomain.Complete
        or not frozenset(ApertureDomain.CrossingSignals) <= ExactSignals
    ):
        return False
    MatchingFactors = tuple(
        Factor for Factor in ApertureDomain.Factors
        if Factor.Signal == Signal
    )
    if len(MatchingFactors) != 1:
        return False
    Factor = MatchingFactors[0]
    return bool(
        DependencyComponents.get("GlobalContractFingerprint", "")
        == Factor.PortGlobalContractFingerprint
        and DependencyComponents.get("ChannelFingerprint", "")
        == Factor.ChannelReservationFingerprint
        and DependencyComponents.get("GlobalKeepoutFingerprint", "")
        == ApertureDomain.StableKeepoutCoreFingerprint
        # A factorized guide fingerprint is a signal-local determinant of
        # this same finite request domain.  The component planner freezes the
        # guide-only plan for the retained placement, so its presence cannot
        # introduce an unrepresented foreign CSP variable.
        and "GuideFactorFingerprint" in DependencyComponents
        and DependencyComponents.get("BlockedNodesFingerprint", "")
        and DependencyComponents.get("DescriptorDomainFingerprint", "")
        and int(DependencyComponents.get("DescriptorCount", 0)) > 0
    )

@dataclass(frozen=True)
class PhysicalSignalRouteDomainContinuation:
    """Replayable pre-sibling candidate cursor for one exact route domain."""

    PreSiblingDomainFingerprint: str
    Signal: str
    RequestDomainFingerprint: str
    RequestDescriptorFingerprints: tuple[str, ...]
    NextDescriptorCursor: int
    Candidates: tuple[NetRouteCandidate, ...]
    CandidateMetadata: tuple[tuple[str, Any], ...] = ()
    CompletedDescriptorFingerprints: frozenset[str] = frozenset()
    Complete: bool = False

    def __post_init__(self) -> None:
        if not self.RequestDomainFingerprint:
            raise ValueError("route-domain continuation domain is unidentified")
        DescriptorCount = len(self.RequestDescriptorFingerprints)
        DescriptorSet = frozenset(self.RequestDescriptorFingerprints)
        if len(DescriptorSet) != DescriptorCount:
            raise ValueError(
                "route-domain descriptor identities must be unique"
            )
        if not 0 <= int(self.NextDescriptorCursor) <= DescriptorCount:
            raise ValueError("route-domain continuation cursor is out of range")
        if not self.CompletedDescriptorFingerprints <= DescriptorSet:
            raise ValueError(
                "route-domain completion contains a foreign descriptor"
            )
        CandidateIds = tuple(
            Candidate.CandidateId for Candidate in self.Candidates
        )
        MetadataIds = tuple(Key for Key, _Value in self.CandidateMetadata)
        if (
            len(set(CandidateIds)) != len(CandidateIds)
            or len(set(MetadataIds)) != len(MetadataIds)
            or set(CandidateIds) != set(MetadataIds)
        ):
            raise ValueError(
                "route-domain candidates require closed unique metadata"
            )
        if (
            self.Complete
            and self.CompletedDescriptorFingerprints != DescriptorSet
        ):
            raise ValueError(
                "complete route-domain continuation has unfinished descriptors"
            )

    @property
    def RemainingDescriptorFingerprints(self) -> frozenset[str]:
        return frozenset(self.RequestDescriptorFingerprints) - (
            self.CompletedDescriptorFingerprints
        )

    @property
    def DescriptorUniverseFingerprint(self) -> str:
        return BuildStableFingerprint((
            "physical-route-descriptor-universe-v1",
            self.RequestDomainFingerprint,
            self.RequestDescriptorFingerprints,
        ))

    @property
    def ProgressFingerprint(self) -> str:
        Metadata = dict(self.CandidateMetadata)
        return BuildStableFingerprint((
            "physical-signal-route-descriptor-progress-v1",
            self.PreSiblingDomainFingerprint,
            self.RequestDomainFingerprint,
            self.RequestDescriptorFingerprints,
            tuple(sorted(self.CompletedDescriptorFingerprints)),
            tuple(
                (
                    Candidate.CandidateId,
                    BuildStableFingerprint((
                        Candidate,
                        Metadata[Candidate.CandidateId],
                    )),
                )
                for Candidate in self.Candidates
            ),
        ))

    def ToProgressDictionary(self) -> dict[str, object]:
        return {
            "PreSiblingDomainFingerprint": (
                self.PreSiblingDomainFingerprint
            ),
            "RequestDomainFingerprint": self.RequestDomainFingerprint,
            "DescriptorUniverseFingerprint": (
                self.DescriptorUniverseFingerprint
            ),
            "DescriptorCount": len(self.RequestDescriptorFingerprints),
            "CompletedDescriptorCount": len(
                self.CompletedDescriptorFingerprints
            ),
            "CompletedDescriptorFingerprints": sorted(
                self.CompletedDescriptorFingerprints
            ),
            "RemainingDescriptorCount": len(
                self.RemainingDescriptorFingerprints
            ),
            "SemanticCandidateCount": len(self.Candidates),
            "CandidateMetadataClosed": (
                len(self.Candidates) == len(self.CandidateMetadata)
            ),
            "ProgressFingerprint": self.ProgressFingerprint,
            "Complete": self.Complete,
            "RawResultCacheAuthoritative": False,
        }

def MergePhysicalSignalRouteDomainDescriptorProgress(
    Existing: PhysicalSignalRouteDomainContinuation | None,
    *,
    PreSiblingDomainFingerprint: str,
    Signal: str,
    RequestDomainFingerprint: str,
    RequestDescriptorFingerprints: tuple[str, ...],
    CompletedDescriptorFingerprints: Iterable[str],
    Candidates: Iterable[NetRouteCandidate],
    CandidateMetadata: Mapping[str, Any],
) -> PhysicalSignalRouteDomainContinuation:
    """Monotonically merge exact descriptor bits and semantic candidates."""
    Descriptors = tuple(RequestDescriptorFingerprints)
    if len(set(Descriptors)) != len(Descriptors):
        raise ValueError("physical route descriptor vector is not unique")
    DescriptorSet = frozenset(Descriptors)
    AddedCompleted = frozenset(CompletedDescriptorFingerprints)
    if not AddedCompleted <= DescriptorSet:
        raise ValueError("completed physical route descriptor is foreign")
    if Existing is not None and (
        Existing.PreSiblingDomainFingerprint
        != str(PreSiblingDomainFingerprint)
        or Existing.Signal != str(Signal)
        or Existing.RequestDomainFingerprint
        != str(RequestDomainFingerprint)
        or Existing.RequestDescriptorFingerprints != Descriptors
    ):
        raise ValueError("physical route progress identity mismatch")

    CandidateById = {
        Candidate.CandidateId: Candidate
        for Candidate in (Existing.Candidates if Existing is not None else ())
    }
    MetadataById = dict(
        Existing.CandidateMetadata if Existing is not None else ()
    )
    for Candidate in Candidates:
        CandidateId = str(Candidate.CandidateId)
        if CandidateId not in CandidateMetadata:
            raise ValueError("semantic route candidate metadata is incomplete")
        Metadata = CandidateMetadata[CandidateId]
        PriorCandidate = CandidateById.get(CandidateId)
        PriorMetadata = MetadataById.get(CandidateId)
        if (
            PriorCandidate is not None
            and (
                PriorCandidate != Candidate
                or PriorMetadata != Metadata
            )
        ):
            raise ValueError("semantic route candidate identity collision")
        CandidateById[CandidateId] = Candidate
        MetadataById[CandidateId] = Metadata
    if set(CandidateById) != set(MetadataById):
        raise ValueError("semantic route candidate metadata is not closed")

    Completed = frozenset((
        *(
            Existing.CompletedDescriptorFingerprints
            if Existing is not None
            else ()
        ),
        *AddedCompleted,
    ))
    Complete = Completed == DescriptorSet
    return PhysicalSignalRouteDomainContinuation(
        PreSiblingDomainFingerprint=str(PreSiblingDomainFingerprint),
        Signal=str(Signal),
        RequestDomainFingerprint=str(RequestDomainFingerprint),
        RequestDescriptorFingerprints=Descriptors,
        # Compatibility telemetry only. Scheduling uses exact set difference.
        NextDescriptorCursor=len(Completed),
        Candidates=tuple(
            CandidateById[CandidateId]
            for CandidateId in sorted(CandidateById)
        ),
        CandidateMetadata=tuple(
            (CandidateId, MetadataById[CandidateId])
            for CandidateId in sorted(CandidateById)
        ),
        CompletedDescriptorFingerprints=Completed,
        Complete=Complete,
    )

def SelectPendingPhysicalRouteDescriptorRows(
    Requests: Iterable[Any],
    Metadata: Iterable[Any],
    DescriptorFingerprints: Iterable[str],
    CompletedDescriptorFingerprints: Iterable[str],
) -> tuple[tuple[Any, Any, str], ...]:
    """Schedule the exact descriptor-set difference in declared order."""
    RequestValues = tuple(Requests)
    MetadataValues = tuple(Metadata)
    DescriptorValues = tuple(DescriptorFingerprints)
    if not (
        len(RequestValues) == len(MetadataValues) == len(DescriptorValues)
    ):
        raise ValueError("physical descriptor rows are not aligned")
    if len(set(DescriptorValues)) != len(DescriptorValues):
        raise ValueError("physical descriptor row identities are not unique")
    Completed = frozenset(CompletedDescriptorFingerprints)
    if not Completed <= frozenset(DescriptorValues):
        raise ValueError("physical descriptor completion is foreign")
    return tuple(
        (Request, MetadataValue, DescriptorFingerprint)
        for Request, MetadataValue, DescriptorFingerprint in zip(
            RequestValues,
            MetadataValues,
            DescriptorValues,
        )
        if DescriptorFingerprint not in Completed
    )

def RetainPhysicalSignalRouteDomainDescriptorProgress(
    Cache: dict[str, Any],
    *,
    PreSiblingDomainFingerprint: str,
    Signal: str,
    RequestDomainFingerprint: str,
    RequestDescriptorFingerprints: tuple[str, ...],
    CompletedDescriptorFingerprints: Iterable[str],
    Candidates: Iterable[NetRouteCandidate],
    CandidateMetadata: Mapping[str, Any],
    MaximumEntries: int = 512,
) -> tuple[PhysicalSignalRouteDomainContinuation, bool]:
    """Publish identity-bound progress independently of raw result eviction."""
    Existing = SelectReplayablePhysicalSignalRouteDomainContinuation(
        Cache,
        PreSiblingDomainFingerprint,
        Signal,
        RequestDomainFingerprint,
        RequestDescriptorFingerprints,
    )
    PriorCompleted = (
        Existing.CompletedDescriptorFingerprints
        if Existing is not None
        else frozenset()
    )
    Progress = MergePhysicalSignalRouteDomainDescriptorProgress(
        Existing,
        PreSiblingDomainFingerprint=PreSiblingDomainFingerprint,
        Signal=Signal,
        RequestDomainFingerprint=RequestDomainFingerprint,
        RequestDescriptorFingerprints=RequestDescriptorFingerprints,
        CompletedDescriptorFingerprints=CompletedDescriptorFingerprints,
        Candidates=Candidates,
        CandidateMetadata=CandidateMetadata,
    )
    Cache[PreSiblingDomainFingerprint] = Progress
    while len(Cache) > MaximumEntries:
        Cache.pop(next(iter(Cache)))
    return Progress, bool(
        Progress.CompletedDescriptorFingerprints > PriorCompleted
    )

def BuildPhysicalRouteDescriptorRemainingCounts(
    Cache: Mapping[str, Any],
    IdentitiesBySignal: Mapping[
        str, PhysicalSignalApertureCandidateDomainIdentity
    ],
    RequestDomainFingerprintsBySignal: Mapping[str, str],
    RequestDescriptorFingerprintsBySignal: Mapping[
        str, tuple[str, ...]
    ],
) -> dict[str, int]:
    """Derive exact remaining work from descriptor identities alone."""
    Remaining = {}
    for Signal, Descriptors in (
        RequestDescriptorFingerprintsBySignal.items()
    ):
        Identity = IdentitiesBySignal.get(Signal)
        Continuation = (
            SelectReplayablePhysicalSignalRouteDomainContinuation(
                Cache,
                Identity.StableDomainFingerprint,
                Signal,
                str(RequestDomainFingerprintsBySignal.get(Signal, "")),
                tuple(Descriptors),
            )
            if Identity is not None
            else None
        )
        Remaining[Signal] = (
            len(Continuation.RemainingDescriptorFingerprints)
            if Continuation is not None
            else len(Descriptors)
        )
    return Remaining

def PhysicalSignalRouteDomainIsCertifiedEmpty(
    Continuation: PhysicalSignalRouteDomainContinuation,
    *,
    Signal: str,
    PreSiblingDomainFingerprint: str,
    RequestDomainFingerprint: str,
) -> bool:
    """Verify a replayed exact exterior domain is completely empty."""
    return bool(
        Continuation.Complete
        and Continuation.Signal == str(Signal)
        and Continuation.PreSiblingDomainFingerprint
        == str(PreSiblingDomainFingerprint)
        and Continuation.RequestDomainFingerprint
        == str(RequestDomainFingerprint)
        and Continuation.CompletedDescriptorFingerprints
        == frozenset(Continuation.RequestDescriptorFingerprints)
        and not Continuation.Candidates
    )

def BuildCertifiedEmptyPhysicalSignalRouteDomainFailure(
    Signal: str,
    Continuation: PhysicalSignalRouteDomainContinuation,
    DependencyComponents: Mapping[str, object],
) -> RoutingFailure:
    """Publish one complete signal-local exterior routing cut."""
    return RoutingFailure(
        Reason=RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
        Stage="PhysicalComponentGlobalCandidateDomain",
        AffectedNets=(str(Signal),),
        Detail=(
            "the certified exterior route domain for the fixed physical "
            "port is empty"
        ),
        RepairActions=(),
        Diagnostics={
            "GlobalPlanDomainComplete": True,
            "CompleteAssignmentCutProof": True,
            "IndependentEmptyCandidateDomainSignals": [str(Signal)],
            "CandidateRequestDependencyComponents": dict(
                DependencyComponents
            ),
            "ConflictGraph": {
                "Classification": "certified-empty-exterior-route-domain",
                "ConflictSignals": [str(Signal)],
                "NoCandidateSignals": [str(Signal)],
                "CompleteAssignmentCutProof": True,
            },
            "ReplayedContinuationFingerprint": (
                Continuation.PreSiblingDomainFingerprint
            ),
            "ImplicitForeignTransitDomainCount": 0,
        },
    )

@dataclass(frozen=True)
class PortablePhysicalSignalRouteDomainContinuation:
    """One complete exterior domain in translation/planar-normal form.

    Capture requires a complete source domain, but replay exposes its
    candidates only.  It cannot transfer a request cursor, descriptor
    completion, or an empty-domain proof across a physical plan identity.
    """

    PortableDomainFingerprint: str
    IdentityFingerprint: str
    Signal: str
    Attachment: Position3
    CanonicalTransform: str
    PortalGeometryById: tuple[tuple[str, tuple[object, ...]], ...]
    Candidates: tuple[NetRouteCandidate, ...]
    CandidateMetadata: tuple[tuple[str, Any], ...]
    Complete: bool = True

    def __post_init__(self) -> None:
        if not self.PortableDomainFingerprint or not self.IdentityFingerprint:
            raise ValueError("portable route domain identity is incomplete")
        if not self.Complete:
            raise ValueError("portable route domains must be complete")

@dataclass(frozen=True)
class PortablePhysicalSignalRouteDomainPreparation:
    """Cheap structural bucket plus deferred full canonicalization inputs."""

    StructuralPrekey: str
    IdentityFingerprint: str
    Plan: PhysicalComponentAssemblyPlan = field(compare=False, repr=False)
    Signal: str
    Descriptors: tuple[CandidateRequestShapeDescriptor, ...] = field(
        compare=False,
        repr=False,
    )
    FixedRequiredNodes: tuple[Position3, ...] = field(compare=False, repr=False)
    BlockedNodes: tuple[Position3, ...] = field(compare=False, repr=False)
    SeedStarts: tuple[Position3, ...] = field(compare=False, repr=False)
    DetachedSeedAnchors: tuple[Position3, ...] = field(
        compare=False,
        repr=False,
    )

def PreparePortablePhysicalSignalRouteDomain(
    Plan: PhysicalComponentAssemblyPlan,
    Signal: str,
    Descriptors: Iterable[CandidateRequestShapeDescriptor],
    FixedRequiredNodes: Iterable[Position3],
    BlockedNodes: Iterable[Position3],
    SeedStarts: Iterable[Position3],
    DetachedSeedAnchors: Iterable[Position3],
) -> PortablePhysicalSignalRouteDomainPreparation:
    """Build an O(descriptor-count) transform-invariant cache prekey."""
    Port = next((
        Value for Value in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
        if str(Value.Signal) == str(Signal)
    ), None)
    Channel = next((
        Value for Value in Plan.PlanningChannels
        if str(Value.Signal) == str(Signal)
    ), None)
    if Port is None or Channel is None:
        raise ValueError("portable route domain requires a fixed port/channel")
    DescriptorValues = tuple(Descriptors)
    FixedValues = tuple(FixedRequiredNodes)
    BlockedValues = tuple(BlockedNodes)
    SeedValues = tuple(SeedStarts)
    DetachedValues = tuple(DetachedSeedAnchors)
    IdentityFingerprint = BuildStableFingerprint((
        "portable-physical-signal-route-domain-identity-v1",
        str(Plan.ComponentGraphFingerprint),
        str(Plan.TechnologyFingerprint),
        str(Signal),
        str(getattr(Port, "Direction", "")),
        int(getattr(Port, "Capacity", 1)),
        int(Channel.Layer),
        int(Channel.Capacity),
        tuple(sorted(map(int, Channel.FeedthroughComponentIds))),
    ))
    AttachmentY = int(Port.Attachment[1])
    StructuralPrekey = BuildStableFingerprint((
        "portable-physical-signal-route-domain-prekey-v1",
        IdentityFingerprint,
        tuple(sorted(
            (
                len(Descriptor.SourcePortal.Path),
                tuple(sorted(
                    len(Portal.Path)
                    for Portal in Descriptor.TargetPortals
                )),
                len(Descriptor.Guide),
                int(Descriptor.Layer),
                int(Descriptor.RoutingY) - AttachmentY,
                int(Descriptor.GuideExpansion),
            )
            for Descriptor in DescriptorValues
        )),
        len(FixedValues),
        len(BlockedValues),
        len(SeedValues),
        len(DetachedValues),
    ))
    return PortablePhysicalSignalRouteDomainPreparation(
        StructuralPrekey=StructuralPrekey,
        IdentityFingerprint=IdentityFingerprint,
        Plan=Plan,
        Signal=str(Signal),
        Descriptors=DescriptorValues,
        FixedRequiredNodes=FixedValues,
        BlockedNodes=BlockedValues,
        SeedStarts=SeedValues,
        DetachedSeedAnchors=DetachedValues,
    )

def _NormalizePortableRoutePosition(
    Position: Position3,
    Attachment: Position3,
    Transform: str,
) -> Position3:
    return TransformPlanarRoutingPosition(
        tuple(
            int(Position[Index]) - int(Attachment[Index])
            for Index in range(3)
        ),
        Transform,
    )

def _PortablePortalGeometry(
    Portal: PinAccessPortal,
    Attachment: Position3,
    Transform: str,
) -> tuple[object, ...]:
    return (
        int(Portal.Layer),
        _NormalizePortableRoutePosition(
            Portal.Terminal,
            Attachment,
            Transform,
        ),
        tuple(
            _NormalizePortableRoutePosition(
                Position,
                Attachment,
                Transform,
            )
            for Position in Portal.Path
        ),
    )

def BuildPortablePhysicalSignalRouteDomainIdentity(
    Plan: PhysicalComponentAssemblyPlan,
    Signal: str,
    Descriptors: Iterable[CandidateRequestShapeDescriptor],
    FixedRequiredNodes: Iterable[Position3],
    BlockedNodes: Iterable[Position3],
    SeedStarts: Iterable[Position3],
    DetachedSeedAnchors: Iterable[Position3],
) -> tuple[str, str, Position3, str, tuple[tuple[str, tuple[object, ...]], ...]]:
    """Canonicalize the complete finite exterior request contract."""
    Port = next((
        Value for Value in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
        if str(Value.Signal) == str(Signal)
    ), None)
    Channel = next((
        Value for Value in Plan.PlanningChannels
        if str(Value.Signal) == str(Signal)
    ), None)
    if Port is None or Channel is None:
        raise ValueError("portable route domain requires a fixed port/channel")
    Attachment = tuple(map(int, Port.Attachment))
    DescriptorValues = tuple(Descriptors)
    FixedRequiredNodes = tuple(FixedRequiredNodes)
    BlockedNodes = tuple(BlockedNodes)
    SeedStarts = tuple(SeedStarts)
    DetachedSeedAnchors = tuple(DetachedSeedAnchors)
    PortalsById = {
        str(Portal.PortalId): Portal
        for Descriptor in DescriptorValues
        for Portal in (Descriptor.SourcePortal, *Descriptor.TargetPortals)
    }
    IdentityFingerprint = PreparePortablePhysicalSignalRouteDomain(
        Plan,
        Signal,
        DescriptorValues,
        FixedRequiredNodes,
        BlockedNodes,
        SeedStarts,
        DetachedSeedAnchors,
    ).IdentityFingerprint
    Candidates = []
    for Transform in PhysicalGlobalAperturePlanarTransforms:
        def Normalize(Position: Position3) -> Position3:
            return _NormalizePortableRoutePosition(
                tuple(map(int, Position)), Attachment, Transform
            )

        PortalGeometryById = tuple(sorted(
            (
                PortalId,
                _PortablePortalGeometry(Portal, Attachment, Transform),
            )
            for PortalId, Portal in PortalsById.items()
        ))
        DescriptorContract = tuple(sorted(
            (
                _PortablePortalGeometry(
                    Descriptor.SourcePortal, Attachment, Transform
                ),
                tuple(sorted(
                    _PortablePortalGeometry(
                        Portal, Attachment, Transform
                    )
                    for Portal in Descriptor.TargetPortals
                )),
                tuple(sorted(
                    (Normalize((X, Attachment[1], Z))[0],
                     Normalize((X, Attachment[1], Z))[2])
                    for X, Z in Descriptor.Guide
                )),
                int(Descriptor.Layer),
                int(Descriptor.RoutingY) - int(Attachment[1]),
                int(Descriptor.GuideExpansion),
            )
            for Descriptor in DescriptorValues
        ))
        Contract = (
            "portable-physical-signal-route-domain-v1",
            IdentityFingerprint,
            DescriptorContract,
            tuple(sorted(Normalize(Value) for Value in FixedRequiredNodes)),
            tuple(sorted(Normalize(Value) for Value in BlockedNodes)),
            tuple(sorted(Normalize(Value) for Value in SeedStarts)),
            tuple(sorted(Normalize(Value) for Value in DetachedSeedAnchors)),
        )
        Candidates.append((Contract, Transform, PortalGeometryById))
    Contract, Transform, PortalGeometryById = min(
        Candidates,
        key=lambda Value: repr(Value[0]),
    )
    return (
        BuildStableFingerprint(Contract),
        IdentityFingerprint,
        Attachment,
        Transform,
        PortalGeometryById,
    )

def _TransformPortableCandidate(
    Candidate: NetRouteCandidate,
    OldAttachment: Position3,
    OldTransform: str,
    NewAttachment: Position3,
    NewTransform: str,
    PortalIdMap: Mapping[str, str],
) -> NetRouteCandidate:
    def Position(Value: Position3) -> Position3:
        Normalized = _NormalizePortableRoutePosition(
            Value, OldAttachment, OldTransform
        )
        return TransformPlanarRoutingPosition(
            Normalized,
            InvertPlanarRoutingTransform(NewTransform),
            NewAttachment,
        )

    def Claims(Value: RoutingResourceClaims) -> RoutingResourceClaims:
        return RoutingResourceClaims(
            WireCells=frozenset(map(Position, Value.WireCells)),
            SupportCells=frozenset(map(Position, Value.SupportCells)),
            RequiredAirCells=frozenset(map(Position, Value.RequiredAirCells)),
            ElectricalCells=frozenset(map(Position, Value.ElectricalCells)),
        )

    InputFacingToOutputDelta = {
        "west": (1, 0, 0), "east": (-1, 0, 0),
        "north": (0, 0, 1), "south": (0, 0, -1),
    }
    OutputDeltaToInputFacing = {
        Value: Key for Key, Value in InputFacingToOutputDelta.items()
    }
    def InputFacing(Value: str | None) -> str | None:
        if Value not in InputFacingToOutputDelta:
            return Value
        Normalized = TransformPlanarRoutingPosition(
            InputFacingToOutputDelta[Value], OldTransform
        )
        Materialized = TransformPlanarRoutingPosition(
            Normalized, InvertPlanarRoutingTransform(NewTransform)
        )
        return OutputDeltaToInputFacing[Materialized]

    Reservations = tuple(
        RoutingReservation(
            Signal=Value.Signal,
            Resource=RoutingResourceId(
                Value.Resource.Kind, Position(Value.Resource.Position)
            ),
            Position=Position(Value.Position),
            Purpose=Value.Purpose,
            InputFacing=InputFacing(Value.InputFacing),
        )
        for Value in Candidate.RepeaterReservations
    )
    Nodes = frozenset(map(Position, Candidate.Nodes))
    ResultClaims = Claims(Candidate.Claims)
    CandidateId = BuildStableFingerprint((
        "portable-route-domain-materialization-v1",
        Candidate.Signal,
        tuple(sorted(Nodes)),
        tuple(sorted(
            NormalizeRoutingEdge(Position(First), Position(Second))
            for First, Second in Candidate.Edges
        )),
        tuple(sorted(map(str, ResultClaims.ResourceIds))),
        PortalIdMap[Candidate.SourcePortalId],
        tuple(sorted(
            PortalIdMap[Value]
            for Value in Candidate.TargetPortalIds.values()
        )),
        tuple(
            (Value.Position, Value.Purpose, Value.InputFacing)
            for Value in Reservations
        ),
        int(Candidate.Layer),
    ))
    return replace(
        Candidate,
        CandidateId=CandidateId,
        SourcePortalId=PortalIdMap[Candidate.SourcePortalId],
        TargetPortalIds={
            Position(Target): PortalIdMap[PortalId]
            for Target, PortalId in Candidate.TargetPortalIds.items()
        },
        Nodes=Nodes,
        Edges=frozenset(
            NormalizeRoutingEdge(Position(First), Position(Second))
            for First, Second in Candidate.Edges
        ),
        Claims=ResultClaims,
        Guide=frozenset(
            (
                Position((X, OldAttachment[1], Z))[0],
                Position((X, OldAttachment[1], Z))[2],
            )
            for X, Z in Candidate.Guide
        ),
        RepeaterWaypoints=tuple(map(Position, Candidate.RepeaterWaypoints)),
        RepeaterReservations=Reservations,
        TargetPaths={
            Position(Target): tuple(map(Position, Path))
            for Target, Path in Candidate.TargetPaths.items()
        },
        BranchClaims={
            Position(Target): Claims(Value)
            for Target, Value in Candidate.BranchClaims.items()
        },
        Envelope=BuildRoutingEnvelope(
            Nodes,
            ResultClaims.SupportCells,
            tuple(Value.Position for Value in Reservations),
        ),
    )

def _TransformPortableCandidateMetadata(
    Metadata: Any,
    OldAttachment: Position3,
    OldTransform: str,
    NewAttachment: Position3,
    NewTransform: str,
) -> Any:
    """Transform the physical axis/lane witness carried by a candidate."""
    if not (
        isinstance(Metadata, tuple)
        and len(Metadata) == 4
        and Metadata[0] in {"X", "Z"}
    ):
        raise ValueError("portable candidate metadata is not geometric")
    Axis, Lane, Layer, SeedNodeCount = Metadata

    def Position(Value: Position3) -> Position3:
        Normalized = _NormalizePortableRoutePosition(
            Value, OldAttachment, OldTransform
        )
        return TransformPlanarRoutingPosition(
            Normalized,
            InvertPlanarRoutingTransform(NewTransform),
            NewAttachment,
        )

    if Axis == "X":
        First = (OldAttachment[0], OldAttachment[1], int(Lane))
        Second = (OldAttachment[0] + 1, OldAttachment[1], int(Lane))
    else:
        First = (int(Lane), OldAttachment[1], OldAttachment[2])
        Second = (int(Lane), OldAttachment[1], OldAttachment[2] + 1)
    NewFirst = Position(First)
    NewSecond = Position(Second)
    Delta = (
        abs(NewSecond[0] - NewFirst[0]),
        abs(NewSecond[2] - NewFirst[2]),
    )
    if Delta == (1, 0):
        NewAxis = "X"
        NewLane = NewFirst[2]
    elif Delta == (0, 1):
        NewAxis = "Z"
        NewLane = NewFirst[0]
    else:
        raise ValueError("portable candidate axis did not remain planar")
    return (NewAxis, int(NewLane), int(Layer), int(SeedNodeCount))

def SelectPortablePhysicalSignalRouteDomainContinuation(
    Cache: Mapping[str, Any],
    PortableDomainFingerprint: str,
    IdentityFingerprint: str,
    Signal: str,
    Attachment: Position3,
    CanonicalTransform: str,
    PortalGeometryById: tuple[tuple[str, tuple[object, ...]], ...],
) -> PhysicalSignalRouteDomainContinuation | None:
    """Rebind positive portable witnesses to current fixed portal IDs."""
    CacheKey = "portable-route-domain:" + PortableDomainFingerprint
    Value = Cache.get(CacheKey)
    return _MaterializePortablePhysicalSignalCompleteDomainCandidates(
        Value,
        PortableDomainFingerprint,
        IdentityFingerprint,
        Signal,
        Attachment,
        CanonicalTransform,
        PortalGeometryById,
        RequirePortableDomainIdentity=True,
    )

def _MaterializePortablePhysicalSignalCompleteDomainCandidates(
    Value: Any,
    PortableDomainFingerprint: str,
    IdentityFingerprint: str,
    Signal: str,
    Attachment: Position3,
    CanonicalTransform: str,
    PortalGeometryById: tuple[tuple[str, tuple[object, ...]], ...],
    *,
    RequirePortableDomainIdentity: bool,
) -> PhysicalSignalRouteDomainContinuation | None:
    """Transform complete-domain candidates without importing its proof."""
    if not isinstance(Value, PortablePhysicalSignalRouteDomainContinuation):
        return None
    if (
        not Value.Complete
        or (RequirePortableDomainIdentity and (
            Value.PortableDomainFingerprint != PortableDomainFingerprint
        ))
        or Value.IdentityFingerprint != IdentityFingerprint
        or Value.Signal != Signal
        or not Value.Candidates
    ):
        return None
    CurrentByGeometry = {
        Geometry: PortalId for PortalId, Geometry in PortalGeometryById
    }
    if (
        len(CurrentByGeometry) != len(PortalGeometryById)
        or len({
            Geometry for _PortalId, Geometry
            in Value.PortalGeometryById
        }) != len(Value.PortalGeometryById)
    ):
        return None
    PortalIdMap = {
        OldId: CurrentByGeometry.get(Geometry, "")
        for OldId, Geometry in Value.PortalGeometryById
    }
    if not PortalIdMap or any(not Current for Current in PortalIdMap.values()):
        return None
    try:
        Candidates = tuple(
            _TransformPortableCandidate(
                Candidate,
                Value.Attachment,
                Value.CanonicalTransform,
                Attachment,
                CanonicalTransform,
                PortalIdMap,
            )
            for Candidate in Value.Candidates
        )
    except (KeyError, ValueError):
        return None
    MetadataByOldId = dict(Value.CandidateMetadata)
    Metadata = tuple(
        (
            Candidate.CandidateId,
            _TransformPortableCandidateMetadata(
                MetadataByOldId[Old.CandidateId],
                Value.Attachment,
                Value.CanonicalTransform,
                Attachment,
                CanonicalTransform,
            ),
        )
        for Old, Candidate in zip(Value.Candidates, Candidates)
        if Old.CandidateId in MetadataByOldId
    )
    if len(Metadata) != len(Candidates):
        return None
    return PhysicalSignalRouteDomainContinuation(
        PreSiblingDomainFingerprint=PortableDomainFingerprint,
        Signal=Signal,
        RequestDomainFingerprint=PortableDomainFingerprint,
        RequestDescriptorFingerprints=(),
        NextDescriptorCursor=0,
        Candidates=Candidates,
        CandidateMetadata=Metadata,
        CompletedDescriptorFingerprints=frozenset(),
        Complete=False,
    )

def SelectPreparedPortablePhysicalSignalRouteDomainContinuation(
    Cache: Mapping[str, Any],
    Preparation: PortablePhysicalSignalRouteDomainPreparation,
) -> tuple[PhysicalSignalRouteDomainContinuation | None, str]:
    """Probe the cheap bucket before paying for full canonical geometry."""
    BucketKey = (
        "portable-route-domain-bucket:" + Preparation.StructuralPrekey
    )
    Bucket = Cache.get(BucketKey)
    if not isinstance(Bucket, Mapping) or not Bucket:
        return None, "structural-bucket-miss"
    Identity = BuildPortablePhysicalSignalRouteDomainIdentity(
        Preparation.Plan,
        Preparation.Signal,
        Preparation.Descriptors,
        Preparation.FixedRequiredNodes,
        Preparation.BlockedNodes,
        Preparation.SeedStarts,
        Preparation.DetachedSeedAnchors,
    )
    FullKey = "portable-route-domain:" + Identity[0]
    if FullKey not in Bucket:
        return None, "full-identity-mismatch"
    Continuation = SelectPortablePhysicalSignalRouteDomainContinuation(
        Bucket,
        Identity[0],
        Identity[1],
        Preparation.Signal,
        Identity[2],
        Identity[3],
        Identity[4],
    )
    if Continuation is None:
        return None, "portal-rebind-mismatch"
    return Continuation, "hit"

def SelectPortableReplayTelemetryReason(
    DomainTelemetry: Mapping[str, Any],
) -> str:
    """Read the normalized portable replay outcome from any domain row."""
    return str(
        DomainTelemetry.get("PortableReplayReason")
        or DomainTelemetry.get("Reason")
        or ""
    )

def RetainPortablePhysicalSignalRouteDomainContinuation(
    Cache: dict[str, Any],
    PortableDomainFingerprint: str,
    IdentityFingerprint: str,
    Signal: str,
    Attachment: Position3,
    CanonicalTransform: str,
    PortalGeometryById: tuple[tuple[str, tuple[object, ...]], ...],
    Candidates: Iterable[NetRouteCandidate],
    CandidateMetadata: Mapping[str, Any],
    *,
    Complete: bool,
) -> PortablePhysicalSignalRouteDomainContinuation | None:
    """Publish only complete, metadata-closed pre-sibling domains."""
    CandidateValues = tuple(Candidates)
    if (
        not Complete
        or any(Value.CandidateId not in CandidateMetadata for Value in CandidateValues)
    ):
        return None
    Value = PortablePhysicalSignalRouteDomainContinuation(
        PortableDomainFingerprint=PortableDomainFingerprint,
        IdentityFingerprint=IdentityFingerprint,
        Signal=Signal,
        Attachment=Attachment,
        CanonicalTransform=CanonicalTransform,
        PortalGeometryById=PortalGeometryById,
        Candidates=CandidateValues,
        CandidateMetadata=tuple(sorted(CandidateMetadata.items())),
    )
    CacheKey = "portable-route-domain:" + PortableDomainFingerprint
    Cache.pop(CacheKey, None)
    Cache[CacheKey] = Value
    PortableKeys = tuple(
        Key for Key in Cache
        if str(Key).startswith("portable-route-domain:")
    )
    for Key in PortableKeys[:-512]:
        Cache.pop(Key, None)
    return Value

def RetainCompletePortablePhysicalSignalRouteDomains(
    Cache: dict[str, Any],
    PreparationsBySignal: Mapping[
        str, PortablePhysicalSignalRouteDomainPreparation
    ],
    RemainingRequestCountsBySignal: Mapping[str, int],
    CandidatesBySignal: Mapping[str, Iterable[NetRouteCandidate]],
    CandidateMetadataBySignal: Mapping[str, Mapping[str, Any]],
) -> tuple[PortablePhysicalSignalRouteDomainContinuation, ...]:
    """Publish the closed pre-sibling domains with portable identities."""
    Retained = []
    for Signal, Preparation in sorted(PreparationsBySignal.items()):
        if int(RemainingRequestCountsBySignal.get(Signal, 0)) != 0:
            continue
        CandidateValues = tuple(CandidatesBySignal.get(Signal, ()))
        CandidateMetadata = CandidateMetadataBySignal.get(Signal, {})
        if any(
            Candidate.CandidateId not in CandidateMetadata
            for Candidate in CandidateValues
        ):
            continue
        Identity = BuildPortablePhysicalSignalRouteDomainIdentity(
            Preparation.Plan,
            Preparation.Signal,
            Preparation.Descriptors,
            Preparation.FixedRequiredNodes,
            Preparation.BlockedNodes,
            Preparation.SeedStarts,
            Preparation.DetachedSeedAnchors,
        )
        BucketKey = (
            "portable-route-domain-bucket:"
            + Preparation.StructuralPrekey
        )
        Bucket = Cache.get(BucketKey)
        if not isinstance(Bucket, dict):
            Bucket = {}
            Cache[BucketKey] = Bucket
            PortableBucketKeys = tuple(
                Key for Key in Cache
                if str(Key).startswith(
                    "portable-route-domain-bucket:"
                )
            )
            for Key in PortableBucketKeys[:-512]:
                Cache.pop(Key, None)
        Value = RetainPortablePhysicalSignalRouteDomainContinuation(
            Cache=Bucket,
            PortableDomainFingerprint=Identity[0],
            IdentityFingerprint=Identity[1],
            Signal=Signal,
            Attachment=Identity[2],
            CanonicalTransform=Identity[3],
            PortalGeometryById=Identity[4],
            Candidates=CandidateValues,
            CandidateMetadata=CandidateMetadata,
            Complete=True,
        )
        if Value is not None:
            Retained.append(Value)
    return tuple(Retained)

def SelectReplayablePhysicalSignalRouteDomainContinuation(
    Cache: Mapping[str, Any],
    PreSiblingDomainFingerprint: str,
    Signal: str,
    RequestDomainFingerprint: str,
    RequestDescriptorFingerprints: tuple[str, ...],
) -> PhysicalSignalRouteDomainContinuation | None:
    """Replay descriptor progress only under its full ordered universe."""
    Value = Cache.get(PreSiblingDomainFingerprint)
    if not isinstance(Value, PhysicalSignalRouteDomainContinuation):
        return None
    if (
        Value.PreSiblingDomainFingerprint != PreSiblingDomainFingerprint
        or Value.Signal != Signal
        or Value.RequestDomainFingerprint != RequestDomainFingerprint
        or Value.RequestDescriptorFingerprints
        != tuple(RequestDescriptorFingerprints)
    ):
        return None
    return Value

def RetainCompletePhysicalSignalRouteDomainContinuations(
    Cache: dict[str, Any],
    IdentitiesBySignal: Mapping[
        str, PhysicalSignalApertureCandidateDomainIdentity
    ],
    RequestDescriptorFingerprintsBySignal: Mapping[
        str, tuple[str, ...]
    ],
    RequestDomainFingerprintsBySignal: Mapping[str, str],
    RemainingRequestCountsBySignal: Mapping[str, int],
    CandidatesBySignal: Mapping[str, Iterable[NetRouteCandidate]],
    CandidateMetadataBySignal: Mapping[str, Mapping[str, Any]],
) -> tuple[PhysicalSignalRouteDomainContinuation, ...]:
    """Publish only fully consumed pre-sibling native candidate domains."""
    Retained = []
    for Signal, Identity in sorted(IdentitiesBySignal.items()):
        Descriptors = tuple(
            RequestDescriptorFingerprintsBySignal.get(Signal, ())
        )
        if int(RemainingRequestCountsBySignal.get(Signal, 0)) != 0:
            continue
        RequestDomainFingerprint = str(
            RequestDomainFingerprintsBySignal.get(Signal, "")
        )
        if not RequestDomainFingerprint:
            continue
        Existing = SelectReplayablePhysicalSignalRouteDomainContinuation(
            Cache,
            Identity.StableDomainFingerprint,
            Signal,
            RequestDomainFingerprint,
            Descriptors,
        )
        if (
            Existing is None
            or Existing.CompletedDescriptorFingerprints
            != frozenset(Descriptors)
        ):
            # Scalar remaining counts cannot manufacture descriptor proof.
            continue
        Candidates = tuple(CandidatesBySignal.get(Signal, ()))
        CandidateMetadata = dict(
            CandidateMetadataBySignal.get(Signal, {})
        )
        # Candidate ordering consumes the axis/lane/layer/seed witness after
        # replay.  A candidate-only continuation is not complete handoff
        # state: publishing it would either crash or require inventing search
        # metadata under a later plan.  Retain only identity-closed payloads.
        if any(
            Candidate.CandidateId not in CandidateMetadata
            for Candidate in Candidates
        ):
            continue
        Continuation = MergePhysicalSignalRouteDomainDescriptorProgress(
            Existing,
            PreSiblingDomainFingerprint=Identity.StableDomainFingerprint,
            Signal=Signal,
            RequestDomainFingerprint=RequestDomainFingerprint,
            RequestDescriptorFingerprints=Descriptors,
            CompletedDescriptorFingerprints=(),
            Candidates=Candidates,
            CandidateMetadata=CandidateMetadata,
        )
        Cache[Identity.StableDomainFingerprint] = Continuation
        Retained.append(Continuation)
    while len(Cache) > 512:
        Cache.pop(next(iter(Cache)))
    return tuple(Retained)

def PropagateLaneFactorArcConsistency(
    Domains: dict[str, tuple[str, ...]],
    SupportIndex: dict[tuple[str, str, str], frozenset[str]],
) -> tuple[dict[str, tuple[str, ...]] | None, int]:
    """Compute the complete binary lane-factor support fixed point."""
    Propagated = {
        Signal: tuple(Values)
        for Signal, Values in Domains.items()
    }
    IntersectionCount = 0
    Changed = True
    while Changed:
        Changed = False
        DomainSets = {
            Signal: frozenset(Values)
            for Signal, Values in Propagated.items()
        }
        for Signal in sorted(Propagated):
            SupportedValues = []
            for Fingerprint in Propagated[Signal]:
                HasSupport = True
                for OtherSignal in sorted(Propagated):
                    if OtherSignal == Signal:
                        continue
                    IntersectionCount += 1
                    if not (
                        SupportIndex.get(
                            (Signal, Fingerprint, OtherSignal),
                            frozenset(),
                        )
                        & DomainSets[OtherSignal]
                    ):
                        HasSupport = False
                        break
                if HasSupport:
                    SupportedValues.append(Fingerprint)
            Supported = tuple(SupportedValues)
            if not Supported:
                return None, IntersectionCount
            if Supported != Propagated[Signal]:
                Propagated[Signal] = Supported
                Changed = True
    return Propagated, IntersectionCount

def BuildPhysicalPortCorridorFactor(
    Port: PhysicalComponentPortReservation,
    Candidate: NetRouteCandidate,
    RequestDependencyFingerprint: str,
) -> PhysicalPortCorridorFactor:
    """Bind one exact route-tree result to its physical port request."""
    if Candidate.Signal != Port.Signal:
        raise ValueError("corridor candidate and port signals differ")
    if not RequestDependencyFingerprint:
        raise ValueError("corridor factor requires request dependency identity")
    if Port.Attachment not in Candidate.Nodes:
        raise ValueError("corridor candidate does not terminate at its port")

    Origin = Port.Attachment

    def Relative(Position: Position3) -> Position3:
        return tuple(
            int(Position[Index]) - int(Origin[Index])
            for Index in range(3)
        )

    NormalizedClaims = tuple(
        (
            Name,
            tuple(sorted(Relative(Position) for Position in Positions)),
        )
        for Name, Positions in (
            ("wire", Candidate.Claims.WireCells),
            ("support", Candidate.Claims.SupportCells),
            ("air", Candidate.Claims.RequiredAirCells),
            ("electrical", Candidate.Claims.ElectricalCells),
        )
    )
    NormalizedIdentityFingerprint = BuildStableFingerprint((
        "physical-port-corridor-normalized-v1",
        Port.Direction,
        int(Port.Capacity),
        int(Candidate.Layer),
        tuple(sorted(Relative(Position) for Position in Candidate.Nodes)),
        tuple(sorted(
            tuple(sorted((Relative(First), Relative(Second))))
            for First, Second in Candidate.Edges
        )),
        NormalizedClaims,
        tuple(sorted(
            Relative(Position)
            for Position in Candidate.RepeaterWaypoints
        )),
    ))
    RouteCandidateFingerprint = BuildStableFingerprint((
        "physical-port-corridor-exact-candidate-v1",
        Candidate.CandidateId,
        Candidate.Signal,
        tuple(sorted(Candidate.Nodes)),
        tuple(sorted(Candidate.Edges)),
        tuple(sorted(map(str, Candidate.Claims.ResourceIds))),
        int(Candidate.Layer),
    ))
    return PhysicalPortCorridorFactor(
        Signal=Port.Signal,
        PortReservationFingerprint=Port.ReservationFingerprint,
        PortGlobalContractFingerprint=(
            BuildPhysicalPortGlobalContractFingerprint(Port)
        ),
        RequestDependencyFingerprint=RequestDependencyFingerprint,
        RouteCandidateId=Candidate.CandidateId,
        RouteCandidateFingerprint=RouteCandidateFingerprint,
        NormalizedIdentityFingerprint=NormalizedIdentityFingerprint,
        Layer=int(Candidate.Layer),
        Nodes=frozenset(Candidate.Nodes),
        Claims=Candidate.Claims,
        Candidate=Candidate,
    )

def BuildPhysicalPortCorridorDomain(
    Port: PhysicalComponentPortReservation,
    Candidates: Iterable[NetRouteCandidate],
    RequestDependencyFingerprint: str,
    ResourceGraphFingerprint: str,
    TechnologyFingerprint: str,
    *,
    Complete: bool,
    PortableRequestFamilyFingerprint: str = "",
) -> PhysicalPortCorridorDomain:
    """Build a deterministic finite domain without inventing guide claims."""
    FactorsByIdentity: dict[str, PhysicalPortCorridorFactor] = {}
    for Candidate in Candidates:
        Factor = BuildPhysicalPortCorridorFactor(
            Port,
            Candidate,
            RequestDependencyFingerprint,
        )
        Existing = FactorsByIdentity.get(
            Factor.NormalizedIdentityFingerprint
        )
        if Existing is None or Factor.RouteCandidateId < (
            Existing.RouteCandidateId
        ):
            FactorsByIdentity[
                Factor.NormalizedIdentityFingerprint
            ] = Factor
    Factors = tuple(
        FactorsByIdentity[Fingerprint]
        for Fingerprint in sorted(FactorsByIdentity)
    )
    GlobalContractFingerprint = (
        BuildPhysicalPortGlobalContractFingerprint(Port)
    )
    DomainFingerprint = BuildStableFingerprint((
        "physical-port-corridor-domain-v2",
        Port.Signal,
        GlobalContractFingerprint,
        RequestDependencyFingerprint,
        ResourceGraphFingerprint,
        TechnologyFingerprint,
        bool(Complete),
        str(PortableRequestFamilyFingerprint),
        tuple(
            Factor.NormalizedIdentityFingerprint
            for Factor in Factors
        ),
    ))
    return PhysicalPortCorridorDomain(
        DomainFingerprint=DomainFingerprint,
        Signal=Port.Signal,
        PortReservationFingerprint=Port.ReservationFingerprint,
        PortGlobalContractFingerprint=GlobalContractFingerprint,
        RequestDependencyFingerprint=RequestDependencyFingerprint,
        ResourceGraphFingerprint=ResourceGraphFingerprint,
        TechnologyFingerprint=TechnologyFingerprint,
        Factors=Factors,
        Complete=bool(Complete),
        PortableRequestFamilyFingerprint=str(
            PortableRequestFamilyFingerprint
        ),
    )

def CaptureCompletePhysicalPortCorridorDomains(
    Plan: PhysicalComponentAssemblyPlan | None,
    CandidatesBySignal: Mapping[str, Iterable[NetRouteCandidate]],
    RequestDependencyFingerprintsBySignal: Mapping[str, str],
    RemainingRequestCountsBySignal: Mapping[str, int],
    Resources: RoutingResources,
) -> tuple[PhysicalPortCorridorDomain, ...]:
    """Persist only exact signal domains whose finite request cursor closed."""
    if Plan is None:
        return ()
    PortsBySignal = {Port.Signal: Port for Port in Plan.Ports}
    Captured = []
    for Signal in sorted(CandidatesBySignal):
        Port = PortsBySignal.get(Signal)
        DependencyFingerprint = str(
            RequestDependencyFingerprintsBySignal.get(Signal, "")
        )
        if (
            Port is None
            or not DependencyFingerprint
            or int(RemainingRequestCountsBySignal.get(Signal, 0)) != 0
        ):
            continue
        Domain = BuildPhysicalPortCorridorDomain(
            Port,
            CandidatesBySignal[Signal],
            DependencyFingerprint,
            Plan.ResourceGraphFingerprint,
            Plan.TechnologyFingerprint,
            Complete=True,
        )
        Resources.PhysicalPortCorridorDomainCache[
            Domain.DomainFingerprint
        ] = Domain
        Captured.append(Domain)
    return tuple(Captured)

def BuildPreparedPhysicalExteriorGuideColumnsBySignal(
    Preparation: PreparedPhysicalComponentPortFactorDomain | None,
) -> dict[str, frozenset[Position2]]:
    """Union every legal prepared port guide/seam into one stable fabric."""
    if Preparation is None or not Preparation.Complete:
        return {}
    return {
        Signal: frozenset((
            *(
                Cell
                for LaneFactor in LaneFactors
                for Cell in LaneFactor.GuideCells
            ),
            *(
                (int(Position[0]), int(Position[2]))
                for LaneFactor in LaneFactors
                for Seam in LaneFactor.Seams
                for Position in Seam.GlobalPath
            ),
        ))
        for Signal, LaneFactors in Preparation.LaneFactorsBySignal
    }

def PhysicalPortCorridorFactorsCompatible(
    First: PhysicalPortCorridorFactor,
    Second: PhysicalPortCorridorFactor,
) -> bool:
    """Test exact authoritative claims; coarse guides never participate."""
    if First.Signal == Second.Signal:
        return (
            First.NormalizedIdentityFingerprint
            == Second.NormalizedIdentityFingerprint
        )
    return not ComponentClaimsConflict(First.Claims, Second.Claims)

def BuildPhysicalPortCorridorArcSupportIndex(
    Domains: Mapping[str, PhysicalPortCorridorDomain],
) -> dict[tuple[str, str, str], frozenset[str]]:
    """Compile binary exact-corridor support for deterministic AC passes."""
    Support: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for Signal, Domain in Domains.items():
        if Signal != Domain.Signal:
            raise ValueError("corridor domain stored under the wrong signal")
    Signals = tuple(sorted(Domains))
    for FirstIndex, FirstSignal in enumerate(Signals):
        for SecondSignal in Signals[FirstIndex + 1:]:
            for First in Domains[FirstSignal].Factors:
                for Second in Domains[SecondSignal].Factors:
                    if not PhysicalPortCorridorFactorsCompatible(
                        First,
                        Second,
                    ):
                        continue
                    Support[(
                        FirstSignal,
                        First.NormalizedIdentityFingerprint,
                        SecondSignal,
                    )].add(Second.NormalizedIdentityFingerprint)
                    Support[(
                        SecondSignal,
                        Second.NormalizedIdentityFingerprint,
                        FirstSignal,
                    )].add(First.NormalizedIdentityFingerprint)
    return {
        Key: frozenset(Values)
        for Key, Values in Support.items()
    }

def PropagatePhysicalPortCorridorArcConsistency(
    Domains: Mapping[str, PhysicalPortCorridorDomain],
) -> tuple[
    dict[str, tuple[PhysicalPortCorridorFactor, ...]] | None,
    int,
    bool,
]:
    """Prune exact corridor factors and report whether emptiness is proven."""
    ProofComplete = bool(Domains) and all(
        Domain.Complete for Domain in Domains.values()
    )
    if any(not Domain.Factors for Domain in Domains.values()):
        return None, 0, ProofComplete
    Support = BuildPhysicalPortCorridorArcSupportIndex(Domains)
    FingerprintDomains = {
        Signal: tuple(
            Factor.NormalizedIdentityFingerprint
            for Factor in Domain.Factors
        )
        for Signal, Domain in Domains.items()
    }
    Propagated, IntersectionCount = PropagateLaneFactorArcConsistency(
        FingerprintDomains,
        Support,
    )
    if Propagated is None:
        return None, IntersectionCount, ProofComplete
    return {
        Signal: tuple(
            Factor
            for Factor in Domains[Signal].Factors
            if Factor.NormalizedIdentityFingerprint
            in frozenset(Propagated[Signal])
        )
        for Signal in Domains
    }, IntersectionCount, ProofComplete

def SelectReusablePhysicalPortCorridorCandidates(
    CachedDomains: Mapping[str, PhysicalPortCorridorDomain],
    PortsBySignal: Mapping[str, PhysicalComponentPortReservation],
    RequestDependencyFingerprintBySignal: Mapping[str, str],
    ResourceGraphFingerprint: str,
    TechnologyFingerprint: str,
    CurrentRequestShapesBySignal: Mapping[
        str, tuple[CandidateRequestShapeDescriptor, ...]
    ] | None = None,
) -> dict[str, tuple[NetRouteCandidate, ...]]:
    """Select complete exact global domains under unchanged dependencies."""
    CurrentRequestShapesBySignal = dict(
        CurrentRequestShapesBySignal or {}
    )

    def RebindCandidate(
        Candidate: NetRouteCandidate,
        Shapes: tuple[CandidateRequestShapeDescriptor, ...],
    ) -> NetRouteCandidate | None:
        if not Shapes:
            return Candidate
        TargetKeys = tuple(Candidate.TargetPortalIds)
        Matches = tuple(sorted(
            (
                Shape
                for Shape in Shapes
                if (
                    int(Shape.Layer) == int(Candidate.Layer)
                    and frozenset(Shape.SourcePortal.Path).issubset(
                        Candidate.Nodes
                    )
                    and len(Shape.TargetPortals) == len(TargetKeys)
                    and all(
                        frozenset(Portal.Path).issubset(Candidate.Nodes)
                        for Portal in Shape.TargetPortals
                    )
                )
            ),
            key=lambda Shape: (
                str(Shape.SourcePortal.PortalId),
                tuple(
                    str(Portal.PortalId)
                    for Portal in Shape.TargetPortals
                ),
            ),
        ))
        if not Matches:
            return None
        Shape = Matches[0]
        return replace(
            Candidate,
            SourcePortalId=str(Shape.SourcePortal.PortalId),
            TargetPortalIds={
                Target: str(Portal.PortalId)
                for Target, Portal in zip(
                    TargetKeys,
                    Shape.TargetPortals,
                )
            },
        )

    Reused: dict[str, tuple[NetRouteCandidate, ...]] = {}
    for Signal in sorted(PortsBySignal):
        Port = PortsBySignal[Signal]
        RequestFingerprint = str(
            RequestDependencyFingerprintBySignal.get(Signal, "")
        )
        if not RequestFingerprint:
            continue
        GlobalContractFingerprint = (
            BuildPhysicalPortGlobalContractFingerprint(Port)
        )
        Matching = tuple(
            Domain
            for Domain in CachedDomains.values()
            if (
                Domain.Complete
                and Domain.Signal == Signal
                and Domain.PortGlobalContractFingerprint
                == GlobalContractFingerprint
                and Domain.RequestDependencyFingerprint
                == RequestFingerprint
                and Domain.ResourceGraphFingerprint
                == ResourceGraphFingerprint
                and Domain.TechnologyFingerprint
                == TechnologyFingerprint
                and Domain.Factors
                and all(
                    isinstance(Factor.Candidate, NetRouteCandidate)
                    and Factor.Candidate.Signal == Signal
                    and Port.Attachment in Factor.Candidate.Nodes
                    for Factor in Domain.Factors
                )
            )
        )
        if not Matching:
            continue
        Domain = min(Matching, key=lambda Value: Value.DomainFingerprint)
        Rebound = tuple(
            RebindCandidate(
                Factor.Candidate,
                CurrentRequestShapesBySignal.get(Signal, ()),
            )
            for Factor in Domain.Factors
        )
        if any(Candidate is None for Candidate in Rebound):
            continue
        Reused[Signal] = tuple(
            Candidate for Candidate in Rebound if Candidate is not None
        )
    return Reused

def BuildPhysicalGlobalPlanContinuationState(
    Plan: PhysicalComponentAssemblyPlan,
    RequestDependencyFingerprints: Mapping[str, str],
    RemainingRequestCounts: Mapping[str, int],
    CorridorDomains: Iterable[PhysicalPortCorridorDomain],
    CertificateFingerprints: Iterable[str],
    *,
    CompletedWork: int,
    ResumeCursor: PhysicalGlobalPlanResumeCursor | None = None,
) -> PhysicalGlobalPlanContinuationState:
    """Capture proof-neutral progress for one incomplete exact plan."""
    PortsBySignal = {Port.Signal: Port for Port in Plan.Ports}
    CorridorDomainFingerprints = []
    for Domain in CorridorDomains:
        Port = PortsBySignal.get(Domain.Signal)
        if (
            Port is None
            or Domain.PortReservationFingerprint
            != Port.ReservationFingerprint
            or Domain.PortGlobalContractFingerprint
            != BuildPhysicalPortGlobalContractFingerprint(Port)
        ):
            raise ValueError(
                "corridor continuation domain does not belong to its plan"
            )
        CorridorDomainFingerprints.append((
            Domain.Signal,
            Domain.DomainFingerprint,
        ))
    Dependencies = tuple(sorted(
        (str(Signal), str(Fingerprint))
        for Signal, Fingerprint
        in RequestDependencyFingerprints.items()
        if str(Fingerprint)
    ))
    Remaining = tuple(sorted(
        (str(Signal), max(0, int(Count)))
        for Signal, Count in RemainingRequestCounts.items()
    ))
    Domains = tuple(sorted(CorridorDomainFingerprints))
    Certificates = tuple(sorted({
        str(Fingerprint)
        for Fingerprint in CertificateFingerprints
        if str(Fingerprint)
    }))
    if ResumeCursor is not None:
        if ResumeCursor.PlanFingerprint != Plan.PlanFingerprint:
            raise ValueError(
                "global continuation cursor belongs to another plan"
            )
        if (
            not ResumeCursor.ApertureDomainFingerprint
            or ResumeCursor.ApertureDomainFingerprint not in Certificates
        ):
            raise ValueError(
                "global continuation cursor aperture is not certified"
            )
        if (
            not ResumeCursor.CursorFingerprint
            or ResumeCursor.CompletedWork <= 0
            or ResumeCursor.State is None
        ):
            raise ValueError(
                "global continuation cursor has no resumable progress"
            )
    StateFingerprint = BuildStableFingerprint((
        "physical-global-plan-continuation-v1",
        Plan.PlanFingerprint,
        Dependencies,
        Remaining,
        Domains,
        Certificates,
        (
            ResumeCursor.CursorFingerprint
            if ResumeCursor is not None
            else ""
        ),
    ))
    return PhysicalGlobalPlanContinuationState(
        StateFingerprint=StateFingerprint,
        PlanFingerprint=Plan.PlanFingerprint,
        RequestDependencyFingerprints=Dependencies,
        RemainingRequestCounts=Remaining,
        CorridorDomainFingerprints=Domains,
        CertificateFingerprints=Certificates,
        CompletedWork=max(0, int(CompletedWork)),
        Complete=False,
        ResumeCursor=ResumeCursor,
    )

def RetainIncompletePhysicalGlobalPlan(
    Frontier: Mapping[str, RetainedPhysicalGlobalPlanFrontierEntry],
    Assembly: PreparedPhysicalComponentAssembly,
    Continuation: PhysicalGlobalPlanContinuationState,
    *,
    EnqueuedSequence: int,
) -> dict[str, RetainedPhysicalGlobalPlanFrontierEntry]:
    """Insert or refresh an incomplete plan without rejecting its contract."""
    PlanFingerprint = Assembly.Plan.PlanFingerprint
    if Continuation.Complete:
        raise ValueError("a complete global plan cannot enter the frontier")
    if Continuation.PlanFingerprint != PlanFingerprint:
        raise ValueError("global continuation and assembly identities differ")
    if EnqueuedSequence < 0:
        raise ValueError("frontier sequence must be non-negative")
    Existing = Frontier.get(PlanFingerprint)
    if Continuation.ResumeCursor is None:
        raise ValueError(
            "an incomplete global plan requires a resumable cursor"
        )
    if Continuation.CompletedWork <= 0:
        raise ValueError(
            "an incomplete global-plan continuation made no progress"
        )
    if Existing is not None:
        ExistingCursor = Existing.Continuation.ResumeCursor
        CandidateCursor = Continuation.ResumeCursor
        if (
            ExistingCursor is None
            or CandidateCursor.ApertureDomainFingerprint
            != ExistingCursor.ApertureDomainFingerprint
            or CandidateCursor.CompletedWork
            <= ExistingCursor.CompletedWork
        ):
            # A stale or regressed publication cannot replace the richer
            # runnable continuation already held by the frontier.
            return dict(Frontier)
    Entry = RetainedPhysicalGlobalPlanFrontierEntry(
        Assembly=Assembly,
        Continuation=Continuation,
        EnqueuedSequence=(
            Existing.EnqueuedSequence
            if Existing is not None
            else int(EnqueuedSequence)
        ),
        LastScheduledSequence=(
            Existing.LastScheduledSequence if Existing is not None else -1
        ),
        ScheduleCount=(Existing.ScheduleCount if Existing is not None else 0),
        AccumulatedCompletedWork=(
            Continuation.ResumeCursor.CompletedWork
        ),
    )
    return {**Frontier, PlanFingerprint: Entry}

def SelectNextRetainedPhysicalGlobalPlan(
    Frontier: Mapping[str, RetainedPhysicalGlobalPlanFrontierEntry],
    *,
    ScheduleSequence: int,
) -> tuple[
    RetainedPhysicalGlobalPlanFrontierEntry,
    dict[str, RetainedPhysicalGlobalPlanFrontierEntry],
]:
    """Schedule retained plans fairly without changing proof status."""
    if not Frontier:
        raise ValueError("physical global-plan frontier is empty")
    if ScheduleSequence < 0:
        raise ValueError("schedule sequence must be non-negative")
    Selected = min(
        Frontier.values(),
        key=lambda Entry: (
            Entry.ScheduleCount,
            Entry.LastScheduledSequence,
            Entry.EnqueuedSequence,
            Entry.PlanFingerprint,
        ),
    )
    Scheduled = replace(
        Selected,
        LastScheduledSequence=int(ScheduleSequence),
        ScheduleCount=Selected.ScheduleCount + 1,
    )
    return Scheduled, {
        **Frontier,
        Scheduled.PlanFingerprint: Scheduled,
    }

def ShouldScheduleRetainedPhysicalGlobalPlan(
    Frontier: Mapping[str, RetainedPhysicalGlobalPlanFrontierEntry],
    *,
    PreviousPlanWasRetained: bool,
) -> bool:
    """Finish retained authoritative work before opening another plan.

    A yielded plan has neither failed nor proved unsatisfiable.  Opening new
    port/channel tuples while such a plan has monotonic resumable progress
    turns the physical-plan layer into a speculative retry portfolio and
    discards the architectural value of the continuation.  The scheduler may
    select another plan only after the retained frontier completes, proves a
    cut, or loses its typed resumable identity.
    """
    del PreviousPlanWasRetained
    return bool(Frontier)

def BuildPhysicalGlobalPlanYieldDeadline(
    SharedDeadline: RoutingDeadline,
    RetainedPlanCount: int,
    *,
    CurrentPlanWasRetained: bool = False,
) -> RoutingDeadline:
    """Reserve an equal proof-neutral share for unseen/retained plans.

    This is scheduling, not a route-search budget: the authoritative router
    keeps its production controls and exact caches.  Until it exposes a
    resumable cursor, expiry of this child deadline yields the plan back to
    the retained frontier while the shared deadline remains authoritative.
    """
    if RetainedPlanCount < 0:
        raise ValueError("retained plan count must be non-negative")
    if CurrentPlanWasRetained and RetainedPlanCount < 1:
        raise ValueError(
            "a selected retained plan must be present in the frontier"
        )
    StartedAt = monotonic()
    Remaining = max(0.0, SharedDeadline.ExpiresAt - StartedAt)
    # Only admitted authoritative work participates.  The scheduler no
    # longer reserves time for speculative unseen plans while a typed
    # continuation exists.  A fresh plan with no frontier therefore owns the
    # remaining interval; retained plans divide it fairly among themselves.
    ParticipantCount = max(
        1,
        int(RetainedPlanCount)
        + (0 if CurrentPlanWasRetained else 1),
    )
    return RoutingDeadline(
        StartedAt=StartedAt,
        ExpiresAt=min(
            SharedDeadline.ExpiresAt,
            StartedAt + Remaining / ParticipantCount,
        ),
    )

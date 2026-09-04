"""Frozen access preparation, candidate ordering, and routing budgets."""

from __future__ import annotations

from dataclasses import (
    replace,
)
from typing import (
    Any,
    Iterable,
    Mapping,
)
from PhysicalDesign.Contracts.Placement import ClusterInterfaceStateProof
from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason
from PhysicalDesign.Runtime.Reliability import BuildStableFingerprint
from PhysicalDesign.Policy import PhysicalDesignPolicy
from PhysicalDesign.Redstone.Technology import RedstoneRoutingTechnology
from PhysicalDesign.Placement.PreRouteInterface import DerivedPlacementCandidate, DerivedRoutingEnvelope, DeriveRoutingEnvelopes, PlacementAccessDemand
from PhysicalDesign.Geometry.Rotation import RotatedCellSize
from PhysicalDesign.Placement.Access.Geometry import MeasureDerivedPerimeterInterfaceDemand, MeasureDerivedPerimeterInterfaceLaunchDemandByFace
from PhysicalDesign.Placement.Engine.Clusters import PcbPlacement
from .Portfolios import (
    PlacementGenerationRoutingReserveSeconds,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .Candidates import (
        PcbPlacementCandidate,
    )
    from .Demand import (
        TopologyDemandProfile,
    )


def IsDerivedSingleComponentPlacementSource(
    SourceGenerator: str,
) -> bool:
    """Identify a fixed pre-route geometry member without policy widening."""
    return SourceGenerator in {
        "derived-perimeter-row-beam",
        "derived-pin-aligned-core",
        # Retained while old focused fixtures still name the original member.
        "derived-compact",
    }

def UsesDerivedPerimeterTerminals(
    SourceGenerator: str,
) -> bool:
    """Keep terminal placement coupled to a derived compact core."""
    return SourceGenerator in {
        "derived-perimeter-row-beam",
        "derived-pin-aligned-core",
    }

def PrepareDerivedPlacementForFrozenAccessContract(
    Placement: PcbPlacement,
) -> PcbPlacement:
    """Transfer small-design local access ownership to the ring contract.

    Packed placement can opportunistically freeze short local routes before
    the perimeter factor is built.  Those routes were selected without the
    complete capacity problem and can obstruct a different signal's only
    legal escape.  A derived candidate therefore publishes macro geometry
    and terminals only; the selected access-fabric witness becomes the sole
    immutable local-access owner.
    """
    Source = Placement.Placed
    Diagnostics = dict(Source.LocalRouteDiagnostics or {})
    Diagnostics["__DerivedFrozenAccessContract__"] = {
        "PlacementOwnedFrozenSignalCount": len(
            Source.FrozenNetWires or {}
        ),
        "PlacementOwnedLocalClaimCount": len(
            Source.LocalRouteClaims or ()
        ),
        "Mode": "pre-route-access-fabric",
    }
    OptionalClaims = tuple(
        Claim
        for Claim in (Source.LocalRouteClaims or ())
        if str(Claim.Signal) in (Source.FrozenNetWires or {})
    )
    return replace(
        Placement,
        Placed=replace(
            Source,
            FrozenNetWires={},
            LocalNetBranches={},
            LocalNetTargets={},
            LocalRouteClaims=(),
            LocalRouteDiagnostics=Diagnostics,
            DerivedLocalRouteClaims=OptionalClaims,
        ),
        ClusterLocalRouteTemplates=(),
        CompleteClusterInterfaceAccess=False,
        # Only a route which placement had already certified as complete for
        # its signal can be an optional local-access value.  Partial local
        # fragments remain unowned and are represented by the normal escape
        # domain instead of being mistaken for a completed signal tree.
        DerivedLocalRouteClaims=OptionalClaims,
    )

def BuildPlacementAccessDemand(
    Placement: PcbPlacement,
    PeakBoundaryDemand: int,
    Technology: RedstoneRoutingTechnology,
) -> PlacementAccessDemand:
    """Measure one placed geometry without consulting routing policy knobs."""
    Gates = tuple(Placement.Placed.PlacedGates)
    if not Gates:
        raise ValueError("placement access demand requires placed gates")
    MinimumX = min(Gate.X for Gate in Gates)
    MinimumZ = min(Gate.Z for Gate in Gates)
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in Gates
    )
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in Gates
    )
    TerminalIdentities = {
        (str(Signal), tuple(Gate.OutputPin))
        for Gate in Gates
        if Gate.OutputPin is not None
        for Signal in Gate.Outputs
    }
    TerminalIdentities.update(
        (str(Signal), tuple(Gate.InputPins[InputIndex]))
        for Gate in Gates
        for InputIndex, Signal in enumerate(Gate.Inputs)
    )
    # A single packed component has a finite pre-route placement/access
    # domain.  It may start from the physical one-deck lower bound and let
    # the exact capacity witness select the smallest usable layer contract.
    # Multi-component designs retain the established conservative floor until
    # their component-factor path consumes the same contract explicitly.
    MinimumRoutingLayerCount = (
        Technology.MinimumPhysicalRoutingLayerCount
        if len(Placement.Clusters) == 1
        else Technology.MinimumRoutingLayerCount
    )
    # A frozen derived ring owns selected external slots and the paired
    # source endpoint of any signal that reaches one.  Interior-only pins
    # remain ordinary authoritative portal work, so treating every pin as
    # perimeter demand would inflate track width and footprint with routing
    # slack that the fixed access contract never materializes.
    DerivedInterfaceTerminalCount, DerivedActivePerimeterFaces = (
        MeasureDerivedPerimeterInterfaceDemand(
            Placement,
            Technology=Technology,
        )
    )
    DerivedPerimeterFaceLaunchDemand = (
        MeasureDerivedPerimeterInterfaceLaunchDemandByFace(
            Placement,
            Technology=Technology,
        )
    )
    if DerivedInterfaceTerminalCount:
        TerminalCount = DerivedInterfaceTerminalCount
        ActivePerimeterFaces = DerivedActivePerimeterFaces
        # The aggregate measurement retains a legacy count for a root whose
        # path has no provable horizontal face.  Such a root cannot honestly
        # be charged to a perimeter face, so use face-resolved pressure only
        # when it covers the exact active-face contract.  ``TerminalCount``
        # remains in the envelope calculation as a separate total-capacity
        # lower bound in either case.
        PerimeterFaceLaunchDemand = (
            tuple(DerivedPerimeterFaceLaunchDemand.items())
            if set(DerivedPerimeterFaceLaunchDemand)
            == set(ActivePerimeterFaces)
            else ()
        )
    else:
        TerminalCount = len(TerminalIdentities)
        ActivePerimeterFaces = ("north", "south", "west", "east")
        PerimeterFaceLaunchDemand = ()
    return PlacementAccessDemand(
        ComponentCount=max(1, len(Placement.Clusters)),
        TerminalCount=TerminalCount,
        PeakBoundaryDemand=max(0, int(PeakBoundaryDemand)),
        CoreBounds=(MinimumX, MinimumZ, MaximumX, MaximumZ),
        TrackPitch=Technology.TrackPitch,
        AccessLength=Technology.AccessLength,
        MinimumRoutingLayerCount=MinimumRoutingLayerCount,
        MaximumRoutingLayerCount=(
            Technology.MaximumRoutableLayerCount
        ),
        TechnologyFingerprint=BuildStableFingerprint({
            "TechnologyVersion": Technology.TechnologyVersion,
            "TrackPitch": Technology.TrackPitch,
            "AccessLength": Technology.AccessLength,
            "RoutingLayerPitch": Technology.RoutingLayerPitch,
            "MinimumPhysicalRoutingLayerCount": (
                Technology.MinimumPhysicalRoutingLayerCount
            ),
            "MinimumRoutingLayerCount": (
                Technology.MinimumRoutingLayerCount
            ),
            "MaximumRoutingLayerCount": (
                Technology.MaximumRoutableLayerCount
            ),
        }),
        ActivePerimeterFaces=ActivePerimeterFaces,
        PerimeterFaceLaunchDemand=PerimeterFaceLaunchDemand,
    )

def BuildDerivedPlacementCandidate(
    Candidate: PcbPlacementCandidate,
    Envelope: DerivedRoutingEnvelope,
    *,
    Complete: bool,
    WorkCount: int,
    IncompleteReason: str = "",
    FullEnvelopeBounds: tuple[int, int, int, int] | None = None,
) -> DerivedPlacementCandidate:
    """Bind one placed geometry to one explicit derived layer contract."""
    return DerivedPlacementCandidate(
        CandidateId=Candidate.CandidateId,
        GeometryFingerprint=Candidate.PlacementFingerprint,
        # The selector must compare the physical contract that routing is
        # allowed to occupy, not the NAND-only core.  Otherwise a seemingly
        # smaller core can win while its required perimeter access consumes a
        # larger real footprint.  The envelope is derived entirely from the
        # placed macro hull and technology pitch before selection.
        Bounds=(
            FullEnvelopeBounds
            if FullEnvelopeBounds is not None
            else Envelope.EnvelopeBounds
        ),
        RoutingEnvelope=Envelope,
        Complete=Complete,
        WorkCount=WorkCount,
        IncompleteReason=IncompleteReason,
    )

def BuildFrozenEnvelopeRoutingPolicy(
    Policy: PhysicalDesignPolicy,
    Envelope: DerivedRoutingEnvelope,
) -> PhysicalDesignPolicy:
    """Bind one selected physical layer contract to router policy.

    The envelope is selected before routing.  Its layer count must therefore
    constrain every explicit authoritative check and the final route;
    otherwise a lower-layer factor could accidentally be certified using the
    legacy three-layer graph.
    """
    return replace(
        Policy,
        Placement=replace(
            Policy.Placement,
            MaximumRoutingLayers=Envelope.RoutingLayerCount,
        ),
        AdaptiveRouting=replace(
            Policy.AdaptiveRouting,
            Enabled=False,
        ),
        TrackAssignment=replace(
            Policy.TrackAssignment,
            # The envelope has already chosen one exact routing-layer
            # contract.  Re-scanning lower layer ceilings here would be a
            # second assignment attempt over a different control setting.
            MinimizeMaximumRoutingLayer=False,
        ),
        GlobalRouting=replace(
            Policy.GlobalRouting,
            # A frozen pre-route witness is the one permitted assignment.
            # Keep legacy rip-up helpers out of this compact production path.
            MaximumRipupPasses=0,
        ),
    )

def BuildDerivedRoutingEnvelopeDomain(
    Demand: PlacementAccessDemand,
    Placement: PcbPlacement,
) -> tuple[DerivedRoutingEnvelope, ...]:
    """Materialize the bounded layer domain for one placed geometry.

    A structurally single-component compile begins with a certified compact
    row-beam incumbent.  Its declared routing deck count is a finite upper
    bound for the first compact-selection increment: candidates above it
    cannot satisfy the no-height-regression milestone, and constructing their
    identical access geometry would only spend pre-route work.  This is an
    incumbent bound, not a widening policy; the selected domain remains
    fixed before access construction begins.
    """
    Envelopes = DeriveRoutingEnvelopes(Demand)
    if len(Placement.Clusters) != 1:
        return Envelopes
    IncumbentLayerCount = max(
        Demand.MinimumRoutingLayerCount,
        min(
            Demand.MaximumRoutingLayerCount,
            max(1, int(Placement.LayerCount)),
        ),
    )
    return tuple(
        Envelope
        for Envelope in Envelopes
        if Envelope.RoutingLayerCount <= IncumbentLayerCount
    )

def SummarizePreRouteAccessFabric(
    Fabric: Any | None,
) -> dict[str, object] | None:
    """Publish bounded fabric evidence without serializing every stub claim.

    A raw pre-route materialization can contain hundreds of legal escape
    stubs, each with a large physical-claim set.  Those stubs remain in the
    frozen in-memory contract consumed by the authoritative selector, but a
    failure artifact only needs the fixed topology, exact outer bounds,
    terminal-domain frontier, and first incomplete endpoint.  Keeping the
    artifact at that boundary makes lazy materialization observable without
    turning a typed first-core report into a multi-megabyte duplicate of the
    candidate domain.
    """
    if Fabric is None:
        return None
    TerminalDomains = tuple(getattr(Fabric, "TerminalDomains", ()))
    IncompleteTerminalDomains = [
        {
            "Signal": str(getattr(Domain, "Signal", "")),
            "Terminal": list(getattr(Domain, "Terminal", ())),
            "EscapeStubCount": len(getattr(Domain, "EscapeStubs", ())),
            "IncompleteReason": str(getattr(
                Domain,
                "IncompleteReason",
                "",
            )),
        }
        for Domain in TerminalDomains
        if not bool(getattr(Domain, "Complete", False))
    ]
    PhysicalClaims = getattr(Fabric, "PhysicalClaims", None)
    return {
        "FabricFingerprint": str(getattr(Fabric, "FabricFingerprint", "")),
        "TopologyKind": str(getattr(Fabric, "TopologyKind", "")),
        "Complete": bool(getattr(Fabric, "Complete", False)),
        "IncompleteReason": str(getattr(Fabric, "IncompleteReason", "")),
        "AccessRingTrackCount": int(getattr(
            Fabric,
            "AccessRingTrackCount",
            0,
        )),
        "AccessRingFingerprint": str(getattr(
            Fabric,
            "AccessRingFingerprint",
            "",
        )),
        "OuterBounds": (
            list(getattr(Fabric, "OuterBounds", ()))
            if getattr(Fabric, "OuterBounds", None) is not None
            else None
        ),
        "ActiveFaces": list(getattr(Fabric, "ActiveFaces", ())),
        "NodeCount": len(getattr(Fabric, "Nodes", ())),
        "EdgeCount": len(getattr(Fabric, "Edges", ())),
        "CapacityResourceCount": len(getattr(
            Fabric,
            "CapacityResourceIds",
            (),
        )),
        "TerminalDomainCount": len(TerminalDomains),
        "CompleteTerminalDomainCount": sum(
            bool(getattr(Domain, "Complete", False))
            for Domain in TerminalDomains
        ),
        "IncompleteTerminalDomains": IncompleteTerminalDomains,
        "LegalEscapeExpansionCount": int(getattr(
            Fabric,
            "LegalEscapeExpansionCount",
            0,
        )),
        "LegalEscapeExpansionLimit": (
            int(getattr(Fabric, "LegalEscapeExpansionLimit"))
            if getattr(Fabric, "LegalEscapeExpansionLimit", None)
            is not None
            else None
        ),
        "LegalEscapeWorkLimitKind": str(getattr(
            Fabric,
            "LegalEscapeWorkLimitKind",
            "",
        )),
        "LegalEscapeDirectionStateUpperBound": (
            int(getattr(
                Fabric,
                "LegalEscapeDirectionStateUpperBound",
            ))
            if getattr(
                Fabric,
                "LegalEscapeDirectionStateUpperBound",
                None,
            ) is not None
            else None
        ),
        "PhysicalClaimCounts": {
            "WireCells": len(getattr(PhysicalClaims, "WireCells", ())),
            "SupportCells": len(getattr(PhysicalClaims, "SupportCells", ())),
            "RequiredAirCells": len(getattr(
                PhysicalClaims,
                "RequiredAirCells",
                (),
            )),
            "ElectricalCells": len(getattr(
                PhysicalClaims,
                "ElectricalCells",
                (),
            )),
        },
    }

def SummarizePrePlacementCapacityResults(
    Results: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Retain the bounded proof outcome without embedding full domains."""
    Summaries: list[dict[str, object]] = []
    for Result in Results:
        SelectedCandidateIds = Result.get("SelectedCandidateIds", ())
        Fabric = Result.get("PlacementAccessFabric")
        IncompleteTerminalDomains = (
            [
                {
                    "Signal": Domain.get("Signal", ""),
                    "Terminal": Domain.get("Terminal", []),
                    "IncompleteReason": Domain.get("IncompleteReason", ""),
                }
                for Domain in Fabric.get("TerminalDomains", [])
                if not bool(Domain.get("Complete", False))
            ]
            if isinstance(Fabric, Mapping)
            and "TerminalDomains" in Fabric
            else list(Fabric.get("IncompleteTerminalDomains", ()))
            if isinstance(Fabric, Mapping)
            else []
        )
        Summaries.append({
            "CandidateId": Result.get("CandidateId", ""),
            "PlacementFingerprint": Result.get(
                "PlacementFingerprint",
                "",
            ),
            "Success": bool(Result.get("Success", False)),
            "Complete": bool(Result.get("Complete", False)),
            "ExpansionCount": int(Result.get("ExpansionCount", 0)),
            "SelectedCandidateCount": (
                len(SelectedCandidateIds)
                if isinstance(SelectedCandidateIds, (list, tuple))
                else 0
            ),
            "ConflictSignals": list(Result.get("ConflictSignals", ())),
            "IncompleteReason": str(Result.get("IncompleteReason", "")),
            "IncompleteTerminalDomains": IncompleteTerminalDomains,
            "FirstUnroutableSignal": str(Result.get(
                "FirstUnroutableSignal",
                "",
            )),
            "MaximumRoutedSignalCount": int(Result.get(
                "MaximumRoutedSignalCount",
                0,
            )),
            "FrontierSignals": list(Result.get("FrontierSignals", ())),
        })
    return Summaries

def PlacementCandidateOrder(
    Value: PcbPlacementCandidate,
    ConfiguredSpacing: int,
) -> tuple[object, ...]:
    """Return the stable demand-first order used for placement failover."""
    if (
        bool(getattr(Value, "JointPortfolioCandidate", False))
        and Value.TopologyDemand is not None
    ):
        RetentionFingerprint = (
            Value.PlacementRetentionFingerprint
            or BuildPlacementRetentionFingerprint(
                Value.Placement,
                Value.TopologyDemand
                .MandatoryAccessOwnershipFingerprint,
                IncludeLocalClaims=False,
            )
        )
        return (
            0,
            Value.TopologyDemand.JointOrderKey,
            abs(Value.RoutingSpacing - ConfiguredSpacing),
            RetentionFingerprint,
        )
    return (
        1,
        0 if Value.Placement.PackedClusters else 1,
        0 if (Value.Placement.Placed.LocalRouteClaims or ()) else 1,
        Value.JointExactScore,
        Value.FeedbackScore,
        abs(Value.RoutingSpacing - ConfiguredSpacing),
        Value.PlacementFingerprint,
    )

def PlacementCandidateIsExactAccessLegal(
    Value: PcbPlacementCandidate,
) -> bool:
    """Reject a proved mandatory-access conflict before routing."""
    return bool(
        Value.TopologyDemand is None
        or Value.TopologyDemand.MandatoryAccessConflictResources == 0
    )

def PlacementNeedsDemandDiversity(
    Candidates: list[PcbPlacementCandidate],
    ConfiguredSpacing: int,
) -> bool:
    """Return whether the best generated placement still needs more diversity."""
    if not Candidates:
        return True
    Best = min(
        Candidates,
        key=lambda Value: PlacementCandidateOrder(Value, ConfiguredSpacing),
    )
    return any((
        Best.BoundaryOverflow,
        Best.PinScarcityCount,
        Best.GuideOverflowPeak,
        Best.GuideOverflowCells,
        Best.PinEscapeConflictCount,
    ))

def PlacementPortfolioGenerationNotAfter(
    Policy: PhysicalDesignPolicy,
    *,
    DeadlineExpiresAt: float,
    CurrentTime: float,
    RequiresDenseBoundaryRouting: bool = False,
) -> float:
    """Freeze one absolute routing floor for a retained placement portfolio."""
    RemainingSeconds = max(0.0, DeadlineExpiresAt - CurrentTime)
    RoutingReserveSeconds = min(
        PlacementGenerationRoutingReserveSeconds(
            Policy,
            RequiresDenseBoundaryRouting,
        ),
        max(0.01, RemainingSeconds * 0.5),
    )
    return DeadlineExpiresAt - RoutingReserveSeconds

def RequiresDenseBoundaryRoutingReserve(
    Demand: TopologyDemandProfile,
    Policy: PhysicalDesignPolicy,
) -> bool:
    """Reserve routing time for interfaces that need joint ownership proof."""
    return (
        Demand.RequiresJointPortfolio
        or Demand.MaximumTerminalBankDemand
        >= Policy.Organization.MaximumClusterEntrances
    )

def RequiresDenseBoundaryLeaseRouting(
    Placed: Any,
    Policy: PhysicalDesignPolicy,
) -> bool:
    """Return whether one placement owns a joint boundary-lease interface."""
    LeaseRequests = tuple(
        getattr(Placed, "ClusterBoundaryLeaseRequests", ())
    )
    LeaseTerminalCount = sum(
        1 + len(tuple(getattr(Request, "TargetTerminals", ())))
        for Request in LeaseRequests
    )
    return (
        bool(LeaseRequests)
        and LeaseTerminalCount >= Policy.Organization.MaximumClusterEntrances
    )

def RequiresExactClusterInterfaceSolve(
    Demand: TopologyDemandProfile | None,
    Placed: Any,
    Policy: PhysicalDesignPolicy,
) -> bool:
    """Gate the exact interface path using measured structure only."""
    return bool(
        Demand is not None
        and bool(getattr(Placed, "CompleteClusterInterfaceAccess", False))
        and (
            Demand.RequiresJointPortfolio
            or Demand.MandatoryAccessConflictResources > 0
            or RequiresDenseBoundaryLeaseRouting(Placed, Policy)
        )
    )

def BuildClusterInterfaceUnsatProof(
    StateProofs: Iterable[ClusterInterfaceStateProof],
    ExpectedComponentStateFingerprints: Iterable[str] = (),
    *,
    PlacementPortfolioDomainComplete: bool = True,
) -> dict[str, object]:
    """Build one deterministic proof scoped to named component states.

    ``PlacementPortfolioDomainComplete`` must only be true when placement
    generation itself is exhaustive.  A complete proof for every retained
    placement is not an architectural proof when legal placements were
    pruned by a scoring or work budget.
    """
    Proofs = tuple(sorted(
        StateProofs,
        key=lambda Proof: (
            str(Proof.ComponentStateFingerprint)
            or str(Proof.PlacementStateFingerprint),
            str(Proof.PlacementStateFingerprint),
            str(Proof.ComponentVariant),
            str(Proof.Status),
        ),
    ))
    if not Proofs:
        raise ValueError("cluster interface proof requires retained states")
    StateFingerprints = tuple(
        Proof.ComponentStateFingerprint
        or Proof.PlacementStateFingerprint
        for Proof in Proofs
    )
    if len(set(StateFingerprints)) != len(StateFingerprints):
        raise ValueError(
            "cluster interface proof contains a repeated component state"
    )
    ExpectedStateFingerprints = tuple(map(
        str,
        ExpectedComponentStateFingerprints,
    ))
    ExpectedStateFingerprints = tuple(
        sorted(ExpectedStateFingerprints)
    )
    if len(set(ExpectedStateFingerprints)) != len(
        ExpectedStateFingerprints
    ):
        raise ValueError(
            "cluster interface expected domain contains a repeated "
            "component state"
        )
    ProvenStateSet = frozenset(StateFingerprints)
    ExpectedStateSet = frozenset(ExpectedStateFingerprints)
    DomainIdentityComplete = bool(
        not ExpectedStateFingerprints
        or ProvenStateSet == ExpectedStateSet
    )
    ProofFingerprint = BuildStableFingerprint(tuple(
        Proof.StructuralIdentity() for Proof in Proofs
    ))
    NamedComponentStateProofComplete = bool(
        DomainIdentityComplete
        and all(
            Proof.Exhaustive
            and Proof.DomainComplete
            and Proof.OwnershipComplete
            and Proof.RealizabilityComplete
            for Proof in Proofs
        )
    )
    return {
        "Complete": bool(
            NamedComponentStateProofComplete
            and PlacementPortfolioDomainComplete
        ),
        "NamedComponentStateProofComplete": (
            NamedComponentStateProofComplete
        ),
        "ComponentStateDomainComplete": DomainIdentityComplete,
        "PlacementPortfolioDomainComplete": bool(
            PlacementPortfolioDomainComplete
        ),
        "ProofScope": "named-placement-component-states",
        "ArchitecturalUnsatisfiabilityProven": False,
        "ExpectedComponentStateCount": len(ExpectedStateFingerprints),
        "ProvenComponentStateCount": len(StateFingerprints),
        "MissingComponentStateFingerprints": sorted(
            ExpectedStateSet - ProvenStateSet
        ),
        "UnexpectedComponentStateFingerprints": sorted(
            ProvenStateSet - ExpectedStateSet
        ) if ExpectedStateFingerprints else [],
        "ExecutableRepairAllowed": False,
        "BroadFallbackAllowed": False,
        "AttemptedStateCount": len(Proofs),
        "StateProofs": [
            Proof.ToDictionary() for Proof in Proofs
        ],
        "ProofFingerprint": ProofFingerprint,
    }

def BuildClusterInterfaceComponentStateFingerprint(
    PlacementStateFingerprint: str,
    ComponentVariant: int,
) -> str:
    """Identify one requested component selection within one placement."""
    return BuildStableFingerprint((
        "cluster-interface-component-state-v1",
        str(PlacementStateFingerprint),
        int(ComponentVariant),
    ))

def ShouldEnableClusterBoundaryLeaseInterface(
    *,
    ScaleGeometryPressure: bool,
    TopologyRequiresJointPortfolio: bool,
    IsPostPinBankRepairEpoch: bool = False,
) -> bool:
    """Materialize boundary contracts whenever compact clustered geometry can.

    Reconvergent placement previously disabled the lease interface until a
    post-pin-bank epoch.  That left the exact candidate screen blind to the
    simultaneous source/target portal ownership later proved impossible by
    the authoritative planner.  Scale-widened placements retain their proven
    route path; compact topology-triggered placements now carry the same typed
    boundary contract from placement through routing.
    """
    del TopologyRequiresJointPortfolio, IsPostPinBankRepairEpoch
    return not ScaleGeometryPressure

def PlacementFeedbackRoutingSlotCount(
    *,
    HasRemainingPlacementAlternative: bool,
    ReconvergentAccessPressure: bool,
    AttemptedCandidateCount: int,
) -> int:
    """Reserve one later route while establishing reconvergent-cut feedback."""
    return (
        2
        if (
            HasRemainingPlacementAlternative
            and ReconvergentAccessPressure
            and AttemptedCandidateCount < 2
        )
        else 1
    )

def RetainedPlacementRoutingSlotCount(
    *,
    RemainingRetainedCandidates: int,
    HighFanoutFeedbackRoutingSlots: int,
    HasRemainingPlacementAlternative: bool,
    TopologyPortfolioTriggered: bool,
    AttemptedCandidateCount: int,
) -> int:
    """Reserve an equal route slice for every live exact geometry."""
    return max(
        1,
        RemainingRetainedCandidates,
        HighFanoutFeedbackRoutingSlots,
        (
            2
            if (
                HasRemainingPlacementAlternative
                and TopologyPortfolioTriggered
                and AttemptedCandidateCount == 0
            )
            else 1
        ),
    )

def BuildPlacementRelocationVariant(
    *,
    RelocationGenerationCount: int,
    ReconvergentAccessPressure: bool,
) -> int:
    """Select repair strength from attempts against the current exact cut."""
    return (
        RelocationGenerationCount
        + 2
        + (
            9
            if (
                RelocationGenerationCount > 0
                and ReconvergentAccessPressure
            )
            else 0
        )
    )

def DenseRetainedLeaseProofSliceSeconds(
    *,
    RemainingSeconds: float,
    RemainingRetainedCandidates: int,
    MinimumProofSeconds: float = 10.0,
    PublicationReserveSeconds: float = 2.0,
    PrioritizeHigherOrderCutProof: bool = False,
) -> float:
    """Fund a useful exact lease proof while preserving the shared deadline.

    A fresh higher-order geometry epoch must reach boundary assignment once;
    equal six-way slicing repeatedly expired all states during immutable portal
    preparation. Give only its primary exact state a bounded lead share.
    """
    if RemainingSeconds <= 0:
        return 0.001
    if RemainingRetainedCandidates < 1:
        raise ValueError("RemainingRetainedCandidates must be positive")
    AvailableSeconds = max(
        0.001,
        RemainingSeconds - PublicationReserveSeconds,
    )
    FairShareSeconds = (
        RemainingSeconds / RemainingRetainedCandidates
    )
    PriorityProofSeconds = (
        min(
            40.0,
            max(
                20.0,
                RemainingSeconds * 0.45,
            ),
        )
        if PrioritizeHigherOrderCutProof
        else 0.0
    )
    return min(
        AvailableSeconds,
        max(
            FairShareSeconds,
            MinimumProofSeconds,
            PriorityProofSeconds,
        ),
    )

def TopologyPortfolioRoutingFraction(
    *,
    HasRemainingPlacementAlternative: bool,
    AttemptedCandidateCount: int,
    AuthoritativeMandatoryAccessConflictObserved: bool = False,
) -> float:
    """Give the ranked lead state enough time to expose an exact cut."""
    if AuthoritativeMandatoryAccessConflictObserved:
        # The routed portal-domain prescreen is more authoritative than the
        # placement-only pin-access score.  Once it disqualifies the ranked
        # lead, the next zero-conflict sibling becomes the effective lead and
        # owns the remaining bounded portfolio slice.
        return 1.0
    if (
        HasRemainingPlacementAlternative
        and AttemptedCandidateCount == 0
    ):
        return 0.75
    return 1.0

def ShouldGiveRankedJointPortfolioLeadSlice(
    *,
    ActiveRelocatedPortfolioCandidate: bool,
    CandidateId: str,
    PrimaryCandidateId: str | None,
) -> bool:
    """Reserve the established lead slice for the ranked active candidate."""
    return (
        ActiveRelocatedPortfolioCandidate
        and PrimaryCandidateId is not None
        and CandidateId == PrimaryCandidateId
    )

def IsAuthoritativeMandatoryAccessConflict(
    Failure: RoutingFailure,
) -> bool:
    """Recognize a complete retained generated-portal-domain cut proof."""
    Diagnostics = Failure.Diagnostics or {}
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    Proof = Diagnostics.get("MandatoryAccessProof", {})
    return (
        Failure.Reason == RoutingFailureReason.TrackAssignmentConflict
        and Failure.Stage == "InitialCandidateAssignment"
        and isinstance(ConflictGraph, dict)
        and ConflictGraph.get("Classification")
        == "mandatory-boundary-capacity-cut"
        and bool(Failure.AffectedNets)
        and isinstance(Proof, dict)
        and Proof.get("Kind")
        == "generated-fixed-portal-domain-exhausted"
        and Proof.get("Complete") is True
        and not bool(Proof.get("BudgetExhausted", False))
        and not bool(Proof.get("DeadlineExceeded", False))
        and bool(Proof.get("ConflictFingerprint"))
    )

def PromoteAuthoritativeMandatoryAccessConflict(
    Profile: TopologyDemandProfile,
    Failure: RoutingFailure,
) -> TopologyDemandProfile:
    """Merge a routed exact portal cut into one immutable topology profile.

    Placement can score fixed pin-access ownership before building routing
    layers, but only the authoritative portal domain can prove collisions
    across every retained generated fixed portal/access alternative. Normalize
    physical locations and owner cardinalities so the score and fingerprint
    remain rename-independent.
    """
    if not IsAuthoritativeMandatoryAccessConflict(Failure):
        return Profile
    Diagnostics = Failure.Diagnostics or {}
    Proof = Diagnostics["MandatoryAccessProof"]
    ConflictResourceCount = max(
        1,
        int(
            Proof.get(
                "ConflictPositionCount",
                Diagnostics.get("MandatoryConflictPositionCount", 0),
            )
        ),
    )
    return replace(
        Profile,
        MandatoryAccessConflictResources=ConflictResourceCount,
        MandatoryAccessConflictSignals=tuple(sorted(map(
            str,
            Failure.AffectedNets,
        ))),
        MandatoryAccessConflictFingerprint=str(
            Proof["ConflictFingerprint"]
        ),
    )

def BuildPlacementRetentionFingerprint(
    Placement: PcbPlacement,
    MandatoryAccessOwnershipFingerprint: str = "",
    IncludeLocalClaims: bool = True,
) -> str:
    """Fingerprint anonymous relative geometry for candidate retention."""
    Gates = tuple(Placement.Placed.PlacedGates or ())
    Claims = tuple(
        Placement.Placed.LocalRouteClaims or ()
        if IncludeLocalClaims
        else ()
    )
    Positions = [
        (int(Gate.X), int(Gate.Y), int(Gate.Z))
        for Gate in Gates
    ]
    Positions.extend(
        (int(Node[0]), int(Node[1]), int(Node[2]))
        for Claim in Claims
        for Node in Claim.Nodes
    )
    MinimumX = min((Position[0] for Position in Positions), default=0)
    MinimumY = min((Position[1] for Position in Positions), default=0)
    MinimumZ = min((Position[2] for Position in Positions), default=0)

    def NormalizePosition(
        Position: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        return (
            int(Position[0]) - MinimumX,
            int(Position[1]) - MinimumY,
            int(Position[2]) - MinimumZ,
        )

    return BuildStableFingerprint({
        "AnonymousRelativeGates": sorted(
            (
                str(getattr(Gate.Kind, "value", Gate.Kind)),
                *NormalizePosition((Gate.X, Gate.Y, Gate.Z)),
                int(Gate.Rotation),
                bool(getattr(Gate, "MirrorX", False)),
            )
            for Gate in Gates
        ),
        "AnonymousRelativeLocalClaims": sorted(
            tuple(sorted(
                NormalizePosition(Node)
                for Node in Claim.Nodes
            ))
            for Claim in Claims
        ),
        "MandatoryAccessOwnershipFingerprint": (
            MandatoryAccessOwnershipFingerprint
        ),
        "InterClusterChannelFingerprint": (
            getattr(
                getattr(
                    Placement,
                    "InterClusterRoutingChannel",
                    None,
                ),
                "ChannelFingerprint",
                "",
            )
        ),
    })

def BuildClusterInterfacePlacementTopologyFingerprint(
    Placement: PcbPlacement,
    SignalTopologyFingerprints: Mapping[str, str],
) -> str:
    """Fingerprint the boundary ownership topology a placement exposes.

    Absolute translation, portal identifiers, and signal names are excluded.
    The identity changes only when a structural terminal moves relative to the
    component, changes boundary side, or changes its cluster ownership pair.
    """
    Requests = tuple(
        getattr(
            Placement.Placed,
            "ClusterBoundaryLeaseRequests",
            (),
        )
        or ()
    )
    Positions = [
        tuple(int(Coordinate) for Coordinate in Position)
        for Request in Requests
        for Position in (
            *((Request.SourceTerminal,) if Request.SourceTerminal else ()),
            *tuple(Request.TargetTerminals),
        )
    ]
    MinimumX = min((Position[0] for Position in Positions), default=0)
    MinimumY = min((Position[1] for Position in Positions), default=0)
    MinimumZ = min((Position[2] for Position in Positions), default=0)

    def Normalize(
        Position: tuple[int, int, int] | None,
    ) -> tuple[int, int, int] | None:
        if Position is None:
            return None
        return (
            int(Position[0]) - MinimumX,
            int(Position[1]) - MinimumY,
            int(Position[2]) - MinimumZ,
        )

    return BuildStableFingerprint(tuple(sorted(
        (
            int(Request.SourceCluster),
            int(Request.TargetCluster),
            str(Request.SourceBoundarySide),
            str(Request.TargetBoundarySide),
            Normalize(Request.SourceTerminal),
            tuple(sorted(Normalize(Value) for Value in Request.TargetTerminals)),
            bool(Request.CompletePinAccess),
            SignalTopologyFingerprints.get(Request.Signal, ""),
        )
        for Request in Requests
    )))

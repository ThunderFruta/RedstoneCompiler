"""Final immutable physical component assembly publication."""

from __future__ import annotations

from ....Regions.Planning.PhysicalPlanning import BuildPhysicalComponentAssemblyChoiceFingerprint

from ....Regions.Planning.PhysicalPlanning import BuildPhysicalComponentAssemblyPlanDomainFingerprint

from ....Regions.Planning.PhysicalPlanning import BuildPhysicalComponentPortSolverCacheKey

from ....Regions.Proofs.Validation import BuildPhysicalLocalAccessDomainFingerprint

from ....Regions.Proofs.Validation import BuildPhysicalPortApertureContractFingerprint

from ....Regions.Proofs.Validation import BuildPhysicalPortSeamContractFingerprint

from ....Regions.Planning.InterfacePlanning import ComponentInterfaceContract

from ....Regions.Planning.InterfacePlanning import IterClosedComponentContracts

from .....Contracts.Component import PhysicalComponentAssemblyPlan

from .....Contracts.Component import PhysicalComponentBoundaryPortReservation

from .....Contracts.Component import PhysicalComponentChannelReservation

from .....Contracts.Component import PhysicalComponentPortReservation

from .....Contracts.Component import PreparedPhysicalComponentFeedthroughEndpointDomain

from .....Contracts.PhysicalInterface import PhysicalPortLaneFactor

from .....Contracts.PhysicalInterface import PhysicalPortSeamFactor

from .....Contracts.PhysicalInterface import PreparedPhysicalComponentAssembly

from .....Contracts.PhysicalInterface import PreparedPhysicalComponentPortFactorDomain

from .....Contracts.Results import RoutingResources

from .....Contracts.Failures import RoutingFailure

from .....Contracts.Failures import RoutingFailureReason

from .....Contracts.Failures import RoutingStageError

from .....Constraints.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint

from .....Constraints.PhysicalClaims import ComponentClaimsConflict

from .....Runtime.Reliability import BuildStableFingerprint

from .....Runtime.Reliability import RoutingDeadline

from .....Resources.ResourceGraph import BuildRoutingEnvelope

from .....Resources.ResourceGraph import FindSelfClaimConflicts

from .....Resources.ResourceGraph import RoutingResourceClaims

from .....Redstone.Technology import DefaultRedstoneRoutingTechnology

from collections import Counter

from collections import defaultdict

from dataclasses import replace

from itertools import combinations

from math import prod

from types import SimpleNamespace

from typing import Any

from typing import Callable

from typing import Iterable

from ...Assignment.AssignmentState import BuildPhysicalLocalPortPairUnsupportedIndex, BuildPhysicalPortNoGoodKeys, FindProofQualifiedCompleteDomainNoGoodCore, FindProofQualifiedUniversalNoGoodCore, GetPersistentPhysicalComponentPortCspState, OrderPhysicalPortOptionsByPreferences, PropagateExactNoGoodClauses, SelectBinaryExactNoGoodClauses, SelectExactNoGoodCspBranch

from ...Candidates.CandidateGuides import PropagateLaneFactorArcConsistency

from ..ExteriorConnectors import (
    BuildPhysicalBoundaryPortAssignmentFingerprint,
    SelectPhysicalFactorBranchSignal,
)

from ...Guides.PhysicalGuides import BuildComponentKeepoutAvoidingGlobalGuides, BuildExplicitPhysicalComponentFeedthrough, FindSignalClaimConflicts, MaterializeSupportedPhysicalPortReservation, PreparePhysicalComponentFeedthroughEndpointDomain

from ...Assignment.TrackPortfolio import BuildSeamOnlyPhysicalComponentPortReservation, InterleavePhysicalPortSeamsByEgressClass


def FinalizePreparedPhysicalComponentAssembly(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    Resources: RoutingResources,
    *,
    Problem: Any,
    CoarsePlan: Any,
    AccessCertificate: Any,
    ResourceGraph: Any,
    ComponentGraphFingerprint: Any,
    ComponentKeepoutNodes: Any,
    MinimumPlacementY: Any,
    ChannelReservations: Any,
    SelectedPorts: Any,
    SelectedBoundaryPorts: Any,
    AssemblyChoiceFingerprint: Any,
    BoundaryIteratorCacheKey: Any,
) -> PreparedPhysicalComponentAssembly:
    """Bind the selected ports and channels into the published plan."""
    KeepoutNodes = ComponentKeepoutNodes
    KeepoutClaims = ResourceGraph.BuildRouteClaims(KeepoutNodes)
    Envelope = BuildRoutingEnvelope(KeepoutNodes)
    if SelectedBoundaryPorts is None:
        raise ValueError("selected physical ports have no frozen boundary")
    GlobalBoundaryPorts = tuple(sorted(
        SelectedBoundaryPorts,
        key=lambda Value: Value.Signal,
    ))
    PhysicalInterfaceFingerprint = BuildStableFingerprint((
        Problem.Interface.InterfaceFingerprint,
        tuple(
            Value.ReservationFingerprint for Value in SelectedPorts
        ),
        tuple(
            Value.ToDictionary()
            for Value in Problem.Interface.Feedthroughs
        ),
    ))
    PortAssignmentFingerprint = BuildStableFingerprint(tuple(
        (
            Value.Signal,
            Value.ReservationFingerprint,
        )
        for Value in SelectedPorts
    ))
    Resources.PreferredPhysicalComponentPortReservationsBySignal = {
        Value.Signal: Value.ReservationFingerprint
        for Value in SelectedPorts
    }
    ResourceGraphFingerprint = Preparation.ResourceGraphFingerprint
    TechnologyFingerprint = BuildStableFingerprint(
        repr(getattr(ResourceGraph, "Technology", None))
    )
    LocalAccessDomainFingerprint = (
        BuildPhysicalLocalAccessDomainFingerprint(Problem)
    )
    GlobalKeepoutNodes = tuple(sorted(
        (
            int(X),
            ResourceGraph.Technology.RoutingY(
                MinimumPlacementY,
                int(Layer),
            ),
            int(Z),
        )
        for Layer, Cells in sorted(
            Preparation.ComponentKeepoutGuideCellsByLayer
        )
        for X, Z in Cells
    ))
    GlobalKeepoutFingerprint = BuildStableFingerprint((
        "physical-component-global-keepout-v1",
        GlobalKeepoutNodes,
    ))
    PlanFingerprint = BuildStableFingerprint((
        Problem.PlacementFingerprint,
        ComponentGraphFingerprint,
        ResourceGraphFingerprint,
        TechnologyFingerprint,
        PhysicalInterfaceFingerprint,
        AccessCertificate.CertificateFingerprint,
        LocalAccessDomainFingerprint,
        GlobalKeepoutFingerprint,
        Preparation.ExteriorFabricSetFingerprint,
        Preparation.ExteriorRegionFingerprint,
        Preparation.ExteriorCapacityLedgerFingerprint,
        PortAssignmentFingerprint,
        tuple(
            Value.ReservationFingerprint for Value in SelectedPorts
        ),
        tuple(
            Value.ReservationFingerprint
            for Value in ChannelReservations
        ),
    ))
    Plan = PhysicalComponentAssemblyPlan(
        PlanFingerprint=PlanFingerprint,
        PortAssignmentFingerprint=PortAssignmentFingerprint,
        PlacementFingerprint=Problem.PlacementFingerprint,
        ComponentGraphFingerprint=ComponentGraphFingerprint,
        ResourceGraphFingerprint=ResourceGraphFingerprint,
        TechnologyFingerprint=TechnologyFingerprint,
        InterfaceFingerprint=PhysicalInterfaceFingerprint,
        ComponentId=Problem.Interface.ComponentId,
        EnvelopeMinimum=(
            Envelope.MinimumX,
            Envelope.MinimumY,
            Envelope.MinimumZ,
        ),
        EnvelopeMaximum=(
            Envelope.MaximumX,
            Envelope.MaximumY,
            Envelope.MaximumZ,
        ),
        KeepoutClaims=KeepoutClaims,
        Ports=SelectedPorts,
        Channels=(),
        Corridors=tuple(ChannelReservations),
        GlobalKeepoutNodes=GlobalKeepoutNodes,
        GlobalKeepoutFingerprint=GlobalKeepoutFingerprint,
        Feedthroughs=Problem.Interface.Feedthroughs,
        AssemblyChoiceFingerprint=AssemblyChoiceFingerprint,
        StageOrder=(
            "PhysicalBoundaryPlanning",
            "AuthoritativeGlobalReserve",
            "LocalSupportBinding",
            "ClosedComponentCompilation",
            "AuthoritativeDetailedRouting",
        ),
        Complete=True,
        AccessCertificateFingerprint=(
            AccessCertificate.CertificateFingerprint
        ),
        LocalAccessDomainFingerprint=LocalAccessDomainFingerprint,
        ExteriorFabricSetFingerprint=(
            Preparation.ExteriorFabricSetFingerprint
        ),
        ExteriorRegionFingerprint=(
            Preparation.ExteriorRegionFingerprint
        ),
        ExteriorCapacityLedgerFingerprint=(
            Preparation.ExteriorCapacityLedgerFingerprint
        ),
        ExteriorFabrics=Preparation.ExteriorFabrics,
        GlobalBoundaryPorts=GlobalBoundaryPorts,
        SelectedLocalPortSupports=(),
    )
    # The physical assembly fixes only the port seam and attachment.  Owned
    # terminal access remains a closed-component compilation variable; binding
    # one access candidate here would make global planning local-first again.
    BoundDomains = Problem.OwnedTerminalDomains
    BoundInterface = replace(
        Problem.Interface,
        InterfaceFingerprint=PhysicalInterfaceFingerprint,
        PhysicalPortReservations=SelectedPorts,
        PhysicalAssemblyPlanFingerprint=PlanFingerprint,
        Complete=bool(
            Problem.Interface.Complete
            and all(Domain.Candidates for Domain in BoundDomains)
        ),
    )
    BoundProblem = replace(
        Problem,
        ProblemFingerprint=BuildStableFingerprint((
            Problem.ProblemFingerprint,
            PlanFingerprint,
        )),
        OwnedTerminalDomains=BoundDomains,
        Interface=BoundInterface,
        PhysicalAssemblyPlan=Plan,
        ReservedGlobalClaimsBySignal=(),
        DomainComplete=bool(
            Problem.DomainComplete
            and BoundInterface.Complete
            and all(Domain.Candidates for Domain in BoundDomains)
        ),
    )
    EmittedFingerprintsByDomain = getattr(
        Resources,
        "PhysicalComponentAssemblyPlanFingerprintsByDomain",
        None,
    )
    if EmittedFingerprintsByDomain is None:
        EmittedFingerprintsByDomain = {}
        Resources.PhysicalComponentAssemblyPlanFingerprintsByDomain = (
            EmittedFingerprintsByDomain
        )
    EmittedFingerprints = EmittedFingerprintsByDomain.setdefault(
        BoundaryIteratorCacheKey,
        set(),
    )
    # Replayed fixed-boundary iterations can still request additional local
    # assignments for a known global boundary, so do not consume the
    # returned boundary fingerprint here.
    #
    # Infeasible branches add boundary fingerprints above; successful emits
    # are deduplicated by the physical-plan fingerprint below.
    if PlanFingerprint in EmittedFingerprints:
        raise RoutingStageError(RoutingFailure(
            Reason=(
                RoutingFailureReason.ComponentAssemblyIdentityMismatch
            ),
            Stage="PhysicalAssemblyPlanDomainDuplicate",
            Detail=(
                "the monotonic physical assembly domain emitted a prior "
                "plan fingerprint"
            ),
            Diagnostics={
                "AssemblyPlanDomainFingerprint": (
                    BoundaryIteratorCacheKey
                ),
                "RepeatedPlanFingerprint": PlanFingerprint,
                "RepeatedPlanFingerprintCount": 1,
                "BoundaryIteratorContinuationPreserved": True,
                "BoundaryIteratorCacheCleared": False,
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    EmittedFingerprints.add(PlanFingerprint)
    return PreparedPhysicalComponentAssembly(
        Plan=Plan,
        Problem=BoundProblem,
        GlobalGuidePlan=CoarsePlan,
        PortFactorDomain=Preparation,
    )

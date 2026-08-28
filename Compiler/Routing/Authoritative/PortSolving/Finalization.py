"""Final immutable physical component assembly publication."""

from __future__ import annotations

from ...Components.PhysicalPlanning import BuildPhysicalComponentAssemblyChoiceFingerprint

from ...Components.PhysicalPlanning import BuildPhysicalComponentAssemblyPlanDomainFingerprint

from ...Components.PhysicalPlanning import BuildPhysicalComponentPortSolverCacheKey

from ...Components.Validation import BuildPhysicalLocalAccessDomainFingerprint

from ...Components.Validation import BuildPhysicalPortApertureContractFingerprint

from ...Components.Validation import BuildPhysicalPortSeamContractFingerprint

from ...Components.InterfacePlanning import ComponentInterfaceContract

from ...Components.InterfacePlanning import IterClosedComponentContracts

from ...Contracts.Component import PhysicalComponentAssemblyPlan

from ...Contracts.Component import PhysicalComponentBoundaryPortReservation

from ...Contracts.Component import PhysicalComponentChannelReservation

from ...Contracts.Component import PhysicalComponentPortReservation

from ...Contracts.Component import PreparedPhysicalComponentFeedthroughEndpointDomain

from ...Contracts.PhysicalInterface import PhysicalPortLaneFactor

from ...Contracts.PhysicalInterface import PhysicalPortSeamFactor

from ...Contracts.PhysicalInterface import PreparedPhysicalComponentAssembly

from ...Contracts.PhysicalInterface import PreparedPhysicalComponentPortFactorDomain

from ...Contracts.Results import RoutingResources

from ...Failures import RoutingFailure

from ...Failures import RoutingFailureReason

from ...Failures import RoutingStageError

from ...Interfaces.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint

from ...Interfaces.PhysicalClaims import ComponentClaimsConflict

from ...Reliability import BuildStableFingerprint

from ...Reliability import RoutingDeadline

from ...ResourceGraph import BuildRoutingEnvelope

from ...ResourceGraph import FindSelfClaimConflicts

from ...ResourceGraph import RoutingResourceClaims

from ...Technology import DefaultRedstoneRoutingTechnology

from collections import Counter

from collections import defaultdict

from dataclasses import replace

from itertools import combinations

from math import prod

from types import SimpleNamespace

from typing import Any

from typing import Callable

from typing import Iterable

from ..AssignmentState import (
    BuildPhysicalLocalPortPairUnsupportedIndex,
    BuildPhysicalPortNoGoodKeys,
    FindProofQualifiedCompleteDomainNoGoodCore,
    FindProofQualifiedUniversalNoGoodCore,
    GetPersistentPhysicalComponentPortCspState,
    OrderPhysicalPortOptionsByPreferences,
    PropagateExactNoGoodClauses,
    SelectBinaryExactNoGoodClauses,
    SelectExactNoGoodCspBranch,
)

from ..CandidateGuides import (
    PropagateLaneFactorArcConsistency,
)

from ..ExteriorConnectors import (
    BuildPhysicalBoundaryPortAssignmentFingerprint,
    SelectPhysicalFactorBranchSignal,
)

from ..PhysicalGuides import (
    BuildComponentKeepoutAvoidingGlobalGuides,
    BuildExplicitPhysicalComponentFeedthrough,
    FindSignalClaimConflicts,
    MaterializeSupportedPhysicalPortReservation,
    PreparePhysicalComponentFeedthroughEndpointDomain,
)

from ..TrackPortfolio import (
    BuildSeamOnlyPhysicalComponentPortReservation,
    InterleavePhysicalPortSeamsByEgressClass,
)


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

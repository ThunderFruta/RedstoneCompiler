"""Prepared physical-port solve identity and feasibility validation."""

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

from typing import NamedTuple


class PreparedPortSolveInputs(NamedTuple):
    Problem: Any
    CoarsePlan: Any
    AccessCertificate: Any
    ResourceGraph: Any
    LocalSeamNoGoodClauses: Any
    CurrentResourceGraphFingerprint: Any
    CurrentGuideFingerprint: Any
    LaneFactorsBySignal: Any
    CurrentLocalAccessFactorsBySignal: Any
    CurrentApertureFactorsBySignal: Any
    CurrentLocalApertureSupportBySignal: Any
    CurrentDomainFingerprint: Any


def ValidatePreparedPhysicalComponentPortFactorDomain(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    Resources: RoutingResources,
) -> PreparedPortSolveInputs:
    """Validate immutable identities before exact port search."""
    if not Preparation.Complete:
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete,
            Stage="PhysicalComponentAssemblyPlanning",
            Detail="physical component port factor preparation is incomplete",
        ))
    Problem = Preparation.Problem
    CoarsePlan = Preparation.CoarsePlan
    AccessCertificate = Preparation.AccessCertificate
    ResourceGraph = Resources.ResourceGraph
    LocalSeamNoGoodClauses = getattr(
        Resources,
        "RejectedPhysicalComponentLocalSeamReservationSets",
        set(),
    )
    if ResourceGraph is None:
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ComponentAssemblyIdentityMismatch,
            Stage="PhysicalComponentAssemblyPlanning",
            Detail="prepared physical port factors require a resource graph",
        ))
    CurrentResourceGraphFingerprint = (
        Preparation.ResourceGraphFingerprint
        if (
            Preparation.ExteriorFabrics
            and all(
                Fabric.Complete
                and Fabric.ResourceGraphFingerprint
                == Preparation.ResourceGraphFingerprint
                and Fabric.RegionFingerprint
                == Preparation.ExteriorRegionFingerprint
                for Fabric in Preparation.ExteriorFabrics
            )
        )
        else BuildStableFingerprint((
            getattr(ResourceGraph, "GraphVersion", ""),
            "",
            (),
            0,
            0,
        ))
    )
    CurrentGuideFingerprint = BuildStableFingerprint((
        tuple(
            (Signal, tuple(sorted(Values)))
            for Signal, Values in sorted(CoarsePlan.Guides.items())
        ),
        tuple(sorted(CoarsePlan.Layers.items())),
    ))
    LaneFactorsBySignal = dict(Preparation.LaneFactorsBySignal)
    # The preparation and every nested factor/certificate model are frozen.
    # Re-decomposing the complete lane domain on each learned-clause re-solve
    # cannot reveal a valid state change; it only rebuilds the same large
    # local-support index.  Preserve live resource-graph and guide checks,
    # then trust the preparation's construction-time factor identity.
    CurrentLocalAccessFactorsBySignal = (
        Preparation.LocalAccessFactorsBySignal
    )
    CurrentApertureFactorsBySignal = (
        Preparation.ApertureFactorsBySignal
    )
    CurrentLocalApertureSupportBySignal = (
        Preparation.LocalApertureSupportBySignal
    )
    CurrentDomainFingerprint = Preparation.DomainFingerprint
    IdentityMismatches = tuple(
        Name
        for Name, Matches in (
            (
                "placement",
                Problem.PlacementFingerprint
                == Preparation.PlacementFingerprint,
            ),
            (
                "resource-graph",
                CurrentResourceGraphFingerprint
                == Preparation.ResourceGraphFingerprint,
            ),
            (
                "guide",
                CurrentGuideFingerprint == Preparation.GuideFingerprint,
            ),
            (
                "access-certificate",
                AccessCertificate.CertificateFingerprint
                == Preparation.AccessCertificateFingerprint,
            ),
            (
                "access-certificate-placement",
                AccessCertificate.PlacementFingerprint
                == Preparation.AccessCertificatePlacementFingerprint
                == Preparation.PlacementFingerprint,
            ),
            (
                "access-certificate-resource-graph",
                AccessCertificate.ResourceGraphFingerprint
                == Preparation.AccessCertificateResourceGraphFingerprint,
            ),
            (
                "access-certificate-component-graph",
                AccessCertificate.ComponentGraphFingerprint
                == Preparation.AccessCertificateComponentGraphFingerprint
                == Preparation.ComponentGraphFingerprint,
            ),
            (
                "factor-domain",
                CurrentDomainFingerprint == Preparation.DomainFingerprint,
            ),
            (
                "local-access-factor-domain",
                CurrentLocalAccessFactorsBySignal
                == Preparation.LocalAccessFactorsBySignal,
            ),
            (
                "aperture-factor-domain",
                CurrentApertureFactorsBySignal
                == Preparation.ApertureFactorsBySignal,
            ),
            (
                "local-aperture-support-domain",
                CurrentLocalApertureSupportBySignal
                == Preparation.LocalApertureSupportBySignal,
            ),
            (
                "exterior-fixed-claim-certificates",
                all(
                    Certificate.Complete
                    and Certificate.PlacementFingerprint
                    == Preparation.PlacementFingerprint
                    and Certificate.ResourceGraphFingerprint
                    == Preparation.ResourceGraphFingerprint
                    for Certificate
                    in Preparation.ExteriorFixedClaimCertificates
                ),
            ),
        )
        if not Matches
    )
    if IdentityMismatches:
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ComponentAssemblyIdentityMismatch,
            Stage="PhysicalComponentAssemblyPlanning",
            Detail=(
                "prepared physical port factor identity mismatch: "
                + ", ".join(IdentityMismatches)
            ),
            Diagnostics={
                "PreparationFingerprint": Preparation.DomainFingerprint,
                "IdentityMismatches": list(IdentityMismatches),
            },
        ))
    if not Preparation.Feasible:
        EmptySignals = tuple(
            Signal
            for Signal, Values in Preparation.LaneFactorsBySignal
            if not Values
        )
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
            Stage="PhysicalComponentAssemblyPlanning",
            AffectedNets=EmptySignals,
            Detail=(
                "at least one component port has no legal exact seam "
                "compatible with the reserved global corridors"
            ),
            Diagnostics={
                "EmptyPortSignals": list(EmptySignals),
                "LaneFactorDiagnosticsBySignal": dict(
                    Preparation.DiagnosticsBySignal
                ),
                "ComponentFabricConstructionComplete": True,
                "OwnershipSearchComplete": True,
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    return PreparedPortSolveInputs(
        Problem,
        CoarsePlan,
        AccessCertificate,
        ResourceGraph,
        LocalSeamNoGoodClauses,
        CurrentResourceGraphFingerprint,
        CurrentGuideFingerprint,
        LaneFactorsBySignal,
        CurrentLocalAccessFactorsBySignal,
        CurrentApertureFactorsBySignal,
        CurrentLocalApertureSupportBySignal,
        CurrentDomainFingerprint,
    )

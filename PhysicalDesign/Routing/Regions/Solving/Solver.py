"""Public component solve dispatch, materialization, and routed handoff validation."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from hashlib import sha256
from itertools import combinations, islice, product
from math import prod as ProductIntegers
from time import monotonic
from typing import Any, Callable, Iterable, Mapping


from ....Contracts.Component import ClosedComponentInterface, ComponentFeedthroughContract, ComponentForeignTransitDomain, ComponentInterfacePort, ComponentRoutingFabric, ComponentRoutingProblem, ComponentRoutingSolveResult, ComponentTerminalAccessCandidate, ComponentTerminalAccessDomain, RoutedComponentNet, RoutedComponentTemplate
from ....Contracts.Core import Position3
from ....Interfaces.PhysicalClaims import _MergeClaims, ComponentClaimsCompatibleForOwners, ComponentClaimsConflict
from ....Resources.ResourceGraph import FindSelfClaimConflicts, LocalRouteClaim, PinAccessPortal, RoutingEdge, RoutingReservation, RoutingResourceId, RoutingResourceKind, RoutingResourceClaims
from ....Redstone.Technology import DefaultRedstoneRoutingTechnology

try:
    from RedstoneCompiler.RustRouting import (
        BuildFabricSubtreesBatchWithTelemetry as _BuildFabricSubtreesBatchWithTelemetry,
    )
    from RedstoneCompiler.RustRouting import BuildRouteClaimsBatch as _BuildRouteClaimsBatch
    from RedstoneCompiler.RustRouting import (
        BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
    )
    from RedstoneCompiler.RustRouting import GetRoutingThreadCount as _GetRoutingThreadCount
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

from ..Core import CompleteComponentNetPortfolioStaticContext, _NormalizedEdge
from .DynamicSolver import SolveComponentRoutingProblemDynamic
from ..Interfaces.Fabric import BuildComponentRoutingFabric
from .LegacySolver import _SolveComponentRoutingProblemLegacy
def SolveComponentRoutingProblem(
    Problem: ComponentRoutingProblem,
    *,
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
    SymbolicNetStateCache: dict[str, Any] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
    DiscoveryVariantLimit: int | None = 8,
    DiscoveryVariantLimitsBySignal: dict[
        str, int | None
    ] | None = None,
    RequiredForeignTransitSignals: frozenset[str] = frozenset(),
    StopAfterCompleteNetVariantPortfolioSignal: str | None = None,
    StaticPortfolioContextsBySignal: dict[
        str, CompleteComponentNetPortfolioStaticContext
    ] | None = None,
    StopAfterOwnedSignalFrontierProof: bool = False,
    StopAfterSymbolicCapacityProof: bool = False,
) -> ComponentRoutingSolveResult:
    """Dispatch physical tree fabrics to DP and retain the legacy oracle."""
    UseDynamicSolver = bool(
        StopAfterCompleteNetVariantPortfolioSignal is None
        and Problem.Interface is not None
        and (
            (
                Problem.PhysicalAssemblyPlan is not None
                and Problem.Interface.PhysicalPortReservations
            )
            or (
                StopAfterOwnedSignalFrontierProof
                and Problem.PhysicalAssemblyPlan is None
                and not Problem.Interface.PhysicalPortReservations
                and not Problem.ReservedGlobalClaimsBySignal
            )
            or (
                StopAfterSymbolicCapacityProof
                and Problem.PhysicalAssemblyPlan is None
                and Problem.Interface.PhysicalPortReservations
                and not Problem.ReservedGlobalClaimsBySignal
            )
        )
        and Problem.Fabric.TopologyKind in {
            "tree",
            "tree-forest",
            "closed-component-port-forest-v3",
            "closed-component-bridged-forest-v1",
        }
    )
    if UseDynamicSolver:
        return SolveComponentRoutingProblemDynamic(
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
            RequiredForeignTransitSignals=RequiredForeignTransitSignals,
            RouteClaimsConstructionCache=RouteClaimsConstructionCache,
            SymbolicNetStateCache=SymbolicNetStateCache,
            StopAfterOwnedSignalFrontierProof=(
                StopAfterOwnedSignalFrontierProof
            ),
            StopAfterSymbolicCapacityProof=(
                StopAfterSymbolicCapacityProof
            ),
        )
    return _SolveComponentRoutingProblemLegacy(
        Problem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        ForbiddenAssignmentFingerprints=ForbiddenAssignmentFingerprints,
        ForbiddenExportPortsBySignal=ForbiddenExportPortsBySignal,
        ForbiddenForeignCandidateFingerprintsBySignal=(
            ForbiddenForeignCandidateFingerprintsBySignal
        ),
        ForbiddenForeignAssignmentPairs=ForbiddenForeignAssignmentPairs,
        VariantPortfolioCache=VariantPortfolioCache,
        NetVariantConstructionCache=NetVariantConstructionCache,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        NetVariantDiscoveryStateCache=NetVariantDiscoveryStateCache,
        DiscoveryVariantLimit=DiscoveryVariantLimit,
        DiscoveryVariantLimitsBySignal=DiscoveryVariantLimitsBySignal,
        RequiredForeignTransitSignals=RequiredForeignTransitSignals,
        StopAfterCompleteNetVariantPortfolioSignal=(
            StopAfterCompleteNetVariantPortfolioSignal
        ),
        StaticPortfolioContextsBySignal=StaticPortfolioContextsBySignal,
    )


def MaterializeRoutedComponentTemplate(
    Placed: Any,
    Template: RoutedComponentTemplate,
) -> Any:
    """Freeze component trees and every proved continuation escape corridor."""
    ExistingClaims = tuple(getattr(Placed, "LocalRouteClaims", ()) or ())
    ComponentSignals = frozenset(Net.Signal for Net in Template.Nets)
    RetainedClaims = tuple(
        Claim for Claim in ExistingClaims if Claim.Signal not in ComponentSignals
    )
    ComponentClaims = tuple(
        LocalRouteClaim(
            Signal=Net.Signal,
            ClusterId=-1,
            Root=Net.Root,
            ConnectedTargets=Net.CoveredTerminals,
            BoundaryNodes=Net.ExportedPorts or tuple(Net.Nodes),
            Nodes=Net.Nodes,
            Edges=Net.Edges,
            Claims=Net.Claims,
            ExactRouteSignalBlocks=len(Net.WireCells),
            ExactRouteRefreshBlocks=len(Net.RepeaterInputFacings),
            ExactRouteSupportBlocks=len(Net.SupportCells),
        )
        for Net in Template.Nets
    )
    ForeignEscapeClaims = tuple(
        LocalRouteClaim(
            Signal=Signal,
            ClusterId=-2,
            Root=Terminal,
            # A passive target witness already connects its gate terminal to
            # the exported boundary node.  Source terminals are harmless in
            # this set because they are not present in a profile's targets.
            ConnectedTargets=(Terminal,),
            BoundaryNodes=(Candidate.Path[-1],),
            Nodes=frozenset(Candidate.Path),
            Edges=frozenset(
                _NormalizedEdge(First, Second)
                for First, Second in zip(
                    Candidate.Path,
                    Candidate.Path[1:],
                )
            ),
            Claims=Candidate.Claims,
            ExactRouteSignalBlocks=len(Candidate.Claims.WireCells),
            ExactRouteSupportBlocks=len(Candidate.Claims.SupportCells),
        )
        for Signal, Terminal, Candidate in (
            Template.ForeignEscapeReservations
        )
        if Candidate.Path
    )
    ExternalContinuationClaims = tuple(
        LocalRouteClaim(
            Signal=Signal,
            ClusterId=-3,
            Root=Terminal,
            ConnectedTargets=(Terminal,),
            BoundaryNodes=(Candidate.Path[-1],),
            Nodes=frozenset(Candidate.Path),
            Edges=frozenset(
                _NormalizedEdge(First, Second)
                for First, Second in zip(
                    Candidate.Path,
                    Candidate.Path[1:],
                )
            ),
            Claims=Candidate.Claims,
            ExactRouteSignalBlocks=len(Candidate.Claims.WireCells),
            ExactRouteSupportBlocks=len(Candidate.Claims.SupportCells),
        )
        for Signal, Terminal, Candidate in (
            Template.ExternalContinuationReservations
        )
        if Candidate.Path
    )
    ForeignTransitClaims = tuple(
        LocalRouteClaim(
            Signal=Net.Signal,
            ClusterId=-4,
            Root=Net.Root,
            ConnectedTargets=tuple(
                Position
                for Position in Net.CoveredTerminals
                if Position != Net.Root
            ),
            BoundaryNodes=tuple(Net.CoveredTerminals),
            Nodes=Net.Nodes,
            Edges=Net.Edges,
            Claims=Net.Claims,
            RepeaterReservations=tuple(
                RoutingReservation(
                    Signal=Net.Signal,
                    Resource=RoutingResourceId(
                        RoutingResourceKind.Wire,
                        Position,
                    ),
                    Position=Position,
                    Purpose="Repeater",
                    InputFacing=Facing,
                )
                for Position, Facing in Net.RepeaterInputFacings
            ),
            ExactRouteSignalBlocks=len(Net.WireCells),
            ExactRouteRefreshBlocks=len(Net.RepeaterInputFacings),
            ExactRouteSupportBlocks=len(Net.SupportCells),
        )
        for Net in Template.ForeignTransitReservations
    )
    Diagnostics = dict(getattr(Placed, "LocalRouteDiagnostics", {}) or {})
    Diagnostics["__RoutedComponentTemplate__"] = Template.ToDictionary()
    Diagnostics["__RoutedComponentGlobalHandoff__"] = {
        "RetiredClusterBoundaryLeaseRequestCount": len(
            getattr(Placed, "ClusterBoundaryLeaseRequests", ()) or ()
        ),
        "GlobalAccessPolicy": (
            "authoritative-route-assignment-with-frozen-component-obstacles"
        ),
        "FrozenForeignEscapeClaimCount": len(ForeignEscapeClaims),
        "FrozenForeignEscapeSignals": sorted({
            Claim.Signal for Claim in ForeignEscapeClaims
        }),
        "FrozenExternalContinuationClaimCount": len(
            ExternalContinuationClaims
        ),
        "FrozenForeignTransitClaimCount": len(
            ForeignTransitClaims
        ),
        "FrozenForeignTransitSignals": sorted({
            Claim.Signal for Claim in ForeignTransitClaims
        }),
        "FabricFingerprint": Template.FabricFingerprint,
        "ArchivedChannelFingerprint": str(getattr(
            getattr(Placed, "InterClusterRoutingChannel", None),
            "ChannelFingerprint",
            "",
        )),
        "InterfaceFingerprint": Template.InterfaceFingerprint,
        "ImplicitForeignTransitDomainCount": int(
            Template.Diagnostics.get(
                "ImplicitForeignTransitDomainCount",
                0,
            )
        ),
    }
    ActiveChannel = getattr(
        Placed,
        "InterClusterRoutingChannel",
        None,
    )
    return replace(
        Placed,
        LocalRouteClaims=(
            *RetainedClaims,
            *ComponentClaims,
            *ExternalContinuationClaims,
            *ForeignEscapeClaims,
            *ForeignTransitClaims,
        ),
        LocalRouteDiagnostics=Diagnostics,
        RoutedComponentTemplates=(
            *(getattr(Placed, "RoutedComponentTemplates", ()) or ()),
            Template,
        ),
        RoutedComponentRoutingChannels=(
            *(
                getattr(
                    Placed,
                    "RoutedComponentRoutingChannels",
                    (),
                )
                or ()
            ),
            *((ActiveChannel,) if ActiveChannel is not None else ()),
        ),
        # The complete component template replaces the dense boundary-lease
        # pre-solver.  Remaining global nets are assigned by the ordinary
        # authoritative router against immutable component claims, with the
        # proved passive witnesses retained in their portal domains.
        ClusterBoundaryLeaseRequests=(),
        CompleteClusterInterfaceAccess=False,
        InterClusterRoutingChannel=None,
    )


def ValidateRoutedComponentHandoff(
    Placed: Any,
    Template: RoutedComponentTemplate,
    *,
    PlacementFingerprint: str,
    LocalTemplateFingerprint: str,
) -> dict[str, object]:
    """Validate every immutable identity before ordinary global routing."""
    Channel = getattr(Placed, "InterClusterRoutingChannel", None)
    if Channel is None:
        ArchivedChannels = tuple(
            getattr(
                Placed,
                "RoutedComponentRoutingChannels",
                (),
            )
            or ()
        )
        Channel = ArchivedChannels[-1] if ArchivedChannels else None
    ChannelFingerprint = str(
        getattr(Channel, "ChannelFingerprint", "")
    )
    if Template.PlacementFingerprint != PlacementFingerprint:
        raise ValueError("routed component placement fingerprint mismatch")
    if Template.LocalTemplateFingerprint != LocalTemplateFingerprint:
        raise ValueError("routed component local-template fingerprint mismatch")
    if Template.FabricFingerprint == "" or ChannelFingerprint == "":
        raise ValueError("routed component fabric identity is missing")
    InterfaceDiagnostic = (
        (getattr(Placed, "LocalRouteDiagnostics", {}) or {})
        .get("__RoutedComponentGlobalHandoff__", {})
    )
    if (
        InterfaceDiagnostic.get("FabricFingerprint")
        != Template.FabricFingerprint
    ):
        raise ValueError("routed component fabric fingerprint mismatch")
    if (
        InterfaceDiagnostic.get("ArchivedChannelFingerprint")
        != ChannelFingerprint
    ):
        raise ValueError(
            "routed component archived-channel fingerprint mismatch"
        )
    ArchivedFabricFingerprint = (
        BuildComponentRoutingFabric(Channel).FabricFingerprint
    )
    if (
        Template.InterfaceFingerprint
        and InterfaceDiagnostic.get("InterfaceFingerprint")
        != Template.InterfaceFingerprint
    ):
        raise ValueError("routed component interface fingerprint mismatch")
    if int(InterfaceDiagnostic.get(
        "ImplicitForeignTransitDomainCount",
        0,
    )) != 0:
        raise ValueError(
            "routed component handoff contains implicit foreign transit"
        )
    PlacedTemplates = tuple(
        getattr(Placed, "RoutedComponentTemplates", ()) or ()
    )
    if not any(
        Value.RoutedTemplateFingerprint
        == Template.RoutedTemplateFingerprint
        and Value.ExportedPortFingerprint
        == Template.ExportedPortFingerprint
        and Value.ClaimsFingerprint == Template.ClaimsFingerprint
        for Value in PlacedTemplates
    ):
        raise ValueError("routed component template identity was not frozen")
    LocalClaims = tuple(
        getattr(Placed, "LocalRouteClaims", ()) or ()
    )
    for Net in Template.Nets:
        if not any(
            Claim.Signal == Net.Signal
            and Claim.Root == Net.Root
            and Claim.Nodes == Net.Nodes
            and Claim.Claims == Net.Claims
            and tuple(Claim.BoundaryNodes)
            == (Net.ExportedPorts or tuple(Net.Nodes))
            for Claim in LocalClaims
        ):
            raise ValueError(
                "routed component net claim was not frozen exactly"
            )
    for Net in Template.ForeignTransitReservations:
        if not any(
            int(getattr(Claim, "ClusterId", 0)) == -4
            and Claim.Signal == Net.Signal
            and Claim.Root == Net.Root
            and Claim.Nodes == Net.Nodes
            and Claim.Claims == Net.Claims
            and tuple(Claim.BoundaryNodes)
            == tuple(Net.CoveredTerminals)
            for Claim in LocalClaims
        ):
            raise ValueError(
                "routed component foreign transit was not frozen exactly"
            )
    return {
        "PlacementFingerprint": PlacementFingerprint,
        "LocalTemplateFingerprint": LocalTemplateFingerprint,
        "FabricFingerprint": Template.FabricFingerprint,
        "ArchivedChannelFingerprint": ChannelFingerprint,
        "ArchivedFabricFingerprint": ArchivedFabricFingerprint,
        "FabricAugmentedForExactAccess": (
            ArchivedFabricFingerprint
            != Template.FabricFingerprint
        ),
        "RoutedTemplateFingerprint": (
            Template.RoutedTemplateFingerprint
        ),
        "ExportedPortFingerprint": (
            Template.ExportedPortFingerprint
        ),
        "ClaimsFingerprint": Template.ClaimsFingerprint,
        "InterfaceFingerprint": Template.InterfaceFingerprint,
        "Valid": True,
    }


def PreserveRoutedComponentForeignEscapes(
    Placed: Any,
    RawPortals: dict[
        tuple[str, Position3, int],
        tuple[PinAccessPortal, ...],
    ],
) -> tuple[
    dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]],
    dict[str, object],
]:
    """Preserve witnesses unless their corridor is already a frozen claim."""
    Result = dict(RawPortals)
    PreservedCount = 0
    FrozenClaimCount = 0
    ExportedSourcePortCount = 0
    RequiredCount = 0
    ContinuationRequiredCount = 0
    ContinuationPreservedCount = 0
    ContinuationMissingCount = 0
    FrozenWitnesses = frozenset(
        (Claim.Signal, Claim.Root)
        for Claim in (
            getattr(Placed, "LocalRouteClaims", ()) or ()
        )
        if int(getattr(Claim, "ClusterId", -1)) == -2
    )
    ProducerTerminals = frozenset(
        tuple(Gate.OutputPin)
        for Gate in getattr(Placed, "PlacedGates", ())
        if getattr(Gate, "OutputPin", None) is not None
    )
    for Template in (
        getattr(Placed, "RoutedComponentTemplates", ()) or ()
    ):
        for Signal, Terminal, Candidate in getattr(
            Template,
            "ExternalContinuationReservations",
            (),
        ):
            ContinuationRequiredCount += 1
            MatchingKeys = tuple(
                Key
                for Key in Result
                if Key[0] == Signal and Key[1] == Terminal
            )
            SelectedValues = tuple(
                Portal
                for Key in MatchingKeys
                for Portal in Result.get(Key, ())
                if (
                    tuple(Portal.Path) == Candidate.Path
                    and Portal.Claims == Candidate.Claims
                )
            )
            for Key in MatchingKeys:
                Result.pop(Key, None)
            if not SelectedValues:
                ContinuationMissingCount += 1
                continue
            for Portal in SelectedValues:
                Key = (Signal, Terminal, int(Portal.Layer))
                Result[Key] = (
                    *Result.get(Key, ()),
                    Portal,
                )
            ContinuationPreservedCount += 1
        for Signal, Terminal, Candidate in (
            Template.ForeignEscapeReservations
        ):
            RequiredCount += 1
            if (Signal, Terminal) in FrozenWitnesses:
                # The terminal has been replaced by a same-net continuation
                # claim.  Keeping its pre-materialization portal would make
                # the global matcher look up a target access path that no
                # longer exists in the transformed profile.
                for Key in tuple(Result):
                    if Key[0] == Signal and Key[1] == Terminal:
                        Result.pop(Key)
                if Terminal in ProducerTerminals:
                    Port = Candidate.Path[-1]
                    PortClaims = RoutingResourceClaims(
                        WireCells=frozenset((Port,)),
                        SupportCells=frozenset((
                            (Port[0], Port[1] - 1, Port[2]),
                        )),
                        ElectricalCells=frozenset(
                            DefaultRedstoneRoutingTechnology
                            .BuildElectricalExclusions({Port})
                        ),
                    )
                    Portal = PinAccessPortal(
                        PortalId=(
                            "routed-component-foreign-source-"
                            f"{Candidate.CandidateFingerprint}"
                        ),
                        Signal=Signal,
                        Terminal=Port,
                        Layer=int(Candidate.Layer),
                        Path=(Port,),
                        Edges=frozenset(),
                        Claims=PortClaims,
                        Length=1,
                        BendCount=0,
                        ViaCount=0,
                        Cost=0,
                    )
                    Result[
                        (Signal, Port, int(Candidate.Layer))
                    ] = (Portal,)
                    ExportedSourcePortCount += 1
                FrozenClaimCount += 1
                continue
            MatchingKeys = tuple(
                Key
                for Key in Result
                if Key[0] == Signal and Key[1] == Terminal
            )
            Matched = False
            Witness = None
            WitnessKey = None
            for Key in MatchingKeys:
                Values = Result[Key]
                Witnesses = tuple(
                    Value
                    for Value in Values
                    if (
                        tuple(Value.Path) == Candidate.Path
                        and Value.Claims == Candidate.Claims
                    )
                )
                if not Witnesses:
                    continue
                Witness = min(
                    Witnesses,
                    key=lambda Value: (
                        Value.Cost,
                        Value.PortalId,
                    ),
                )
                Matched = True
                WitnessKey = Key
                break
            if not Matched:
                PathEdges = frozenset(
                    _NormalizedEdge(First, Second)
                    for First, Second in zip(
                        Candidate.Path,
                        Candidate.Path[1:],
                    )
                )
                BendCount = sum(
                    (
                        First[0] - Previous[0],
                        First[1] - Previous[1],
                        First[2] - Previous[2],
                    )
                    != (
                        Second[0] - First[0],
                        Second[1] - First[1],
                        Second[2] - First[2],
                    )
                    for Previous, First, Second
                    in zip(
                        Candidate.Path,
                        Candidate.Path[1:],
                        Candidate.Path[2:],
                    )
                )
                ViaCount = sum(
                    First[1] != Second[1]
                    for First, Second in zip(
                        Candidate.Path,
                        Candidate.Path[1:],
                    )
                )
                Witness = PinAccessPortal(
                    PortalId=(
                        "routed-component-foreign-"
                        f"{Candidate.CandidateFingerprint}"
                    ),
                    Signal=Signal,
                    Terminal=Terminal,
                    Layer=int(Candidate.Layer),
                    Path=Candidate.Path,
                    Edges=PathEdges,
                    Claims=Candidate.Claims,
                    Length=len(Candidate.Path),
                    BendCount=BendCount,
                    ViaCount=ViaCount,
                    Cost=int(Candidate.Cost),
                )
            assert Witness is not None
            Key = WitnessKey or (
                Signal, Terminal, int(Candidate.Layer)
            )
            Result[Key] = (
                Witness,
                *(
                    Value
                    for Value in Result.get(Key, ())
                    if Value is not Witness
                ),
            )
            PreservedCount += 1
    return Result, {
        "RequiredWitnessCount": RequiredCount,
        "PreservedWitnessCount": PreservedCount,
        "ConsumedByFrozenClaimCount": FrozenClaimCount,
        "ExportedSourcePortCount": ExportedSourcePortCount,
        "ContinuationRequiredCount": ContinuationRequiredCount,
        "ContinuationPreservedCount": ContinuationPreservedCount,
        "ContinuationMissingCount": ContinuationMissingCount,
        "Complete": (
            PreservedCount + FrozenClaimCount == RequiredCount
            and ContinuationMissingCount == 0
        ),
    }

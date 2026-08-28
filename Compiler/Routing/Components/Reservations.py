"""Final physical component port and channel reservation validation."""

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

from .Validation import _Fingerprint
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

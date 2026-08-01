"""Closed-component compilation and authoritative global assembly stage."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any, Callable

from .ComponentRouter import (
    ComponentClaimsConflict,
    MaterializeRoutedComponentTemplate,
    SolveComponentRoutingProblem,
    ValidateRoutedComponentHandoff,
)
from .Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from .Models import (
    ComponentRoutingProblem,
    ComponentRoutingSolveResult,
    PhysicalComponentAssemblyPlan,
    PhysicalComponentChannelReservation,
    PhysicalComponentPortReservation,
    Position3,
    RoutedComponentNet,
    RoutedComponentTemplate,
)
from .ResourceGraph import RoutingResourceClaims


_CompletedComponentTemplateCache: dict[
    str,
    tuple[
        Position3,
        RoutedComponentTemplate,
        tuple[tuple[str, str], ...],
    ],
] = {}


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
    ExpectedCandidateByTerminal = {
        (Port.Signal, TerminalFingerprint): CandidateFingerprint
        for Port in Plan.Ports
        for TerminalFingerprint, CandidateFingerprint in zip(
            Port.OwnedTerminalFingerprints,
            Port.OwnedCandidateFingerprints,
        )
    }
    if any(
        not (
            len(Port.OwnedTerminals)
            == len(Port.OwnedTerminalFingerprints)
            == len(Port.OwnedCandidateFingerprints)
            == len(Port.OwnedAccessCandidates)
        )
        for Port in Plan.Ports
    ):
        raise ValueError(
            "physical port does not assign every owned terminal"
        )
    ExportedDomainKeys = frozenset(
        (Domain.Signal, Domain.TerminalFingerprint)
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal in PortsBySignal
    )
    if ExportedDomainKeys != frozenset(ExpectedCandidateByTerminal):
        raise ValueError(
            "physical ports and exported terminal domains differ"
        )
    CandidateByTerminal = {}
    for Domain in Problem.OwnedTerminalDomains:
        Key = (Domain.Signal, Domain.TerminalFingerprint)
        ExpectedFingerprint = ExpectedCandidateByTerminal.get(Key)
        if ExpectedFingerprint is None:
            continue
        if (
            len(Domain.Candidates) != 1
            or Domain.Candidates[0].CandidateFingerprint
            != ExpectedFingerprint
            or Domain.Candidates[0]
            != PortsBySignal[Domain.Signal].OwnedAccessCandidates[
                PortsBySignal[
                    Domain.Signal
                ].OwnedTerminalFingerprints.index(
                    Domain.TerminalFingerprint
                )
            ]
        ):
            raise ValueError(
                "physical terminal access is not bound exactly"
            )
        CandidateByTerminal[Key] = Domain.Candidates[0]
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
        if (
            frozenset(Port.LocalPath[1:])
            & frozenset(Port.GlobalPath[1:])
        ):
            raise ValueError(
                "local and global port paths overlap beyond the seam"
            )
        if any(
            not _ClaimsContain(
                Port.Claims,
                CandidateByTerminal[
                    (Port.Signal, TerminalFingerprint)
                ].Claims,
            )
            for TerminalFingerprint
            in Port.OwnedTerminalFingerprints
        ):
            raise ValueError(
                "physical port omits assigned terminal-access claims"
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
    DomainByKey = {
        (Domain.Signal, Domain.Terminal): Domain
        for Domain in Problem.OwnedTerminalDomains
    }
    for Port in Plan.Ports:
        Net = NetBySignal.get(Port.Signal)
        if Net is None:
            raise ValueError(
                "component template omitted a physical port signal"
            )
        RequiredLocalNodes = frozenset(Port.LocalPath)
        RequiredAccessNodes = frozenset(
            Position
            for Terminal in Port.OwnedTerminals
            for Candidate in DomainByKey[
                (Port.Signal, Terminal)
            ].Candidates
            for Position in Candidate.Path
        )
        if (
            not RequiredLocalNodes <= Net.Nodes
            or not RequiredAccessNodes <= Net.Nodes
        ):
            raise ValueError(
                "component template changed its assigned local access"
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
                not any(
                    Entry in Net.Nodes and Exit in Net.Nodes
                    for Entry, Exit in Contract.EndpointPairs
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


def FinalizePhysicalComponentChannelReservations(
    Channels: tuple[PhysicalComponentChannelReservation, ...],
    Ports: tuple[PhysicalComponentPortReservation, ...],
    ResourceGraph: Any,
    *,
    MinimumPlacementY: int,
    EnvelopeMinimum: Position3,
    EnvelopeMaximum: Position3,
    KeepoutClaims: RoutingResourceClaims | None = None,
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
        if ComponentClaimsConflict(First.Claims, Second.Claims)
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

    KeepoutColumns = frozenset(
        (Position[0], Position[2])
        for Position in (
            *KeepoutClaims.WireCells,
            *KeepoutClaims.SupportCells,
            *KeepoutClaims.RequiredAirCells,
            *KeepoutClaims.ElectricalCells,
        )
    ) if KeepoutClaims is not None else frozenset()

    def InsideEnvelope(Position: Position3) -> bool:
        if KeepoutClaims is not None:
            return (Position[0], Position[2]) in KeepoutColumns
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
                (Current[0] - 1, Current[1], Current[2]),
                (Current[0] + 1, Current[1], Current[2]),
                (Current[0], Current[1] - 1, Current[2]),
                (Current[0], Current[1] + 1, Current[2]),
                (Current[0], Current[1], Current[2] - 1),
                (Current[0], Current[1], Current[2] + 1),
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
        Claims = ResourceGraph.BuildRouteClaims(ChannelNodes)
        ResourceIds = tuple(map(str, sorted(
            Claims.ResourceIds,
            key=str,
        )))
        Result.append(replace(
            Channel,
            GuideCells=tuple(sorted({
                (Position[0], Position[2])
                for Position in ExteriorGuideNodes
            })),
            ResourceIds=ResourceIds,
            Claims=Claims,
            ReservationFingerprint=_Fingerprint((
                "connected-physical-component-channel-v1",
                Channel.Signal,
                Channel.Layer,
                tuple(sorted(Channel.GuideCells)),
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
        if ComponentClaimsConflict(First.Claims, Second.Claims)
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


def BuildCompletedComponentTemplateCacheFingerprint(
    Problem: ComponentRoutingProblem,
) -> str:
    """Identify topology/port/technology-equivalent component compiles."""
    if Problem.Interface is None:
        raise ValueError("closed interface required for template caching")
    Origin = _Origin(Problem)

    def NormalizeValues(
        Values: Any,
    ) -> tuple[Position3, ...]:
        return tuple(sorted(
            _Normalize(Value, Origin) for Value in Values
        ))

    DomainIdentity = tuple(sorted(
        (
            Domain.TerminalRole,
            _Normalize(Domain.Terminal, Origin),
            tuple(sorted(
                (
                    _Normalize(Candidate.Attachment, Origin),
                    NormalizeValues(Candidate.Path),
                    NormalizeValues(Candidate.Claims.WireCells),
                    NormalizeValues(Candidate.Claims.SupportCells),
                    NormalizeValues(
                        Candidate.Claims.RequiredAirCells
                    ),
                    NormalizeValues(
                        Candidate.Claims.ElectricalCells
                    ),
                    Candidate.Layer,
                )
                for Candidate in Domain.Candidates
            )),
        )
        for Domain in Problem.OwnedTerminalDomains
    ))
    ClaimIdentity = tuple(sorted(
        (
            "component"
            if Claim.Signal in Problem.ComponentSignals
            else "foreign",
            NormalizeValues(Claim.Claims.WireCells),
            NormalizeValues(Claim.Claims.SupportCells),
            NormalizeValues(Claim.Claims.RequiredAirCells),
            NormalizeValues(Claim.Claims.ElectricalCells),
        )
        for Claim in (
            *Problem.LocalClaims,
            *Problem.ImmutableClaims,
        )
    ))
    SignalIdentityByName = dict(_SignalStructuralIdentities(Problem))

    def AssemblySignalIdentity(Signal: str) -> str:
        return SignalIdentityByName.get(
            Signal,
            "foreign-global-channel",
        )

    Plan = Problem.PhysicalAssemblyPlan
    PhysicalContractIdentity = (
        (
            tuple(sorted(
                (
                    AssemblySignalIdentity(Port.Signal),
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
                for Port in Plan.Ports
            )),
            tuple(sorted(
                (
                    AssemblySignalIdentity(Channel.Signal),
                    Channel.Layer,
                    tuple(sorted(
                        (
                            X - Origin[0],
                            Z - Origin[2],
                        )
                        for X, Z in Channel.GuideCells
                    )),
                    _NormalizedClaimsIdentity(
                        Channel.Claims,
                        Origin,
                    ),
                    Channel.Capacity,
                    len(Channel.FeedthroughComponentIds),
                )
                for Channel in Plan.Channels
            )),
            tuple(sorted(
                (
                    AssemblySignalIdentity(Feedthrough.Signal),
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
                for Feedthrough in Plan.Feedthroughs
            )),
        )
        if Plan is not None
        else ()
    )
    Technology = getattr(Problem.ResourceGraph, "Technology", None)
    return _Fingerprint((
        "completed-component-template-v2",
        Problem.Interface.InterfaceFingerprint,
        Problem.Fabric.FabricFingerprint,
        DomainIdentity,
        ClaimIdentity,
        tuple(sorted(SignalIdentityByName.values())),
        PhysicalContractIdentity,
        Problem.MaximumPowerDistance,
        repr(Technology),
    ))


def _MoveNet(
    Value: RoutedComponentNet,
    Delta: Position3,
    Signal: str | None = None,
) -> RoutedComponentNet:
    Claims = _MoveClaims(Value.Claims, Delta)
    Nodes = frozenset(_Move(Position, Delta) for Position in Value.Nodes)
    Edges = frozenset(
        tuple(sorted((_Move(First, Delta), _Move(Second, Delta))))
        for First, Second in Value.Edges
    )
    Repeaters = tuple(
        (_Move(Position, Delta), Facing)
        for Position, Facing in Value.Repeaters
    )
    ExportedPorts = tuple(
        _Move(Position, Delta) for Position in Value.ExportedPorts
    )
    CoveredTerminals = tuple(
        _Move(Position, Delta) for Position in Value.CoveredTerminals
    )
    return replace(
        Value,
        Signal=Signal or Value.Signal,
        Root=_Move(Value.Root, Delta),
        Nodes=Nodes,
        Edges=Edges,
        WireCells=Claims.WireCells - frozenset(
            Position for Position, _Facing in Repeaters
        ),
        SupportCells=Claims.SupportCells,
        Repeaters=Repeaters,
        Claims=Claims,
        CoveredTerminals=CoveredTerminals,
        ExportedPorts=ExportedPorts,
        NetFingerprint=_Fingerprint((
            tuple(sorted(Nodes)),
            tuple(sorted(Edges)),
            Repeaters,
            ExportedPorts,
        )),
    )


def _InstantiateCachedTemplate(
    Problem: ComponentRoutingProblem,
    CachedOrigin: Position3,
    Cached: RoutedComponentTemplate,
    CachedSignalIdentities: tuple[tuple[str, str], ...],
    CacheFingerprint: str,
) -> RoutedComponentTemplate | None:
    if (
        Cached.ForeignEscapeReservations
        or Cached.ExternalContinuationReservations
    ):
        return None
    TargetOrigin = _Origin(Problem)
    Delta = tuple(
        TargetOrigin[Index] - CachedOrigin[Index]
        for Index in range(3)
    )
    SignalTranslation = _BuildSignalTranslation(
        CachedSignalIdentities,
        _SignalStructuralIdentities(Problem),
    )
    if SignalTranslation is None:
        return None
    Nets = tuple(
        _MoveNet(
            Value,
            Delta,
            SignalTranslation.get(Value.Signal),
        )
        for Value in Cached.Nets
    )
    ExpectedTerminalsBySignal = {
        Signal: tuple(sorted(
            Domain.Terminal
            for Domain in Problem.OwnedTerminalDomains
            if Domain.Signal == Signal
        ))
        for Signal in Problem.ComponentSignals
    }
    if any(
        tuple(sorted(Net.CoveredTerminals))
        != ExpectedTerminalsBySignal.get(Net.Signal, ())
        for Net in Nets
    ):
        return None
    ForeignTransits = tuple(
        _MoveNet(
            Value,
            Delta,
            SignalTranslation.get(Value.Signal),
        )
        for Value in Cached.ForeignTransitReservations
    )
    Claims = RoutingResourceClaims(
        WireCells=frozenset().union(*(
            Value.Claims.WireCells
            for Value in (*Nets, *ForeignTransits)
        )),
        SupportCells=frozenset().union(*(
            Value.Claims.SupportCells
            for Value in (*Nets, *ForeignTransits)
        )),
        RequiredAirCells=frozenset().union(*(
            Value.Claims.RequiredAirCells
            for Value in (*Nets, *ForeignTransits)
        )),
        ElectricalCells=frozenset().union(*(
            Value.Claims.ElectricalCells
            for Value in (*Nets, *ForeignTransits)
        )),
    )
    if Problem.ResourceGraph is not None and any(
        Problem.ResourceGraph.BuildRouteClaims(Value.Nodes)
        != Value.Claims
        for Value in (*Nets, *ForeignTransits)
    ):
        return None
    ExportedPorts = tuple(sorted(
        (Net.Signal, Position)
        for Net in Nets
        for Position in Net.ExportedPorts
    ))
    Diagnostics = {
        **Cached.Diagnostics,
        "CompletedTemplateCacheHit": True,
        "CompletedTemplateCacheFingerprint": CacheFingerprint,
        "CompletedTemplateTranslationDelta": list(Delta),
    }
    RoutedFingerprint = _Fingerprint((
        Problem.ProblemFingerprint,
        tuple(Value.NetFingerprint for Value in Nets),
        tuple(Value.NetFingerprint for Value in ForeignTransits),
        ExportedPorts,
    ))
    return replace(
        Cached,
        ProblemFingerprint=Problem.ProblemFingerprint,
        PlacementFingerprint=Problem.PlacementFingerprint,
        LocalTemplateFingerprint=Problem.LocalTemplateFingerprint,
        FabricFingerprint=Problem.Fabric.FabricFingerprint,
        RoutedTemplateFingerprint=RoutedFingerprint,
        Nets=Nets,
        ExportedPorts=ExportedPorts,
        Claims=Claims,
        ProofFingerprint=_Fingerprint((
            RoutedFingerprint,
            "completed-template-cache",
        )),
        ExpansionCount=0,
        Diagnostics=Diagnostics,
        ForeignTransitReservations=ForeignTransits,
        InterfaceFingerprint=Problem.Interface.InterfaceFingerprint,
    )


@dataclass(frozen=True)
class ComponentAssemblyResult:
    """Frozen component claims and their validated global handoff."""

    Placed: Any
    Template: RoutedComponentTemplate
    HandoffDiagnostics: dict[str, object]
    PhysicalAssemblyPlan: PhysicalComponentAssemblyPlan
def CompileClosedComponent(
    Problem: ComponentRoutingProblem,
    *,
    AssemblyPlan: PhysicalComponentAssemblyPlan | None = None,
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
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
    DiscoveryVariantLimit: int | None = 8,
    DiscoveryVariantLimitsBySignal: dict[
        str, int | None
    ] | None = None,
    RequiredForeignTransitSignals: frozenset[str] = frozenset(),
) -> ComponentRoutingSolveResult:
    """Compile one closed local component without invoking global retries."""
    if Problem.Interface is None:
        raise ValueError(
            "production component compilation requires a closed interface"
        )
    Declared = Problem.Interface.DeclaredFeedthroughSignals
    Actual = frozenset(
        Value.Signal for Value in Problem.ForeignTransitDomains
    )
    if Actual - Declared:
        raise ValueError(
            "component problem contains implicit foreign transit domains"
        )
    EffectiveAssemblyPlan = (
        AssemblyPlan or Problem.PhysicalAssemblyPlan
    )
    if EffectiveAssemblyPlan is not None:
        if not EffectiveAssemblyPlan.Complete:
            raise ValueError(
                "component compilation requires a complete physical "
                "assembly plan"
            )
        if (
            Problem.PhysicalAssemblyPlan is None
            or Problem.PhysicalAssemblyPlan.PlanFingerprint
            != EffectiveAssemblyPlan.PlanFingerprint
            or Problem.Interface.PhysicalAssemblyPlanFingerprint
            != EffectiveAssemblyPlan.PlanFingerprint
            or Problem.Interface.InterfaceFingerprint
            != EffectiveAssemblyPlan.InterfaceFingerprint
        ):
            raise ValueError(
                "component problem and physical assembly identities differ"
            )
        _ValidatePhysicalProblemContract(
            Problem,
            EffectiveAssemblyPlan,
        )
        if (
            ForbiddenAssignmentFingerprints
            or (ForbiddenExportPortsBySignal or {})
            or (
                ForbiddenForeignCandidateFingerprintsBySignal
                or {}
            )
            or ForbiddenForeignAssignmentPairs
            or RequiredForeignTransitSignals
        ):
            raise ValueError(
                "physical component compilation cannot reopen its "
                "immutable assembly plan"
            )
    CacheEligible = bool(
        not ForbiddenAssignmentFingerprints
        and not (ForbiddenExportPortsBySignal or {})
        and not (
            ForbiddenForeignCandidateFingerprintsBySignal or {}
        )
        and not ForbiddenForeignAssignmentPairs
        and not RequiredForeignTransitSignals
    )
    CacheFingerprint = (
        BuildCompletedComponentTemplateCacheFingerprint(Problem)
        if CacheEligible
        else ""
    )
    CacheKey = CacheFingerprint
    Cached = (
        _CompletedComponentTemplateCache.get(CacheKey)
        if CacheEligible
        else None
    )
    if Cached is not None:
        (
            CachedOrigin,
            CachedTemplate,
            CachedSignalIdentities,
        ) = Cached
        Instantiated = _InstantiateCachedTemplate(
            Problem,
            CachedOrigin,
            CachedTemplate,
            CachedSignalIdentities,
            CacheFingerprint,
        )
        if Instantiated is not None:
            _ValidatePhysicalTemplate(Problem, Instantiated)
            return ComponentRoutingSolveResult(
                Status="feasible",
                Template=Instantiated,
                ProofFingerprint=Instantiated.ProofFingerprint,
                ExpansionCount=0,
                Diagnostics=Instantiated.Diagnostics,
            )
    Result = SolveComponentRoutingProblem(
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
        VariantPortfolioCache=VariantPortfolioCache,
        NetVariantConstructionCache=NetVariantConstructionCache,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        NetVariantDiscoveryStateCache=NetVariantDiscoveryStateCache,
        DiscoveryVariantLimit=DiscoveryVariantLimit,
        DiscoveryVariantLimitsBySignal=(
            DiscoveryVariantLimitsBySignal
        ),
        RequiredForeignTransitSignals=RequiredForeignTransitSignals,
    )
    if Result.Feasible and Result.Template is not None:
        _ValidatePhysicalTemplate(Problem, Result.Template)
    if (
        CacheEligible
        and Result.Feasible
        and Result.Template is not None
    ):
        TemplateDiagnostics = {
            **Result.Template.Diagnostics,
            "CompletedTemplateCacheHit": False,
            "CompletedTemplateCacheFingerprint": CacheFingerprint,
            "CompletedTemplateTranslationDelta": [0, 0, 0],
        }
        Template = replace(
            Result.Template,
            Diagnostics=TemplateDiagnostics,
        )
        Result = replace(
            Result,
            Template=Template,
            Diagnostics=TemplateDiagnostics,
        )
        _CompletedComponentTemplateCache[CacheKey] = (
            _Origin(Problem),
            Template,
            _SignalStructuralIdentities(Problem),
        )
    return Result


def AssembleClosedComponentForGlobalRouting(
    Placed: Any,
    Template: RoutedComponentTemplate,
    *,
    PhysicalAssemblyPlan: PhysicalComponentAssemblyPlan,
    PlacementFingerprint: str,
    LocalTemplateFingerprint: str,
) -> ComponentAssemblyResult:
    """Freeze local claims against the immutable port-first assembly plan."""
    if not PhysicalAssemblyPlan.Complete:
        raise ValueError("physical component assembly plan is incomplete")
    if (
        PhysicalAssemblyPlan.PlacementFingerprint
        != PlacementFingerprint
        or Template.InterfaceFingerprint
        != PhysicalAssemblyPlan.InterfaceFingerprint
    ):
        raise ValueError(
            "physical component assembly handoff identity mismatch"
        )
    Diagnostics = dict(
        getattr(Placed, "LocalRouteDiagnostics", {}) or {}
    )
    Diagnostics["__PhysicalComponentAssemblyPlan__"] = (
        PhysicalAssemblyPlan.ToDictionary()
    )
    StagedPlaced = replace(
        Placed,
        LocalRouteDiagnostics=Diagnostics,
    )
    Materialized = MaterializeRoutedComponentTemplate(
        StagedPlaced,
        Template,
    )
    try:
        Handoff = ValidateRoutedComponentHandoff(
            Materialized,
            Template,
            PlacementFingerprint=PlacementFingerprint,
            LocalTemplateFingerprint=LocalTemplateFingerprint,
        )
    except ValueError as Error:
        raise RoutingStageError(RoutingFailure(
            Reason=(
                RoutingFailureReason
                .ComponentAssemblyIdentityMismatch
            ),
            Stage="ComponentAssemblyIdentityValidation",
            Detail=str(Error),
            Diagnostics={
                "PlacementFingerprint": PlacementFingerprint,
                "LocalTemplateFingerprint": (
                    LocalTemplateFingerprint
                ),
                "PhysicalAssemblyPlanFingerprint": (
                    PhysicalAssemblyPlan.PlanFingerprint
                ),
                "InterfaceFingerprint": (
                    PhysicalAssemblyPlan.InterfaceFingerprint
                ),
                "RoutedTemplateFingerprint": (
                    Template.RoutedTemplateFingerprint
                ),
                "FabricFingerprint": Template.FabricFingerprint,
                "ImplicitForeignTransitDomainCount": 0,
            },
        )) from Error
    return ComponentAssemblyResult(
        Placed=Materialized,
        Template=Template,
        HandoffDiagnostics=Handoff,
        PhysicalAssemblyPlan=PhysicalAssemblyPlan,
    )

"""Portal-domain assembly and route materialization primitives."""

from __future__ import annotations

from ..Actions import PropagateRoutePower

from ..Actions import PruneRedundantRepeaterReservations

from ..Contracts.Component import PhysicalComponentAssemblyPlan

from ..Contracts.Core import Position2

from ..Contracts.Core import Position3

from ..Contracts.Results import RoutingResources

from ..Failures import RoutingFailure

from ..Failures import RoutingFailureReason

from ..Failures import RoutingStageError

from ..Interfaces.BoundaryRelations import BuildPhysicalComponentGlobalPortalId

from ..Reliability import BuildStableFingerprint

from ..ResourceGraph import BuildRoutingEnvelope

from ..ResourceGraph import FindSelfClaimConflicts

from ..ResourceGraph import NetRouteCandidate

from ..ResourceGraph import NormalizeRoutingEdge

from ..ResourceGraph import PinAccessPortal

from ..ResourceGraph import RoutingReservation

from ..ResourceGraph import RoutingResourceId

from ..ResourceGraph import RoutingResourceKind

from ..Technology import DefaultRedstoneRoutingTechnology

from ..Technology import RedstoneRoutingTechnology

from collections import Counter

from collections import deque

from dataclasses import replace

from math import ceil

from typing import Any

from typing import Iterable

from typing import Mapping

import os

def FilterSourceConnectedTargetBranches(
    Root: Position3,
    SourceNodes: Iterable[Position3],
    TargetBranches: Iterable[Iterable[Position3]],
    ResourceGraph: Any,
) -> tuple[tuple[Position3, ...], ...]:
    """Omit target branches already connected by immutable access geometry.

    A terminal can legitimately be both a target and part of the producer's
    fixed access path (for example, a reconvergent fanout whose portal
    ingress coincides with the source ingress).  The native tree kernel owns
    *new* connections.  Asking it to add such a branch makes it reject the
    already-present overlap as a cyclic second attachment, even though the
    final immutable access geometry is connected and electrically legal.

    Build the same-signal fixed graph up front, then pass only target branches
    outside the root component to native search.  Omitted branches remain in
    the required-node payload and are restored by ``_MaterializeCandidate``,
    so this normalizes one immutable request; it does not release a claim or
    create a second route attempt.
    """
    BranchValues = []
    for Branch in TargetBranches:
        Value = tuple(Branch)
        if Value:
            BranchValues.append(Value)
    Branches = tuple(BranchValues)
    if not Branches:
        return ()
    FixedNodes = {
        tuple(Root),
        *(tuple(Position) for Position in SourceNodes),
        *(Position for Branch in Branches for Position in Branch),
    }
    FixedGraph = _BuildCandidateGraph(FixedNodes, ResourceGraph)
    RootComponent = _FindComponentNodes(FixedGraph, tuple(Root))
    if not RootComponent:
        # A missing source node is not proof that any branch is redundant.
        # Leave the native request untouched and let its ordinary typed
        # failure classification describe the incomplete route domain.
        return Branches
    return tuple(
        Branch
        for Branch in Branches
        if not frozenset(Branch) <= RootComponent
    )

def SelectGraphAccessStarts(
    AccessPath: tuple[Position3, ...],
    RegionNodes: frozenset[Position3],
    PreferOutermost: bool = False,
) -> tuple[Position3, ...]:
    """Keep only terminal access cells represented by the routing graph."""
    Starts = tuple(
        Position for Position in AccessPath
        if Position in RegionNodes
    )
    return (
        (Starts[-1],)
        if PreferOutermost and Starts
        else Starts
    )

def PortalPathRespectsOutwardAccess(
    PortalPath: Iterable[Position3],
    AccessPath: tuple[Position3, ...],
) -> bool:
    """Reject a pin-start portal that exits across a macro's pin bank."""
    Path = tuple(PortalPath)
    if not Path or len(AccessPath) < 2:
        return True
    try:
        StartIndex = AccessPath.index(Path[0])
    except ValueError:
        return False
    if StartIndex > 0 or len(Path) < 2:
        return True
    OutwardDelta = (
        AccessPath[1][0] - AccessPath[0][0],
        AccessPath[1][2] - AccessPath[0][2],
    )
    PortalDelta = (
        Path[1][0] - Path[0][0],
        Path[1][2] - Path[0][2],
    )
    return (
        PortalDelta[0] * OutwardDelta[0]
        + PortalDelta[1] * OutwardDelta[1]
    ) > 0

def RequiredRoutingLayerCountForAccess(
    MinimumY: int,
    AccessPositions: frozenset[Position3],
    GuideExpansion: int,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
    MinimumLayerCount: int | None = None,
) -> int:
    """Return the lowest layer count that can serve the highest terminal.

    A redstone stair changes elevation and horizontal position together. The
    terminal portal envelope can therefore bridge at most ``GuideExpansion``
    vertical cells before reaching a routing plane. This is a necessary,
    deterministic layer floor for vertically stacked placements.
    """
    if GuideExpansion < 0:
        raise ValueError("GuideExpansion cannot be negative")
    LayerFloor = (
        Technology.MinimumRoutingLayerCount
        if MinimumLayerCount is None
        else int(MinimumLayerCount)
    )
    if LayerFloor < 1:
        raise ValueError("MinimumLayerCount must be positive")
    if not AccessPositions:
        return LayerFloor
    HighestAccessY = max(Position[1] for Position in AccessPositions)
    LowestRoutingY = Technology.RoutingY(MinimumY, 0)
    RequiredRoutingY = max(
        LowestRoutingY,
        HighestAccessY - GuideExpansion,
    )
    AdditionalHeight = max(0, RequiredRoutingY - LowestRoutingY)
    return max(
        LayerFloor,
        1 + ceil(AdditionalHeight / Technology.RoutingLayerPitch),
    )

def RequiredPhysicalAssemblyRoutingLayerCount(
    Plan: PhysicalComponentAssemblyPlan | None,
) -> int:
    """Return the immutable assembly's minimum visible layer domain.

    Physical channels are selected before closed-component compilation.  A
    later authoritative route may start with fewer adaptive layers, but it
    may not make a selected channel (including a feedthrough channel)
    disappear from the resource graph.
    """
    if Plan is None:
        return 0
    PlanningChannels = Plan.PlanningChannels
    Layers = tuple(int(Channel.Layer) for Channel in PlanningChannels)
    InvalidLayers = tuple(sorted({Layer for Layer in Layers if Layer < 0}))
    if InvalidLayers:
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
            Stage="PhysicalComponentGlobalPlanning",
            AffectedNets=tuple(sorted({
                Channel.Signal
                for Channel in PlanningChannels
                if int(Channel.Layer) < 0
            })),
            Detail=(
                "immutable physical assembly declares a negative routing "
                "layer"
            ),
            Diagnostics={
                "PlanFingerprint": Plan.PlanFingerprint,
                "DeclaredChannelLayers": list(sorted(set(Layers))),
                "InvalidChannelLayers": list(InvalidLayers),
                "GlobalPlanDomainComplete": True,
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    return max(Layers, default=-1) + 1

def SelectHierarchicalRoutingMaximumLayerCount(
    PolicyLayerLimit: int,
    TechnologyMaximumLayerCount: int,
    InterfaceDeckLayer: int | None,
    Plan: PhysicalComponentAssemblyPlan | None = None,
) -> int:
    """Authorize one explicit component deck above the flat policy cap."""
    PolicyMaximumLayerCount = (
        min(TechnologyMaximumLayerCount, PolicyLayerLimit)
        if PolicyLayerLimit > 0
        else TechnologyMaximumLayerCount
    )
    PlanRequiredLayerCount = (
        RequiredPhysicalAssemblyRoutingLayerCount(Plan)
        if Plan is not None
        else 0
    )
    PlanDeckLayer = (
        PlanRequiredLayerCount - 1
        if PlanRequiredLayerCount > PolicyMaximumLayerCount
        else None
    )
    DeclaredDeckLayers = tuple(
        Layer
        for Layer in (InterfaceDeckLayer, PlanDeckLayer)
        if Layer is not None
    )
    if not DeclaredDeckLayers:
        return PolicyMaximumLayerCount
    EffectiveInterfaceDeckLayer = max(map(int, DeclaredDeckLayers))
    RequiredDeckLayerCount = EffectiveInterfaceDeckLayer + 1
    if (
        EffectiveInterfaceDeckLayer < 0
        or RequiredDeckLayerCount > TechnologyMaximumLayerCount
    ):
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
            Stage="PhysicalComponentGlobalPlanning",
            AffectedNets=tuple(sorted({
                Channel.Signal
                for Channel in Plan.PlanningChannels
                if int(Channel.Layer) >= TechnologyMaximumLayerCount
            })),
            Detail=(
                "the explicitly declared component interface deck is "
                "outside the routing technology layer limit"
            ),
            Diagnostics={
                "PlanFingerprint": str(getattr(
                    Plan,
                    "PlanFingerprint",
                    "",
                )),
                "InterfaceDeckLayer": EffectiveInterfaceDeckLayer,
                "RequiredInterfaceDeckLayerCount": (
                    RequiredDeckLayerCount
                ),
                "PolicyMaximumLayerCount": PolicyMaximumLayerCount,
                "TechnologyMaximumLayerCount": (
                    TechnologyMaximumLayerCount
                ),
                "InterfaceDeckAuthorization": "rejected-by-technology",
                "GlobalPlanDomainComplete": Plan is not None,
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    return max(PolicyMaximumLayerCount, RequiredDeckLayerCount)

def ValidatePhysicalAssemblyRoutingLayerLimit(
    Plan: PhysicalComponentAssemblyPlan | None,
    RequiredLayerCount: int,
    EffectiveMaximumLayerCount: int,
    PolicyMaximumLayerCount: int,
    TechnologyMaximumLayerCount: int,
) -> None:
    """Reject an immutable channel contract outside legal layer limits."""
    if Plan is None or RequiredLayerCount <= EffectiveMaximumLayerCount:
        return
    PlanningChannels = Plan.PlanningChannels
    OffendingSignals = tuple(sorted({
        Channel.Signal
        for Channel in PlanningChannels
        if int(Channel.Layer) >= EffectiveMaximumLayerCount
    }))
    raise RoutingStageError(RoutingFailure(
        Reason=RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
        Stage="PhysicalComponentGlobalPlanning",
        AffectedNets=OffendingSignals,
        Detail=(
            "immutable physical assembly requires a routing layer outside "
            "the policy and technology limits"
        ),
        Diagnostics={
            "PlanFingerprint": Plan.PlanFingerprint,
            "RequiredPhysicalAssemblyLayerCount": RequiredLayerCount,
            "EffectiveMaximumLayerCount": EffectiveMaximumLayerCount,
            "PolicyMaximumLayerCount": PolicyMaximumLayerCount,
            "TechnologyMaximumLayerCount": TechnologyMaximumLayerCount,
            "OffendingSignals": list(OffendingSignals),
            "DeclaredChannelLayers": {
                Channel.Signal: int(Channel.Layer)
                for Channel in sorted(
                    PlanningChannels,
                    key=lambda Value: (Value.Signal, int(Value.Layer)),
                )
            },
            "GlobalPlanDomainComplete": True,
            "ImplicitForeignTransitDomainCount": 0,
        },
    ))

def SelectInitialRoutingLayerCount(
    MinimumLayerCount: int,
    EffectiveMaximumLayerCount: int,
    RequiredAccessLayerCount: int,
    AdaptiveLayerCount: int,
    AdaptiveLayerFloor: int,
    NegotiatedLayerFloor: int,
    ExistingRouteLayerCount: int,
    PlacementWasRelocated: bool,
    ForceMaximumAfterPlacementRelocation: bool,
) -> int:
    """Return the smallest legal initial layer budget for one route attempt.

    The fixed access, retained local routes, and negotiated demand each impose
    a lower bound.  A relocation can optionally retain the legacy behavior of
    immediately using all vertical headroom; otherwise it is just another
    geometry candidate and adaptive routing grows one layer at a time.
    """
    if MinimumLayerCount < 1 or EffectiveMaximumLayerCount < MinimumLayerCount:
        raise ValueError("routing layer bounds must be positive and ordered")
    InitialLayerCount = max(
        MinimumLayerCount,
        RequiredAccessLayerCount,
        AdaptiveLayerCount,
        AdaptiveLayerFloor,
        NegotiatedLayerFloor,
        ExistingRouteLayerCount,
    )
    if PlacementWasRelocated and ForceMaximumAfterPlacementRelocation:
        InitialLayerCount = max(InitialLayerCount, EffectiveMaximumLayerCount)
    return min(EffectiveMaximumLayerCount, InitialLayerCount)

def SelectEscalatedRoutingLayerCount(
    LayerCount: int,
    EffectiveMaximumLayerCount: int,
    ConflictClassification: str,
    ForceMaximumAfterPlacementRelocation: bool,
) -> int:
    """Advance the vertical budget without skipping the adaptive ladder."""
    if LayerCount < 1 or EffectiveMaximumLayerCount < LayerCount:
        raise ValueError("routing layer bounds must be positive and ordered")
    if (
        ForceMaximumAfterPlacementRelocation
        and ConflictClassification.startswith("relocated-")
    ):
        return EffectiveMaximumLayerCount
    return min(EffectiveMaximumLayerCount, LayerCount + 1)

def _PortalFromRust(
    Signal: str,
    Terminal: Position3,
    Layer: int,
    Value: Any,
    Resources: RoutingResources,
) -> PinAccessPortal:
    CandidatePath = tuple(Value.Path)
    if not CandidatePath:
        CandidatePath = (Value.Target,)
    return PinAccessPortal(
        PortalId=f"{Signal}:{Terminal}:{Layer}:{Value.PortalId}",
        Signal=Signal,
        Terminal=Terminal,
        Layer=Layer,
        Path=CandidatePath,
        Edges=frozenset(
            NormalizeRoutingEdge(First, Second)
            for First, Second in zip(CandidatePath, CandidatePath[1:])
        ),
        Claims=Resources.ResourceGraph.BuildRouteClaims(CandidatePath),
        Length=Value.Length,
        BendCount=Value.BendCount,
        ViaCount=Value.ViaCount,
        Cost=Value.Length + Value.BendCount * 10 + Value.ViaCount * 7,
    )

def SelectGenericPortalTerminalPaths(
    Profile: Any,
    Plan: PhysicalComponentAssemblyPlan | None,
) -> tuple[tuple[Position3, tuple[Position3, ...]], ...]:
    """Return only terminals still owned by generic portal preparation.

    A fixed physical assembly already owns the global side of each declared
    component seam.  Generating generic portals for those attachments and
    replacing them later both wastes work and briefly gives two stages
    authority over the same endpoint.  External terminals remain generic;
    exact attachments are injected from ``Port.GlobalPath``.
    """
    ExactAttachments = frozenset(
        Port.Attachment
        for Port in getattr(Plan, "Ports", ())
        if str(Port.Signal) == str(Profile.Signal)
    )
    return tuple(
        (Terminal, tuple(Path))
        for Terminal, Path in (
            (Profile.Root, Profile.SourceAccessPath),
            *((
                Target,
                Profile.TargetAccessPaths[Target],
            ) for Target in Profile.Targets),
        )
        if Terminal not in ExactAttachments
    )

def ApplyPhysicalComponentAssemblyPortalDomains(
    Portals: dict[
        tuple[str, Position3, int],
        tuple[PinAccessPortal, ...],
    ],
    Plan: PhysicalComponentAssemblyPlan,
    ResourceGraph: Any,
) -> dict[
    tuple[str, Position3, int],
    tuple[PinAccessPortal, ...],
]:
    """Replace generic component-terminal portals with exact seam portals."""
    Result = dict(Portals)
    PlanningChannels = Plan.PlanningChannels
    ChannelsBySignal = {
        Channel.Signal: Channel for Channel in PlanningChannels
    }
    for Port in Plan.Ports:
        Channel = ChannelsBySignal.get(Port.Signal)
        if Channel is None:
            raise ValueError(
                f"physical port has no channel: {Port.Signal}"
            )
        Path = tuple(Port.GlobalPath)
        if not Path or Path[0] != Port.Attachment:
            raise ValueError(
                f"physical port has no exact global path: {Port.Signal}"
            )
        Layer = int(Channel.Layer)
        Result = {
            Key: Values
            for Key, Values in Result.items()
            if not (
                Key[0] == Port.Signal
                and Key[1] == Port.Attachment
            )
        }
        Portal = PinAccessPortal(
            PortalId=BuildPhysicalComponentGlobalPortalId(Port, Layer),
            Signal=Port.Signal,
            Terminal=Port.Attachment,
            Layer=Layer,
            Path=Path,
            Edges=frozenset(
                NormalizeRoutingEdge(First, Second)
                for First, Second in zip(Path, Path[1:])
            ),
            Claims=ResourceGraph.BuildRouteClaims(Path),
            Length=len(Path),
            BendCount=_CountBends(Path),
            ViaCount=sum(
                First[1] != Second[1]
                for First, Second in zip(Path, Path[1:])
            ),
            Cost=len(Path),
        )
        Result[(Port.Signal, Port.Attachment, Layer)] = (Portal,)
    return Result

def ApplyPlacementAccessAssignmentPortalDomains(
    Portals: dict[
        tuple[str, Position3, int],
        tuple[PinAccessPortal, ...],
    ],
    Fabric: Any,
    Assignment: Any,
    ResourceGraph: Any,
    Technology: RedstoneRoutingTechnology,
    MinimumY: int,
    LayerCount: int,
) -> dict[
    tuple[str, Position3, int],
    tuple[PinAccessPortal, ...],
]:
    """Replace generic portals with the frozen pre-placement escapes."""
    if not getattr(Assignment, "Success", False):
        return Portals
    Domains = {
        (str(Domain.Signal), tuple(Domain.Terminal)): Domain
        for Domain in Fabric.TerminalDomains
    }
    Result = dict(Portals)
    for Signal, Terminal, StubIndex in Assignment.SelectedStubIndices:
        Domain = Domains[(str(Signal), tuple(Terminal))]
        Stub = Domain.EscapeStubs[int(StubIndex)]
        Layer = next((
            CandidateLayer
            for CandidateLayer in range(LayerCount)
            if Technology.RoutingY(MinimumY, CandidateLayer)
            == int(Stub.Ingress[1])
        ), None)
        if Layer is None:
            raise ValueError(
                "placement access escape is outside the routing layer domain"
            )
        Result = {
            Key: Values
            for Key, Values in Result.items()
            if not (Key[0] == Signal and Key[1] == Terminal)
        }
        Path = tuple(Stub.Path)
        Portal = PinAccessPortal(
            PortalId=(
                f"{Signal}:{Terminal}:{Layer}:AccessFabric:"
                f"{Assignment.AssignmentFingerprint}:{StubIndex}"
            ),
            Signal=str(Signal),
            Terminal=tuple(Terminal),
            Layer=Layer,
            Path=Path,
            Edges=frozenset(
                NormalizeRoutingEdge(First, Second)
                for First, Second in zip(Path, Path[1:])
            ),
            Claims=ResourceGraph.BuildRouteClaims(Path),
            Length=len(Path),
            BendCount=_CountBends(Path),
            ViaCount=sum(
                First[1] != Second[1]
                for First, Second in zip(Path, Path[1:])
            ),
            Cost=len(Path),
        )
        Result[(str(Signal), tuple(Terminal), Layer)] = (Portal,)
    return Result

def ApplyPlacementAccessFabricPortalDomains(
    Portals: dict[
        tuple[str, Position3, int],
        tuple[PinAccessPortal, ...],
    ],
    Fabric: Any,
    ResourceGraph: Any,
    Technology: RedstoneRoutingTechnology,
    MinimumY: int,
    LayerCount: int,
) -> dict[
    tuple[str, Position3, int],
    tuple[PinAccessPortal, ...],
]:
    """Publish every fixed fabric escape as an authoritative portal choice.

    A placement access fabric describes a finite terminal escape domain, not
    a completed global assignment.  Retaining every legal stub here lets the
    authoritative track-capacity solver choose the stub together with the
    remaining portal candidates and immutable local claims.  The later
    frozen track preparation then records that single combined witness.
    """
    Result = {
        Key: Values
        for Key, Values in Portals.items()
        if not any(
            Key[0] == str(Domain.Signal)
            and Key[1] == tuple(Domain.Terminal)
            for Domain in Fabric.TerminalDomains
        )
    }
    for Domain in Fabric.TerminalDomains:
        Signal = str(Domain.Signal)
        Terminal = tuple(Domain.Terminal)
        for StubIndex, Stub in enumerate(Domain.EscapeStubs):
            Layer = next((
                CandidateLayer
                for CandidateLayer in range(LayerCount)
                if Technology.RoutingY(MinimumY, CandidateLayer)
                == int(Stub.Ingress[1])
            ), None)
            if Layer is None:
                raise ValueError(
                    "placement access escape is outside the routing layer "
                    "domain"
                )
            Path = tuple(Stub.Path)
            Portal = PinAccessPortal(
                PortalId=(
                    f"{Signal}:{Terminal}:{Layer}:AccessFabricDomain:"
                    f"{Fabric.FabricFingerprint}:{StubIndex}"
                ),
                Signal=Signal,
                Terminal=Terminal,
                Layer=Layer,
                Path=Path,
                Edges=frozenset(
                    NormalizeRoutingEdge(First, Second)
                    for First, Second in zip(Path, Path[1:])
                ),
                Claims=ResourceGraph.BuildRouteClaims(Path),
                Length=len(Path),
                BendCount=_CountBends(Path),
                ViaCount=sum(
                    First[1] != Second[1]
                    for First, Second in zip(Path, Path[1:])
                ),
                Cost=len(Path),
            )
            Key = (Signal, Terminal, Layer)
            Result[Key] = tuple(sorted((
                *Result.get(Key, ()),
                Portal,
            ), key=lambda Value: (Value.Cost, Value.PortalId)))
    return Result

def ResolvePlacementAccessFabricRegionContract(
    MinimumX: int,
    MaximumX: int,
    MinimumZ: int,
    MaximumZ: int,
    Fabric: Any | None,
    Domains: Mapping[tuple[str, Position3], Any],
) -> tuple[
    int,
    int,
    int,
    int,
    frozenset[Position3],
    tuple[int, int, int, int] | None,
]:
    """Expand an authoritative region to the immutable access contract.

    Portal construction and detailed routing must see every physical node the
    frozen fabric can select. This includes fabric edges, ingress nodes, and
    all still-selectable terminal stubs; otherwise an ingress can be emitted
    outside the native context and falsely appear to have no tree.
    """
    Positions = frozenset(
        Position
        for Position in (
            *getattr(Fabric, "Nodes", ()),
            *getattr(Fabric, "IngressNodes", ()),
            *(
                Position
                for Domain in Domains.values()
                for Stub in Domain.EscapeStubs
                for Position in Stub.Path
            ),
        )
    )
    RawOuterBounds = getattr(Fabric, "OuterBounds", None)
    OuterBounds: tuple[int, int, int, int] | None = None
    if RawOuterBounds is not None:
        if len(RawOuterBounds) != 4:
            raise ValueError("placement access fabric outer bounds are invalid")
        OuterBounds = tuple(int(Value) for Value in RawOuterBounds)
        OuterMinimumX, OuterMinimumZ, OuterMaximumX, OuterMaximumZ = (
            OuterBounds
        )
        if OuterMinimumX > OuterMaximumX or OuterMinimumZ > OuterMaximumZ:
            raise ValueError("placement access fabric outer bounds are inverted")
    if Positions:
        MinimumX = min(MinimumX, min(Position[0] for Position in Positions))
        MaximumX = max(MaximumX, max(Position[0] for Position in Positions))
        MinimumZ = min(MinimumZ, min(Position[2] for Position in Positions))
        MaximumZ = max(MaximumZ, max(Position[2] for Position in Positions))
    if OuterBounds is not None:
        OuterMinimumX, OuterMinimumZ, OuterMaximumX, OuterMaximumZ = (
            OuterBounds
        )
        MinimumX = min(MinimumX, OuterMinimumX)
        MaximumX = max(MaximumX, OuterMaximumX)
        MinimumZ = min(MinimumZ, OuterMinimumZ)
        MaximumZ = max(MaximumZ, OuterMaximumZ)
    return (
        MinimumX,
        MaximumX,
        MinimumZ,
        MaximumZ,
        Positions,
        OuterBounds,
    )

def ValidatePhysicalComponentExactAttachmentPortals(
    Profiles: Mapping[str, Any],
    Portals: Mapping[
        tuple[str, Position3, int],
        tuple[PinAccessPortal, ...],
    ],
    Plan: PhysicalComponentAssemblyPlan,
    LayerCount: int,
) -> dict[str, object]:
    """Require every fixed component seam to be visible to global routing."""
    PlanningChannels = Plan.PlanningChannels
    ChannelsBySignal = {
        Channel.Signal: Channel for Channel in PlanningChannels
    }
    MissingAttachments: list[dict[str, object]] = []
    for Port in sorted(Plan.Ports, key=lambda Value: Value.Signal):
        Channel = ChannelsBySignal.get(Port.Signal)
        Layer = int(Channel.Layer) if Channel is not None else -1
        Profile = Profiles.get(Port.Signal)
        ProfileTerminals = frozenset((
            *(() if Profile is None else (Profile.Root,)),
            *(() if Profile is None else tuple(Profile.Targets)),
        ))
        Domain = tuple(Portals.get(
            (Port.Signal, Port.Attachment, Layer),
            (),
        ))
        ExpectedPortalId = BuildPhysicalComponentGlobalPortalId(Port, Layer)
        ExactVisible = any(
            Portal.PortalId == ExpectedPortalId
            and Portal.Terminal == Port.Attachment
            and int(Portal.Layer) == Layer
            and tuple(Portal.Path) == tuple(Port.GlobalPath)
            for Portal in Domain
        )
        Problems = []
        if Channel is None:
            Problems.append("missing-channel")
        if Layer < 0 or Layer >= LayerCount:
            Problems.append("layer-not-visible")
        if Port.Attachment not in ProfileTerminals:
            Problems.append("attachment-not-in-global-profile")
        if not ExactVisible:
            Problems.append("exact-portal-not-visible")
        if Problems:
            MissingAttachments.append({
                "Signal": Port.Signal,
                "Attachment": list(Port.Attachment),
                "Layer": Layer,
                "Problems": Problems,
                "ExpectedPortalId": ExpectedPortalId,
                "VisiblePortalIds": [
                    Portal.PortalId for Portal in Domain
                ],
            })
    Diagnostics = {
        "PlanFingerprint": Plan.PlanFingerprint,
        "LayerCount": LayerCount,
        "DeclaredExactAttachmentCount": len(Plan.Ports),
        "VisibleExactAttachmentCount": (
            len(Plan.Ports) - len(MissingAttachments)
        ),
        "MissingExactAttachments": MissingAttachments,
        "AllDeclaredExactAttachmentsVisible": not MissingAttachments,
        "ExactAttachmentValidationFingerprint": BuildStableFingerprint((
            "physical-exact-attachment-validation-v1",
            Plan.PlanFingerprint,
            int(LayerCount),
            tuple(
                (
                    Port.Signal,
                    tuple(Port.Attachment),
                    int(ChannelsBySignal[Port.Signal].Layer),
                    BuildPhysicalComponentGlobalPortalId(
                        Port,
                        int(ChannelsBySignal[Port.Signal].Layer),
                    ),
                    tuple(tuple(Value) for Value in Port.GlobalPath),
                    tuple(
                        Portal.PortalId
                        for Portal in Portals.get((
                            Port.Signal,
                            Port.Attachment,
                            int(ChannelsBySignal[Port.Signal].Layer),
                        ), ())
                    ),
                )
                for Port in sorted(
                    Plan.Ports,
                    key=lambda Value: Value.Signal,
                )
                if Port.Signal in ChannelsBySignal
            ),
        )),
        "ImplicitForeignTransitDomainCount": 0,
    }
    if MissingAttachments:
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ComponentAssemblyIdentityMismatch,
            Stage="PhysicalComponentGlobalPlanning",
            AffectedNets=tuple(
                Value["Signal"] for Value in MissingAttachments
            ),
            Detail=(
                "one or more immutable component attachment portals are "
                "not visible in the authoritative global portal domain"
            ),
            Diagnostics=Diagnostics,
        ))
    return Diagnostics

def BuildRepeaterReadyPortalDomains(
    Portals: dict[
        tuple[str, Position3, int],
        tuple[PinAccessPortal, ...],
    ],
    Signals: frozenset[str],
    Region: Any,
    Resources: RoutingResources,
    ExtensionLength: int = 3,
    MaximumExtensionsPerPortal: int = 2,
) -> tuple[
    dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]],
    dict[str, object],
]:
    """Add bounded straight landing macros to measured power-cut portals."""
    if ExtensionLength < 2:
        raise ValueError("ExtensionLength must be at least two")
    if MaximumExtensionsPerPortal < 1:
        raise ValueError("MaximumExtensionsPerPortal must be positive")
    if not Signals:
        return dict(Portals), {
            "Enabled": False,
            "Signals": [],
            "ExtendedPortalCount": 0,
        }
    RegionNodes = frozenset(Region.Nodes)
    RegionEdges = frozenset(Region.Edges)
    Result = dict(Portals)
    ExtendedPortalCount = 0
    ExtendedTerminalCount = 0
    DirectionOrder = (
        (1, 0),
        (-1, 0),
        (0, 1),
        (0, -1),
    )
    for Key, Values in sorted(Portals.items()):
        Signal, _Terminal, _Layer = Key
        if Signal not in Signals:
            continue
        ExtendedValues: list[PinAccessPortal] = []
        for Portal in Values:
            Endpoint = Portal.Path[-1]
            PriorDirection: tuple[int, int] | None = None
            if len(Portal.Path) >= 2:
                Previous = Portal.Path[-2]
                Delta = (
                    Endpoint[0] - Previous[0],
                    Endpoint[2] - Previous[2],
                )
                if (
                    Endpoint[1] == Previous[1]
                    and Delta in DirectionOrder
                ):
                    PriorDirection = Delta
            OrderedDirections = tuple(
                sorted(
                    DirectionOrder,
                    key=lambda Direction: (
                        0 if Direction == PriorDirection else 1,
                        Direction,
                    ),
                )
            )
            PortalExtensionCount = 0
            for DeltaX, DeltaZ in OrderedDirections:
                Extension = tuple(
                    (
                        Endpoint[0] + DeltaX * Distance,
                        Endpoint[1],
                        Endpoint[2] + DeltaZ * Distance,
                    )
                    for Distance in range(1, ExtensionLength + 1)
                )
                CandidatePath = tuple((*Portal.Path, *Extension))
                CandidateEdges = frozenset(
                    NormalizeRoutingEdge(First, Second)
                    for First, Second in zip(
                        CandidatePath,
                        CandidatePath[1:],
                    )
                )
                if (
                    len(set(CandidatePath)) != len(CandidatePath)
                    or not frozenset(Extension) <= RegionNodes
                    or not CandidateEdges <= RegionEdges
                ):
                    continue
                CandidateClaims = (
                    Resources.ResourceGraph.BuildRouteClaims(CandidatePath)
                )
                if FindSelfClaimConflicts({
                    Signal: CandidateClaims,
                }):
                    continue
                BendCount = _CountBends(CandidatePath)
                ViaCount = sum(
                    First[1] != Second[1]
                    for First, Second in zip(
                        CandidatePath,
                        CandidatePath[1:],
                    )
                )
                ExtendedValues.append(replace(
                    Portal,
                    PortalId=(
                        f"{Portal.PortalId}:repeater-ready:"
                        f"{DeltaX},{DeltaZ}:{ExtensionLength}"
                    ),
                    Path=CandidatePath,
                    Edges=CandidateEdges,
                    Claims=CandidateClaims,
                    Length=len(CandidatePath),
                    BendCount=BendCount,
                    ViaCount=ViaCount,
                    # Rank a proved straight landing before the spatial-only
                    # portal. Exact material cost is recomputed from the
                    # selected routed tree, so this is only a domain order.
                    Cost=max(
                        0,
                        Portal.Cost
                        + ExtensionLength
                        + max(0, BendCount - Portal.BendCount) * 10
                        - 32,
                    ),
                ))
                ExtendedPortalCount += 1
                PortalExtensionCount += 1
                if PortalExtensionCount >= MaximumExtensionsPerPortal:
                    break
        if ExtendedValues:
            ExtendedTerminalCount += 1
            Result[Key] = tuple((*ExtendedValues, *Values))
    ExtendedSignals = sorted({
        Key[0]
        for Key, Values in Result.items()
        if Key[0] in Signals
        and any(":repeater-ready:" in Portal.PortalId for Portal in Values)
    })
    return Result, {
        "Enabled": True,
        "Signals": sorted(Signals),
        "ExtensionLength": ExtensionLength,
        "MaximumExtensionsPerPortal": MaximumExtensionsPerPortal,
        "ExtendedSignals": ExtendedSignals,
        "ExtendedTerminalCount": ExtendedTerminalCount,
        "ExtendedPortalCount": ExtendedPortalCount,
        "ValidationOutcome": (
            "repeater-ready-domains-materialized"
            if ExtendedPortalCount
            else "no-legal-straight-landing"
        ),
    }

def _CountBends(Path: tuple[Position3, ...]) -> int:
    Directions = [
        (
            Second[0] - First[0],
            Second[1] - First[1],
            Second[2] - First[2],
        )
        for First, Second in zip(Path, Path[1:])
    ]
    return sum(First != Second for First, Second in zip(Directions, Directions[1:]))

def _BuildCandidateGraph(
    Nodes: set[Position3],
    Resources: Any,
) -> dict[Position3, list[Position3]]:
    Result = {Position: [] for Position in Nodes}
    for Position in sorted(Nodes):
        for Neighbor in DefaultRedstoneRoutingTechnology.NeighborPositions(Position):
            if Neighbor not in Nodes:
                continue
            if Resources.BuildPrimitive(Position, Neighbor) is not None:
                Result[Position].append(Neighbor)
    return Result

def _FindPath(
    Graph: dict[Position3, list[Position3]],
    Start: Position3,
    Target: Position3,
) -> tuple[Position3, ...]:
    Parents: dict[Position3, Position3 | None] = {Start: None}
    Pending = deque((Start,))
    while Pending and Target not in Parents:
        Current = Pending.popleft()
        for Neighbor in Graph.get(Current, ()):
            if Neighbor not in Parents:
                Parents[Neighbor] = Current
                Pending.append(Neighbor)
    if Target not in Parents:
        return ()
    Result = []
    Current: Position3 | None = Target
    while Current is not None:
        Result.append(Current)
        Current = Parents[Current]
    return tuple(reversed(Result))

def _FindComponentNodes(
    Graph: dict[Position3, list[Position3]],
    Start: Position3,
) -> set[Position3]:
    """Return the BFS component reachable from Start in Graph."""
    if Start not in Graph:
        return set()
    Result: set[Position3] = {Start}
    Pending = deque((Start,))
    while Pending:
        Current = Pending.popleft()
        for Neighbor in Graph.get(Current, ()):
            if Neighbor in Result:
                continue
            Result.add(Neighbor)
            Pending.append(Neighbor)
    return Result

def _ReserveRepeaters(
    Signal: str,
    Root: Position3,
    Targets: tuple[Position3, ...],
    Graph: dict[Position3, list[Position3]],
    Technology: RedstoneRoutingTechnology,
) -> tuple[tuple[RoutingReservation, ...], dict[Position3, tuple[Position3, ...]]]:
    Reserved: dict[Position3, RoutingReservation] = {}
    Paths = {}
    for Target in Targets:
        Path = _FindPath(Graph, Root, Target)
        if not Path:
            return (), {}
        Paths[Target] = Path
        LastRefresh = 0
        while len(Path) - 1 - LastRefresh >= Technology.MaximumUnrefreshedDustLength:
            Maximum = min(
                len(Path) - 2,
                LastRefresh + Technology.MaximumUnrefreshedDustLength - 1,
            )
            Candidates = []
            for Index in range(LastRefresh + 1, Maximum + 1):
                Previous, Current, Next = Path[Index - 1 : Index + 2]
                if (
                    len(Graph.get(Current, ())) == 2
                    and
                    Previous[1] == Current[1] == Next[1]
                    and (
                        Previous[0] == Current[0] == Next[0]
                        or Previous[2] == Current[2] == Next[2]
                    )
                ):
                    Candidates.append(Index)
            if not Candidates:
                return (), {}
            # Select the latest legal site.  The subsequent power validation
            # remains authoritative, while this avoids the old fixed safety
            # margin placing a repeater early on every long segment.
            Selected = max(Candidates)
            Position = Path[Selected]
            Next = Path[Selected + 1]
            Delta = (Next[0] - Position[0], Next[2] - Position[2])
            Facing = {
                (1, 0): "west",
                (-1, 0): "east",
                (0, 1): "north",
                (0, -1): "south",
            }[Delta]
            Reserved.setdefault(
                Position,
                RoutingReservation(
                    Signal=Signal,
                    Resource=RoutingResourceId(RoutingResourceKind.Wire, Position),
                    Position=Position,
                    Purpose="Repeater",
                    Facing=Facing,
                ),
            )
            LastRefresh = Selected
    Reservations = tuple(Reserved[Position] for Position in sorted(Reserved))
    Reservations = PruneRedundantRepeaterReservations(
        Root,
        Targets,
        Graph,
        Reservations,
        Technology,
    )
    return Reservations, Paths

def _MaterializeCandidate(
    Signal: str,
    Profile: Any,
    SourcePortal: PinAccessPortal,
    TargetPortals: tuple[PinAccessPortal, ...],
    Guide: frozenset[Position2],
    Layer: int,
    Axis: str,
    Lane: int,
    Variant: int,
    RoutedTree: list[Position3] | None,
    Region: Any,
    Resources: RoutingResources,
    Technology: RedstoneRoutingTechnology,
    LengthPenalty: int,
    BendPenalty: int = 0,
    ViaPenalty: int = 0,
    LayerPenalty: int = 0,
    GuideDeviationPenalty: int = 0,
    RepeaterPenalty: int = 2,
    NativeRepeaterReservations: tuple[tuple[Position3, str], ...] = (),
    RejectionCounts: Counter[str] | None = None,
    MaterializationDiagnostics: dict[str, object] | None = None,
) -> NetRouteCandidate | None:
    def RecordMaterialization(
        Reason: str,
        **Diagnostics: object,
    ) -> None:
        if MaterializationDiagnostics is not None:
            MaterializationDiagnostics.update({
                "Status": Reason,
                **Diagnostics,
            })

    if RoutedTree is None:
        if RejectionCounts is not None:
            RejectionCounts["NoTree"] += 1
        RecordMaterialization("no-routed-tree")
        return None
    Nodes = set(RoutedTree)
    SeedNodes = {
        Position
        for Claim in (Profile.Seed.LocalClaims if Profile.Seed is not None else ())
        for Position in Claim.Nodes
    }
    # The native search returns the new connecting tree.  Detached immutable
    # pre-route fragments are represented as target anchors, so restore their
    # complete physical nodes before validating the combined routed net.
    Nodes.update(SeedNodes)
    Nodes.update(Profile.SourceAccessPath)
    Nodes.update(SourcePortal.Path)
    for Target, Portal in zip(Profile.Targets, TargetPortals):
        Nodes.update(Profile.TargetAccessPaths[Target])
        Nodes.update(Portal.Path)
    Claims = Resources.ResourceGraph.BuildRouteClaims(Nodes)
    SelfClaimConflicts = FindSelfClaimConflicts({Signal: Claims})
    if SelfClaimConflicts:
        if os.environ.get("RCS_DEBUG_MATERIALIZE") == Signal:
            print(
                "[debug] authoritative: self-claim conflicts "
                f"signal={Signal} conflicts="
                f"{tuple(sorted(SelfClaimConflicts, key=str))} "
                f"tree={tuple(RoutedTree)}",
                flush=True,
            )
        if RejectionCounts is not None:
            RejectionCounts["SelfClaimConflict"] += 1
        RecordMaterialization(
            "self-claim-conflict",
            ConflictCount=len(SelfClaimConflicts),
        )
        return None
    Graph = _BuildCandidateGraph(Nodes, Resources.ResourceGraph)
    RootComponent = _FindComponentNodes(Graph, Profile.Root)
    MissingPaths = [
        Target for Target in Profile.Targets
        if Target not in RootComponent
    ]
    MissingSeedNodes = sorted(SeedNodes - RootComponent)
    if os.environ.get("RCS_DEBUG_MATERIALIZE") and (
        MissingPaths or MissingSeedNodes
    ):
        import os as _os_debug
        if _os_debug.environ.get("RCS_DEBUG_MATERIALIZE") == Signal:
            Starts = tuple(dict.fromkeys((*Profile.SourceAccessPath, *SourcePortal.Path)))
            Component = _FindComponentNodes(Graph, Profile.Root)
            MinX = min(Position[0] for Position in RoutedTree) if RoutedTree else None
            MaxX = max(Position[0] for Position in RoutedTree) if RoutedTree else None
            print(
                "[debug] authoritative: materialization connectivity failure "
                f"signal={Signal} root={Profile.Root} missing={tuple(MissingPaths)} "
                f"nodes={len(Nodes)} tree={len(RoutedTree)}",
                flush=True,
            )
            print(
                "[debug] authoritative: materialization bounds "
                f"x=({MinX},{MaxX}) y=({min(Position[1] for Position in RoutedTree)},"
                f"{max(Position[1] for Position in RoutedTree)}) "
                f"rootInTree={Profile.Root in Nodes} "
                f"rootComponent={len(Component)} startCount={len(Starts)}",
                flush=True,
            )
            print(
                f"[debug] authoritative: materialization starts={Starts}",
                flush=True,
            )
            print(
                "[debug] authoritative: materialization targetPaths=" +
                str({
                    Target: Profile.TargetAccessPaths[Target]
                    for Target in Profile.Targets
                }),
                flush=True,
            )
            print(
                "[debug] authoritative: materialization sourcePath=" +
                str(Profile.SourceAccessPath),
                flush=True,
            )
            print(
                "[debug] authoritative: routedTreePrefix="
                f"{tuple(sorted(RoutedTree))[:32]}",
                flush=True,
            )
    if MissingPaths or MissingSeedNodes:
        if RejectionCounts is not None:
            RejectionCounts["Disconnected"] += 1
        RecordMaterialization(
            "disconnected",
            MissingTargetCount=len(MissingPaths),
            MissingSeedNodeCount=len(MissingSeedNodes),
        )
        return None
    TargetPaths = {
        Target: _FindPath(Graph, Profile.Root, Target)
        for Target in Profile.Targets
    }
    if any(not Path for Path in TargetPaths.values()):
        if RejectionCounts is not None:
            RejectionCounts["Disconnected"] += 1
        RecordMaterialization("disconnected-target-path")
        return None
    # The native tree search has already proved these refresh sites while
    # carrying signal strength in its state. Preserve them; the Python tree
    # walk below is only a deterministic path/coverage supplement and can
    # choose a different branch through a cyclic tree.
    def IsFlatStraightRepeaterSite(
        Position: Position3,
        Facing: str,
    ) -> bool:
        if len(Graph.get(Position, ())) != 2:
            return False
        FlatNeighbors = tuple(
            Neighbor
            for Neighbor in Graph.get(Position, ())
            if Neighbor[1] == Position[1]
        )
        OutputDelta = {
            "west": (1, 0),
            "east": (-1, 0),
            "north": (0, 1),
            "south": (0, -1),
        }.get(Facing)
        if OutputDelta is None:
            return False
        Output = (
            Position[0] + OutputDelta[0],
            Position[1],
            Position[2] + OutputDelta[1],
        )
        Input = (
            Position[0] - OutputDelta[0],
            Position[1],
            Position[2] - OutputDelta[1],
        )
        return Output in FlatNeighbors and Input in FlatNeighbors

    NativeReservationValues = tuple(NativeRepeaterReservations)
    NativeReservations = {
        Position: RoutingReservation(
            Signal=Signal,
            Resource=RoutingResourceId(RoutingResourceKind.Wire, Position),
            Position=Position,
            Purpose="Repeater",
            Facing=Facing,
        )
        for Position, Facing in NativeReservationValues
        if IsFlatStraightRepeaterSite(Position, Facing)
    }
    NativeGeometryValid = (
        len(NativeReservations) == len(NativeReservationValues)
    )
    InvalidNativeRepeaterPositions = sorted(
        Position
        for Position, _Facing in NativeReservationValues
        if Position not in NativeReservations
    )
    EffectiveRepeaterReservations: dict[Position3, RoutingReservation] = {}
    PoweredNodes: dict[Position3, int] = {}
    RepeaterSource = "python-fallback"
    NativePowerValid = False
    # A native tree can include one redundant reservation at a node that
    # becomes a branch after the access paths are materialized.  Filter every
    # reservation through the physical straight-site predicate, then let the
    # exact directed power model decide whether the usable subset is already
    # sufficient.  Requiring every raw reservation to survive discarded
    # otherwise valid powered trees.
    if NativeReservations:
        NativePoweredNodes = PropagateRoutePower(
            Profile.Root,
            Graph,
            {
                Position: Reservation.Facing
                for Position, Reservation in NativeReservations.items()
                if Reservation.Facing is not None
            },
        )
        NativePowerValid = all(
            NativePoweredNodes.get(Target, 0) > 0
            for Target in Profile.Targets
        )
        if NativePowerValid:
            EffectiveRepeaterReservations = NativeReservations
            PoweredNodes = NativePoweredNodes
            RepeaterSource = "native"
    if not EffectiveRepeaterReservations:
        FallbackReservations, FallbackPaths = _ReserveRepeaters(
            Signal,
            Profile.Root,
            Profile.Targets,
            Graph,
            Technology,
        )
        EffectiveRepeaterReservations = {
            Reservation.Position: Reservation
            for Reservation in FallbackReservations
        }
        PoweredNodes = PropagateRoutePower(
            Profile.Root,
            Graph,
            {
                Position: Reservation.Facing
                for Position, Reservation in EffectiveRepeaterReservations.items()
                if Reservation.Facing is not None
            },
        )
    else:
        FallbackPaths = {}
    PoweredTargetCount = sum(
        PoweredNodes.get(Target, 0) > 0 for Target in Profile.Targets
    )
    if PoweredTargetCount != len(Profile.Targets):
        if RejectionCounts is not None:
            RejectionCounts["NoRepeater"] += 1
            if NativeReservationValues:
                RejectionCounts["NativeRepeaterMaterializationMismatch"] += 1
        RecordMaterialization(
            "native-repeater-mismatch"
            if NativeReservationValues
            else "no-repeater",
            Root=list(Profile.Root),
            Targets=[list(Target) for Target in Profile.Targets],
            NativeRepeaterReservationCount=len(NativeReservationValues),
            UsableNativeRepeaterReservationCount=len(NativeReservations),
            InvalidNativeRepeaterPositions=[
                list(Position)
                for Position in InvalidNativeRepeaterPositions
            ],
            UsableNativeRepeaters=[
                {
                    "Position": list(Position),
                    "Facing": Reservation.Facing,
                }
                for Position, Reservation in sorted(
                    NativeReservations.items()
                )
            ],
            FallbackRepeaters=[
                {
                    "Position": list(Position),
                    "Facing": Reservation.Facing,
                }
                for Position, Reservation in sorted(
                    EffectiveRepeaterReservations.items()
                )
            ],
            FallbackPaths={
                str(Target): [list(Position) for Position in Path]
                for Target, Path in sorted(FallbackPaths.items())
            },
            NativeGeometryValid=NativeGeometryValid,
            NativePowerValid=NativePowerValid,
            FallbackUsed=RepeaterSource != "native",
            PoweredTargetCount=PoweredTargetCount,
            TargetCount=len(Profile.Targets),
        )
        return None
    Edges = frozenset(
        NormalizeRoutingEdge(Position, Neighbor)
        for Position, Neighbors in Graph.items()
        for Neighbor in Neighbors
        if Position < Neighbor
    )
    Length = len(Nodes)
    BendCount = sum(_CountBends(Path) for Path in TargetPaths.values())
    ViaCount = sum(
        First[1] != Second[1]
        for Path in TargetPaths.values()
        for First, Second in zip(Path, Path[1:])
    )
    SourcePortalId = SourcePortal.PortalId
    TargetPortalIds = tuple(
        Portal.PortalId for Portal in TargetPortals
    )
    TargetPortalSignature = "-".join(
        str(PortalId) for PortalId in TargetPortalIds
    )
    RouteFingerprint = BuildStableFingerprint(
        tuple(sorted(Nodes))
    )[:12]
    if Resources.PreparingPhysicalComponentGlobalChannels:
        # Guide axis/lane/variant are search provenance, not physical route
        # identity.  The same exact tree can be rediscovered through several
        # coarse descriptors; keeping those aliases distinct causes a learned
        # exterior/local no-good to revisit an already rejected assignment.
        CandidateId = (
            f"{Signal}:PhysicalGlobal:"
            f"S{SourcePortalId}:T{TargetPortalSignature}:"
            f"R{RouteFingerprint}"
        )
    else:
        CandidateId = (
            f"{Signal}:L{Layer}:{Axis}:{Lane}:V{Variant}:"
            f"S{SourcePortalId}:T{TargetPortalSignature}:"
            f"R{RouteFingerprint}"
        )
    IncrementalLength = len(Nodes - SeedNodes)
    IncrementalMaterialCost = (
        IncrementalLength * max(1, LengthPenalty)
        + BendCount * max(0, BendPenalty)
        + ViaCount * max(0, ViaPenalty)
        + Layer * max(0, LayerPenalty)
        + max(0, GuideDeviationPenalty)
        + len(EffectiveRepeaterReservations) * max(0, RepeaterPenalty)
    )
    TargetPaths = {
        Target: tuple(Path)
        for Target, Path in TargetPaths.items()
    }
    BranchClaims = {
        Target: Resources.ResourceGraph.BuildRouteClaims(Path)
        for Target, Path in TargetPaths.items()
    }
    RecordMaterialization(
        "accepted",
        NativeRepeaterReservationCount=len(NativeReservationValues),
        UsableNativeRepeaterReservationCount=len(NativeReservations),
        NativeGeometryValid=NativeGeometryValid,
        NativePowerValid=NativePowerValid,
        FallbackUsed=RepeaterSource != "native",
        PoweredTargetCount=PoweredTargetCount,
        TargetCount=len(Profile.Targets),
        EffectiveRepeaterReservationCount=len(EffectiveRepeaterReservations),
    )
    return NetRouteCandidate(
        CandidateId=CandidateId,
        Signal=Signal,
        SourcePortalId=SourcePortal.PortalId,
        TargetPortalIds={
            Target: Portal.PortalId
            for Target, Portal in zip(Profile.Targets, TargetPortals)
        },
        Nodes=frozenset(Nodes),
        Edges=Edges,
        Claims=Claims,
        Layer=Layer,
        Guide=Guide,
        RepeaterWaypoints=tuple(
            sorted(EffectiveRepeaterReservations)
        ),
        RepeaterReservations=tuple(
            EffectiveRepeaterReservations[Position]
            for Position in sorted(EffectiveRepeaterReservations)
        ),
        MaterialCost=IncrementalMaterialCost,
        FootprintGrowth=len(Guide),
        Length=Length,
        BendCount=BendCount,
        ViaCount=ViaCount,
        IncrementalMaterialCost=IncrementalMaterialCost,
        IncrementalLength=IncrementalLength,
        SeedNodeCount=len(SeedNodes),
        TargetPaths=TargetPaths,
        BranchClaims=BranchClaims,
        Envelope=BuildRoutingEnvelope(
            Nodes,
            Claims.SupportCells,
            EffectiveRepeaterReservations,
        ),
    )

def ShouldRetryNegotiatedExactAssignment(
    PassIndex: int,
    HasFinalConflicts: bool,
    CompleteSeedDomain: bool,
    DiscoveredCandidateThisPass: bool,
) -> bool:
    """Retry exact selection only after negotiated routing grows the domain."""
    return (
        PassIndex > 0
        and HasFinalConflicts
        and (
            not CompleteSeedDomain
            or DiscoveredCandidateThisPass
        )
    )

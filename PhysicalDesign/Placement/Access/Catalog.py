"""Compile and place the finite, technology-owned pin-access catalog."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Iterable, Mapping

from PhysicalDesign.Cells.Library import CellMacros, PinAccessPattern
from PhysicalDesign.Contracts.Core import Position3
from PhysicalDesign.Contracts.PlacementAccess import (
    PhysicalPinAccessTemplate,
    PlacedPinAccessOption,
    PlacedPinAccessOptionDomain,
    SelectedPlacementPinAccessWitness,
)
from PhysicalDesign.Geometry.Placement import GetGateInputAccess
from PhysicalDesign.Geometry.Rotation import (
    TransformDirection,
    TransformLocalPosition,
)
from PhysicalDesign.Redstone.Technology import (
    RepeaterInputFacingForStep,
    RedstoneRoutingTechnology,
)
from PhysicalDesign.Resources.ResourceGraph import (
    FindClaimConflicts,
    FindSelfClaimConflicts,
    RoutingReservation,
    RoutingResourceClaims,
    RoutingResourceGraph,
    RoutingResourceId,
    RoutingResourceKind,
)
from PhysicalDesign.Runtime.Reliability import BuildStableFingerprint


PhysicalPinAccessCatalogVersion = "physical-pin-access-catalog-v1"
SupportedPinAccessPatternFamilies = frozenset({
    "straight",
    "planar-jog",
})


def _Add(First: Position3, Second: Position3) -> Position3:
    return tuple(First[Index] + Second[Index] for Index in range(3))


def _Subtract(First: Position3, Second: Position3) -> Position3:
    return tuple(First[Index] - Second[Index] for Index in range(3))


def _Scale(Value: Position3, Factor: int) -> Position3:
    return tuple(Component * Factor for Component in Value)


def _Translate(Value: Position3, Origin: Position3) -> Position3:
    return _Add(Value, Origin)


def _TechnologyFingerprint(
    Technology: RedstoneRoutingTechnology,
) -> str:
    return BuildStableFingerprint({
        "Kind": "pin-access-technology-v1",
        "Technology": asdict(Technology),
    })


def _ResourceModelFingerprint(
    ResourceGraph: RoutingResourceGraph,
    StaticExclusionOwnersByPosition: Mapping[
        Position3, frozenset[str]
    ],
    UnownedStaticExclusions: frozenset[Position3],
) -> str:
    return BuildStableFingerprint({
        "Kind": "pin-access-resource-model-v1",
        "GraphVersion": ResourceGraph.GraphVersion,
        "TechnologyFingerprint": _TechnologyFingerprint(
            ResourceGraph.Technology
        ),
        "ActualBlocks": sorted(ResourceGraph.ActualBlocks),
        "ElectricalBlocks": sorted(ResourceGraph.ElectricalBlocks),
        "SolidBlocks": sorted(ResourceGraph.SolidBlocks),
        "StaticKeepOutBlocks": sorted(ResourceGraph.StaticKeepOutBlocks),
        "StaticExclusionOwnersByPosition": [
            (Position, tuple(sorted(Owners)))
            for Position, Owners in sorted(
                StaticExclusionOwnersByPosition.items()
            )
        ],
        "UnownedStaticExclusions": sorted(UnownedStaticExclusions),
    })


def _NormalizeFamilies(
    EnabledPatternFamilies: Iterable[str],
) -> tuple[str, ...]:
    Result = tuple(sorted(set(map(str, EnabledPatternFamilies))))
    Unknown = set(Result) - SupportedPinAccessPatternFamilies
    if Unknown:
        raise ValueError(
            "pin-access catalog has unsupported pattern families: "
            + ",".join(sorted(Unknown))
        )
    if not Result:
        raise ValueError("pin-access catalog requires a pattern family")
    return Result


def _BuildTemplate(
    CellKind: str,
    Pattern: PinAccessPattern,
    *,
    CatalogVersion: str,
    Technology: RedstoneRoutingTechnology,
    TechnologyFingerprint: str,
) -> PhysicalPinAccessTemplate:
    if Pattern.AccessLength != Technology.AccessLength:
        raise ValueError(
            "pin-access seed and routing technology access lengths disagree"
        )
    if Pattern.AccessLength != 3:
        raise ValueError(
            "the v1 pin-access catalog requires a three-cell first leg"
        )
    P = Pattern.ConnectionPosition
    D = Pattern.ApproachDirection
    T = (D[2], 0, -D[0])
    if Pattern.PatternFamily == "straight":
        FirstLegNodes = tuple(
            _Add(P, _Scale(D, Offset))
            for Offset in range(Technology.AccessLength)
        )
        FirstTrackNode = _Add(P, _Scale(D, Technology.AccessLength))
        RepeaterPathIndex = 1
    else:
        Tangent = _Scale(T, Pattern.TangentialSign)
        FirstLegNodes = (
            P,
            _Add(P, D),
            _Add(_Add(P, D), Tangent),
        )
        FirstTrackNode = _Add(_Add(P, D), _Scale(Tangent, 2))
        RepeaterPathIndex = 0
    BlockRoles = tuple(
        (
            Position,
            "repeater" if Index == RepeaterPathIndex else "dust",
        )
        for Index, Position in enumerate(FirstLegNodes)
    )
    return PhysicalPinAccessTemplate(
        CatalogVersion=CatalogVersion,
        CellKind=CellKind,
        TemplateId=f"{CellKind}:{Pattern.PatternId}",
        PatternFamily=Pattern.PatternFamily,
        PinId=Pattern.PinId,
        ConnectionPosition=P,
        ApproachDirection=D,
        TangentialSign=Pattern.TangentialSign,
        FirstLegNodes=FirstLegNodes,
        FirstTrackNode=FirstTrackNode,
        BlockRoles=BlockRoles,
        RepeaterPathIndex=RepeaterPathIndex,
        AllowedRoutingLayers=Pattern.AllowedRoutingLayers,
        TechnologyFingerprint=TechnologyFingerprint,
    )


def BuildPhysicalPinAccessCatalog(
    *,
    Technology: RedstoneRoutingTechnology,
    EnabledPatternFamilies: Iterable[str] = (
        "planar-jog",
        "straight",
    ),
    CatalogVersion: str = PhysicalPinAccessCatalogVersion,
) -> tuple[PhysicalPinAccessTemplate, ...]:
    """Compile every enabled standard-cell pattern into exact local geometry."""
    if not CatalogVersion:
        raise ValueError("pin-access catalog requires a version")
    Families = _NormalizeFamilies(EnabledPatternFamilies)
    TechnologyFingerprint = _TechnologyFingerprint(Technology)
    Templates = []
    for CellKind, Macro in sorted(CellMacros.items()):
        Patterns = (
            Macro.PinAccessPatterns
            if Families == ("straight",)
            else Macro.RoutingAwarePinAccessPatterns
        )
        for Pattern in Patterns:
            if Pattern.PatternFamily not in Families:
                continue
            Templates.append(_BuildTemplate(
                CellKind,
                Pattern,
                CatalogVersion=CatalogVersion,
                Technology=Technology,
                TechnologyFingerprint=TechnologyFingerprint,
            ))
    Ordered = tuple(sorted(
        Templates,
        key=lambda Value: Value.StructuralIdentity(),
    ))
    Fingerprints = tuple(Value.TemplateFingerprint for Value in Ordered)
    if len(Fingerprints) != len(set(Fingerprints)):
        raise ValueError("pin-access catalog contains duplicate templates")
    return Ordered


def _PlacedTerminalBindings(
    PlacedGates: Iterable[Any],
) -> tuple[tuple[Any, str, str, str, Position3, Position3], ...]:
    Results = []
    for Gate in PlacedGates:
        if Gate.OutputPin is not None and Gate.OutputDirection is not None:
            Results.extend(
                (
                    Gate,
                    str(Signal),
                    "Source",
                    "Output0",
                    tuple(Gate.OutputPin),
                    tuple(Gate.OutputDirection),
                )
                for Signal in Gate.Outputs
            )
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Pin, Direction = GetGateInputAccess(Gate, InputIndex)
            Results.append((
                Gate,
                str(Signal),
                "Target",
                f"Input{InputIndex}",
                tuple(Pin),
                tuple(Direction),
            ))
    return tuple(sorted(
        Results,
        key=lambda Value: (
            Value[1],
            str(Value[0].Name),
            Value[2],
            Value[3],
        ),
    ))


def _StaticRoleSignals(Gate: Any, Role: str) -> tuple[str, ...]:
    if Role == "Output0":
        if not Gate.Outputs:
            raise ValueError(
                f"placed cell {Gate.Name} has no signal for {Role}"
            )
        return tuple(sorted(map(str, Gate.Outputs)))
    InputIndex = int(Role[5:])
    if InputIndex >= len(Gate.Inputs):
        raise ValueError(
            f"placed cell {Gate.Name} has no signal for {Role}"
        )
    return (str(Gate.Inputs[InputIndex]),)


def _BuildPlacedStaticExclusionOwnership(
    PlacedGates: Iterable[Any],
    *,
    ResourceGraph: RoutingResourceGraph,
    Technology: RedstoneRoutingTechnology,
    PreOwnedNodesBySignal: Mapping[str, Iterable[Position3]],
) -> tuple[dict[Position3, frozenset[str]], frozenset[Position3]]:
    """Index static exclusions by their explicit logical signal owners."""
    OwnersByStaticPosition: dict[Position3, set[str]] = {}
    for Gate in sorted(PlacedGates, key=lambda Value: str(Value.Name)):
        Macro = CellMacros[str(Gate.Kind).upper()]
        for LocalPosition, Role in Macro.StaticSignalRoles:
            Position = _TransformTemplatePosition(LocalPosition, Gate)
            OwnersByStaticPosition.setdefault(Position, set()).update(
                _StaticRoleSignals(Gate, Role)
            )
    for Signal, Positions in sorted(
        PreOwnedNodesBySignal.items(),
        key=lambda Value: str(Value[0]),
    ):
        for Position in sorted(set(map(tuple, Positions))):
            OwnersByStaticPosition.setdefault(Position, set()).add(
                str(Signal)
            )
    OwnedExclusions: dict[Position3, set[str]] = {}
    UnownedExclusions: set[Position3] = set()
    for StaticPosition in sorted(
        ResourceGraph.ElectricalBlocks | ResourceGraph.SolidBlocks
    ):
        Exclusions = Technology.BuildElectricalExclusions({StaticPosition})
        Owners = OwnersByStaticPosition.get(StaticPosition, set())
        if Owners:
            for Position in Exclusions:
                OwnedExclusions.setdefault(Position, set()).update(Owners)
        else:
            UnownedExclusions.update(Exclusions)
    return (
        {
            Position: frozenset(Owners)
            for Position, Owners in OwnedExclusions.items()
        },
        frozenset(UnownedExclusions),
    )


def _BuildPreOwnedClaimsBySignal(
    PreOwnedNodesBySignal: Mapping[str, Iterable[Position3]],
    ResourceGraph: RoutingResourceGraph,
) -> dict[str, RoutingResourceClaims]:
    Result = {}
    for Signal, Positions in sorted(
        PreOwnedNodesBySignal.items(),
        key=lambda Value: str(Value[0]),
    ):
        Nodes = frozenset(map(tuple, Positions))
        if Nodes:
            Result[str(Signal)] = ResourceGraph.BuildRouteClaims(Nodes)
    return Result


def BuildPlacedPinAccessModelFingerprint(
    PlacedGates: Iterable[Any],
    *,
    ResourceGraph: RoutingResourceGraph,
    PreOwnedNodesBySignal: Mapping[str, Iterable[Position3]] | None = None,
) -> str:
    """Identify the current static placement model without enumerating access."""
    Owners, Unowned = _BuildPlacedStaticExclusionOwnership(
        PlacedGates, ResourceGraph=ResourceGraph,
        Technology=ResourceGraph.Technology,
        PreOwnedNodesBySignal=PreOwnedNodesBySignal or {},
    )
    return _ResourceModelFingerprint(ResourceGraph, Owners, Unowned)


def _TransformTemplatePosition(
    Position: Position3,
    Gate: Any,
) -> Position3:
    Macro = CellMacros[str(Gate.Kind).upper()]
    Local = TransformLocalPosition(
        Position,
        Macro.Footprint,
        int(Gate.Rotation),
        bool(Gate.MirrorX),
    )
    return _Translate(Local, (int(Gate.X), int(Gate.Y), int(Gate.Z)))


def _AdmitPlacedGeometry(
    *,
    ResourceGraph: RoutingResourceGraph,
    Technology: RedstoneRoutingTechnology,
    FirstLegNodes: tuple[Position3, ...],
    FirstTrackNode: Position3,
    BridgePosition: Position3,
    Signal: str,
    StaticExclusionOwnersByPosition: Mapping[
        Position3, frozenset[str]
    ],
    UnownedStaticExclusions: frozenset[Position3],
    PreOwnedClaimsBySignal: Mapping[str, RoutingResourceClaims],
) -> bool:
    Terminal = FirstLegNodes[0]
    if (
        Terminal in ResourceGraph.ActualBlocks
        or BridgePosition not in ResourceGraph.ActualBlocks
        or BridgePosition not in ResourceGraph.ElectricalBlocks
    ):
        return False
    if (
        set(FirstLegNodes[1:])
        & set(ResourceGraph.ActualBlocks)
    ):
        return False
    for First, Second in zip(FirstLegNodes, FirstLegNodes[1:]):
        if ResourceGraph.BuildPrimitive(First, Second) is None:
            return False
    Claims = ResourceGraph.BuildRouteClaims(FirstLegNodes)
    if (
        Claims.SupportCells & ResourceGraph.ActualBlocks
        or Claims.RequiredAirCells & ResourceGraph.ActualBlocks
    ):
        return False
    ExistingSignalClaims = PreOwnedClaimsBySignal.get(Signal)
    CombinedSignalClaims = (
        ResourceGraph.BuildRouteClaims(
            Claims.WireCells | ExistingSignalClaims.WireCells
        )
        if ExistingSignalClaims is not None
        else Claims
    )
    if FindSelfClaimConflicts({Signal: CombinedSignalClaims}):
        return False
    if any(
        FindClaimConflicts({
            Signal: CombinedSignalClaims,
            ForeignSignal: ForeignClaims,
        })
        for ForeignSignal, ForeignClaims in PreOwnedClaimsBySignal.items()
        if ForeignSignal != Signal
    ):
        return False
    for Position in FirstLegNodes:
        if (
            Position in ResourceGraph.StaticKeepOutBlocks
            or Position in UnownedStaticExclusions
        ):
            return False
        Owners = StaticExclusionOwnersByPosition.get(Position, frozenset())
        if Owners and Owners != frozenset({Signal}):
            return False
    return True


def _MaterializeOption(
    Template: PhysicalPinAccessTemplate,
    *,
    Gate: Any,
    Signal: str,
    Role: str,
    PinId: str,
    PhysicalTerminal: Position3,
    PhysicalFace: Position3,
    Layer: int,
    ResourceGraph: RoutingResourceGraph,
    Technology: RedstoneRoutingTechnology,
    ResourceModelFingerprint: str,
    StaticExclusionOwnersByPosition: Mapping[
        Position3, frozenset[str]
    ],
    UnownedStaticExclusions: frozenset[Position3],
    PreOwnedClaimsBySignal: Mapping[str, RoutingResourceClaims],
) -> PlacedPinAccessOption | None:
    if Template.PinId != PinId or Template.CellKind != str(Gate.Kind).upper():
        return None
    CatalogTerminal = _TransformTemplatePosition(
        Template.ConnectionPosition,
        Gate,
    )
    CatalogFace = TransformDirection(
        Template.ApproachDirection,
        int(Gate.Rotation),
        bool(Gate.MirrorX),
    )
    if CatalogTerminal != PhysicalTerminal or CatalogFace != PhysicalFace:
        raise ValueError(
            f"placed pin {Gate.Name}:{PinId} does not match its catalog seed"
        )
    if Layer not in Template.AllowedRoutingLayers:
        return None
    FirstLegNodes = tuple(
        _TransformTemplatePosition(Position, Gate)
        for Position in Template.FirstLegNodes
    )
    FirstTrackNode = _TransformTemplatePosition(
        Template.FirstTrackNode,
        Gate,
    )
    BridgePosition = _TransformTemplatePosition(
        Template.BridgePosition,
        Gate,
    )
    if not _AdmitPlacedGeometry(
        ResourceGraph=ResourceGraph,
        Technology=Technology,
        FirstLegNodes=FirstLegNodes,
        FirstTrackNode=FirstTrackNode,
        BridgePosition=BridgePosition,
        Signal=Signal,
        StaticExclusionOwnersByPosition=(
            StaticExclusionOwnersByPosition
        ),
        UnownedStaticExclusions=UnownedStaticExclusions,
        PreOwnedClaimsBySignal=PreOwnedClaimsBySignal,
    ):
        return None
    BlockRoles = tuple(
        (_TransformTemplatePosition(Position, Gate), BlockRole)
        for Position, BlockRole in Template.BlockRoles
    )
    RepeaterPosition = BlockRoles[Template.RepeaterPathIndex][0]
    if Role == "Source":
        NextPosition = (
            FirstLegNodes[Template.RepeaterPathIndex + 1]
            if Template.RepeaterPathIndex + 1 < len(FirstLegNodes)
            else FirstTrackNode
        )
    else:
        NextPosition = (
            FirstLegNodes[Template.RepeaterPathIndex - 1]
            if Template.RepeaterPathIndex > 0
            else BridgePosition
        )
    RepeaterReservation = RoutingReservation(
        Signal=Signal,
        Resource=RoutingResourceId(
            RoutingResourceKind.Wire,
            RepeaterPosition,
        ),
        Position=RepeaterPosition,
        Purpose="PinAccessRepeater",
        InputFacing=RepeaterInputFacingForStep(
            RepeaterPosition,
            NextPosition,
        ),
    )
    return PlacedPinAccessOption(
        Signal=Signal,
        GateName=str(Gate.Name),
        GateKind=str(Gate.Kind).upper(),
        Role=Role,
        PinId=PinId,
        CatalogVersion=Template.CatalogVersion,
        TemplateId=Template.TemplateId,
        PatternFamily=Template.PatternFamily,
        TemplateFingerprint=Template.TemplateFingerprint,
        TemplateProofFingerprint=Template.ProofFingerprint,
        TechnologyFingerprint=Template.TechnologyFingerprint,
        ResourceModelFingerprint=ResourceModelFingerprint,
        Terminal=PhysicalTerminal,
        Face=PhysicalFace,
        Layer=Layer,
        FirstLegNodes=FirstLegNodes,
        FirstTrackNode=FirstTrackNode,
        BlockRoles=BlockRoles,
        Claims=ResourceGraph.BuildRouteClaims(FirstLegNodes),
        RepeaterReservations=(RepeaterReservation,),
        Template=Template,
    )


def EnumeratePlacedPinAccessOptionDomains(
    PlacedGates: Iterable[Any],
    *,
    ResourceGraph: RoutingResourceGraph,
    Technology: RedstoneRoutingTechnology,
    EnabledPatternFamilies: Iterable[str] = (
        "planar-jog",
        "straight",
    ),
    CatalogVersion: str = PhysicalPinAccessCatalogVersion,
    MaximumGenerationWork: int = 100_000,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    PreOwnedNodesBySignal: Mapping[
        str, Iterable[Position3]
    ] | None = None,
) -> tuple[PlacedPinAccessOptionDomain, ...]:
    """Enumerate deterministic exact option domains for placed logical pins."""
    if MaximumGenerationWork < 1:
        raise ValueError("pin-access generation work cap must be positive")
    Families = _NormalizeFamilies(EnabledPatternFamilies)
    ExpectedTechnologyFingerprint = _TechnologyFingerprint(Technology)
    if _TechnologyFingerprint(ResourceGraph.Technology) != (
        ExpectedTechnologyFingerprint
    ):
        raise ValueError("pin-access resource graph uses another technology")
    Catalog = BuildPhysicalPinAccessCatalog(
        Technology=Technology,
        EnabledPatternFamilies=Families,
        CatalogVersion=CatalogVersion,
    )
    TemplatesByPin = {}
    for Template in Catalog:
        TemplatesByPin.setdefault(
            (Template.CellKind, Template.PinId),
            [],
        ).append(Template)
    Gates = tuple(PlacedGates)
    (
        StaticExclusionOwnersByPosition,
        UnownedStaticExclusions,
    ) = _BuildPlacedStaticExclusionOwnership(
        Gates,
        ResourceGraph=ResourceGraph,
        Technology=Technology,
        PreOwnedNodesBySignal=PreOwnedNodesBySignal or {},
    )
    ResourceModelFingerprint = _ResourceModelFingerprint(
        ResourceGraph,
        StaticExclusionOwnersByPosition,
        UnownedStaticExclusions,
    )
    PreOwnedClaimsBySignal = _BuildPreOwnedClaimsBySignal(
        PreOwnedNodesBySignal or {},
        ResourceGraph,
    )
    Work = 0
    Domains = []
    for (
        Gate,
        Signal,
        Role,
        PinId,
        PhysicalTerminal,
        PhysicalFace,
    ) in _PlacedTerminalBindings(Gates):
        Generated = 0
        Rejected = 0
        Deduplicated = 0
        OptionsByFingerprint = {}
        Complete = True
        IncompleteReason = ""
        Templates = tuple(TemplatesByPin.get(
            (str(Gate.Kind).upper(), PinId),
            (),
        ))
        for Template in Templates:
            for Layer in Template.AllowedRoutingLayers:
                if Work >= MaximumGenerationWork:
                    Complete = False
                    IncompleteReason = "catalog-domain-generation-work-cap"
                    break
                Work += 1
                Option = _MaterializeOption(
                    Template,
                    Gate=Gate,
                    Signal=Signal,
                    Role=Role,
                    PinId=PinId,
                    PhysicalTerminal=PhysicalTerminal,
                    PhysicalFace=PhysicalFace,
                    Layer=Layer,
                    ResourceGraph=ResourceGraph,
                    Technology=Technology,
                    ResourceModelFingerprint=ResourceModelFingerprint,
                    StaticExclusionOwnersByPosition=(
                        StaticExclusionOwnersByPosition
                    ),
                    UnownedStaticExclusions=UnownedStaticExclusions,
                    PreOwnedClaimsBySignal=PreOwnedClaimsBySignal,
                )
                if Option is None:
                    Rejected += 1
                elif Option.PlacedBindingFingerprint in OptionsByFingerprint:
                    Deduplicated += 1
                else:
                    OptionsByFingerprint[
                        Option.PlacedBindingFingerprint
                    ] = Option
                    Generated += 1
                if WorkCheck is not None and Work % 64 == 0:
                    WorkCheck({
                        "Phase": "pin-access-domain-generation",
                        "CompletedWork": Work,
                        "MaximumGenerationWork": MaximumGenerationWork,
                        "CompletedDomainCount": len(Domains),
                        "GateName": str(Gate.Name),
                        "PinId": PinId,
                    })
            if not Complete:
                break
        if not Templates and Work < MaximumGenerationWork:
            Rejected += 1
        DomainId = BuildStableFingerprint({
            "Kind": "placed-pin-access-terminal-v1",
            "Signal": Signal,
            "GateName": str(Gate.Name),
            "Role": Role,
            "PinId": PinId,
            "Terminal": PhysicalTerminal,
        })
        Domains.append(PlacedPinAccessOptionDomain(
            DomainId=DomainId,
            Signal=Signal,
            GateName=str(Gate.Name),
            Role=Role,
            PinId=PinId,
            Terminal=PhysicalTerminal,
            Options=tuple(sorted(
                OptionsByFingerprint.values(),
                key=lambda Value: Value.RankKey(),
            )),
            Complete=Complete,
            IncompleteReason=IncompleteReason,
            CatalogVersion=CatalogVersion,
            TechnologyFingerprint=ExpectedTechnologyFingerprint,
            ResourceModelFingerprint=ResourceModelFingerprint,
            GeneratedOptionCount=Generated,
            RejectedOptionCount=Rejected,
            DeduplicatedOptionCount=Deduplicated,
            MaximumGenerationWork=MaximumGenerationWork,
        ))
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "pin-access-domain-generation-complete",
            "CompletedWork": Work,
            "MaximumGenerationWork": MaximumGenerationWork,
            "DomainCount": len(Domains),
            "Complete": all(Value.Complete for Value in Domains),
        })
    return tuple(sorted(Domains, key=lambda Value: Value.DomainId))


def FreezeSelectedPlacementPinAccessWitness(
    Domains: Iterable[PlacedPinAccessOptionDomain],
    SelectedOptionFingerprints: (
        Mapping[str, str] | Iterable[tuple[str, str]]
    ),
) -> SelectedPlacementPinAccessWitness:
    """Freeze one exact selected option for every supplied terminal domain."""
    OrderedDomains = tuple(sorted(Domains, key=lambda Value: Value.DomainId))
    if not OrderedDomains:
        raise ValueError("selected pin-access witness requires terminal domains")
    SelectedByDomain = dict(SelectedOptionFingerprints)
    if set(SelectedByDomain) != {
        Value.DomainId for Value in OrderedDomains
    }:
        raise ValueError("selected pin-access assignment does not cover domains")
    Selections = []
    for Domain in OrderedDomains:
        Fingerprint = SelectedByDomain[Domain.DomainId]
        Match = next((
            Value
            for Value in Domain.Options
            if Value.SelectionFingerprint == Fingerprint
        ), None)
        if Match is None:
            raise ValueError(
                f"selected pin-access option is absent from {Domain.DomainId}"
            )
        Selections.append(Match)
    CatalogVersions = {Value.CatalogVersion for Value in OrderedDomains}
    TechnologyFingerprints = {
        Value.TechnologyFingerprint for Value in OrderedDomains
    }
    ResourceModelFingerprints = {
        Value.ResourceModelFingerprint for Value in OrderedDomains
    }
    if (
        len(CatalogVersions) != 1
        or len(TechnologyFingerprints) != 1
        or len(ResourceModelFingerprints) != 1
    ):
        raise ValueError("selected pin-access domains use mixed dependencies")
    OrderedSelections = tuple(sorted(
        Selections,
        key=lambda Value: Value.TerminalIdentity(),
    ))
    ClaimsBySignal = []
    for Signal in sorted({Value.Signal for Value in OrderedSelections}):
        SignalOptions = tuple(
            Value for Value in OrderedSelections if Value.Signal == Signal
        )
        ClaimsBySignal.append((
            Signal,
            type(SignalOptions[0].Claims)(
                WireCells=frozenset().union(*(
                    Value.Claims.WireCells for Value in SignalOptions
                )),
                SupportCells=frozenset().union(*(
                    Value.Claims.SupportCells for Value in SignalOptions
                )),
                RequiredAirCells=frozenset().union(*(
                    Value.Claims.RequiredAirCells for Value in SignalOptions
                )),
                ElectricalCells=frozenset().union(*(
                    Value.Claims.ElectricalCells for Value in SignalOptions
                )),
            ),
        ))
    Reservations = tuple(sorted(
        (
            Reservation
            for Value in OrderedSelections
            for Reservation in Value.RepeaterReservations
        ),
        key=lambda Value: (
            Value.Signal,
            Value.Position,
            Value.Purpose,
            str(Value.InputFacing),
        ),
    ))
    return SelectedPlacementPinAccessWitness(
        CatalogVersion=next(iter(CatalogVersions)),
        TechnologyFingerprint=next(iter(TechnologyFingerprints)),
        ResourceModelFingerprint=next(iter(ResourceModelFingerprints)),
        DomainFingerprints=tuple(sorted(
            Value.DomainFingerprint for Value in OrderedDomains
        )),
        Selections=OrderedSelections,
        ClaimsBySignal=tuple(ClaimsBySignal),
        RepeaterReservations=Reservations,
        Complete=True,
        Domains=OrderedDomains,
    )


__all__ = [
    "BuildPhysicalPinAccessCatalog",
    "EnumeratePlacedPinAccessOptionDomains",
    "FreezeSelectedPlacementPinAccessWitness",
    "PhysicalPinAccessCatalogVersion",
    "SupportedPinAccessPatternFamilies",
]

"""Compile and place the finite, technology-owned pin-access catalog."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from PhysicalDesign.Cells.Library import CellMacros, PinAccessPattern
from PhysicalDesign.Contracts.Core import Position3
from PhysicalDesign.Contracts.PlacementAccess import (
    BuildPlacementAccessDomainControlsFingerprint,
    BuildPlacementAccessPatternAttemptId,
    PhysicalPinAccessTemplate,
    PlacementAccessEvaluationControls,
    PlacedPinAccessOption,
    PlacedPinAccessOptionDomain,
    PlacedPinAccessPatternAttempt,
    PlacedPinAccessPatternRequirement,
    PlacementAccessPatternAttemptReason,
    PlacementAccessPatternAttemptStatus,
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
    FreezeRoutingResourceState,
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


def BuildPinAccessTechnologyFingerprint(
    Technology: RedstoneRoutingTechnology,
) -> str:
    """Identify the exact routing technology used by pin-access proofs."""
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
        "Kind": "pin-access-resource-model-v2",
        "GraphVersion": ResourceGraph.GraphVersion,
        "TechnologyFingerprint": BuildPinAccessTechnologyFingerprint(
            ResourceGraph.Technology
        ),
        "ActualBlocks": sorted(ResourceGraph.ActualBlocks),
        "ElectricalBlocks": sorted(ResourceGraph.ElectricalBlocks),
        "SolidBlocks": sorted(ResourceGraph.SolidBlocks),
        "StaticKeepOutBlocks": sorted(ResourceGraph.StaticKeepOutBlocks),
        "BlockStates": CanonicalizeRoutingResourceBlockStates(ResourceGraph),
        "StaticExclusionOwnersByPosition": [
            (Position, tuple(sorted(Owners)))
            for Position, Owners in sorted(
                StaticExclusionOwnersByPosition.items()
            )
        ],
        "UnownedStaticExclusions": sorted(UnownedStaticExclusions),
    })


def _CanonicalPosition(Value: object, Name: str) -> Position3:
    if (
        type(Value) not in (tuple, list)
        or len(Value) != 3
        or any(type(Component) is not int for Component in Value)
    ):
        raise TypeError(f"{Name} must be an exact three-integer position")
    return tuple(Value)


def _CanonicalBlockStateValue(Value: Any) -> Any:
    """Return JSON-shaped state after the shared lower-layer freeze gate."""
    Frozen = FreezeRoutingResourceState(Value)
    if type(Frozen) is MappingProxyType:
        return {
            Key: _CanonicalBlockStateValue(Item)
            for Key, Item in Frozen.items()
        }
    if type(Frozen) is tuple:
        return tuple(_CanonicalBlockStateValue(Item) for Item in Frozen)
    return Frozen


def CanonicalizeRoutingResourceBlockStates(
    ResourceGraph: RoutingResourceGraph,
) -> tuple[tuple[Position3, Any], ...]:
    """Canonicalize complete graph state once for model and observation identities."""
    if type(ResourceGraph) is not RoutingResourceGraph:
        raise TypeError("ResourceGraph must be an exact RoutingResourceGraph")
    return tuple(sorted(
        (
            _CanonicalPosition(Position, "routing resource block-state position"),
            _CanonicalBlockStateValue(State),
        )
        for Position, State in ResourceGraph.BlockStates.items()
    ))


def CollectFrozenNetWireEntries(
    PreOwnedNodesBySignal: Mapping[str, Iterable[Position3]],
) -> tuple[tuple[str, Iterable[Position3]], ...]:
    """Materialize and validate unique logical frozen-wire mapping entries."""
    if not isinstance(PreOwnedNodesBySignal, Mapping):
        raise TypeError("pre-owned pin-access nodes must be a mapping")
    Entries = tuple(PreOwnedNodesBySignal.items())
    Signals = []
    for Signal, _Positions in Entries:
        if type(Signal) is not str:
            raise TypeError("pre-owned pin-access signals must be exact strings")
        Signals.append(Signal)
    if len(Signals) != len(set(Signals)):
        raise ValueError("pre-owned pin-access nodes repeat a signal")
    return tuple(sorted(Entries, key=lambda Value: Value[0]))


def NormalizeFrozenNetWireEntries(
    Entries: Iterable[tuple[str, Iterable[Position3]]],
) -> dict[str, tuple[Position3, ...]]:
    """Normalize already unique frozen-wire entries without losing identity."""
    Result = {}
    for Signal, Positions in Entries:
        Materialized = tuple(
            _CanonicalPosition(Position, "pre-owned pin-access node")
            for Position in Positions
        )
        if len(Materialized) != len(set(Materialized)):
            raise ValueError("pre-owned pin-access nodes repeat a position")
        Result[Signal] = tuple(sorted(Materialized))
    return Result


def NormalizeFrozenNetWires(
    PreOwnedNodesBySignal: Mapping[str, Iterable[Position3]],
) -> dict[str, tuple[Position3, ...]]:
    """Materialize each signal once, rejecting duplicate or noncanonical positions."""
    return NormalizeFrozenNetWireEntries(
        CollectFrozenNetWireEntries(PreOwnedNodesBySignal)
    )


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
    TechnologyFingerprint = BuildPinAccessTechnologyFingerprint(Technology)
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


def ValidateSelectedPlacementPinAccessBindings(
    PlacedGates: Iterable[Any],
    Selections: Iterable[Any],
) -> None:
    """Require selected access to bind every current placed terminal once."""
    CurrentBindings = _CurrentSelectedPlacementPinAccessBindings(PlacedGates)
    SelectedBindings, SelectedTerminalIdentities = (
        _SelectedPlacementPinAccessBindings(Selections)
    )
    if len(SelectedTerminalIdentities) != len(
        set(SelectedTerminalIdentities)
    ):
        raise ValueError("selected pin-access bindings repeat a terminal")
    if SelectedBindings != CurrentBindings:
        raise ValueError(
            "selected pin-access bindings do not match current placement"
        )


def _CurrentSelectedPlacementPinAccessBindings(
    PlacedGates: Iterable[Any],
) -> tuple[tuple[object, ...], ...]:
    """Return the canonical current terminal binding traversal."""
    return tuple(sorted(
        (
            Signal,
            str(Gate.Name),
            str(Gate.Kind).upper(),
            Role,
            PinId,
            Terminal,
            Face,
        )
        for Gate, Signal, Role, PinId, Terminal, Face
        in _PlacedTerminalBindings(PlacedGates)
    ))


def _SelectedPlacementPinAccessBindings(
    Selections: Iterable[Any],
) -> tuple[tuple[tuple[object, ...], ...], tuple[tuple[str, ...], ...]]:
    """Return selected bindings in exactly the current-binding encoding."""
    SelectedBindings = []
    SelectedTerminalIdentities = []
    for Selection in Selections:
        Face = getattr(Selection, "Face", None)
        if Face is None:
            Face = getattr(Selection, "ApproachDirection", None)
        if Face is None:
            raise ValueError(
                "selected pin-access binding has no terminal face"
            )
        TerminalIdentity = (
            str(Selection.Signal),
            str(Selection.GateName),
            str(Selection.Role),
            str(Selection.PinId),
        )
        SelectedTerminalIdentities.append(TerminalIdentity)
        SelectedBindings.append((
            TerminalIdentity[0],
            TerminalIdentity[1],
            str(Selection.GateKind).upper(),
            TerminalIdentity[2],
            TerminalIdentity[3],
            tuple(Selection.Terminal),
            tuple(Face),
        ))
    return (
        tuple(sorted(SelectedBindings)),
        tuple(SelectedTerminalIdentities),
    )


def _BuildSelectedPlacementPinAccessBindingFingerprint(
    Bindings: tuple[tuple[object, ...], ...],
) -> str:
    return BuildStableFingerprint({
        "Kind": "selected-placement-pin-access-binding-v1",
        "Bindings": [
            [
                Signal,
                GateName,
                GateKind,
                Role,
                PinId,
                list(Terminal),
                list(Face),
            ]
            for (
                Signal,
                GateName,
                GateKind,
                Role,
                PinId,
                Terminal,
                Face,
            ) in Bindings
        ],
    })


def BuildSelectedPlacementPinAccessBindingFingerprint(
    PlacedGates: Iterable[Any],
) -> str:
    """Fingerprint every current selected-access terminal binding canonically."""
    return _BuildSelectedPlacementPinAccessBindingFingerprint(
        _CurrentSelectedPlacementPinAccessBindings(PlacedGates)
    )


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


def _ValidateAndNormalizePreOwnedNodesBySignal(
    PreOwnedNodesBySignal: Mapping[str, Iterable[Position3]],
    *,
    ResourceGraph: RoutingResourceGraph,
) -> dict[str, tuple[Position3, ...]]:
    Result = {
        Signal: Positions
        for Signal, Positions in NormalizeFrozenNetWires(
            PreOwnedNodesBySignal
        ).items()
        if Positions
    }
    Missing = sorted(
        set().union(*Result.values())
        - ResourceGraph.ElectricalBlocks
    )
    if Missing:
        raise ValueError(
            "pre-owned pin-access nodes are absent from the resource graph"
        )
    return Result


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
    PreOwnedNodes = _ValidateAndNormalizePreOwnedNodesBySignal(
        PreOwnedNodesBySignal or {},
        ResourceGraph=ResourceGraph,
    )
    Owners, Unowned = _BuildPlacedStaticExclusionOwnership(
        PlacedGates, ResourceGraph=ResourceGraph,
        Technology=ResourceGraph.Technology,
        PreOwnedNodesBySignal=PreOwnedNodes,
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
) -> PlacementAccessPatternAttemptReason | None:
    Terminal = FirstLegNodes[0]
    if (
        Terminal in ResourceGraph.ActualBlocks
        or BridgePosition not in ResourceGraph.ActualBlocks
        or BridgePosition not in ResourceGraph.ElectricalBlocks
    ):
        return PlacementAccessPatternAttemptReason.TerminalOrBridgeUnavailable
    if (
        set(FirstLegNodes[1:])
        & set(ResourceGraph.ActualBlocks)
    ):
        return PlacementAccessPatternAttemptReason.FirstLegOccupied
    for First, Second in zip(FirstLegNodes, FirstLegNodes[1:]):
        if ResourceGraph.BuildPrimitive(First, Second) is None:
            return PlacementAccessPatternAttemptReason.PrimitiveUnavailable
    Claims = ResourceGraph.BuildRouteClaims(FirstLegNodes)
    if (
        Claims.SupportCells & ResourceGraph.ActualBlocks
        or Claims.RequiredAirCells & ResourceGraph.ActualBlocks
    ):
        return PlacementAccessPatternAttemptReason.ClaimOccupancyConflict
    ExistingSignalClaims = PreOwnedClaimsBySignal.get(Signal)
    CombinedSignalClaims = (
        ResourceGraph.BuildRouteClaims(
            Claims.WireCells | ExistingSignalClaims.WireCells
        )
        if ExistingSignalClaims is not None
        else Claims
    )
    if FindSelfClaimConflicts({Signal: CombinedSignalClaims}):
        return PlacementAccessPatternAttemptReason.SelfClaimConflict
    if any(
        FindClaimConflicts({
            Signal: CombinedSignalClaims,
            ForeignSignal: ForeignClaims,
        })
        for ForeignSignal, ForeignClaims in PreOwnedClaimsBySignal.items()
        if ForeignSignal != Signal
    ):
        return PlacementAccessPatternAttemptReason.ForeignClaimConflict
    for Position in FirstLegNodes:
        if (
            Position in ResourceGraph.StaticKeepOutBlocks
            or Position in UnownedStaticExclusions
        ):
            return PlacementAccessPatternAttemptReason.StaticKeepOut
        Owners = StaticExclusionOwnersByPosition.get(Position, frozenset())
        if Owners and Owners != frozenset({Signal}):
            return PlacementAccessPatternAttemptReason.ForeignStaticExclusion
    return None


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
) -> tuple[PlacedPinAccessOption | None, PlacementAccessPatternAttemptReason]:
    if Template.PinId != PinId or Template.CellKind != str(Gate.Kind).upper():
        raise ValueError("pin-access attempt uses a template from another pin")
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
        raise ValueError("pin-access attempt uses a disallowed routing layer")
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
    RejectionReason = _AdmitPlacedGeometry(
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
    )
    if RejectionReason is not None:
        return None, RejectionReason
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
    ), PlacementAccessPatternAttemptReason.Legal


def _SnapshotRoutingResourceGraph(
    ResourceGraph: RoutingResourceGraph,
) -> RoutingResourceGraph:
    """Detach enumeration from later mutation of its live graph input."""
    if type(ResourceGraph) is not RoutingResourceGraph:
        raise TypeError("ResourceGraph must be an exact RoutingResourceGraph")
    return RoutingResourceGraph(
        ActualBlocks=frozenset(ResourceGraph.ActualBlocks),
        ElectricalBlocks=frozenset(ResourceGraph.ElectricalBlocks),
        SolidBlocks=frozenset(ResourceGraph.SolidBlocks),
        Technology=RedstoneRoutingTechnology(**asdict(ResourceGraph.Technology)),
        GraphVersion=ResourceGraph.GraphVersion,
        StaticKeepOutBlocks=frozenset(ResourceGraph.StaticKeepOutBlocks),
        BlockStates=dict(CanonicalizeRoutingResourceBlockStates(ResourceGraph)),
    )


def _BuildDomainEvaluationInputFingerprint(
    Gates: tuple[Any, ...],
    *,
    Catalog: tuple[PhysicalPinAccessTemplate, ...],
    Families: tuple[str, ...],
    CatalogVersion: str,
    MaximumGenerationWork: int,
    TechnologyFingerprint: str,
    ResourceModelFingerprint: str,
) -> str:
    """Bind one finite catalog evaluation to current semantic inputs."""
    return BuildStableFingerprint({
        "Kind": "placed-pin-access-domain-evaluation-input-v2",
        "TerminalBindings": _CurrentSelectedPlacementPinAccessBindings(Gates),
        "CatalogVersion": CatalogVersion,
        "EnabledPatternFamilies": Families,
        "MaximumGenerationWork": MaximumGenerationWork,
        "EvaluationControlsFingerprint": (
            BuildPlacementAccessDomainControlsFingerprint(
                EnabledPatternFamilies=Families,
                CatalogVersion=CatalogVersion,
                MaximumGenerationWork=MaximumGenerationWork,
            )
        ),
        "CatalogTemplateFingerprints": sorted(
            Value.TemplateFingerprint for Value in Catalog
        ),
        "TechnologyFingerprint": TechnologyFingerprint,
        "ResourceModelFingerprint": ResourceModelFingerprint,
    })


def _BuildPatternAttempt(
    *,
    DomainId: str,
    Template: PhysicalPinAccessTemplate,
    Layer: int,
    CatalogVersion: str,
    TechnologyFingerprint: str,
    ResourceModelFingerprint: str,
    Status: PlacementAccessPatternAttemptStatus,
    Reason: PlacementAccessPatternAttemptReason,
    OptionFingerprint: str | None,
) -> PlacedPinAccessPatternAttempt:
    return PlacedPinAccessPatternAttempt(
        AttemptId=BuildPlacementAccessPatternAttemptId(
            DomainId=DomainId,
            TemplateId=Template.TemplateId,
            PatternFamily=Template.PatternFamily,
            TemplateFingerprint=Template.TemplateFingerprint,
            Layer=Layer,
            CatalogVersion=CatalogVersion,
            TechnologyFingerprint=TechnologyFingerprint,
            ResourceModelFingerprint=ResourceModelFingerprint,
        ),
        DomainId=DomainId,
        TemplateId=Template.TemplateId,
        PatternFamily=Template.PatternFamily,
        TemplateFingerprint=Template.TemplateFingerprint,
        Layer=Layer,
        CatalogVersion=CatalogVersion,
        TechnologyFingerprint=TechnologyFingerprint,
        ResourceModelFingerprint=ResourceModelFingerprint,
        Status=Status,
        Reason=Reason,
        OptionFingerprint=OptionFingerprint,
    )


def _BuildPatternRequirement(
    *,
    DomainId: str,
    Template: PhysicalPinAccessTemplate,
    Layer: int,
    CatalogVersion: str,
    TechnologyFingerprint: str,
    ResourceModelFingerprint: str,
) -> PlacedPinAccessPatternRequirement:
    AttemptId = BuildPlacementAccessPatternAttemptId(
        DomainId=DomainId,
        TemplateId=Template.TemplateId,
        PatternFamily=Template.PatternFamily,
        TemplateFingerprint=Template.TemplateFingerprint,
        Layer=Layer,
        CatalogVersion=CatalogVersion,
        TechnologyFingerprint=TechnologyFingerprint,
        ResourceModelFingerprint=ResourceModelFingerprint,
    )
    return PlacedPinAccessPatternRequirement(
        AttemptId=AttemptId,
        DomainId=DomainId,
        TemplateId=Template.TemplateId,
        PatternFamily=Template.PatternFamily,
        TemplateFingerprint=Template.TemplateFingerprint,
        Layer=Layer,
        CatalogVersion=CatalogVersion,
        TechnologyFingerprint=TechnologyFingerprint,
        ResourceModelFingerprint=ResourceModelFingerprint,
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
    WorkCheck: Callable[[dict[str, object]], bool | None] | None = None,
    PreOwnedNodesBySignal: Mapping[
        str, Iterable[Position3]
    ] | None = None,
) -> tuple[PlacedPinAccessOptionDomain, ...]:
    """Enumerate deterministic exact option domains for placed logical pins."""
    if type(MaximumGenerationWork) is not int or MaximumGenerationWork < 1:
        raise ValueError("pin-access generation work cap must be positive")
    Families = _NormalizeFamilies(EnabledPatternFamilies)
    LiveGates = tuple(PlacedGates)
    LiveResourceGraph = ResourceGraph
    LiveTechnology = Technology
    LivePreOwnedNodes = PreOwnedNodesBySignal or {}
    Gates = tuple(deepcopy(Gate) for Gate in LiveGates)
    Technology = RedstoneRoutingTechnology(**asdict(LiveTechnology))
    ResourceGraph = _SnapshotRoutingResourceGraph(LiveResourceGraph)
    ExpectedTechnologyFingerprint = BuildPinAccessTechnologyFingerprint(
        Technology
    )
    if BuildPinAccessTechnologyFingerprint(ResourceGraph.Technology) != (
        ExpectedTechnologyFingerprint
    ):
        raise ValueError("pin-access resource graph uses another technology")
    Catalog = BuildPhysicalPinAccessCatalog(
        Technology=Technology,
        EnabledPatternFamilies=Families,
        CatalogVersion=CatalogVersion,
    )
    PreOwnedNodes = _ValidateAndNormalizePreOwnedNodesBySignal(
        LivePreOwnedNodes,
        ResourceGraph=ResourceGraph,
    )
    TemplatesByPin = {}
    for Template in Catalog:
        TemplatesByPin.setdefault(
            (Template.CellKind, Template.PinId),
            [],
        ).append(Template)
    (
        StaticExclusionOwnersByPosition,
        UnownedStaticExclusions,
    ) = _BuildPlacedStaticExclusionOwnership(
        Gates,
        ResourceGraph=ResourceGraph,
        Technology=Technology,
        PreOwnedNodesBySignal=PreOwnedNodes,
    )
    ResourceModelFingerprint = _ResourceModelFingerprint(
        ResourceGraph,
        StaticExclusionOwnersByPosition,
        UnownedStaticExclusions,
    )
    PreOwnedClaimsBySignal = _BuildPreOwnedClaimsBySignal(
        PreOwnedNodes,
        ResourceGraph,
    )
    EvaluationInputFingerprint = _BuildDomainEvaluationInputFingerprint(
        Gates,
        Catalog=Catalog,
        Families=Families,
        CatalogVersion=CatalogVersion,
        MaximumGenerationWork=MaximumGenerationWork,
        TechnologyFingerprint=ExpectedTechnologyFingerprint,
        ResourceModelFingerprint=ResourceModelFingerprint,
    )
    EvaluationControlsFingerprint = (
        BuildPlacementAccessDomainControlsFingerprint(
            EnabledPatternFamilies=Families,
            CatalogVersion=CatalogVersion,
            MaximumGenerationWork=MaximumGenerationWork,
        )
    )

    def ObserveLiveInput() -> str:
        try:
            CurrentPreOwnedNodes = _ValidateAndNormalizePreOwnedNodesBySignal(
                LivePreOwnedNodes,
                ResourceGraph=LiveResourceGraph,
            )
            (
                CurrentOwners,
                CurrentUnowned,
            ) = _BuildPlacedStaticExclusionOwnership(
                LiveGates,
                ResourceGraph=LiveResourceGraph,
                Technology=LiveTechnology,
                PreOwnedNodesBySignal=CurrentPreOwnedNodes,
            )
            CurrentResourceModelFingerprint = _ResourceModelFingerprint(
                LiveResourceGraph,
                CurrentOwners,
                CurrentUnowned,
            )
            CurrentCatalog = BuildPhysicalPinAccessCatalog(
                Technology=LiveTechnology,
                EnabledPatternFamilies=Families,
                CatalogVersion=CatalogVersion,
            )
            return _BuildDomainEvaluationInputFingerprint(
                LiveGates,
                Catalog=CurrentCatalog,
                Families=Families,
                CatalogVersion=CatalogVersion,
                MaximumGenerationWork=MaximumGenerationWork,
                TechnologyFingerprint=BuildPinAccessTechnologyFingerprint(
                    LiveTechnology
                ),
                ResourceModelFingerprint=CurrentResourceModelFingerprint,
            )
        except (AttributeError, KeyError, TypeError, ValueError) as Error:
            return BuildStableFingerprint({
                "Kind": "invalid-placed-pin-access-domain-live-input-v1",
                "ErrorType": type(Error).__name__,
            })

    def CheckWorkControl(Details: dict[str, object]) -> bool | None:
        if WorkCheck is None:
            return None
        Result = WorkCheck(Details)
        if Result is not None and type(Result) is not bool:
            raise TypeError("pin-access work check must return exact Boolean or None")
        return Result

    Work = 0
    DomainRecords = []
    DeadlineReached = False
    DriftFingerprint = ""
    for (
        Gate,
        Signal,
        Role,
        PinId,
        PhysicalTerminal,
        PhysicalFace,
    ) in _PlacedTerminalBindings(Gates):
        OptionsByFingerprint = {}
        DomainId = BuildStableFingerprint({
            "Kind": "placed-pin-access-terminal-v1",
            "Signal": Signal,
            "GateName": str(Gate.Name),
            "Role": Role,
            "PinId": PinId,
            "Terminal": PhysicalTerminal,
        })
        Templates = tuple(sorted(TemplatesByPin.get(
            (str(Gate.Kind).upper(), PinId),
            (),
        ), key=lambda Value: Value.StructuralIdentity()))
        Plans = tuple(sorted(
            (
                (Template, Layer)
                for Template in Templates
                for Layer in Template.AllowedRoutingLayers
            ),
            key=lambda Value: (
                Value[0].TemplateId,
                Value[1],
                Value[0].TemplateFingerprint,
            ),
        ))
        RequiredPatternManifest = tuple(
            _BuildPatternRequirement(
                DomainId=DomainId,
                Template=Template,
                Layer=Layer,
                CatalogVersion=CatalogVersion,
                TechnologyFingerprint=ExpectedTechnologyFingerprint,
                ResourceModelFingerprint=ResourceModelFingerprint,
            )
            for Template, Layer in Plans
        )
        Attempts = []
        CoversEnabledFamilies = {
            Template.PatternFamily for Template, _Layer in Plans
        } == set(Families)
        Complete = bool(Plans) and CoversEnabledFamilies
        IncompleteReason = (
            "" if Complete else "catalog-domain-missing-certified-patterns"
        )
        for Template, Layer in Plans:
            if DriftFingerprint:
                Complete = False
                IncompleteReason = "catalog-domain-input-drift"
                Attempts.append(_BuildPatternAttempt(
                    DomainId=DomainId,
                    Template=Template,
                    Layer=Layer,
                    CatalogVersion=CatalogVersion,
                    TechnologyFingerprint=ExpectedTechnologyFingerprint,
                    ResourceModelFingerprint=ResourceModelFingerprint,
                    Status=PlacementAccessPatternAttemptStatus.NotEvaluated,
                    Reason=PlacementAccessPatternAttemptReason.InputDrift,
                    OptionFingerprint=None,
                ))
                continue
            if DeadlineReached:
                Complete = False
                IncompleteReason = "catalog-domain-generation-deadline"
                Attempts.append(_BuildPatternAttempt(
                    DomainId=DomainId,
                    Template=Template,
                    Layer=Layer,
                    CatalogVersion=CatalogVersion,
                    TechnologyFingerprint=ExpectedTechnologyFingerprint,
                    ResourceModelFingerprint=ResourceModelFingerprint,
                    Status=PlacementAccessPatternAttemptStatus.NotEvaluated,
                    Reason=PlacementAccessPatternAttemptReason.Deadline,
                    OptionFingerprint=None,
                ))
                continue
            if Work >= MaximumGenerationWork:
                Complete = False
                IncompleteReason = "catalog-domain-generation-work-cap"
                Attempts.append(_BuildPatternAttempt(
                    DomainId=DomainId,
                    Template=Template,
                    Layer=Layer,
                    CatalogVersion=CatalogVersion,
                    TechnologyFingerprint=ExpectedTechnologyFingerprint,
                    ResourceModelFingerprint=ResourceModelFingerprint,
                    Status=PlacementAccessPatternAttemptStatus.NotEvaluated,
                    Reason=PlacementAccessPatternAttemptReason.WorkCap,
                    OptionFingerprint=None,
                ))
                continue
            if WorkCheck is not None:
                WorkControl = CheckWorkControl({
                    "Phase": "pin-access-domain-generation",
                    "CompletedWork": Work,
                    "NextWork": Work + 1,
                    "MaximumGenerationWork": MaximumGenerationWork,
                    "CompletedDomainCount": len(DomainRecords),
                    "GateName": str(Gate.Name),
                    "PinId": PinId,
                    "PatternAttemptId": BuildPlacementAccessPatternAttemptId(
                        DomainId=DomainId,
                        TemplateId=Template.TemplateId,
                        PatternFamily=Template.PatternFamily,
                        TemplateFingerprint=Template.TemplateFingerprint,
                        Layer=Layer,
                        CatalogVersion=CatalogVersion,
                        TechnologyFingerprint=ExpectedTechnologyFingerprint,
                        ResourceModelFingerprint=ResourceModelFingerprint,
                    ),
                })
                if WorkControl is False:
                    DeadlineReached = True
                    Complete = False
                    IncompleteReason = "catalog-domain-generation-deadline"
                    Attempts.append(_BuildPatternAttempt(
                        DomainId=DomainId,
                        Template=Template,
                        Layer=Layer,
                        CatalogVersion=CatalogVersion,
                        TechnologyFingerprint=ExpectedTechnologyFingerprint,
                        ResourceModelFingerprint=ResourceModelFingerprint,
                        Status=PlacementAccessPatternAttemptStatus.NotEvaluated,
                        Reason=PlacementAccessPatternAttemptReason.Deadline,
                        OptionFingerprint=None,
                    ))
                    continue
                CurrentInputFingerprint = ObserveLiveInput()
                if CurrentInputFingerprint != EvaluationInputFingerprint:
                    DriftFingerprint = CurrentInputFingerprint
                    Complete = False
                    IncompleteReason = "catalog-domain-input-drift"
                    Attempts.append(_BuildPatternAttempt(
                        DomainId=DomainId,
                        Template=Template,
                        Layer=Layer,
                        CatalogVersion=CatalogVersion,
                        TechnologyFingerprint=ExpectedTechnologyFingerprint,
                        ResourceModelFingerprint=ResourceModelFingerprint,
                        Status=PlacementAccessPatternAttemptStatus.NotEvaluated,
                        Reason=PlacementAccessPatternAttemptReason.InputDrift,
                        OptionFingerprint=None,
                    ))
                    continue
            Work += 1
            Option, Reason = _MaterializeOption(
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
                Status = PlacementAccessPatternAttemptStatus.Rejected
                OptionFingerprint = None
            elif Option.PlacedBindingFingerprint in OptionsByFingerprint:
                Status = PlacementAccessPatternAttemptStatus.Deduplicated
                Reason = PlacementAccessPatternAttemptReason.DuplicateOption
                OptionFingerprint = Option.PlacedBindingFingerprint
            else:
                OptionsByFingerprint[Option.PlacedBindingFingerprint] = Option
                Status = PlacementAccessPatternAttemptStatus.Legal
                OptionFingerprint = Option.PlacedBindingFingerprint
            Attempts.append(_BuildPatternAttempt(
                DomainId=DomainId,
                Template=Template,
                Layer=Layer,
                CatalogVersion=CatalogVersion,
                TechnologyFingerprint=ExpectedTechnologyFingerprint,
                ResourceModelFingerprint=ResourceModelFingerprint,
                Status=Status,
                Reason=Reason,
                OptionFingerprint=OptionFingerprint,
            ))
        DomainRecords.append({
            "DomainId": DomainId,
            "Signal": Signal,
            "GateName": str(Gate.Name),
            "Role": Role,
            "PinId": PinId,
            "Terminal": PhysicalTerminal,
            "Options": tuple(sorted(
                OptionsByFingerprint.values(),
                key=lambda Value: Value.RankKey(),
            )),
            "Complete": Complete,
            "IncompleteReason": IncompleteReason,
            "RequiredPatternManifest": RequiredPatternManifest,
            "PatternAttempts": tuple(sorted(
                Attempts,
                key=lambda Value: Value.RankKey(),
            )),
        })
    FinalWorkControl = None
    if WorkCheck is not None:
        FinalWorkControl = CheckWorkControl({
            "Phase": "pin-access-domain-generation-complete",
            "CompletedWork": Work,
            "MaximumGenerationWork": MaximumGenerationWork,
            "DomainCount": len(DomainRecords),
            "Complete": all(Value["Complete"] for Value in DomainRecords),
        })
    FinalInputFingerprint = ObserveLiveInput()
    if FinalInputFingerprint != EvaluationInputFingerprint:
        DriftFingerprint = FinalInputFingerprint
    Domains = []
    for Record in DomainRecords:
        Complete = bool(Record["Complete"])
        IncompleteReason = str(Record["IncompleteReason"])
        if DriftFingerprint:
            Complete = False
            IncompleteReason = "catalog-domain-input-drift"
        elif FinalWorkControl is False and Complete:
            Complete = False
            IncompleteReason = "catalog-domain-generation-deadline"
        Attempts = Record["PatternAttempts"]
        Domains.append(PlacedPinAccessOptionDomain(
            DomainId=Record["DomainId"],
            Signal=Record["Signal"],
            GateName=Record["GateName"],
            Role=Record["Role"],
            PinId=Record["PinId"],
            Terminal=Record["Terminal"],
            Options=Record["Options"],
            Complete=Complete,
            IncompleteReason=IncompleteReason,
            CatalogVersion=CatalogVersion,
            TechnologyFingerprint=ExpectedTechnologyFingerprint,
            ResourceModelFingerprint=ResourceModelFingerprint,
            EnabledPatternFamilies=Families,
            RequiredPatternManifest=Record["RequiredPatternManifest"],
            PatternAttempts=Attempts,
            EvaluationControlsFingerprint=EvaluationControlsFingerprint,
            EvaluationInputFingerprint=EvaluationInputFingerprint,
            FinalInputFingerprint=FinalInputFingerprint,
            GeneratedOptionCount=sum(
                Value.Status is PlacementAccessPatternAttemptStatus.Legal
                for Value in Attempts
            ),
            RejectedOptionCount=sum(
                Value.Status is PlacementAccessPatternAttemptStatus.Rejected
                for Value in Attempts
            ),
            DeduplicatedOptionCount=sum(
                Value.Status
                is PlacementAccessPatternAttemptStatus.Deduplicated
                for Value in Attempts
            ),
            MaximumGenerationWork=MaximumGenerationWork,
        ))
    return tuple(sorted(Domains, key=lambda Value: Value.DomainId))


def ValidateCurrentPlacedPinAccessDomainEvidence(
    PlacedGates: Iterable[Any],
    Domains: Iterable[PlacedPinAccessOptionDomain],
    *,
    Technology: RedstoneRoutingTechnology,
    ResourceModelFingerprint: str,
    CurrentControls: PlacementAccessEvaluationControls,
) -> str:
    """Return an empty string only for the exact current catalog universe.

    This validates Physical producer evidence against exact caller-supplied
    current controls.  Constructing those controls from a live Joint policy
    remains the responsibility of the current-policy consumer boundary.
    """
    OrderedDomains = tuple(sorted(Domains, key=lambda Value: Value.DomainId))
    if not OrderedDomains:
        return "RequiredPatternManifestMismatch"
    if type(CurrentControls) is not PlacementAccessEvaluationControls:
        raise TypeError(
            "CurrentControls must be exact PlacementAccessEvaluationControls"
        )
    CurrentControls.__post_init__()
    First = OrderedDomains[0]
    if any(
        Domain.EnabledPatternFamilies
        != CurrentControls.EnabledPatternFamilies
        or Domain.CatalogVersion != CurrentControls.CatalogVersion
        or Domain.MaximumGenerationWork
        != CurrentControls.MaximumGenerationWork
        for Domain in OrderedDomains
    ):
        return "EvaluationControlsMismatch"
    Families = _NormalizeFamilies(CurrentControls.EnabledPatternFamilies)
    Catalog = BuildPhysicalPinAccessCatalog(
        Technology=Technology,
        EnabledPatternFamilies=Families,
        CatalogVersion=CurrentControls.CatalogVersion,
    )
    TemplatesByPin = {}
    for Template in Catalog:
        TemplatesByPin.setdefault(
            (Template.CellKind, Template.PinId),
            [],
        ).append(Template)
    ExpectedManifests = {}
    Gates = tuple(PlacedGates)
    for Gate, Signal, Role, PinId, PhysicalTerminal, _Face in (
        _PlacedTerminalBindings(Gates)
    ):
        DomainId = BuildStableFingerprint({
            "Kind": "placed-pin-access-terminal-v1",
            "Signal": Signal,
            "GateName": str(Gate.Name),
            "Role": Role,
            "PinId": PinId,
            "Terminal": PhysicalTerminal,
        })
        Plans = tuple(sorted(
            (
                (Template, Layer)
                for Template in TemplatesByPin.get(
                    (str(Gate.Kind).upper(), PinId),
                    (),
                )
                for Layer in Template.AllowedRoutingLayers
            ),
            key=lambda Value: (
                Value[0].TemplateId,
                Value[1],
                Value[0].TemplateFingerprint,
            ),
        ))
        ExpectedManifests[DomainId] = tuple(
            _BuildPatternRequirement(
                DomainId=DomainId,
                Template=Template,
                Layer=Layer,
                CatalogVersion=CurrentControls.CatalogVersion,
                TechnologyFingerprint=BuildPinAccessTechnologyFingerprint(
                    Technology
                ),
                ResourceModelFingerprint=ResourceModelFingerprint,
            )
            for Template, Layer in Plans
        )
    if set(ExpectedManifests) != {
        Domain.DomainId for Domain in OrderedDomains
    }:
        return "RequiredPatternManifestMismatch"
    if any(
        Domain.RequiredPatternManifest
        != ExpectedManifests[Domain.DomainId]
        for Domain in OrderedDomains
    ):
        return "RequiredPatternManifestMismatch"
    ExpectedInputFingerprint = _BuildDomainEvaluationInputFingerprint(
        Gates,
        Catalog=Catalog,
        Families=Families,
        CatalogVersion=CurrentControls.CatalogVersion,
        MaximumGenerationWork=CurrentControls.MaximumGenerationWork,
        TechnologyFingerprint=BuildPinAccessTechnologyFingerprint(
            Technology
        ),
        ResourceModelFingerprint=ResourceModelFingerprint,
    )
    if any(
        Domain.EvaluationInputFingerprint != ExpectedInputFingerprint
        or Domain.FinalInputFingerprint != ExpectedInputFingerprint
        for Domain in OrderedDomains
    ):
        return "DomainEvaluationInputMismatch"
    return ""


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
    "BuildPinAccessTechnologyFingerprint",
    "BuildPhysicalPinAccessCatalog",
    "BuildSelectedPlacementPinAccessBindingFingerprint",
    "CanonicalizeRoutingResourceBlockStates",
    "CollectFrozenNetWireEntries",
    "EnumeratePlacedPinAccessOptionDomains",
    "FreezeSelectedPlacementPinAccessWitness",
    "PhysicalPinAccessCatalogVersion",
    "NormalizeFrozenNetWires",
    "NormalizeFrozenNetWireEntries",
    "SupportedPinAccessPatternFamilies",
    "ValidateCurrentPlacedPinAccessDomainEvidence",
    "ValidateSelectedPlacementPinAccessBindings",
]

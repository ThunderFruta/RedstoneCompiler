"""Fresh, typed validation of a selected placement-access observation.

This module deliberately validates supplied immutable evidence only.  It never
enumerates options, selects candidates, solves domains, materializes fabric, or
routes; a receipt is therefore an observation and never a source of authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from PhysicalDesign.Contracts.PlacementAccess import (
    BuildPlacementAccessProblemFingerprint,
    CurrentSelectedPlacementAccessInputIdentity,
    CurrentSelectedPlacementAccessValidation,
    CurrentSelectedPlacementAccessValidationReason,
    CurrentSelectedPlacementAccessValidationStatus,
    PlacementAccessSolveResult,
    PlacementAccessSolveStatus,
    SelectedPlacementPinAccessWitness,
)
from PhysicalDesign.Geometry.Placement import PlacedGate, ValidatePlacedGateContract
from PhysicalDesign.Placement.Access.Catalog import (
    _BuildSelectedPlacementPinAccessBindingFingerprint,
    _SelectedPlacementPinAccessBindings,
    BuildPinAccessTechnologyFingerprint,
    BuildPlacedPinAccessModelFingerprint,
    BuildSelectedPlacementPinAccessBindingFingerprint,
    CanonicalizeRoutingResourceBlockStates,
    CollectFrozenNetWireEntries,
    NormalizeFrozenNetWireEntries,
    ValidateSelectedPlacementPinAccessBindings,
)
from PhysicalDesign.Redstone.Technology import RedstoneRoutingTechnology
from PhysicalDesign.Resources.ResourceGraph import (
    RoutingResourceGraph,
    RoutingResourceGraphVersion,
)
from PhysicalDesign.Runtime.Reliability import BuildStableFingerprint


def _Position(Value: object, Name: str) -> tuple[int, int, int]:
    if (
        type(Value) not in (tuple, list)
        or len(Value) != 3
        or any(type(Component) is not int for Component in Value)
    ):
        raise TypeError(f"{Name} must be an exact three-integer position")
    return tuple(Value)


def _TechnologySnapshot(
    Technology: RedstoneRoutingTechnology,
) -> RedstoneRoutingTechnology:
    if type(Technology) is not RedstoneRoutingTechnology:
        raise TypeError("Technology must be an exact RedstoneRoutingTechnology")
    return RedstoneRoutingTechnology(**asdict(Technology))


def _PlacedGateSnapshot(Gate: object) -> tuple[PlacedGate, dict[str, object]]:
    if type(Gate) is not PlacedGate:
        raise TypeError("PlacedGates must contain exact PlacedGate values")
    ValidatePlacedGateContract(Gate)
    if (
        type(Gate.Name) is not str
        or type(Gate.Kind) is not str
        or any(type(Value) is not int for Value in (Gate.X, Gate.Y, Gate.Z))
        or type(Gate.Rotation) is not int
        or type(Gate.MirrorX) is not bool
    ):
        raise TypeError("placed gate identity and geometry must have exact types")
    if any(type(Value) is not str for Value in (*Gate.Outputs, *Gate.Inputs)):
        raise TypeError("placed gate signals must be exact strings")
    Snapshot = PlacedGate(
        Name=Gate.Name,
        Kind=Gate.Kind,
        X=Gate.X,
        Y=Gate.Y,
        Z=Gate.Z,
        Outputs=list(Gate.Outputs),
        Inputs=list(Gate.Inputs),
        Attrs=dict(Gate.Attrs),
        InputPins=[_Position(Value, "placed input pin") for Value in Gate.InputPins],
        OutputPin=(
            _Position(Gate.OutputPin, "placed output pin")
            if Gate.OutputPin is not None else None
        ),
        Rotation=Gate.Rotation,
        MirrorX=Gate.MirrorX,
        InputDirections=[
            _Position(Value, "placed input direction")
            for Value in Gate.InputDirections
        ],
        OutputDirection=(
            _Position(Gate.OutputDirection, "placed output direction")
            if Gate.OutputDirection is not None else None
        ),
    )
    ValidatePlacedGateContract(Snapshot)
    return Snapshot, {
        "Name": Snapshot.Name,
        "Kind": Snapshot.Kind,
        "Origin": [Snapshot.X, Snapshot.Y, Snapshot.Z],
        "Outputs": list(Snapshot.Outputs),
        "Inputs": list(Snapshot.Inputs),
        "InputPins": [list(Value) for Value in Snapshot.InputPins],
        "OutputPin": list(Snapshot.OutputPin) if Snapshot.OutputPin else None,
        "Rotation": Snapshot.Rotation,
        "MirrorX": Snapshot.MirrorX,
        "InputDirections": [list(Value) for Value in Snapshot.InputDirections],
        "OutputDirection": (
            list(Snapshot.OutputDirection)
            if Snapshot.OutputDirection else None
        ),
    }


def _ResourceGraphSnapshot(
    ResourceGraph: RoutingResourceGraph,
) -> tuple[RoutingResourceGraph, dict[str, object]]:
    if type(ResourceGraph) is not RoutingResourceGraph:
        raise TypeError("ResourceGraph must be an exact RoutingResourceGraph")
    GraphTechnology = _TechnologySnapshot(ResourceGraph.Technology)
    ActualBlocks = frozenset(
        _Position(Value, "resource graph actual block")
        for Value in ResourceGraph.ActualBlocks
    )
    ElectricalBlocks = frozenset(
        _Position(Value, "resource graph electrical block")
        for Value in ResourceGraph.ElectricalBlocks
    )
    SolidBlocks = frozenset(
        _Position(Value, "resource graph solid block")
        for Value in ResourceGraph.SolidBlocks
    )
    StaticKeepOutBlocks = frozenset(
        _Position(Value, "resource graph keep-out block")
        for Value in ResourceGraph.StaticKeepOutBlocks
    )
    CanonicalBlockStates = CanonicalizeRoutingResourceBlockStates(ResourceGraph)
    BlockStates = dict(CanonicalBlockStates)
    if type(ResourceGraph.GraphVersion) is not str or not ResourceGraph.GraphVersion:
        raise TypeError("resource graph version must be a nonempty exact string")
    Snapshot = RoutingResourceGraph(
        ActualBlocks=ActualBlocks,
        ElectricalBlocks=ElectricalBlocks,
        SolidBlocks=SolidBlocks,
        Technology=GraphTechnology,
        GraphVersion=ResourceGraph.GraphVersion,
        StaticKeepOutBlocks=StaticKeepOutBlocks,
        BlockStates=BlockStates,
    )
    return Snapshot, {
        "GraphVersion": Snapshot.GraphVersion,
        "Technology": asdict(GraphTechnology),
        "ActualBlocks": [list(Value) for Value in sorted(ActualBlocks)],
        "ElectricalBlocks": [list(Value) for Value in sorted(ElectricalBlocks)],
        "SolidBlocks": [list(Value) for Value in sorted(SolidBlocks)],
        "StaticKeepOutBlocks": [
            list(Value) for Value in sorted(StaticKeepOutBlocks)
        ],
        "BlockStates": [
            [list(Position), State]
            for Position, State in CanonicalBlockStates
        ],
    }


def _RequireSupportedCurrentResourceGraph(
    ResourceGraph: RoutingResourceGraph,
) -> None:
    if type(ResourceGraph) is not RoutingResourceGraph:
        raise TypeError("ResourceGraph must be an exact RoutingResourceGraph")
    if ResourceGraph.GraphVersion != RoutingResourceGraphVersion:
        raise ValueError("current selected-access validation requires routing-resource-graph-v3")


def _FrozenNetWiresSnapshot(
    FrozenNetWires: Mapping[str, Iterable[tuple[int, int, int]]],
) -> tuple[
    dict[str, tuple[tuple[int, int, int], ...]],
    list[list[object]],
    bool,
]:
    if not isinstance(FrozenNetWires, Mapping):
        raise TypeError("FrozenNetWires must be a mapping, not None")
    Items = CollectFrozenNetWireEntries(FrozenNetWires)
    Reattestable = all(iter(Positions) is not Positions for _Signal, Positions in Items)
    Result = NormalizeFrozenNetWireEntries(Items)
    return Result, [
        [Signal, [list(Position) for Position in Positions]]
        for Signal, Positions in Result.items()
    ], Reattestable


@dataclass(frozen=True)
class _CurrentSnapshot:
    Gates: tuple[PlacedGate, ...]
    TerminalBindingFingerprint: str
    Technology: RedstoneRoutingTechnology
    TechnologyFingerprint: str
    ResourceGraph: RoutingResourceGraph
    FrozenNetWires: dict[str, tuple[tuple[int, int, int], ...]]
    FrozenNetWiresReattestable: bool
    CurrentObservationFingerprint: str
    ResourceModelFingerprint: str | None


def _TakeCurrentSnapshot(
    Gates: tuple[object, ...],
    ResourceGraph: RoutingResourceGraph,
    Technology: RedstoneRoutingTechnology,
    FrozenNetWires: Mapping[str, Iterable[tuple[int, int, int]]],
    *,
    IncludeResourceModel: bool,
) -> _CurrentSnapshot:
    _RequireSupportedCurrentResourceGraph(ResourceGraph)
    GateSnapshots = tuple(sorted(
        (_PlacedGateSnapshot(Gate) for Gate in Gates),
        key=lambda Value: BuildStableFingerprint(Value[1]),
    ))
    SnapshotGates = tuple(Value[0] for Value in GateSnapshots)
    SnapshotGraph, GraphPayload = _ResourceGraphSnapshot(ResourceGraph)
    SnapshotTechnology = _TechnologySnapshot(Technology)
    SnapshotFrozenWires, FrozenPayload, FrozenNetWiresReattestable = (
        _FrozenNetWiresSnapshot(FrozenNetWires)
    )
    TerminalBindingFingerprint = BuildSelectedPlacementPinAccessBindingFingerprint(
        SnapshotGates
    )
    Observation = BuildStableFingerprint({
        "Kind": "current-selected-placement-access-observation-v1",
        "PlacedGates": [Value[1] for Value in GateSnapshots],
        "ResourceGraph": GraphPayload,
        "Technology": asdict(SnapshotTechnology),
        "FrozenNetWires": FrozenPayload,
        "TerminalBindingFingerprint": TerminalBindingFingerprint,
    })
    return _CurrentSnapshot(
        Gates=SnapshotGates,
        TerminalBindingFingerprint=TerminalBindingFingerprint,
        Technology=SnapshotTechnology,
        TechnologyFingerprint=BuildPinAccessTechnologyFingerprint(
            SnapshotTechnology
        ),
        ResourceGraph=SnapshotGraph,
        FrozenNetWires=SnapshotFrozenWires,
        FrozenNetWiresReattestable=FrozenNetWiresReattestable,
        CurrentObservationFingerprint=Observation,
        ResourceModelFingerprint=(
            BuildPlacedPinAccessModelFingerprint(
                SnapshotGates,
                ResourceGraph=SnapshotGraph,
                PreOwnedNodesBySignal=SnapshotFrozenWires,
            )
            if IncludeResourceModel else None
        ),
    )


def _SolveResultFingerprint(Result: PlacementAccessSolveResult) -> str:
    Document = Result.ToDictionary()
    Fingerprint = Document["ResultFingerprint"]
    if type(Fingerprint) is not str or not Fingerprint:
        raise ValueError("placement-access solve result fingerprint is invalid")
    return Fingerprint


def _InputIdentity(
    Snapshot: _CurrentSnapshot,
    SolveResultFingerprint: str,
    Witness: SelectedPlacementPinAccessWitness | None,
    *,
    FinalObservationFingerprint: str | None = None,
) -> CurrentSelectedPlacementAccessInputIdentity:
    return CurrentSelectedPlacementAccessInputIdentity(
        TerminalBindingFingerprint=Snapshot.TerminalBindingFingerprint,
        WitnessFingerprint=(
            Witness.WitnessFingerprint if Witness is not None else None
        ),
        DomainFingerprint=(Witness.DomainFingerprint if Witness is not None else None),
        SolveResultFingerprint=SolveResultFingerprint,
        WitnessCatalogVersion=(Witness.CatalogVersion if Witness is not None else None),
        TechnologyFingerprint=Snapshot.TechnologyFingerprint,
        ResourceModelFingerprint=Snapshot.ResourceModelFingerprint or "",
        CurrentObservationFingerprint=Snapshot.CurrentObservationFingerprint,
        FinalObservationFingerprint=FinalObservationFingerprint,
    )


def _Publish(
    Status: CurrentSelectedPlacementAccessValidationStatus,
    Reason: CurrentSelectedPlacementAccessValidationReason,
    Identity: CurrentSelectedPlacementAccessInputIdentity,
    *,
    LiveGates: tuple[object, ...],
    ResourceGraph: RoutingResourceGraph,
    Technology: RedstoneRoutingTechnology,
    FrozenNetWires: Mapping[str, Iterable[tuple[int, int, int]]],
) -> CurrentSelectedPlacementAccessValidation:
    FinalSnapshot = _TakeCurrentSnapshot(
        LiveGates,
        ResourceGraph,
        Technology,
        FrozenNetWires,
        IncludeResourceModel=False,
    )
    if (
        FinalSnapshot.CurrentObservationFingerprint
        != Identity.CurrentObservationFingerprint
    ):
        Identity = CurrentSelectedPlacementAccessInputIdentity(
            TerminalBindingFingerprint=Identity.TerminalBindingFingerprint,
            WitnessFingerprint=Identity.WitnessFingerprint,
            DomainFingerprint=Identity.DomainFingerprint,
            SolveResultFingerprint=Identity.SolveResultFingerprint,
            WitnessCatalogVersion=Identity.WitnessCatalogVersion,
            TechnologyFingerprint=Identity.TechnologyFingerprint,
            ResourceModelFingerprint=Identity.ResourceModelFingerprint,
            CurrentObservationFingerprint=Identity.CurrentObservationFingerprint,
            FinalObservationFingerprint=(
                FinalSnapshot.CurrentObservationFingerprint
            ),
        )
        Status = CurrentSelectedPlacementAccessValidationStatus.Mismatch
        Reason = (
            CurrentSelectedPlacementAccessValidationReason.
            CurrentInputChangedDuringValidation
        )
    return CurrentSelectedPlacementAccessValidation(
        Status=Status,
        Reason=Reason,
        InputIdentity=Identity,
    )


def ValidateCurrentSelectedPlacementAccess(
    PlacedGates: Iterable[PlacedGate],
    SelectedWitness: SelectedPlacementPinAccessWitness | None,
    SolveResult: PlacementAccessSolveResult,
    *,
    ResourceGraph: RoutingResourceGraph,
    Technology: RedstoneRoutingTechnology,
    FrozenNetWires: Mapping[str, Iterable[tuple[int, int, int]]],
) -> CurrentSelectedPlacementAccessValidation:
    """Re-attest supplied source evidence against one immutable current snapshot.

    A decoded or stored receipt is never sufficient: every consumer must call
    this function again with the full witness, solve result, and current inputs.
    """
    if type(SolveResult) is not PlacementAccessSolveResult:
        raise TypeError("SolveResult must be an exact PlacementAccessSolveResult")
    if SelectedWitness is not None and type(SelectedWitness) is not SelectedPlacementPinAccessWitness:
        raise TypeError("SelectedWitness must be an exact selected witness or None")
    LiveGates = tuple(PlacedGates)
    _RequireSupportedCurrentResourceGraph(ResourceGraph)
    Snapshot = _TakeCurrentSnapshot(
        LiveGates,
        ResourceGraph,
        Technology,
        FrozenNetWires,
        IncludeResourceModel=True,
    )
    if Snapshot.ResourceModelFingerprint is None:
        raise ValueError("current selected-access snapshot has no resource identity")
    SolveResult.__post_init__()
    if SolveResult.SelectedWitness is not None:
        if type(SolveResult.SelectedWitness) is not SelectedPlacementPinAccessWitness:
            raise TypeError("solve result selected witness must be exact")
        SolveResult.SelectedWitness.__post_init__()
    if SelectedWitness is not None:
        SelectedWitness.__post_init__()
    SolveFingerprint = _SolveResultFingerprint(SolveResult)

    def Publish(
        Status: CurrentSelectedPlacementAccessValidationStatus,
        Reason: CurrentSelectedPlacementAccessValidationReason,
        Witness: SelectedPlacementPinAccessWitness | None,
    ) -> CurrentSelectedPlacementAccessValidation:
        if not Snapshot.FrozenNetWiresReattestable:
            raise TypeError(
                "FrozenNetWires contains a one-shot iterable that cannot be re-attested"
            )
        return _Publish(
            Status,
            Reason,
            _InputIdentity(Snapshot, SolveFingerprint, Witness),
            LiveGates=LiveGates,
            ResourceGraph=ResourceGraph,
            Technology=Technology,
            FrozenNetWires=FrozenNetWires,
        )

    if SolveResult.Status is PlacementAccessSolveStatus.Incomplete:
        if SelectedWitness is not None:
            raise ValueError("incomplete solve requires no selected witness")
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Unresolved,
            CurrentSelectedPlacementAccessValidationReason.IncompleteSolve,
            None,
        )
    if SolveResult.Status is PlacementAccessSolveStatus.Unsatisfiable:
        if SelectedWitness is not None:
            raise ValueError("unsatisfiable solve requires no selected witness")
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Unresolved,
            CurrentSelectedPlacementAccessValidationReason.UnsatisfiableSolve,
            None,
        )
    if SelectedWitness is None or SolveResult.SelectedWitness is None:
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Mismatch,
            CurrentSelectedPlacementAccessValidationReason.SolveWitnessMismatch,
            None,
        )
    if (
        SolveResult.SelectedWitness != SelectedWitness
        or SolveResult.SelectedWitness.WitnessFingerprint
        != SelectedWitness.WitnessFingerprint
    ):
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Mismatch,
            CurrentSelectedPlacementAccessValidationReason.SolveWitnessMismatch,
            SelectedWitness,
        )
    if SolveResult.Domains != SelectedWitness.Domains:
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Mismatch,
            CurrentSelectedPlacementAccessValidationReason.SolveDomainEvidenceMismatch,
            SelectedWitness,
        )
    if not SelectedWitness.Domains:
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Unresolved,
            CurrentSelectedPlacementAccessValidationReason.MissingDomainEvidence,
            SelectedWitness,
        )
    if any(not Domain.Complete for Domain in SelectedWitness.Domains):
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Unresolved,
            CurrentSelectedPlacementAccessValidationReason.IncompleteDomainEvidence,
            SelectedWitness,
        )
    if (
        BuildPlacementAccessProblemFingerprint(SolveResult.Domains)
        != SolveResult.ProblemFingerprint
    ):
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Mismatch,
            CurrentSelectedPlacementAccessValidationReason.SolveProblemFingerprintMismatch,
            SelectedWitness,
        )
    SelectedBindingFingerprint = _BuildSelectedPlacementPinAccessBindingFingerprint(
        _SelectedPlacementPinAccessBindings(SelectedWitness.Selections)[0]
    )
    if SelectedBindingFingerprint != Snapshot.TerminalBindingFingerprint:
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Mismatch,
            CurrentSelectedPlacementAccessValidationReason.TerminalBindingsMismatch,
            SelectedWitness,
        )
    ValidateSelectedPlacementPinAccessBindings(
        Snapshot.Gates,
        SelectedWitness.Selections,
    )
    if Snapshot.ResourceGraph.Technology != Snapshot.Technology:
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Mismatch,
            CurrentSelectedPlacementAccessValidationReason.ResourceGraphTechnologyMismatch,
            SelectedWitness,
        )
    if SelectedWitness.TechnologyFingerprint != Snapshot.TechnologyFingerprint:
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Mismatch,
            CurrentSelectedPlacementAccessValidationReason.TechnologyFingerprintMismatch,
            SelectedWitness,
        )
    if SelectedWitness.ResourceModelFingerprint != Snapshot.ResourceModelFingerprint:
        return Publish(
            CurrentSelectedPlacementAccessValidationStatus.Mismatch,
            CurrentSelectedPlacementAccessValidationReason.ResourceModelFingerprintMismatch,
            SelectedWitness,
        )
    return Publish(
        CurrentSelectedPlacementAccessValidationStatus.Verified,
        CurrentSelectedPlacementAccessValidationReason.Current,
        SelectedWitness,
    )


__all__ = ["ValidateCurrentSelectedPlacementAccess"]

"""Importable helpers used by final placement commit phases."""

from __future__ import annotations

from collections import deque
from math import ceil
from typing import Any
from Compiler.Placement.Rotation import RotatedCellSize
from Compiler.Placement.Geometry import BuildPlacedGate
from Compiler.Placement.PreRouteInterface import SolveDerivedPerimeterSlotDomain
from Compiler.Routing.Technology import DefaultRedstoneRoutingTechnology
from Compiler.Routing.Actions.Validation import BuildPhysicalGraphs, ValidatePhysicalRoutes
from Compiler.Routing.ResourceGraph import FindClaimConflicts, LocalRouteClaim
from .Clustering import PcbGatesConflict
from .Compactness import BuildDerivedPinAlignmentOffsets
from .Constraints import ExactStatePlacedGateGeometry
from .Costs import BuildInterClusterBoundaryDemand, BuildInterClusterGapPlan
from .MandatoryAccess import BuildDerivedPerimeterTerminalSlotDomain
from functools import partial
from .CommitState import (
    PlacementCommitState,
    SetPlacementCommitState,
)


def CheckWork(Context, Phase: str, **Diagnostics: object) -> None:
    if Context.WorkCheck is not None:
        Context.WorkCheck({'Phase': Phase, **Diagnostics})


def StackEndpoints(Context, StackId: int) -> tuple[int, int]:
    Values = Context.StackMembers[StackId]
    return (Values[0], Values[-1])


def AddCluster(Context, StackId: int, Endpoint: int, Candidate: int) -> None:
    Members = Context.StackMembers[StackId]
    if len(Members) >= Context.MaximumClusterStack:
        return
    if Endpoint == Members[0]:
        Members.insert(0, Candidate)
        Context.Assignment[Candidate] = Context.Assignment[Members[1]]
    elif Endpoint == Members[-1]:
        Members.append(Candidate)
        Context.Assignment[Candidate] = Context.Assignment[Members[-2]]
    else:
        raise ValueError('Cannot stack cluster on a non-endpoint')
    Context.StackByCluster[Candidate] = StackId


def MergeStacks(Context, SourceStack: int, SourceEndpoint: int, RightStack: int, TargetEndpoint: int) -> None:
    SourceMembers = Context.StackMembers[SourceStack]
    TargetMembers = Context.StackMembers[RightStack]
    if len(SourceMembers) + len(TargetMembers) > Context.MaximumClusterStack:
        return
    BestMerge: tuple[int, ...] | None = None
    for OrientedSource in (tuple(SourceMembers), tuple(reversed(SourceMembers))):
        if SourceEndpoint not in OrientedSource:
            continue
        if OrientedSource[-1] != SourceEndpoint:
            continue
        for OrientedTarget in (tuple(TargetMembers), tuple(reversed(TargetMembers))):
            if TargetEndpoint not in OrientedTarget:
                continue
            if OrientedTarget[0] != TargetEndpoint:
                continue
            CandidateStack = OrientedSource + OrientedTarget[1:]
            if len(set(CandidateStack)) != len(CandidateStack):
                continue
            if BestMerge is None or CandidateStack < BestMerge:
                BestMerge = CandidateStack
    if BestMerge is None:
        return
    Context.StackMembers[SourceStack] = list(BestMerge)
    for Member in BestMerge:
        Context.StackByCluster[Member] = SourceStack
        Context.Assignment[Member] = Context.Assignment[SourceMembers[0]]
    del Context.StackMembers[RightStack]


def FindExactStateConflict(Context, State: dict[str, object], StateOrdinal: int, StateCount: int) -> tuple[dict[int, ClusterLayoutVariant], tuple[Any, Any] | None, dict[str, dict[int, int]], dict[int, tuple[int, int]], tuple[dict[str, object], ...], tuple[ExactStatePlacedGateGeometry, ...]]:
    StateSlots = {int(Index): tuple(Slot) for Index, Slot in dict(State['Slots']).items()}
    StateVariants = {ClusterIndex: Context.VariantByTransform[ClusterIndex][int(dict(State['Transforms'])[str(ClusterIndex)]['Rotation']), bool(dict(State['Transforms'])[str(ClusterIndex)]['MirrorX'])] for ClusterIndex in range(len(Context.Clusters))}

    def BuildStateGeometry() -> tuple[int, int, dict[int, int], dict[int, int], dict[int, int], dict[int, int]]:
        StateColumnCount = max((Slot[0] for Slot in StateSlots.values()), default=-1) + 1
        StateRowCount = max((Slot[1] for Slot in StateSlots.values()), default=-1) + 1
        StateColumnWidths = {Column: max((StateVariants[Index].Width for Index, Slot in StateSlots.items() if Slot[0] == Column), default=1) for Column in range(StateColumnCount)}
        StateRowDepths = {Row: max((StateVariants[Index].Depth for Index, Slot in StateSlots.items() if Slot[1] == Row), default=1) for Row in range(StateRowCount)}
        StateGapPlan = BuildInterClusterGapPlan(BuildInterClusterBoundaryDemand(Context.Module, Context.Clusters, StateSlots, WorkCheck=Context.WorkCheck), ColumnCount=StateColumnCount, RowCount=StateRowCount, RoutingSpacing=Context.RoutingSpacing, TrackPitch=Context.ExactScreenTrackPitch, Enabled=Context.ExactScreenDemandSpacing)
        return (StateColumnCount, StateRowCount, StateColumnWidths, StateRowDepths, StateGapPlan.ColumnSpacingByBoundary(), StateGapPlan.RowSpacingByBoundary())
    StateColumnCount, StateRowCount, StateColumnWidths, StateRowDepths, StateColumnExtra, StateRowExtra = BuildStateGeometry()

    def BuildStateGates() -> list[tuple[int, Any]]:
        StateColumnOrigins: dict[int, int] = {}
        NextStateX = 0
        for Column in range(StateColumnCount):
            StateColumnOrigins[Column] = NextStateX
            NextStateX += StateColumnWidths[Column]
            if Column + 1 < StateColumnCount:
                NextStateX += Context.ColumnGap + StateColumnExtra[Column]
        StateRowOrigins: dict[int, int] = {}
        NextStateZ = 0
        for Row in range(StateRowCount):
            StateRowOrigins[Row] = NextStateZ
            NextStateZ += StateRowDepths[Row]
            if Row + 1 < StateRowCount:
                NextStateZ += Context.RowGap + StateRowExtra[Row]
        StateGates: list[tuple[int, Any]] = []
        for ClusterIndex, Names in enumerate(Context.Clusters):
            SlotX, SlotZ = StateSlots[ClusterIndex]
            Variant = StateVariants[ClusterIndex]
            for Name in Names:
                LocalX, LocalZ = Variant.Positions[Name]
                StateGates.append((ClusterIndex, BuildPlacedGate(Context.InternalByName[Name], StateColumnOrigins[SlotX] + LocalX, 1 + Context.ClusterStackLevels[ClusterIndex] * Context.PackingPolicy.ClusterDeckPitch, StateRowOrigins[SlotZ] + LocalZ, Variant.Rotations[Name], Variant.Mirrors[Name])))
        return StateGates
    PairChecks = 0
    LastConflict: tuple[int, Any, int, Any] | None = None
    ExactSlotRepairs: list[dict[str, object]] = []
    for Attempt in range(32):
        CheckWork(Context, 'joint-exact-screen-clearance', CandidateIndex=State['CandidateIndex'], CandidateOrdinal=StateOrdinal, CandidateCount=StateCount, Attempt=Attempt, PairChecks=PairChecks)
        StateGates = BuildStateGates()
        Conflict = None
        for GateIndex, (FirstCluster, First) in enumerate(StateGates):
            for SecondCluster, Second in StateGates[GateIndex + 1:]:
                if FirstCluster == SecondCluster:
                    continue
                PairChecks += 1
                if PairChecks % 128 == 0:
                    CheckWork(Context, 'joint-exact-screen-pairs', CandidateIndex=State['CandidateIndex'], CandidateOrdinal=StateOrdinal, CandidateCount=StateCount, Attempt=Attempt, PairChecks=PairChecks)
                if PcbGatesConflict(First, Second):
                    Conflict = (FirstCluster, First, SecondCluster, Second)
                    break
            if Conflict is not None:
                break
        if Conflict is None:
            return (StateVariants, None, {'Columns': dict(StateColumnExtra), 'Rows': dict(StateRowExtra)}, dict(StateSlots), tuple(ExactSlotRepairs), tuple((ExactStatePlacedGateGeometry.FromPlacedGate(Gate) for _ClusterIndex, Gate in StateGates)))
        LastConflict = Conflict
        FirstCluster, First, SecondCluster, Second = Conflict
        FirstSlot = StateSlots[FirstCluster]
        SecondSlot = StateSlots[SecondCluster]
        if FirstSlot[0] != SecondSlot[0]:
            StateColumnExtra[min(FirstSlot[0], SecondSlot[0])] += 1
            continue
        if FirstSlot[1] != SecondSlot[1]:
            StateRowExtra[min(FirstSlot[1], SecondSlot[1])] += 1
            continue
        RelocatedCluster = max(FirstCluster, SecondCluster)
        PreviousSlot = StateSlots[RelocatedCluster]
        NewSlot = (max((Slot[0] for Slot in StateSlots.values()), default=-1) + 1, PreviousSlot[1])
        StateSlots[RelocatedCluster] = NewSlot
        ExactSlotRepairs.append({'Attempt': Attempt, 'RelocatedCluster': RelocatedCluster, 'FromSlot': list(PreviousSlot), 'ToSlot': list(NewSlot), 'ConflictClusters': sorted((FirstCluster, SecondCluster)), 'ConflictMembers': [First.Name, Second.Name]})
        StateColumnCount, StateRowCount, StateColumnWidths, StateRowDepths, StateColumnExtra, StateRowExtra = BuildStateGeometry()
        continue
    if LastConflict is None:
        raise AssertionError('Exact joint screen exhausted without a conflict record')
    _FirstCluster, First, _SecondCluster, Second = LastConflict
    return (StateVariants, (First, Second), {'Columns': dict(StateColumnExtra), 'Rows': dict(StateRowExtra)}, dict(StateSlots), tuple(ExactSlotRepairs), ())


def CheckMandatoryAccessScreen(Context, Diagnostics: dict[str, object]) -> None:
    CheckWork(Context, str(Diagnostics.get('Phase', 'joint-exact-mandatory-access-profile')), **{Key: Value for Key, Value in Diagnostics.items() if Key != 'Phase'})


def PlaceTerminalBank(Context, Gates: list[Any], BankZ: int, OutwardStep: int, PortNames: list[str], LocalizeByInternalPins: bool=False) -> None:
    """Place a legal terminal bank, optionally ordered by internal pins."""
    PortIndexes = {Signal: Index for Index, Signal in enumerate(PortNames)}

    def TerminalSignal(Gate: Any) -> str:
        return Gate.Outputs[0] if Gate.Kind.value == 'INPUT' else Gate.Inputs[0]
    InternalPinsBySignal: dict[str, list[tuple[int, int, int]]] = {}
    InternalOutputsBySignal: dict[str, tuple[int, int, int]] = {}
    if LocalizeByInternalPins:
        for Existing in Context.PlacedGates:
            for InputIndex, Signal in enumerate(Existing.Inputs):
                InternalPinsBySignal.setdefault(Signal, []).append(Existing.InputPins[InputIndex])
            if Existing.OutputPin is not None:
                for Signal in Existing.Outputs:
                    InternalOutputsBySignal[Signal] = Existing.OutputPin

    def TerminalAnchorX(Gate: Any) -> int:
        Signal = TerminalSignal(Gate)
        Pins = InternalPinsBySignal.get(Signal, ()) if Gate.Kind.value == 'INPUT' else (InternalOutputsBySignal[Signal],) if Signal in InternalOutputsBySignal else ()
        if not Pins:
            return PortIndexes[Signal]
        Values = sorted((Pin[0] for Pin in Pins))
        return Values[(len(Values) - 1) // 2]
    Ordered = sorted(Gates, key=lambda Gate: (TerminalAnchorX(Gate) if LocalizeByInternalPins else PortIndexes[TerminalSignal(Gate)], PortIndexes[TerminalSignal(Gate)], Gate.Name))
    if LocalizeByInternalPins and Ordered:
        AnchorXs = [TerminalAnchorX(Gate) for Gate in Ordered]
        CenterX = (min(AnchorXs) + max(AnchorXs)) // 2
        LocalizedSpacing = max(3 + Context.RoutingSpacing, ceil((max(AnchorXs) - min(AnchorXs)) / max(1, len(Ordered) - 1)))
        TerminalSpacings = (LocalizedSpacing, LocalizedSpacing + 1)
    else:
        CenterX = (Context.InternalMinimumX + Context.InternalMaximumX) // 2
        TerminalSpacings = (4 + Context.RoutingSpacing, 3 + Context.RoutingSpacing) if Context.PackedMode and Context.PlacementPolicy is not None and Context.PlacementPolicy.PreferWideTerminalBanks else (2, 3) if Context.PackedMode else (4 + Context.RoutingSpacing, 3 + Context.RoutingSpacing) if Context.PlacementPolicy is not None and Context.PlacementPolicy.PreferWideTerminalBanks else (3 + Context.RoutingSpacing, 4 + Context.RoutingSpacing)
    for Spacing in TerminalSpacings:
        CheckWork(Context, 'terminal-bank-spacing', Spacing=Spacing)
        BankWidth = max(1, 1 + Spacing * (len(Ordered) - 1))
        StartX = CenterX - BankWidth // 2 + (Context.PlacementPolicy.TerminalBankOffsetX if Context.PlacementPolicy is not None and Ordered and (Ordered[0].Kind.value == 'INPUT') else 0)
        for Setback in range(32):
            CheckWork(Context, 'terminal-bank-setback', Spacing=Spacing, Setback=Setback)
            CandidateZ = BankZ + Setback * OutwardStep
            Terminals = [BuildPlacedGate(Gate, StartX + Index * Spacing, 1, CandidateZ, 0, False) for Index, Gate in enumerate(Ordered)]
            ConflictsWithPlacement = any((PcbGatesConflict(Terminal, Existing) for Terminal in Terminals for Existing in Context.PlacedGates))
            ConflictsWithinBank = any((PcbGatesConflict(First, Second) for Index, First in enumerate(Terminals) for Second in Terminals[Index + 1:]))
            if ConflictsWithPlacement or ConflictsWithinBank:
                continue
            Context.PlacedGates.extend(Terminals)
            return
    raise ValueError('Could not place grouped terminal bank legally')


def PlaceLocalizedTerminals(Context, Gates: list[Any], PortIndexes: dict[str, int]) -> list[Any] | None:
    """Place packed-mode I/O on the exterior shell of the NAND fabric."""
    PlacedMinimumX = min((Gate.X for Gate in Context.PlacedGates))
    PlacedMaximumX = max((Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] for Gate in Context.PlacedGates))
    PlacedMinimumZ = min((Gate.Z for Gate in Context.PlacedGates))
    PlacedMaximumZ = max((Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] for Gate in Context.PlacedGates))
    Producers = {Signal: Gate for Gate in Context.PlacedGates if Gate.OutputPin is not None for Signal in Gate.Outputs}
    Targets: dict[str, list[tuple[int, int, int]]] = {}
    for Existing in Context.PlacedGates:
        for InputIndex, Signal in enumerate(Existing.Inputs):
            Targets.setdefault(Signal, []).append(Existing.InputPins[InputIndex])

    def TerminalKind(Gate: Any) -> str:
        Kind = getattr(Gate, 'Kind', '')
        return str(getattr(Kind, 'value', Kind))

    def TerminalSignal(Gate: Any) -> str:
        return Gate.Outputs[0] if TerminalKind(Gate) == 'INPUT' else Gate.Inputs[0]
    if Context.UseDerivedSingleComponentPlacement:
        DesiredPinsByTerminal = {Gate.Name: tuple(Targets.get(TerminalSignal(Gate), ()) if TerminalKind(Gate) == 'INPUT' else (Producers[TerminalSignal(Gate)].OutputPin,) if TerminalSignal(Gate) in Producers and Producers[TerminalSignal(Gate)].OutputPin is not None else ()) for Gate in Gates}
        Context.DerivedPerimeterSlotDomainValue = BuildDerivedPerimeterTerminalSlotDomain(Gates, Context.PlacedGates, DesiredPinsByTerminal, WorkCheck=partial(CheckWork, Context))
        Context.DerivedPerimeterSlotAssignmentValue = SolveDerivedPerimeterSlotDomain(Context.DerivedPerimeterSlotDomainValue, Context.TerminalPlacementPolicy.MaximumTerminalAssignmentExpansions, WorkCheck=partial(CheckWork, Context))
        if not Context.DerivedPerimeterSlotAssignmentValue.Success:
            return None
        TerminalGateByName = {Gate.Name: Gate for Gate in Gates}
        return [BuildPlacedGate(TerminalGateByName[Slot.TerminalName], *Slot.Origin, Slot.Rotation, Slot.MirrorX) for Slot in Context.DerivedPerimeterSlotAssignmentValue.SelectedSlots]

    def TerminalCluster(Gate: Any) -> int | None:
        Signal = TerminalSignal(Gate)
        if TerminalKind(Gate) == 'INPUT':
            CandidateClusters = {Context.ClusterByGate[Consumer.Name] for Consumer in Context.TerminalConsumers.get(Signal, ()) if Consumer.Name in Context.ClusterByGate}
        else:
            Producer = Producers.get(Signal)
            CandidateClusters: set[int] = set()
            if Producer is not None and Producer.Name in Context.ClusterByGate:
                CandidateClusters.add(Context.ClusterByGate[Producer.Name])
        return min(CandidateClusters) if CandidateClusters else None

    def TerminalOrderKey(Value: Any) -> tuple[Any, ...]:
        ClusterIndex = TerminalCluster(Value)
        return (ClusterIndex is None, ClusterIndex if ClusterIndex is not None else 10 ** 6, PortIndexes[TerminalSignal(Value)], Value.Name)

    def CandidateExteriorFace(Candidate: Any) -> str:
        """Classify the shell face reached by one legal terminal cell."""
        Width, Depth = RotatedCellSize(Candidate.Kind, Candidate.Rotation)
        MaximumX = Candidate.X + Width - 1
        MaximumZ = Candidate.Z + Depth - 1
        if MaximumZ < Context.InternalMinimumZ:
            return 'north'
        if Candidate.Z > Context.InternalMaximumZ:
            return 'south'
        if MaximumX < Context.InternalMinimumX:
            return 'west'
        return 'east'
    PreCutInternalSignalsByTerminal: dict[str, frozenset[str]] = {}
    for First, Second in Context.AssignmentConstraints.PairwiseConflictEdges:
        FirstSignal = str(First)
        SecondSignal = str(Second)
        if FirstSignal in PortIndexes and SecondSignal not in PortIndexes and (FirstSignal != SecondSignal):
            PreCutInternalSignalsByTerminal[FirstSignal] = frozenset({*PreCutInternalSignalsByTerminal.get(FirstSignal, frozenset()), SecondSignal})
        if SecondSignal in PortIndexes and FirstSignal not in PortIndexes and (FirstSignal != SecondSignal):
            PreCutInternalSignalsByTerminal[SecondSignal] = frozenset({*PreCutInternalSignalsByTerminal.get(SecondSignal, frozenset()), FirstSignal})
    PreInternalPinsBySignal: dict[str, set[tuple[int, int, int]]] = {}
    for Existing in Context.PlacedGates:
        if Existing.OutputPin is not None:
            for Signal in Existing.Outputs:
                PreInternalPinsBySignal.setdefault(str(Signal), set()).add(Existing.OutputPin)
        for InputIndex, Signal in enumerate(Existing.Inputs):
            PreInternalPinsBySignal.setdefault(str(Signal), set()).add(Existing.InputPins[InputIndex])
    PreCutTerminalPinSpacing = 3 + Context.RoutingSpacing if Context.TerminalPlacementPolicy.EnableJointClusterOrientation and PreCutInternalSignalsByTerminal else 0
    TypedTerminalPlacementPressure = Context.TerminalPlacementPolicy.EnableJointClusterOrientation or (Context.PlacementPolicy is not None and Context.PlacementPolicy.PreferWideTerminalBanks)
    PreferTerminalRoutingCost = TypedTerminalPlacementPressure and len(Context.RelocationPrioritySignals) >= 3
    OptionsByGate: list[tuple[str, list[tuple[tuple[Any, ...], Any]]]] = []
    for Gate in sorted(Gates, key=TerminalOrderKey):
        CheckWork(Context, 'localized-terminal', GateName=Gate.Name)
        Signal = TerminalSignal(Gate)
        DesiredPins = Targets.get(Signal, []) if TerminalKind(Gate) == 'INPUT' else [Producers[Signal].OutputPin]
        TargetXs = sorted((Pin[0] for Pin in DesiredPins))
        TargetZs = sorted((Pin[2] for Pin in DesiredPins))
        TargetMiddle = len(DesiredPins) // 2
        MedianAnchor = ((TargetXs[(len(TargetXs) - 1) // 2] + TargetXs[TargetMiddle]) // 2, DesiredPins[0][1], (TargetZs[(len(TargetZs) - 1) // 2] + TargetZs[TargetMiddle]) // 2)
        if Context.UseDerivedSingleComponentPlacement:
            PinY = DesiredPins[0][1]
            CandidatePinPositions = {*((X, PinY, Context.InternalMinimumZ - 1) for X in range(Context.InternalMinimumX, Context.InternalMaximumX + 1)), *((X, PinY, Context.InternalMaximumZ + 1) for X in range(Context.InternalMinimumX, Context.InternalMaximumX + 1)), *((Context.InternalMinimumX - 1, PinY, Z) for Z in range(Context.InternalMinimumZ, Context.InternalMaximumZ + 1)), *((Context.InternalMaximumX + 1, PinY, Z) for Z in range(Context.InternalMinimumZ, Context.InternalMaximumZ + 1))}
        else:
            CandidatePinPositions = {(Pin[0] + DeltaX, Pin[1], Pin[2] + DeltaZ) for Pin in DesiredPins for DeltaX, DeltaZ in BuildDerivedPinAlignmentOffsets()}
        if len(DesiredPins) > 1 and (not Context.UseDerivedSingleComponentPlacement):
            CandidatePinPositions.update(((MedianAnchor[0] + DeltaX, MedianAnchor[1], MedianAnchor[2] + DeltaZ) for DeltaX in range(-3, 4) for DeltaZ in range(-3, 4) if abs(DeltaX) + abs(DeltaZ) <= 3))
        if not Context.UseDerivedSingleComponentPlacement:
            ShellAnchors = (*DesiredPins, MedianAnchor)
            ShellClearance = Context.TerminalPlacementPolicy.TerminalShellClearance
            ShellLateralSearch = Context.TerminalPlacementPolicy.TerminalShellLateralSearch + (PreCutTerminalPinSpacing if Signal in PreCutInternalSignalsByTerminal else 0)
            ShellZ = Context.InternalMinimumZ - ShellClearance if TerminalKind(Gate) == 'INPUT' else Context.InternalMaximumZ + ShellClearance
            for Anchor in ShellAnchors:
                CandidatePinPositions.update(((Anchor[0] + Delta, Anchor[1], ShellZ) for Delta in range(-ShellLateralSearch, ShellLateralSearch + 1)))
        Options = []
        for Rotation in (0, 90, 180, 270):
            CheckWork(Context, 'localized-terminal-rotation', GateName=Gate.Name, Rotation=Rotation)
            Origin = BuildPlacedGate(Gate, 0, 1, 0, Rotation, False)
            LocalPin = Origin.OutputPin if TerminalKind(Gate) == 'INPUT' else Origin.InputPins[0]
            for PinPosition in sorted(CandidatePinPositions):
                Candidate = BuildPlacedGate(Gate, PinPosition[0] - LocalPin[0], PinPosition[1], PinPosition[2] - LocalPin[2], Rotation, False)
                if any((PcbGatesConflict(Candidate, Existing) for Existing in Context.PlacedGates)):
                    continue
                CandidatePin = Candidate.OutputPin if TerminalKind(Gate) == 'INPUT' else Candidate.InputPins[0]
                if PreCutTerminalPinSpacing > 0 and any((abs(CandidatePin[0] - InternalPin[0]) + abs(CandidatePin[2] - InternalPin[2]) < PreCutTerminalPinSpacing for InternalSignal in PreCutInternalSignalsByTerminal.get(Signal, frozenset()) for InternalPin in PreInternalPinsBySignal.get(InternalSignal, set()))):
                    continue
                Distance = sum((abs(CandidatePin[0] - Pin[0]) + abs(CandidatePin[2] - Pin[2]) for Pin in DesiredPins))
                MaximumDistance = max((abs(CandidatePin[0] - Pin[0]) + abs(CandidatePin[2] - Pin[2]) for Pin in DesiredPins))
                CandidateWidth, CandidateDepth = RotatedCellSize(Candidate.Kind, Candidate.Rotation)
                CandidateMaximumX = Candidate.X + CandidateWidth - 1
                CandidateMaximumZ = Candidate.Z + CandidateDepth - 1
                IsOutsideCore = CandidateMaximumX < Context.InternalMinimumX or Candidate.X > Context.InternalMaximumX or CandidateMaximumZ < Context.InternalMinimumZ or (Candidate.Z > Context.InternalMaximumZ)
                if not IsOutsideCore:
                    continue
                MinimumX = min(PlacedMinimumX, Candidate.X)
                MaximumX = max(PlacedMaximumX, Candidate.X + CandidateWidth)
                MinimumZ = min(PlacedMinimumZ, Candidate.Z)
                MaximumZ = max(PlacedMaximumZ, Candidate.Z + CandidateDepth)
                Width = MaximumX - MinimumX
                Depth = MaximumZ - MinimumZ
                Options.append(((MaximumDistance, Distance, Width * Depth, max(Width, Depth), Candidate.X, Candidate.Z, Rotation), Candidate))
        if not Options:
            return None
        OrderedOptions = sorted(Options, key=lambda Value: ((Value[0][0], Value[0][1], Value[0][2], Value[0][3]) if PreferTerminalRoutingCost else (Value[0][2], Value[0][3], Value[0][0], Value[0][1])) + (Value[0][4:],))

        def ExteriorFace(Option: tuple[tuple[Any, ...], Any]) -> str:
            return CandidateExteriorFace(Option[1])
        FaceRepresentatives: list[tuple[tuple[Any, ...], Any]] = []
        SeenFaces: set[str] = set()
        for Option in OrderedOptions:
            Face = ExteriorFace(Option)
            if Face in SeenFaces:
                continue
            SeenFaces.add(Face)
            FaceRepresentatives.append(Option)
        SelectedOptions = list(FaceRepresentatives)
        for Option in OrderedOptions:
            if Option in SelectedOptions:
                continue
            SelectedOptions.append(Option)
            if len(SelectedOptions) >= Context.TerminalPlacementPolicy.MaximumTerminalPlacementCandidates:
                break
        OptionsByGate.append((Gate.Name, SelectedOptions[:Context.TerminalPlacementPolicy.MaximumTerminalPlacementCandidates]))
    RequiredTerminalLayoutCount = Context.DerivedTerminalLayoutVariantIndex + 1 if Context.UseDerivedSingleComponentPlacement else 1
    RetainedTerminalSelections: dict[tuple[object, ...], tuple[tuple[Any, ...], tuple[tuple[tuple[Any, ...], Any], ...]]] = {}
    AssignmentExpansions = 0
    StopAfterFirstLegalTerminalAssignment = TypedTerminalPlacementPressure and Context.RelocationVariant >= 2 and (RequiredTerminalLayoutCount == 1)
    TerminalAssignmentExpansionLimit = min(Context.TerminalPlacementPolicy.MaximumTerminalAssignmentExpansions, 4096) if StopAfterFirstLegalTerminalAssignment else Context.TerminalPlacementPolicy.MaximumTerminalAssignmentExpansions
    MinimumTerminalPinSpacing = 3 + Context.RoutingSpacing if TypedTerminalPlacementPressure else 0
    CutScopedTerminalPinPairs = frozenset((tuple(sorted((str(First), str(Second)))) for First, Second in Context.AssignmentConstraints.PairwiseConflictEdges if str(First) in PortIndexes and str(Second) in PortIndexes and (str(First) != str(Second))))
    CutScopedInternalSignalsByTerminal: dict[str, frozenset[str]] = {}
    for First, Second in Context.AssignmentConstraints.PairwiseConflictEdges:
        FirstSignal = str(First)
        SecondSignal = str(Second)
        if FirstSignal in PortIndexes and SecondSignal not in PortIndexes and (FirstSignal != SecondSignal):
            CutScopedInternalSignalsByTerminal[FirstSignal] = frozenset({*CutScopedInternalSignalsByTerminal.get(FirstSignal, frozenset()), SecondSignal})
        if SecondSignal in PortIndexes and FirstSignal not in PortIndexes and (FirstSignal != SecondSignal):
            CutScopedInternalSignalsByTerminal[SecondSignal] = frozenset({*CutScopedInternalSignalsByTerminal.get(SecondSignal, frozenset()), FirstSignal})
    InternalPinsBySignal: dict[str, frozenset[tuple[int, int, int]]] = {}
    MutableInternalPinsBySignal: dict[str, set[tuple[int, int, int]]] = {}
    for Existing in Context.PlacedGates:
        if Existing.OutputPin is not None:
            for Signal in Existing.Outputs:
                MutableInternalPinsBySignal.setdefault(str(Signal), set()).add(Existing.OutputPin)
        for InputIndex, Signal in enumerate(Existing.Inputs):
            MutableInternalPinsBySignal.setdefault(str(Signal), set()).add(Existing.InputPins[InputIndex])
    InternalPinsBySignal = {Signal: frozenset(Pins) for Signal, Pins in MutableInternalPinsBySignal.items()}
    CutScopedTerminalPinSpacing = 3 + Context.RoutingSpacing if Context.TerminalPlacementPolicy.EnableJointClusterOrientation and (CutScopedTerminalPinPairs or CutScopedInternalSignalsByTerminal) else 0

    def TerminalConnectionPin(Candidate: Any) -> tuple[int, int, int]:
        return Candidate.OutputPin if getattr(Candidate.Kind, 'value', Candidate.Kind) == 'INPUT' else Candidate.InputPins[0]
    TerminalCandidates = tuple((Candidate for _GateName, Options in OptionsByGate for _Key, Candidate in Options))
    TerminalPinByIdentity = {id(Candidate): TerminalConnectionPin(Candidate) for Candidate in TerminalCandidates}
    TerminalBoundsByIdentity = {id(Candidate): (Candidate.X, Candidate.X + RotatedCellSize(Candidate.Kind, Candidate.Rotation)[0], Candidate.Z, Candidate.Z + RotatedCellSize(Candidate.Kind, Candidate.Rotation)[1]) for Candidate in TerminalCandidates}
    TerminalConflictCache: dict[tuple[int, int], bool] = {}
    MinimumRemainingRoutingCosts: list[tuple[int, int]] = [(0, 0) for _ in range(len(OptionsByGate) + 1)]
    for OptionIndex in range(len(OptionsByGate) - 1, -1, -1):
        _GateName, Options = OptionsByGate[OptionIndex]
        RemainingMaximumDistance, RemainingTotalDistance = MinimumRemainingRoutingCosts[OptionIndex + 1]
        MinimumRemainingRoutingCosts[OptionIndex] = (RemainingMaximumDistance + min((Key[0] for Key, _Candidate in Options)), RemainingTotalDistance + min((Key[1] for Key, _Candidate in Options)))

    def TerminalCandidatesConflict(First: Any, Second: Any) -> bool:
        FirstIdentity = id(First)
        SecondIdentity = id(Second)
        Key = (FirstIdentity, SecondIdentity) if FirstIdentity < SecondIdentity else (SecondIdentity, FirstIdentity)
        Conflict = TerminalConflictCache.get(Key)
        if Conflict is None:
            Conflict = PcbGatesConflict(First, Second)
            TerminalConflictCache[Key] = Conflict
        return Conflict
    InternalAccessPositionsBySignal: dict[str, frozenset[tuple[int, int, int]]] = {}
    MutableInternalAccessPositionsBySignal: dict[str, set[tuple[int, int, int]]] = {}
    for Existing in Context.PlacedGates:
        if Existing.OutputPin is not None and Existing.OutputDirection is not None:
            for Signal in Existing.Outputs:
                MutableInternalAccessPositionsBySignal.setdefault(str(Signal), set()).update(((Existing.OutputPin[0] + Existing.OutputDirection[0] * Offset, Existing.OutputPin[1] + Existing.OutputDirection[1] * Offset, Existing.OutputPin[2] + Existing.OutputDirection[2] * Offset) for Offset in range(DefaultRedstoneRoutingTechnology.AccessLength)))
        for InputIndex, Signal in enumerate(Existing.Inputs):
            Pin = Existing.InputPins[InputIndex]
            Direction = Existing.InputDirections[InputIndex]
            MutableInternalAccessPositionsBySignal.setdefault(str(Signal), set()).update(((Pin[0] + Direction[0] * Offset, Pin[1] + Direction[1] * Offset, Pin[2] + Direction[2] * Offset) for Offset in range(DefaultRedstoneRoutingTechnology.AccessLength)))
    InternalAccessPositionsBySignal = {Signal: frozenset(Positions) for Signal, Positions in MutableInternalAccessPositionsBySignal.items()}

    def TerminalAccessPositions(Candidate: Any) -> frozenset[tuple[int, int, int]]:
        Pin = TerminalConnectionPin(Candidate)
        Direction = Candidate.OutputDirection if getattr(Candidate.Kind, 'value', Candidate.Kind) == 'INPUT' else Candidate.InputDirections[0]
        return frozenset(((Pin[0] + Direction[0] * Offset, Pin[1] + Direction[1] * Offset, Pin[2] + Direction[2] * Offset) for Offset in range(DefaultRedstoneRoutingTechnology.AccessLength)))
    TerminalAccessPositionsByIdentity = {id(Candidate): TerminalAccessPositions(Candidate) for Candidate in TerminalCandidates}
    TerminalAccessExclusionsByIdentity = {Identity: DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(Positions) for Identity, Positions in TerminalAccessPositionsByIdentity.items()}
    InternalAccessExclusionsBySignal = {Signal: DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(Positions) for Signal, Positions in InternalAccessPositionsBySignal.items()}
    TerminalInternalAccessConflictCountByIdentity = {id(Candidate): sum((1 for OtherSignal, OtherPositions in InternalAccessPositionsBySignal.items() if TerminalSignal(Candidate) != OtherSignal and (TerminalAccessPositionsByIdentity[id(Candidate)] & InternalAccessExclusionsBySignal[OtherSignal] or OtherPositions & TerminalAccessExclusionsByIdentity[id(Candidate)]))) for Candidate in TerminalCandidates}
    TerminalAccessConflictCache: dict[tuple[int, int], bool] = {}

    def TerminalAccessConflicts(First: Any, Second: Any) -> bool:
        FirstIdentity = id(First)
        SecondIdentity = id(Second)
        Key = (FirstIdentity, SecondIdentity) if FirstIdentity < SecondIdentity else (SecondIdentity, FirstIdentity)
        Conflict = TerminalAccessConflictCache.get(Key)
        if Conflict is None:
            Conflict = bool(TerminalAccessPositionsByIdentity[FirstIdentity] & TerminalAccessExclusionsByIdentity[SecondIdentity] or TerminalAccessPositionsByIdentity[SecondIdentity] & TerminalAccessExclusionsByIdentity[FirstIdentity])
            TerminalAccessConflictCache[Key] = Conflict
        return Conflict

    def SelectionAccessConflictCount(Selected: tuple[tuple[tuple[Any, ...], Any], ...]) -> int:
        Candidates = tuple((Candidate for _Key, Candidate in Selected))
        return sum((TerminalInternalAccessConflictCountByIdentity[id(Candidate)] for Candidate in Candidates)) + sum((TerminalSignal(First) != TerminalSignal(Second) and TerminalAccessConflicts(First, Second) for Index, First in enumerate(Candidates) for Second in Candidates[Index + 1:]))

    def SelectionAccessIdentity(Selected: tuple[tuple[tuple[Any, ...], Any], ...]) -> tuple[object, ...]:
        """Identify a complete terminal layout by physical access shape.

            A rotation-only change on the same side rarely alters the shared
            capacity problem.  Retain the best legal representative for each
            signal-to-perimeter-face pattern, so every fixed member changes
            the topology of its terminal escape domain rather than spending a
            domain slot on cosmetic orientation.
            """
        return tuple(sorted(((str(TerminalSignal(Candidate)), str(Candidate.Name), CandidateExteriorFace(Candidate)) for _Key, Candidate in Selected)))

    def WorstRetainedTerminalScore() -> tuple[Any, ...] | None:
        if len(RetainedTerminalSelections) < RequiredTerminalLayoutCount:
            return None
        return max((Score for Score, _Selection in RetainedTerminalSelections.values()))

    def RetainTerminalSelection(Selected: tuple[tuple[tuple[Any, ...], Any], ...]) -> None:
        """Retain the best fixed representatives without a retry queue."""
        Identity = SelectionAccessIdentity(Selected)
        Score = SelectionScore(Selected)
        Existing = RetainedTerminalSelections.get(Identity)
        if Existing is not None and Existing[0] <= Score:
            return
        RetainedTerminalSelections[Identity] = (Score, Selected)
        if len(RetainedTerminalSelections) <= RequiredTerminalLayoutCount:
            return
        WorstIdentity = max(RetainedTerminalSelections, key=lambda Value: (RetainedTerminalSelections[Value][0], Value))
        del RetainedTerminalSelections[WorstIdentity]

    def SelectionScore(Selected: tuple[tuple[tuple[Any, ...], Any], ...]) -> tuple[Any, ...]:
        SelectedBounds = tuple((TerminalBoundsByIdentity[id(Candidate)] for _Key, Candidate in Selected))
        MinimumX = min((PlacedMinimumX, *(Bounds[0] for Bounds in SelectedBounds)))
        MaximumX = max((PlacedMaximumX, *(Bounds[1] for Bounds in SelectedBounds)))
        MinimumZ = min((PlacedMinimumZ, *(Bounds[2] for Bounds in SelectedBounds)))
        MaximumZ = max((PlacedMaximumZ, *(Bounds[3] for Bounds in SelectedBounds)))
        Width = MaximumX - MinimumX
        Depth = MaximumZ - MinimumZ
        AreaScore = (Width * Depth, max(Width, Depth))
        RoutingScore = (sum((Key[0] for Key, _Candidate in Selected)), sum((Key[1] for Key, _Candidate in Selected)))
        FaceCounts = {Face: sum((CandidateExteriorFace(Candidate) == Face for _Key, Candidate in Selected)) for Face in ('north', 'south', 'east', 'west')}
        RingScore = (max(FaceCounts.values(), default=0), sum((Count * Count for Count in FaceCounts.values())), *AreaScore)
        BaseScore = (*RingScore, *RoutingScore) if Context.PreferAccessRingTerminals else (*RoutingScore, *AreaScore) if PreferTerminalRoutingCost else (*AreaScore, *RoutingScore)
        return ((SelectionAccessConflictCount(Selected), *BaseScore) if Context.UseDerivedSingleComponentPlacement else BaseScore) + (tuple(((Candidate.Name, Candidate.X, Candidate.Z, Candidate.Rotation) for _Key, Candidate in Selected)),)

    def SelectionLowerBound(Index: int, Selected: tuple[tuple[tuple[Any, ...], Any], ...]) -> tuple[int, int, int, int]:
        """Return a monotone prefix bound for exact terminal assignment."""
        SelectedBounds = tuple((TerminalBoundsByIdentity[id(Candidate)] for _Key, Candidate in Selected))
        MinimumX = min((PlacedMinimumX, *(Bounds[0] for Bounds in SelectedBounds)))
        MaximumX = max((PlacedMaximumX, *(Bounds[1] for Bounds in SelectedBounds)))
        MinimumZ = min((PlacedMinimumZ, *(Bounds[2] for Bounds in SelectedBounds)))
        MaximumZ = max((PlacedMaximumZ, *(Bounds[3] for Bounds in SelectedBounds)))
        Width = MaximumX - MinimumX
        Depth = MaximumZ - MinimumZ
        AreaScore = (Width * Depth, max(Width, Depth))
        if Context.PreferAccessRingTerminals:
            FaceCounts = {Face: sum((CandidateExteriorFace(Candidate) == Face for _Key, Candidate in Selected)) for Face in ('north', 'south', 'east', 'west')}
            BaseLowerBound = (max(FaceCounts.values(), default=0), sum((Count * Count for Count in FaceCounts.values())), *AreaScore)
            return (SelectionAccessConflictCount(Selected), *BaseLowerBound) if Context.UseDerivedSingleComponentPlacement else BaseLowerBound
        RemainingMaximumDistance, RemainingTotalDistance = MinimumRemainingRoutingCosts[Index]
        RoutingScore = (sum((Key[0] for Key, _Candidate in Selected)) + RemainingMaximumDistance, sum((Key[1] for Key, _Candidate in Selected)) + RemainingTotalDistance)
        BaseLowerBound = (*RoutingScore, *AreaScore) if PreferTerminalRoutingCost else (*AreaScore, *RoutingScore)
        return (SelectionAccessConflictCount(Selected), *BaseLowerBound) if Context.UseDerivedSingleComponentPlacement else BaseLowerBound
    PrunedAssignmentExpansions = 0

    def SearchTerminalAssignments(Index: int, Selected: tuple[tuple[tuple[Any, ...], Any], ...]) -> None:
        nonlocal AssignmentExpansions
        nonlocal PrunedAssignmentExpansions
        if AssignmentExpansions >= TerminalAssignmentExpansionLimit:
            return
        AssignmentExpansions += 1
        LowerBound = SelectionLowerBound(Index, Selected)
        WorstScore = WorstRetainedTerminalScore()
        if WorstScore is not None and LowerBound > WorstScore[:len(LowerBound)]:
            PrunedAssignmentExpansions += 1
            return
        if Index == len(OptionsByGate):
            RetainTerminalSelection(Selected)
            return
        _GateName, Options = OptionsByGate[Index]
        for Option in Options:
            _Key, Candidate = Option
            if any((TerminalCandidatesConflict(Candidate, Existing) for _SelectedKey, Existing in Selected)):
                continue
            CandidatePin = TerminalPinByIdentity[id(Candidate)]
            if MinimumTerminalPinSpacing > 0 and any((abs(CandidatePin[0] - SelectedPin[0]) + abs(CandidatePin[2] - SelectedPin[2]) < MinimumTerminalPinSpacing for SelectedPin in (TerminalPinByIdentity[id(Existing)] for _SelectedKey, Existing in Selected))):
                continue
            CandidateSignal = TerminalSignal(Candidate)
            if CutScopedTerminalPinSpacing > 0 and any((abs(CandidatePin[0] - InternalPin[0]) + abs(CandidatePin[2] - InternalPin[2]) < CutScopedTerminalPinSpacing for InternalSignal in CutScopedInternalSignalsByTerminal.get(CandidateSignal, frozenset()) for InternalPin in InternalPinsBySignal.get(InternalSignal, frozenset()))):
                continue
            if CutScopedTerminalPinSpacing > 0 and any((tuple(sorted((CandidateSignal, TerminalSignal(Existing)))) in CutScopedTerminalPinPairs and abs(CandidatePin[0] - TerminalPinByIdentity[id(Existing)][0]) + abs(CandidatePin[2] - TerminalPinByIdentity[id(Existing)][2]) < CutScopedTerminalPinSpacing for _SelectedKey, Existing in Selected)):
                continue
            SearchTerminalAssignments(Index + 1, (*Selected, Option))
            if StopAfterFirstLegalTerminalAssignment and RetainedTerminalSelections:
                return
    SearchTerminalAssignments(0, ())
    CheckWork(Context, 'localized-terminal-search-complete', AssignmentExpansions=AssignmentExpansions, StopAfterFirstLegalTerminalAssignment=StopAfterFirstLegalTerminalAssignment, RequestedDerivedTerminalLayoutVariantIndex=Context.DerivedTerminalLayoutVariantIndex, RetainedTerminalLayoutCount=len(RetainedTerminalSelections), NandCount=Context.NandCount, RelocationVariant=Context.RelocationVariant, PrunedAssignmentExpansions=PrunedAssignmentExpansions)
    OrderedTerminalSelections = tuple(sorted(RetainedTerminalSelections.values(), key=lambda Value: Value[0]))
    if not OrderedTerminalSelections:
        return None
    if Context.DerivedTerminalLayoutVariantIndex >= len(OrderedTerminalSelections):
        raise ValueError('derived terminal layout variant exceeds the complete access-distinct terminal domain')
    _Score, SelectedTerminalLayout = OrderedTerminalSelections[Context.DerivedTerminalLayoutVariantIndex]
    return [Candidate for _Key, Candidate in SelectedTerminalLayout]


def ValidateBoundaryEscapes(Context, Candidate: LocalRouteClaim) -> None:
    """Keep fixed local trees from consuming another net's pin escape."""
    for OtherSignal, AccessClaims in Context.AccessClaimsBySignal.items():
        if OtherSignal == Candidate.Signal:
            continue
        Conflicts = FindClaimConflicts({Candidate.Signal: Candidate.Claims, OtherSignal: AccessClaims})
        if Conflicts:
            Resource = min(Conflicts, key=str)
            raise ValueError(f'Local route blocks boundary escape at {Resource}: {Candidate.Signal},{OtherSignal}')


def FindLocalPath(Context, Starts: set[tuple[int, int, int]], Target: tuple[int, int, int], Signal: str) -> tuple[tuple[int, int, int], ...]:
    """Find one bounded component-plane extension from an owned tree."""
    OtherClaims = [Claim for Claim in Context.LocalRouteClaims if Claim.Signal != Signal]
    Blocked = set().union(*(Claim.Claims.ElectricalCells for Claim in OtherClaims)) if OtherClaims else set()
    Parents: dict[tuple[int, int, int], tuple[int, int, int] | None] = {Start: None for Start in sorted(Starts)}
    Distances = {Start: 0 for Start in Starts}
    Pending = deque(sorted(Starts))
    CompletedNodes = 0
    while Pending and Target not in Parents:
        CompletedNodes += 1
        if CompletedNodes % 256 == 0:
            CheckWork(Context, 'local-path-search', Signal=Signal, CompletedNodes=CompletedNodes, PendingNodes=len(Pending))
        Current = Pending.popleft()
        Distance = Distances[Current]
        if Distance >= Context.MaximumLocalRouteLength:
            continue
        for Neighbor in sorted(DefaultRedstoneRoutingTechnology.NeighborPositions(Current)):
            if Neighbor in Parents:
                continue
            if not (Context.MinimumRouteX <= Neighbor[0] <= Context.MaximumRouteX and Context.MinimumRouteZ <= Neighbor[2] <= Context.MaximumRouteZ and (Context.MinimumRouteY <= Neighbor[1] <= Context.MaximumRouteY)):
                continue
            if Neighbor in Context.ActualBlocks and Neighbor != Target:
                continue
            if Neighbor in Context.LocalResourceGraph.StaticKeepOut and Neighbor not in Context.AccessBySignal.get(Signal, set()) and (Neighbor != Target):
                continue
            if Neighbor in Blocked and Neighbor != Target:
                continue
            if Context.LocalResourceGraph.BuildPrimitive(Current, Neighbor) is None:
                continue
            Support = (Neighbor[0], Neighbor[1] - 1, Neighbor[2])
            if Support in Context.ActualBlocks and Neighbor != Target:
                continue
            Parents[Neighbor] = Current
            Distances[Neighbor] = Distance + 1
            Pending.append(Neighbor)
    if Target not in Parents:
        return ()
    Result = []
    Current = Target
    while Current is not None and Current not in Starts:
        Result.append(Current)
        Current = Parents[Current]
    if Current is not None:
        Result.append(Current)
    return tuple(reversed(Result))


def SelectBoundaryNodes(Context, Nodes: frozenset[tuple[int, int, int]], AllTargets: list[tuple[int, int, int]], ConnectedTargets: list[tuple[int, int, int]]) -> tuple[tuple[int, int, int], ...]:
    """Expose only deterministic continuation points for remote sinks."""
    Unresolved = sorted(set(AllTargets) - set(ConnectedTargets))
    return tuple(sorted({min(Nodes, key=lambda Position: (abs(Target[0] - Position[0]) + abs(Target[1] - Position[1]) + abs(Target[2] - Position[2]), Position)) for Target in Unresolved}))


def ValidateLocalSignalStrength(Context, Candidate: LocalRouteClaim) -> None:
    """Reject local trees that require a repeater not yet reserved."""
    Graph: dict[tuple[int, int, int], set[tuple[int, int, int]]] = {Position: set() for Position in Candidate.Nodes}
    for First, Second in Candidate.Edges:
        Graph[First].add(Second)
        Graph[Second].add(First)
    Distances = {Candidate.Root: 0}
    Pending = deque((Candidate.Root,))
    while Pending:
        if len(Distances) % 256 == 0:
            CheckWork(Context, 'local-signal-strength', Signal=Candidate.Signal, CompletedNodes=len(Distances), PendingNodes=len(Pending))
        Current = Pending.popleft()
        for Neighbor in Graph.get(Current, ()):
            if Neighbor in Distances:
                continue
            Distances[Neighbor] = Distances[Current] + 1
            Pending.append(Neighbor)
    MaximumDistance = max((Distances.get(Target, 10 ** 9) for Target in Candidate.ConnectedTargets), default=0)
    if MaximumDistance >= DefaultRedstoneRoutingTechnology.MaximumUnrefreshedDustLength and (not Candidate.RepeaterReservations):
        raise ValueError(f'Local route requires a repeater before its farthest sink: {Candidate.Signal} distance={MaximumDistance}')


def ValidateLocalPhysicalConnectivity(Context, Candidate: LocalRouteClaim) -> None:
    """Reject local claims that are connected only in the abstract graph."""
    CandidateProducer = Context.Producers.get(Candidate.Signal)
    if CandidateProducer is None:
        raise ValueError(f'Local route has no producer: {Candidate.Signal}')
    CandidateSupports = set(Candidate.Claims.SupportCells) - Context.ActualBlocks
    PhysicalGraphs = BuildPhysicalGraphs({Candidate.Signal: set(Candidate.Nodes)}, Context.ActualBlocks, CandidateSupports, Context.SolidBlocks)
    ValidatePhysicalRoutes(PhysicalGraphs, {Candidate.Signal: CandidateProducer}, {Candidate.Signal: list(Candidate.ConnectedTargets)})


def ValidateContinuationPortal(Context, Candidate: LocalRouteClaim, AllTargets: list[tuple[int, int, int]]) -> None:
    """Require an unclaimed legal frontier for every partial tree."""
    if set(AllTargets).issubset(Candidate.ConnectedTargets):
        return
    if not Candidate.BoundaryNodes:
        raise ValueError(f'Partial local route has no continuation node: {Candidate.Signal}')
    if Context.MaximumEntrancesPerSignal is not None and len(Candidate.BoundaryNodes) > Context.MaximumEntrancesPerSignal:
        raise ValueError(f'Partial local route exceeds per-signal entrance budget: {Candidate.Signal} entrances={len(Candidate.BoundaryNodes)}')
    ForeignElectrical = set().union(*(Claim.Claims.ElectricalCells for Claim in Context.LocalRouteClaims if Claim.Signal != Candidate.Signal)) if Context.LocalRouteClaims else set()
    for Boundary in Candidate.BoundaryNodes:
        for Neighbor in sorted(DefaultRedstoneRoutingTechnology.NeighborPositions(Boundary)):
            if Neighbor in Candidate.Nodes or Neighbor in Context.ActualBlocks:
                continue
            if Neighbor in ForeignElectrical:
                continue
            if Context.LocalResourceGraph.BuildPrimitive(Boundary, Neighbor) is not None:
                return
    raise ValueError(f'Partial local route has no legal continuation portal: {Candidate.Signal}')


def BuildClusterLocalRouteTemplateCacheKey(Context, ClusterIndex: int) -> tuple[object, ...]:
    """Identify reusable internal routing independently of the slot."""
    Variant = Context.SelectedClusterVariants[ClusterIndex]
    return (Context.ClusterStructuralSignatures.get(ClusterIndex, ''), tuple(sorted(Context.Clusters[ClusterIndex])), Variant.Rotation, Variant.MirrorX, repr(Context.PackingPolicy))


def PreferredBoundarySide(Context, Signal: str) -> str:
    ExternalGates = [Context.GateByName[Gate.Name] for Gate in Context.ConsumersBySignal.get(Signal, ()) if Gate.Name not in Context.NameSet and Gate.Name in Context.GateByName]
    Producer = Context.ProducersBySignal.get(Signal)
    if Producer is not None and Producer.Name not in Context.NameSet and (Producer.Name in Context.GateByName):
        ExternalGates.append(Context.GateByName[Producer.Name])
    if not ExternalGates:
        return 'East'
    TargetX = sum((Gate.X for Gate in ExternalGates)) / len(ExternalGates)
    TargetZ = sum((Gate.Z for Gate in ExternalGates)) / len(ExternalGates)
    DeltaX = TargetX - Context.ClusterCenterX
    DeltaZ = TargetZ - Context.ClusterCenterZ
    if abs(DeltaX) >= abs(DeltaZ):
        return 'East' if DeltaX >= 0 else 'West'
    return 'South' if DeltaZ >= 0 else 'North'

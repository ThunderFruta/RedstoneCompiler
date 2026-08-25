"""Exhaustive logic and routed-redstone simulation."""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
import os
from itertools import product
from pathlib import Path
from time import monotonic
from typing import Any

try:
    from RedstoneCompiler.RustRouting import EvaluateLogicPrograms
except (ImportError, AttributeError):
    EvaluateLogicPrograms = None

from ..Routing.Actions import (
    BuildPhysicalGraphs,
    BuildRoutingResources,
    FindFlatRouteConflicts,
    PropagateRoutePower,
    ValidatePhysicalRoutes,
    ValidateTemplateIsolation,
)


Position = tuple[int, int, int]
MaximumRenderedMinecraftTruthTableRows = 8


def ShouldSimulateRenderedMinecraftTruthTable(ReferenceModule: Any) -> bool:
    """Return whether exact rendered settling fits the parity-proven bound."""
    return (
        1 << len(tuple(ReferenceModule.Inputs))
    ) <= MaximumRenderedMinecraftTruthTableRows


@dataclass(frozen=True)
class RedstoneTruthTableRow:
    """One ideal-versus-physical truth-table comparison."""

    Inputs: tuple[bool, ...]
    ExpectedOutputs: tuple[bool, ...]
    SimulatedOutputs: tuple[bool, ...]

    @property
    def Passed(self) -> bool:
        return self.ExpectedOutputs == self.SimulatedOutputs


@dataclass(frozen=True)
class RedstoneSimulationReport:
    """Exhaustive physical simulation result for one routed module."""

    ModuleName: str
    InputNames: tuple[str, ...]
    OutputNames: tuple[str, ...]
    Rows: tuple[RedstoneTruthTableRow, ...]
    Backend: str = "python"
    RuntimeSeconds: float = 0.0
    Diagnostics: dict[str, Any] | None = None

    @property
    def Passed(self) -> bool:
        return all(Row.Passed for Row in self.Rows)

    @property
    def FailedRows(self) -> tuple[RedstoneTruthTableRow, ...]:
        return tuple(Row for Row in self.Rows if not Row.Passed)


# Java Edition redstone subset used by the emitted cell library.  It is kept
# deliberately small and explicit: opaque blocks, dust, redstone torches,
# repeaters, levers, and lamps.  This is the first verifier in the pipeline
# that evaluates cross-net power through rendered blocks; the older routed-net
# delivery analysis below remains a useful logical cross-check.
_MinecraftDirections: dict[str, Position] = {
    "north": (0, 0, -1), "south": (0, 0, 1),
    "east": (1, 0, 0), "west": (-1, 0, 0),
    "up": (0, 1, 0), "down": (0, -1, 0),
}
_HorizontalDirections = ("north", "south", "east", "west")
_OppositeHorizontalDirection = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
}
_NonConductors = frozenset({
    "minecraft:air", "minecraft:redstone_wire", "minecraft:redstone_torch",
    "minecraft:redstone_wall_torch", "minecraft:repeater", "minecraft:lever",
    "minecraft:oak_sign", "minecraft:redstone_lamp",
})


def _MinecraftOffset(PositionValue: Position, Direction: str) -> Position:
    Delta = _MinecraftDirections[Direction]
    return tuple(PositionValue[Axis] + Delta[Axis] for Axis in range(3))


def _MinecraftIsConductor(State: dict[str, Any] | None) -> bool:
    return State is not None and State.get("Name") not in _NonConductors


def _MinecraftBlockProperties(State: dict[str, Any]) -> dict[str, str]:
    return {str(Key): str(Value) for Key, Value in State.get("Properties", {}).items()}


def _MinecraftWireNeighbors(
    PositionValue: Position,
    State: dict[str, Any],
) -> tuple[Position, ...]:
    Properties = _MinecraftBlockProperties(State)
    Result: list[Position] = []
    for Direction in _HorizontalDirections:
        Shape = Properties.get(Direction, "none")
        if Shape == "none":
            continue
        Neighbor = _MinecraftOffset(PositionValue, Direction)
        if Shape == "up":
            Neighbor = _MinecraftOffset(Neighbor, "up")
        Result.append(Neighbor)
    return tuple(Result)


def _BuildMinecraftWireAdjacency(
    Blocks: dict[Position, dict[str, Any]],
) -> dict[Position, tuple[Position, ...]]:
    """Build the undirected connection graph declared by dust block states.

    A lower stair cell names the upper dust with an ``up`` connection, while
    the upper cell names only its same-height horizontal neighbor.  The block
    states therefore describe the two ends asymmetrically even though redstone
    power crosses the connection in both directions.
    """
    WirePositions = {
        PositionValue
        for PositionValue, State in Blocks.items()
        if State["Name"] == "minecraft:redstone_wire"
    }
    Mutable = {PositionValue: set() for PositionValue in WirePositions}
    for PositionValue in sorted(WirePositions):
        for Neighbor in _MinecraftWireNeighbors(
            PositionValue,
            Blocks[PositionValue],
        ):
            if Neighbor not in WirePositions:
                continue
            Mutable[PositionValue].add(Neighbor)
            Mutable[Neighbor].add(PositionValue)
    return {
        PositionValue: tuple(sorted(Neighbors))
        for PositionValue, Neighbors in Mutable.items()
    }


@dataclass(frozen=True)
class MinecraftRedstoneBlockMapResult:
    """Settled Java-redstone subset state for one rendered block map."""

    DustPower: dict[Position, int]
    TorchLit: dict[Position, bool]
    RepeaterPowered: dict[Position, bool]
    LampLit: dict[Position, bool]
    Stable: bool
    CycleDetected: bool
    Ticks: int


def SimulateMinecraftRedstoneBlockMap(
    Blocks: dict[Position, dict[str, Any]],
    LeverPower: dict[Position, bool],
    MaximumTicks: int = 64,
) -> MinecraftRedstoneBlockMapResult:
    """Settle the emitted Java-edition redstone subset.

    The model follows the power distinctions important to the compiler: a lit
    torch powers the opaque block above it, dust can be powered through the
    opaque block beneath it, and a repeater emits only from its front.  State
    transitions are synchronous game-tick steps with deterministic cycle
    detection, which makes torch feedback a verifier failure rather than an
    invisible same-net routing detail.
    """
    Dust = tuple(sorted(P for P, S in Blocks.items() if S["Name"] == "minecraft:redstone_wire"))
    Torches = tuple(sorted(P for P, S in Blocks.items() if S["Name"] in ("minecraft:redstone_torch", "minecraft:redstone_wall_torch")))
    Repeaters = tuple(sorted(P for P, S in Blocks.items() if S["Name"] == "minecraft:repeater"))
    Lamps = tuple(sorted(P for P, S in Blocks.items() if S["Name"] == "minecraft:redstone_lamp"))
    DustPower = {P: 0 for P in Dust}
    WireAdjacency = _BuildMinecraftWireAdjacency(Blocks)
    TorchLit = {P: _MinecraftBlockProperties(Blocks[P]).get("lit", "true") == "true" for P in Torches}
    RepeaterPowered = {P: _MinecraftBlockProperties(Blocks[P]).get("powered", "false") == "true" for P in Repeaters}
    Seen: set[tuple[Any, ...]] = set()

    def Emissions() -> dict[Position, int]:
        Powered: dict[Position, int] = {}
        def Emit(Target: Position, Strength: int) -> None:
            Powered[Target] = max(Powered.get(Target, 0), Strength)
        for PositionValue, On in LeverPower.items():
            if On:
                for Direction in _MinecraftDirections:
                    Emit(_MinecraftOffset(PositionValue, Direction), 15)
        for PositionValue, Lit in TorchLit.items():
            if not Lit:
                continue
            State = Blocks[PositionValue]
            Properties = _MinecraftBlockProperties(State)
            Attached = _MinecraftOffset(PositionValue, {
                "north": "south", "south": "north", "east": "west", "west": "east",
            }.get(Properties.get("facing", "down"), "down"))
            for Direction in _MinecraftDirections:
                Target = _MinecraftOffset(PositionValue, Direction)
                if Target != Attached:
                    # A torch activates adjacent components, but (unlike a
                    # lever) only strongly powers an opaque block above it.
                    # This distinction is exactly why the block-over-torch
                    # stack is electrically significant.
                    if _MinecraftIsConductor(Blocks.get(Target)) and Direction != "up":
                        continue
                    Emit(Target, 15)
        for PositionValue, On in RepeaterPowered.items():
            if On:
                Facing = _MinecraftBlockProperties(Blocks[PositionValue]).get(
                    "facing",
                    "north",
                )
                Emit(_MinecraftOffset(PositionValue, Facing), 15)
        return Powered

    for Tick in range(MaximumTicks):
        BasePower = Emissions()
        BlockPower = {
            PositionValue: BasePower.get(PositionValue, 0)
            for PositionValue, State in Blocks.items()
            if _MinecraftIsConductor(State)
        }
        # Dust updates are immediate in-game.  Its settled state is the
        # maximum decaying strength reachable from any direct power source.
        # Compute that fixed point with one deterministic strongest-first
        # traversal instead of repeatedly scanning every dust cell.
        NextDust = {PositionValue: 0 for PositionValue in Dust}
        PendingDust: list[tuple[int, Position]] = []
        for PositionValue in Dust:
            Strength = min(15, max(
                BasePower.get(PositionValue, 0),
                BlockPower.get(_MinecraftOffset(PositionValue, "down"), 0),
            ))
            if Strength <= 0:
                continue
            NextDust[PositionValue] = Strength
            heappush(PendingDust, (-Strength, PositionValue))
        while PendingDust:
            NegativeStrength, PositionValue = heappop(PendingDust)
            Strength = -NegativeStrength
            if NextDust[PositionValue] != Strength or Strength <= 1:
                continue
            CandidateStrength = Strength - 1
            for Neighbor in WireAdjacency[PositionValue]:
                if CandidateStrength <= NextDust[Neighbor]:
                    continue
                NextDust[Neighbor] = CandidateStrength
                heappush(PendingDust, (-CandidateStrength, Neighbor))
        NextTorch = {}
        for PositionValue in Torches:
            Properties = _MinecraftBlockProperties(Blocks[PositionValue])
            Attached = _MinecraftOffset(PositionValue, {
                "north": "south", "south": "north", "east": "west", "west": "east",
            }.get(Properties.get("facing", "down"), "down"))
            NextTorch[PositionValue] = BlockPower.get(Attached, 0) == 0
        NextRepeater = dict(RepeaterPowered)
        for PositionValue in Repeaters:
            Properties = _MinecraftBlockProperties(Blocks[PositionValue])
            Facing = Properties.get("facing", "north")
            Opposite = _OppositeHorizontalDirection[Facing]
            Rear = _MinecraftOffset(PositionValue, Opposite)
            SideDirections = tuple(Direction for Direction in _HorizontalDirections if Direction not in (Facing, Opposite))
            Locked = any(
                SidePosition in RepeaterPowered
                and RepeaterPowered[SidePosition]
                and _MinecraftOffset(
                    SidePosition,
                    _MinecraftBlockProperties(Blocks[SidePosition]).get(
                        "facing",
                        "north",
                    ),
                ) == PositionValue
                for SidePosition in (
                    _MinecraftOffset(PositionValue, Direction)
                    for Direction in SideDirections
                )
            )
            DirectRepeaterInput = bool(
                RepeaterPowered.get(Rear, False)
                and Rear in Blocks
                and Blocks[Rear]["Name"] == "minecraft:repeater"
                and _MinecraftOffset(
                    Rear,
                    _MinecraftBlockProperties(Blocks[Rear]).get(
                        "facing",
                        "north",
                    ),
                ) == PositionValue
            )
            InputPower = DirectRepeaterInput or max(
                BasePower.get(Rear, 0),
                NextDust.get(Rear, 0),
                BlockPower.get(Rear, 0),
            ) > 0
            if not Locked:
                # Truth-table acceptance asks for the settled combinational
                # state.  A repeater's configured delay changes only when the
                # transition occurs, not that state, so applying its observed
                # input on the next synchronous iteration avoids mistaking an
                # in-flight transition for a stable result.
                NextRepeater[PositionValue] = InputPower
        Signature = (tuple(sorted(NextDust.items())), tuple(sorted(NextTorch.items())), tuple(sorted(NextRepeater.items())))
        if NextDust == DustPower and NextTorch == TorchLit and NextRepeater == RepeaterPowered:
            FinalPower = Emissions()
            LampLit = {
                PositionValue: FinalPower.get(PositionValue, 0) > 0 or any(
                    FinalPower.get(_MinecraftOffset(PositionValue, Direction), 0) > 0
                    or BlockPower.get(_MinecraftOffset(PositionValue, Direction), 0) > 0
                    or NextDust.get(_MinecraftOffset(PositionValue, Direction), 0) > 0
                    for Direction in _MinecraftDirections
                )
                for PositionValue in Lamps
            }
            return MinecraftRedstoneBlockMapResult(NextDust, NextTorch, NextRepeater, LampLit, True, False, Tick + 1)
        if Signature in Seen:
            return MinecraftRedstoneBlockMapResult(NextDust, NextTorch, NextRepeater, {}, False, True, Tick + 1)
        Seen.add(Signature)
        DustPower, TorchLit, RepeaterPowered = NextDust, NextTorch, NextRepeater
    return MinecraftRedstoneBlockMapResult(DustPower, TorchLit, RepeaterPowered, {}, False, False, MaximumTicks)


def _RenderedGateBlockPositions(RoutedDesign: Any) -> dict[str, tuple[Position, ...]]:
    """Recover rendered component positions without guessing from colors."""
    from SchemEncoder.Writer262 import LoadTemplate
    from Compiler.Placement.Rotation import TransformLocalPosition
    from Templates import LitematicTemplates

    Result: dict[str, tuple[Position, ...]] = {}
    Templates = {
        str(Name).upper(): PathValue
        for Name, PathValue in LitematicTemplates.items()
    }
    for Gate in RoutedDesign.PlacedGates:
        Template = LoadTemplate(Templates[Gate.Kind.upper()])
        Positions = []
        for LocalPosition in Template.Blocks:
            Transformed = TransformLocalPosition(
                LocalPosition,
                (Template.Size[0], Template.Size[2]),
                Gate.Rotation,
                Gate.MirrorX,
            )
            Positions.append((
                Gate.X + Transformed[0],
                Gate.Y + LocalPosition[1],
                Gate.Z + Transformed[2],
            ))
        Result[Gate.Name] = tuple(Positions)
    return Result


def SimulateRenderedMinecraftTruthTable(
    RoutedDesign: Any,
    ReferenceModule: Any | None = None,
) -> RedstoneSimulationReport:
    """Verify truth-table rows by settling the exact emitted block layout."""
    from SchemEncoder.Writer262 import BuildLitematicBlockMap

    Started = monotonic()
    ReferenceModule = ReferenceModule or RoutedDesign.Module
    InputNames = tuple(ReferenceModule.Inputs)
    OutputNames = tuple(ReferenceModule.Outputs)
    Build = BuildLitematicBlockMap(RoutedDesign)
    MaximumSettlingTicks = max(
        64,
        1 + sum(
            State["Name"] in (
                "minecraft:redstone_torch",
                "minecraft:redstone_wall_torch",
                "minecraft:repeater",
            )
            for State in Build.Blocks.values()
        ),
    )
    GatePositions = _RenderedGateBlockPositions(RoutedDesign)
    InputLevers: dict[str, Position] = {}
    OutputLamps: dict[str, Position] = {}
    for Gate in RoutedDesign.PlacedGates:
        Positions = GatePositions[Gate.Name]
        if Gate.Kind == "INPUT":
            Levers = [PositionValue for PositionValue in Positions if Build.Blocks.get(PositionValue, {}).get("Name") == "minecraft:lever"]
            if len(Levers) != 1 or not Gate.Outputs:
                raise ValueError(f"Rendered INPUT cell {Gate.Name} has no unique lever")
            InputLevers[Gate.Outputs[0]] = Levers[0]
        elif Gate.Kind == "OUTPUT":
            Lamps = [PositionValue for PositionValue in Positions if Build.Blocks.get(PositionValue, {}).get("Name") == "minecraft:redstone_lamp"]
            if len(Lamps) != 1 or not Gate.Inputs:
                raise ValueError(f"Rendered OUTPUT cell {Gate.Name} has no unique lamp")
            OutputLamps[Gate.Inputs[0]] = Lamps[0]
    MissingInputs = set(InputNames) - set(InputLevers)
    MissingOutputs = set(OutputNames) - set(OutputLamps)
    if MissingInputs or MissingOutputs:
        raise ValueError(f"Rendered I/O map is incomplete: inputs={sorted(MissingInputs)}, outputs={sorted(MissingOutputs)}")
    Rows = []
    StableRows = 0
    CycleRows = 0
    MaximumTicks = 0
    for Bits in product((False, True), repeat=len(InputNames)):
        Assignment = dict(zip(InputNames, Bits))
        Expected = EvaluateLogicModule(ReferenceModule, Assignment)
        Result = SimulateMinecraftRedstoneBlockMap(
            Build.Blocks,
            {InputLevers[Name]: Assignment[Name] for Name in InputNames},
            MaximumTicks=MaximumSettlingTicks,
        )
        MaximumTicks = max(MaximumTicks, Result.Ticks)
        StableRows += int(Result.Stable)
        CycleRows += int(Result.CycleDetected)
        Outputs = tuple(Result.LampLit.get(OutputLamps[Name], False) for Name in OutputNames)
        Row = RedstoneTruthTableRow(
            Bits,
            tuple(Expected[Name] for Name in OutputNames),
            Outputs,
        )
        Rows.append(Row)
        if not Row.Passed:
            break
    return RedstoneSimulationReport(
        ModuleName=ReferenceModule.Name,
        InputNames=InputNames,
        OutputNames=OutputNames,
        Rows=tuple(Rows),
        Backend="minecraft-java-subset",
        RuntimeSeconds=round(monotonic() - Started, 6),
        Diagnostics={
            "Model": "java-redstone-subset-v1",
            "StableRows": StableRows,
            "CycleRows": CycleRows,
            "MaximumTicks": MaximumTicks,
            "MaximumSettlingTicks": MaximumSettlingTicks,
            "RenderedBlockCount": len(Build.Blocks),
            "LogicalCrossCheckAvailable": True,
        },
    )


def EvaluateLogicModule(
    Module: Any,
    Assignment: dict[str, bool],
) -> dict[str, bool]:
    """Evaluate an ideal combinational IR module."""
    Values = dict(Assignment)
    Pending = list(Module.Gates)
    while Pending:
        Progress = False
        Remaining = []
        for Gate in Pending:
            Kind = Gate.Kind.value
            if Kind == "INPUT":
                for Output in Gate.Outputs:
                    Values[Output] = bool(Assignment[Output])
                Progress = True
                continue
            if any(Input not in Values for Input in Gate.Inputs):
                Remaining.append(Gate)
                continue
            Inputs = [Values[Input] for Input in Gate.Inputs]
            if Kind == "NAND":
                Result = not all(Inputs)
            elif Kind == "AND":
                Result = all(Inputs)
            elif Kind == "OR":
                Result = any(Inputs)
            elif Kind == "XOR":
                Result = sum(Inputs) % 2 == 1
            elif Kind == "NOT":
                Result = not Inputs[0]
            elif Kind in ("BUFFER", "OUTPUT"):
                Result = Inputs[0]
            else:
                raise ValueError(f"Unsupported simulated gate kind {Kind}")
            for Output in Gate.Outputs:
                Values[Output] = Result
            Progress = True
        if not Progress:
            Names = ", ".join(Gate.Name for Gate in Remaining)
            raise ValueError(
                f"Logic simulation could not resolve combinational gates: {Names}"
            )
        Pending = Remaining
    return Values


def SimulatePoweredNet(
    Root: Position,
    Graph: dict[Position, list[Position]],
    Repeaters: dict[Position, str],
) -> dict[Position, int]:
    """Propagate redstone strength through dust and directed repeaters."""
    if Root not in Graph:
        raise ValueError(f"Routed signal root is missing at {Root}")
    return PropagateRoutePower(Root, Graph, Repeaters)


def BuildPhysicalDeliveryMap(
    RoutedDesign: Any,
) -> dict[str, set[tuple[str, int]]]:
    """Return consumer inputs reached when each routed source is powered."""
    CachedDelivery = getattr(RoutedDesign, "PhysicalDeliveryMap", None)
    if CachedDelivery:
        return {
            str(Signal): set(Owners)
            for Signal, Owners in CachedDelivery.items()
        }
    PlacedGates = RoutedDesign.PlacedGates
    Producers = {
        Signal: Gate
        for Gate in PlacedGates
        if Gate.OutputPin is not None
        for Signal in Gate.Outputs
    }
    Targets: dict[str, list[Position]] = {}
    TargetOwners: dict[str, list[tuple[str, int]]] = {}
    for Signal in RoutedDesign.NetWires:
        Targets[Signal] = []
        TargetOwners[Signal] = []
    for Gate in PlacedGates:
        for InputIndex, Signal in enumerate(Gate.Inputs):
            if Signal not in Targets:
                continue
            Targets[Signal].append(Gate.InputPins[InputIndex])
            TargetOwners[Signal].append((Gate.Name, InputIndex))

    NetWires = {
        Signal: set(Positions)
        for Signal, Positions in RoutedDesign.NetWires.items()
    }
    AuthoritativeValidationComplete = bool(
        getattr(RoutedDesign, "ZeroResourceConflicts", False)
    )
    if not AuthoritativeValidationComplete:
        Conflicts, ConflictCounts = FindFlatRouteConflicts(NetWires)
        if Conflicts:
            raise ValueError(
                "Redstone simulation found cross-net shorts: "
                f"{ConflictCounts.most_common(5)}"
            )
    ActualBlocks = frozenset(getattr(
        RoutedDesign,
        "SimulationActualBlocks",
        (),
    ))
    ElectricalBlocks = frozenset(getattr(
        RoutedDesign,
        "SimulationElectricalBlocks",
        (),
    ))
    SolidBlocks = frozenset(getattr(
        RoutedDesign,
        "SimulationSolidBlocks",
        (),
    ))
    if not ActualBlocks:
        Resources = BuildRoutingResources(
            type(
                "SimulationPlacement",
                (),
                {"PlacedGates": PlacedGates},
            )()
        )
        ActualBlocks = Resources.StaticGeometry.ActualBlocks
        ElectricalBlocks = Resources.StaticGeometry.ElectricalBlocks
        SolidBlocks = Resources.StaticGeometry.SolidBlocks
    if not AuthoritativeValidationComplete:
        ValidateTemplateIsolation(
            NetWires,
            ActualBlocks,
            ElectricalBlocks,
            SolidBlocks,
            Producers,
            Targets,
            getattr(RoutedDesign, "TemplateAccessBySignal", None) or None,
        )
    Graphs = BuildPhysicalGraphs(
        NetWires,
        set(ActualBlocks),
        set(RoutedDesign.Supports),
        set(SolidBlocks),
    )
    if not AuthoritativeValidationComplete:
        ValidatePhysicalRoutes(Graphs, Producers, Targets)

    Delivered: dict[str, set[tuple[str, int]]] = {}
    for Signal, Graph in Graphs.items():
        Root = Producers[Signal].OutputPin
        Powers = SimulatePoweredNet(
            Root,
            Graph,
            {
                Position: Facing
                for Position, Facing in RoutedDesign.Repeaters.items()
                if Position in Graph
            },
        )
        Delivered[Signal] = {
            Owner
            for Owner, Target in zip(TargetOwners[Signal], Targets[Signal])
            if Powers.get(Target, 0) > 0
        }
    return Delivered


def EvaluatePhysicalModule(
    RoutedDesign: Any,
    Assignment: dict[str, bool],
    Delivered: dict[str, set[tuple[str, int]]],
) -> dict[str, bool]:
    """Evaluate NAND cells using values delivered to physical input pins."""
    Values = dict(Assignment)
    Pending = [
        Gate
        for Gate in RoutedDesign.PlacedGates
        if Gate.Kind not in ("INPUT", "OUTPUT")
    ]
    while Pending:
        Progress = False
        Remaining = []
        for Gate in Pending:
            if any(Input not in Values for Input in Gate.Inputs):
                Remaining.append(Gate)
                continue
            PhysicalInputs = [
                Values[Signal]
                and (Gate.Name, InputIndex) in Delivered.get(Signal, set())
                for InputIndex, Signal in enumerate(Gate.Inputs)
            ]
            if Gate.Kind != "NAND":
                raise ValueError(
                    f"Physical simulator requires NAND cells, got {Gate.Kind}"
                )
            Result = not all(PhysicalInputs)
            for Output in Gate.Outputs:
                Values[Output] = Result
            Progress = True
        if not Progress:
            Names = ", ".join(Gate.Name for Gate in Remaining)
            raise ValueError(
                f"Redstone simulation could not resolve cells: {Names}"
            )
        Pending = Remaining

    for Gate in RoutedDesign.PlacedGates:
        if Gate.Kind != "OUTPUT":
            continue
        Signal = Gate.Inputs[0]
        Values[Signal] = Values.get(Signal, False) and (
            Gate.Name,
            0,
        ) in Delivered.get(Signal, set())
    return Values


def BuildIndexedLogicProgram(
    Module: Any,
    InputNames: tuple[str, ...],
    OutputNames: tuple[str, ...],
    InputEnabled: dict[tuple[str, int], bool] | None = None,
) -> tuple[int, list[tuple[int, list[tuple[int, bool]], list[int]]], list[int]]:
    """Compile combinational gates into deterministic indexed instructions."""
    KindCodes = {
        "NAND": 0,
        "AND": 1,
        "OR": 2,
        "XOR": 3,
        "NOT": 4,
        "BUFFER": 5,
    }
    SignalIndices = {
        Signal: Index for Index, Signal in enumerate(InputNames)
    }
    Instructions = []
    Pending = [
        Gate
        for Gate in Module.Gates
        if getattr(Gate.Kind, "value", Gate.Kind) not in ("INPUT", "OUTPUT")
    ]
    while Pending:
        Remaining = []
        Progress = False
        for Gate in Pending:
            if any(Signal not in SignalIndices for Signal in Gate.Inputs):
                Remaining.append(Gate)
                continue
            Kind = getattr(Gate.Kind, "value", Gate.Kind)
            if Kind not in KindCodes:
                raise ValueError(f"Unsupported native simulated gate kind {Kind}")
            InputValues = [
                (
                    SignalIndices[Signal],
                    True if InputEnabled is None else InputEnabled.get(
                        (Gate.Name, InputIndex), False
                    ),
                )
                for InputIndex, Signal in enumerate(Gate.Inputs)
            ]
            OutputValues = []
            for Signal in Gate.Outputs:
                if Signal not in SignalIndices:
                    SignalIndices[Signal] = len(SignalIndices)
                OutputValues.append(SignalIndices[Signal])
            Instructions.append((KindCodes[Kind], InputValues, OutputValues))
            Progress = True
        if not Progress:
            Names = ", ".join(Gate.Name for Gate in Remaining)
            raise ValueError(
                "Native simulation could not order combinational gates: "
                f"{Names}"
            )
        Pending = Remaining
    MissingOutputs = [
        Signal for Signal in OutputNames if Signal not in SignalIndices
    ]
    if MissingOutputs:
        raise ValueError(
            "Native simulation outputs were not produced: "
            + ", ".join(MissingOutputs)
        )
    return (
        len(SignalIndices),
        Instructions,
        [SignalIndices[Signal] for Signal in OutputNames],
    )


def SimulateRoutedTruthTablePython(
    RoutedDesign: Any,
    ReferenceModule: Any | None = None,
    Delivered: dict[str, set[tuple[str, int]]] | None = None,
) -> RedstoneSimulationReport:
    """Compare every input combination with the original Python evaluator."""
    Started = monotonic()
    PhysicalModule = RoutedDesign.Module
    ReferenceModule = ReferenceModule or PhysicalModule
    InputNames = tuple(ReferenceModule.Inputs)
    OutputNames = tuple(ReferenceModule.Outputs)
    Delivered = Delivered or BuildPhysicalDeliveryMap(RoutedDesign)
    Rows = []
    for Bits in product((False, True), repeat=len(InputNames)):
        Assignment = dict(zip(InputNames, Bits))
        ExpectedValues = EvaluateLogicModule(ReferenceModule, Assignment)
        PhysicalValues = EvaluatePhysicalModule(
            RoutedDesign,
            Assignment,
            Delivered,
        )
        Rows.append(
            RedstoneTruthTableRow(
                Inputs=Bits,
                ExpectedOutputs=tuple(
                    ExpectedValues[Name]
                    for Name in OutputNames
                ),
                SimulatedOutputs=tuple(
                    PhysicalValues[Name]
                    for Name in OutputNames
                ),
            )
        )
    return RedstoneSimulationReport(
        ModuleName=ReferenceModule.Name,
        InputNames=InputNames,
        OutputNames=OutputNames,
        Rows=tuple(Rows),
        Backend="python",
        RuntimeSeconds=round(monotonic() - Started, 6),
    )


def SimulateRoutedTruthTable(
    RoutedDesign: Any,
    ReferenceModule: Any | None = None,
) -> RedstoneSimulationReport:
    """Compare every physical input combination using native parallel work."""
    Started = monotonic()
    PhysicalModule = RoutedDesign.Module
    ReferenceModule = ReferenceModule or PhysicalModule
    if (
        EvaluateLogicPrograms is None
        or os.environ.get("RCS_FORCE_PYTHON_SIMULATION") == "1"
    ):
        return SimulateRoutedTruthTablePython(RoutedDesign, ReferenceModule)
    InputNames = tuple(ReferenceModule.Inputs)
    OutputNames = tuple(ReferenceModule.Outputs)
    Delivered = BuildPhysicalDeliveryMap(RoutedDesign)
    ReferenceSignalCount, ReferenceInstructions, ReferenceOutputs = (
        BuildIndexedLogicProgram(
            ReferenceModule,
            InputNames,
            OutputNames,
        )
    )
    PhysicalInputEnabled = {
        (Gate.Name, InputIndex): (
            Gate.Name,
            InputIndex,
        ) in Delivered.get(Signal, set())
        for Gate in RoutedDesign.PlacedGates
        if Gate.Kind == "NAND"
        for InputIndex, Signal in enumerate(Gate.Inputs)
    }
    PhysicalSignalCount, PhysicalInstructions, PhysicalOutputs = (
        BuildIndexedLogicProgram(
            PhysicalModule,
            InputNames,
            OutputNames,
            PhysicalInputEnabled,
        )
    )
    OutputGates = {
        Gate.Inputs[0]: Gate
        for Gate in RoutedDesign.PlacedGates
        if Gate.Kind == "OUTPUT" and Gate.Inputs
    }
    PhysicalOutputEnabled = [
        (
            OutputGates[Signal].Name,
            0,
        ) in Delivered.get(Signal, set())
        for Signal in OutputNames
    ]
    NativeRows = EvaluateLogicPrograms(
        len(InputNames),
        ReferenceSignalCount,
        ReferenceInstructions,
        ReferenceOutputs,
        PhysicalSignalCount,
        PhysicalInstructions,
        PhysicalOutputs,
        PhysicalOutputEnabled,
    )
    Rows = tuple(
        RedstoneTruthTableRow(
            Inputs=tuple(
                AssignmentIndex
                & (1 << (len(InputNames) - InputIndex - 1))
                != 0
                for InputIndex in range(len(InputNames))
            ),
            ExpectedOutputs=tuple(
                ExpectedMask & (1 << OutputIndex) != 0
                for OutputIndex in range(len(OutputNames))
            ),
            SimulatedOutputs=tuple(
                SimulatedMask & (1 << OutputIndex) != 0
                for OutputIndex in range(len(OutputNames))
            ),
        )
        for AssignmentIndex, (ExpectedMask, SimulatedMask) in enumerate(NativeRows)
    )
    return RedstoneSimulationReport(
        ModuleName=ReferenceModule.Name,
        InputNames=InputNames,
        OutputNames=OutputNames,
        Rows=Rows,
        Backend="native-parallel",
        RuntimeSeconds=round(monotonic() - Started, 6),
    )


def WriteTruthTable(
    Report: RedstoneSimulationReport,
    OutputPath: Path,
) -> Path:
    """Write a complete human-readable physical truth table."""
    OutputPath.parent.mkdir(parents=True, exist_ok=True)
    InputHeader = " ".join(Report.InputNames)
    OutputHeader = " ".join(Report.OutputNames)
    Lines = [
        f"Redstone simulation: {Report.ModuleName}",
        f"Inputs: {InputHeader}",
        f"Outputs: {OutputHeader}",
        "",
        f"{InputHeader} | expected {OutputHeader} | simulated {OutputHeader} | result",
    ]
    for Row in Report.Rows:
        Inputs = " ".join("1" if Value else "0" for Value in Row.Inputs)
        Expected = " ".join(
            "1" if Value else "0"
            for Value in Row.ExpectedOutputs
        )
        Simulated = " ".join(
            "1" if Value else "0"
            for Value in Row.SimulatedOutputs
        )
        Lines.append(
            f"{Inputs} | {Expected} | {Simulated} | "
            f"{'PASS' if Row.Passed else 'FAIL'}"
        )
    Lines.extend(
        (
            "",
            f"Overall: {'PASS' if Report.Passed else 'FAIL'}",
            f"Rows: {len(Report.Rows)}",
        )
    )
    OutputPath.write_text("\n".join(Lines) + "\n", encoding="utf-8")
    return OutputPath

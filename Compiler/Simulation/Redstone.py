"""Exhaustive logic and routed-redstone simulation."""

from __future__ import annotations

from dataclasses import dataclass
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

    @property
    def Passed(self) -> bool:
        return all(Row.Passed for Row in self.Rows)

    @property
    def FailedRows(self) -> tuple[RedstoneTruthTableRow, ...]:
        return tuple(Row for Row in self.Rows if not Row.Passed)


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
    Conflicts, ConflictCounts = FindFlatRouteConflicts(NetWires)
    if Conflicts:
        raise ValueError(
            "Redstone simulation found cross-net shorts: "
            f"{ConflictCounts.most_common(5)}"
        )
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

"""Exhaustive logic and routed-redstone simulation."""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import product
from pathlib import Path
from typing import Any

from ..Routing.Actions import (
    BuildPhysicalGraphs,
    BuildRoutingResources,
    FindFlatRouteConflicts,
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


def RepeaterOutputDelta(Facing: str) -> Position:
    """Return signal travel direction for the compiler's repeater states."""
    try:
        return {
            "west": (1, 0, 0),
            "east": (-1, 0, 0),
            "north": (0, 0, 1),
            "south": (0, 0, -1),
        }[Facing]
    except KeyError as Error:
        raise ValueError(f"Unsupported repeater facing {Facing}") from Error


def SimulatePoweredNet(
    Root: Position,
    Graph: dict[Position, list[Position]],
    Repeaters: dict[Position, str],
) -> dict[Position, int]:
    """Propagate redstone strength through dust and directed repeaters."""
    if Root not in Graph:
        raise ValueError(f"Routed signal root is missing at {Root}")
    Powers = {Root: 15}
    Pending: list[tuple[int, Position]] = [(-15, Root)]
    while Pending:
        NegativePower, Current = heappop(Pending)
        Power = -NegativePower
        if Power != Powers.get(Current):
            continue
        CurrentFacing = Repeaters.get(Current)
        if CurrentFacing is not None:
            Delta = RepeaterOutputDelta(CurrentFacing)
            OutputPosition = (
                Current[0] + Delta[0],
                Current[1] + Delta[1],
                Current[2] + Delta[2],
            )
            Neighbors = (
                (OutputPosition, 15)
                if OutputPosition in Graph[Current]
                else ()
            )
            CandidateValues = (
                [Neighbors]
                if Neighbors
                else []
            )
        else:
            CandidateValues = []
            for Neighbor in Graph[Current]:
                NeighborFacing = Repeaters.get(Neighbor)
                if NeighborFacing is not None:
                    Delta = RepeaterOutputDelta(NeighborFacing)
                    InputPosition = (
                        Neighbor[0] - Delta[0],
                        Neighbor[1] - Delta[1],
                        Neighbor[2] - Delta[2],
                    )
                    if Current != InputPosition or Power <= 0:
                        continue
                    CandidatePower = 15
                else:
                    CandidatePower = Power - 1
                if CandidatePower > 0:
                    CandidateValues.append((Neighbor, CandidatePower))

        for Neighbor, CandidatePower in CandidateValues:
            if CandidatePower <= Powers.get(Neighbor, 0):
                continue
            Powers[Neighbor] = CandidatePower
            heappush(Pending, (-CandidatePower, Neighbor))
    return Powers


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


def SimulateRoutedTruthTable(
    RoutedDesign: Any,
    ReferenceModule: Any | None = None,
) -> RedstoneSimulationReport:
    """Compare every physical input combination with ideal logic."""
    PhysicalModule = RoutedDesign.Module
    ReferenceModule = ReferenceModule or PhysicalModule
    InputNames = tuple(ReferenceModule.Inputs)
    OutputNames = tuple(ReferenceModule.Outputs)
    Delivered = BuildPhysicalDeliveryMap(RoutedDesign)
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

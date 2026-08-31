"""Reconstruct causal logic and physical-block traces for Fabric mismatches."""

from __future__ import annotations

from typing import Any


FailureTraceSchemaVersion = 1


def _PositionKey(Value: object) -> tuple[int, int, int] | None:
    if (
        not isinstance(Value, list)
        or len(Value) != 3
        or not all(type(Axis) is int for Axis in Value)
    ):
        return None
    return tuple(Value)


def _LogicalBlockValue(State: object) -> bool | None:
    """Read the boolean redstone state exposed by one dynamic block."""
    if not isinstance(State, dict):
        return None
    Name = State.get("Name")
    Properties = State.get("Properties", {})
    if not isinstance(Properties, dict):
        return None
    if Name == "minecraft:redstone_wire":
        try:
            return int(Properties.get("power", "0")) > 0
        except (TypeError, ValueError):
            return None
    for Property in ("powered", "lit"):
        Value = Properties.get(Property)
        if isinstance(Value, bool):
            return Value
        if isinstance(Value, str) and Value.lower() in {"true", "false"}:
            return Value.lower() == "true"
    return None


def _ObservedBlocks(
    Diagnostics: dict[str, object],
) -> dict[tuple[int, int, int], dict[str, object]]:
    Values = Diagnostics.get("TraceBlocks", [])
    if not isinstance(Values, list):
        return {}
    Result = {}
    for Value in Values:
        if not isinstance(Value, dict):
            continue
        Position = _PositionKey(Value.get("Position"))
        if Position is not None:
            Result[Position] = Value
    return Result


def _BlockFailure(
    Block: dict[str, object],
    Expected: bool,
) -> dict[str, object] | None:
    Actual = _LogicalBlockValue(Block.get("State"))
    if Actual is None or Actual == Expected:
        return None
    return {
        "FixturePosition": list(Block["Position"]),
        "WorldPosition": Block.get("WorldPosition"),
        "State": Block.get("State"),
        "ExpectedPowered": Expected,
        "ActualPowered": Actual,
    }


def _SignalTrace(
    *,
    Signal: str,
    Expected: bool | None,
    SignalDocument: dict[str, object] | None,
    FallbackProbe: object,
    Observed: dict[tuple[int, int, int], dict[str, object]],
) -> dict[str, object]:
    RawPositions = (
        SignalDocument.get("ProbePositions", [])
        if SignalDocument is not None
        else []
    )
    if not RawPositions and FallbackProbe is not None:
        RawPositions = [FallbackProbe]
    Blocks = []
    for RawPosition in RawPositions:
        Position = _PositionKey(RawPosition)
        if Position is None or Position not in Observed:
            continue
        Block = Observed[Position]
        Blocks.append({
            "FixturePosition": list(Position),
            "WorldPosition": Block.get("WorldPosition"),
            "State": Block.get("State"),
            "Powered": _LogicalBlockValue(Block.get("State")),
        })
    Failures = []
    if Expected is not None:
        for RawPosition in RawPositions:
            Position = _PositionKey(RawPosition)
            if Position is None or Position not in Observed:
                continue
            Failure = _BlockFailure(Observed[Position], Expected)
            if Failure is not None:
                Failures.append(Failure)
    ComparableValues = [
        Value["Powered"]
        for Value in Blocks
        if Value["Powered"] is not None
    ]
    Status = "unobserved"
    if Expected is not None and ComparableValues:
        Status = "mismatch" if Failures else "match"
    return {
        "Signal": Signal,
        "Expected": Expected,
        "Status": Status,
        "ObservedValues": ComparableValues,
        "Blocks": Blocks,
        "MismatchBlocks": Failures,
        "FirstMismatchBlock": Failures[0] if Failures else None,
    }


def _PhysicalProbeTrace(
    RawPositions: object,
    Observed: dict[tuple[int, int, int], dict[str, object]],
) -> list[dict[str, object]]:
    if not isinstance(RawPositions, list):
        return []
    Result = []
    for RawPosition in RawPositions:
        Position = _PositionKey(RawPosition)
        if Position is None or Position not in Observed:
            continue
        Block = Observed[Position]
        Result.append({
            "FixturePosition": list(Position),
            "WorldPosition": Block.get("WorldPosition"),
            "State": Block.get("State"),
            "Powered": _LogicalBlockValue(Block.get("State")),
        })
    return Result


def BuildFabricFailureTrace(
    Fixture: dict[str, object],
    Diagnostics: dict[str, object],
) -> dict[str, object] | None:
    """Build an output-to-input subcircuit trace from one live mismatch snapshot."""
    TraceMap = Fixture.get("Trace")
    FailureKind = "mismatch"
    Mismatch = Diagnostics.get("Mismatch")
    if not isinstance(Mismatch, dict):
        FailureKind = "timeout"
        Mismatch = Diagnostics.get("Timeout")
    if not isinstance(TraceMap, dict) or not isinstance(Mismatch, dict):
        return None
    FailedOutput = Mismatch.get("Output")
    ExpectedOutput = Mismatch.get("Expected")
    ActualOutput = Mismatch.get("Actual")
    if not isinstance(FailedOutput, str) or type(ExpectedOutput) is not bool:
        return None

    RawExpectedSignals = Mismatch.get("ExpectedSignals", {})
    ExpectedSignals = {
        str(Name): Value
        for Name, Value in RawExpectedSignals.items()
        if type(Value) is bool
    } if isinstance(RawExpectedSignals, dict) else {}
    ExpectedSignals.setdefault(FailedOutput, ExpectedOutput)

    RawGates = TraceMap.get("Gates", [])
    RawSignals = TraceMap.get("Signals", [])
    Gates = {
        str(Value["Name"]): Value
        for Value in RawGates
        if isinstance(Value, dict) and isinstance(Value.get("Name"), str)
    } if isinstance(RawGates, list) else {}
    Signals = {
        str(Value["Name"]): Value
        for Value in RawSignals
        if isinstance(Value, dict) and isinstance(Value.get("Name"), str)
    } if isinstance(RawSignals, list) else {}
    GateByOutput = {
        str(Signal): Gate
        for Gate in Gates.values()
        for Signal in Gate.get("Outputs", [])
        if isinstance(Signal, str)
    }
    Observed = _ObservedBlocks(Diagnostics)

    RootGate = GateByOutput.get(FailedOutput)
    Entries: list[dict[str, object]] = []
    Visited: set[str] = set()

    def VisitGate(
        Gate: dict[str, object],
        Depth: int,
        ParentGate: str | None,
        OutputSignal: str | None = None,
    ) -> None:
        GateName = str(Gate["Name"])
        if GateName in Visited:
            return
        Visited.add(GateName)
        Outputs = [str(Value) for Value in Gate.get("Outputs", [])]
        Inputs = [str(Value) for Value in Gate.get("Inputs", [])]
        Signal = OutputSignal or (Outputs[0] if Outputs else "")
        Expected = ExpectedSignals.get(Signal)
        SignalDocument = Signals.get(Signal)
        OutputTrace = _SignalTrace(
            Signal=Signal,
            Expected=Expected,
            SignalDocument=SignalDocument,
            FallbackProbe=Gate.get("OutputProbePosition"),
            Observed=Observed,
        )
        InputTraces = [
            _SignalTrace(
                Signal=Input,
                Expected=ExpectedSignals.get(Input),
                SignalDocument=Signals.get(Input),
                FallbackProbe=None,
                Observed=Observed,
            )
            for Input in Inputs
        ]
        Entry = {
            "Depth": Depth,
            "ParentGate": ParentGate,
            "CircuitPath": Gate.get("CircuitPath", [TraceMap.get("Circuit"), GateName]),
            "Gate": GateName,
            "GateKind": Gate.get("Kind"),
            "PhysicalBlocks": _PhysicalProbeTrace(
                Gate.get("ProbePositions", []),
                Observed,
            ),
            "Output": OutputTrace,
            "Inputs": InputTraces,
        }
        Entries.append(Entry)
        for Input in Inputs:
            Producer = GateByOutput.get(Input)
            if Producer is not None:
                VisitGate(Producer, Depth + 1, GateName, Input)

    if RootGate is not None:
        VisitGate(RootGate, 0, None, FailedOutput)
    elif FailedOutput in Signals:
        ProducerName = Signals[FailedOutput].get("ProducerGate")
        Producer = Gates.get(str(ProducerName))
        if Producer is not None:
            VisitGate(Producer, 0, None, FailedOutput)

    MismatchingEntries = [
        Entry
        for Entry in Entries
        if Entry["Output"]["Status"] == "mismatch"
    ]
    CausalEntries = [
        Entry
        for Entry in MismatchingEntries
        if all(Input["Status"] == "match" for Input in Entry["Inputs"])
    ]
    Candidates = CausalEntries or MismatchingEntries
    FirstFailure = max(Candidates, key=lambda Value: Value["Depth"]) if Candidates else None
    FirstBlock = (
        FirstFailure["Output"]["FirstMismatchBlock"]
        if FirstFailure is not None
        else None
    )
    return {
        "SchemaVersion": FailureTraceSchemaVersion,
        "FailureKind": FailureKind,
        "Circuit": str(TraceMap.get("Circuit", Fixture.get("TopModule", ""))),
        "FailedOutput": FailedOutput,
        "Inputs": Mismatch.get("Inputs", {}),
        "Expected": ExpectedOutput,
        "Actual": ActualOutput,
        "TestedVectorsBeforeFailure": Mismatch.get("TestedVectorsBeforeFailure"),
        "SubcircuitTrace": Entries,
        "FirstFailingSubcircuit": (
            {
                "CircuitPath": FirstFailure["CircuitPath"],
                "Gate": FirstFailure["Gate"],
                "GateKind": FirstFailure["GateKind"],
                "Signal": FirstFailure["Output"]["Signal"],
            }
            if FirstFailure is not None
            else None
        ),
        "FirstFailingBlock": FirstBlock,
    }

#!/usr/bin/env python3
"""Benchmark pinned Freerouting against the compiler's NAND graph suite.

This is deliberately an abstract PCB-routing comparison.  The adapter preserves
the exact NAND hypergraph, but uses a deterministic synthetic placement and PCB
rules.  A clean result is not a Redstone placement or simulation acceptance.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import hypot, isclose
import os
from pathlib import Path
import platform
import re
import signal
from statistics import mean, median, pstdev
import subprocess
import sys
from time import monotonic
from typing import Any, Iterable, Sequence


RepositoryRoot = Path(__file__).resolve().parents[2]
AdapterScriptPath = Path(__file__).resolve()
NativeAcceptanceScriptPath = RepositoryRoot / "Scripts/Routing/RunRouterAcceptance.py"
UpstreamMetadataPath = (
    RepositoryRoot / "Tools/ExternalRouters/Freerouting/Upstream.json"
)
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))

from Compiler.Synthesis.Diagram import WriteNandDiagram
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly
from Compiler.Synthesis.Validation import ValidateNandOnlyDesign
from SVDecoder import Sv


SchemaVersion = "freerouting-logical-suite-v1"
AdapterVersion = "nand-to-specctra-synthetic-placement-v1"
FreeroutingVersion = "2.3.0"
FreeroutingBuildDate = "2026-08-07"
FreeroutingTagCommit = "2d4de019aa89e9fa3dc1dc44e09bf509760cafc1"
ExpectedJarBytes = 62_995_156
ExpectedJarSha256 = (
    "3cf18d608437740bc497db6b8ef5888e2e60a08de0def20691d1bad0c0e0ee24"
)
DefaultJarPath = (
    RepositoryRoot
    / "Tools/ExternalRouters/Freerouting/Upstream/freerouting-2.3.0.jar"
)

LayerNames = ("L0", "L1", "L2", "L3")
ResolutionUnitsPerMicrometer = 10
TraceWidthMicrometers = 200
ClearanceMicrometers = 200
ColumnPitchMicrometers = 6_000
RowPitchMicrometers = 4_000
BoardMarginMicrometers = 3_000
MaximumAutoroutePasses = 100
OptimizerThreads = 1


@dataclass(frozen=True)
class BenchmarkCase:
    """One case mirrored from the native router acceptance matrix."""

    Name: str
    ExamplePath: Path
    TopModule: str
    RequiredRuns: int
    TruthTableRows: int
    RuntimeCeilingSeconds: float
    NativeComparison: str


BenchmarkCases = (
    BenchmarkCase(
        Name="FullAdder",
        ExamplePath=Path("Examples/FullAdder.sv"),
        TopModule="FullAdder",
        RequiredRuns=5,
        TruthTableRows=8,
        RuntimeCeilingSeconds=10.0,
        NativeComparison="LOGICAL_GRAPH_ONLY",
    ),
    BenchmarkCase(
        Name="RippleCarryAdder4",
        ExamplePath=Path("Examples/RippleCarryAdder4.sv"),
        TopModule="RippleCarryAdder4",
        RequiredRuns=3,
        TruthTableRows=512,
        RuntimeCeilingSeconds=25.0,
        NativeComparison="LOGICAL_GRAPH_ONLY",
    ),
    BenchmarkCase(
        Name="RippleCarryAdder8",
        ExamplePath=Path("Examples/RippleCarryAdder8.sv"),
        TopModule="RippleCarryAdder8",
        RequiredRuns=3,
        TruthTableRows=131_072,
        RuntimeCeilingSeconds=30.0,
        NativeComparison="LOGICAL_GRAPH_ONLY",
    ),
    BenchmarkCase(
        Name="CarryLookaheadAdder4",
        ExamplePath=Path("Examples/CarryLookaheadAdder4.sv"),
        TopModule="CarryLookaheadAdder4",
        RequiredRuns=2,
        TruthTableRows=512,
        RuntimeCeilingSeconds=120.0,
        NativeComparison="SYNTHETIC_PLACEMENT_ONLY",
    ),
)


@dataclass(frozen=True)
class ExternalComponent:
    """One fixed synthetic PCB component corresponding to a NAND-IR gate."""

    Reference: str
    GateName: str
    Kind: str
    ImageName: str
    Level: int
    XMicrometers: int
    YMicrometers: int


@dataclass(frozen=True)
class ExternalNet:
    """One exact NAND signal hyperedge represented as a PCB net."""

    Name: str
    Signal: str
    Pins: tuple[str, ...]
    SinkCount: int
    HpwlMicrometers: int


@dataclass(frozen=True)
class ExternalProblem:
    """Deterministic synthetic PCB placement and exact logical connectivity."""

    Module: str
    Components: tuple[ExternalComponent, ...]
    Nets: tuple[ExternalNet, ...]
    BoardWidthMicrometers: int
    BoardHeightMicrometers: int
    MaximumLevel: int


FinalRouterLinePattern = re.compile(
    r"Auto-routing stage completed: started with (?P<Initial>\d+) "
    r"unrouted (?:nets|items), completed in (?P<Seconds>[0-9.]+) seconds, "
    r"final score: (?P<Score>[0-9.]+) \((?P<Unrouted>\d+) unrouted "
    r"and (?P<Violations>\d+) violations\), using "
    r"(?P<Cpu>[0-9.]+) total CPU seconds, "
    r"(?P<Allocated>[0-9.]+) GB total allocated, and "
    r"(?P<Heap>[0-9.]+) MB peak heap usage\."
)
PassLinePattern = re.compile(
    r"Auto-routing pass #(?P<Pass>\d+).*?completed in "
    r"(?P<Seconds>[0-9.]+) seconds.*?\((?P<Unrouted>\d+) unrouted "
    r"and (?P<Violations>\d+) violations\)"
)
DrcSesImportPattern = re.compile(
    r"SES file import complete: (?P<Wires>\d+) wires, "
    r"(?P<Vias>\d+) vias imported"
)
JobLinePattern = re.compile(
    r"Job '.*?' finished with state: (?P<State>[A-Z_]+) "
    r"\(elapsed: (?P<Seconds>[0-9.]+) seconds"
)
SExpressionTokenPattern = re.compile(
    r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+'
)


def Sha256File(InputPath: Path) -> str:
    """Return a streaming SHA-256 digest for an artifact."""
    Digest = sha256()
    with InputPath.open("rb") as InputFile:
        for Chunk in iter(lambda: InputFile.read(1024 * 1024), b""):
            Digest.update(Chunk)
    return Digest.hexdigest()


def WriteJson(OutputPath: Path, Payload: object) -> None:
    """Write stable, human-readable JSON."""
    OutputPath.parent.mkdir(parents=True, exist_ok=True)
    OutputPath.write_text(json.dumps(Payload, indent=2, sort_keys=True) + "\n")


def VerifyFreeroutingInstall(JarPath: Path) -> dict[str, object]:
    """Reject a missing, truncated, or silently changed router binary."""
    if not JarPath.is_file():
        raise FileNotFoundError(
            f"pinned Freerouting JAR is missing: {JarPath}"
        )
    ActualBytes = JarPath.stat().st_size
    ActualSha256 = Sha256File(JarPath)
    if ActualBytes != ExpectedJarBytes or ActualSha256 != ExpectedJarSha256:
        raise RuntimeError(
            "Freerouting install does not match the pinned v2.3.0 asset: "
            f"bytes={ActualBytes}, sha256={ActualSha256}"
        )
    try:
        RecordedJarPath = str(JarPath.relative_to(RepositoryRoot))
    except ValueError:
        RecordedJarPath = str(JarPath)
    return {
        "Version": FreeroutingVersion,
        "BuildDate": FreeroutingBuildDate,
        "TagCommit": FreeroutingTagCommit,
        "JarPath": RecordedJarPath,
        "JarBytes": ActualBytes,
        "JarSha256": ActualSha256,
    }


def BuildNandPayload(
    Case: BenchmarkCase,
    CaseDirectory: Path,
) -> tuple[dict[str, Any], dict[str, float], Path]:
    """Build and time the exact pre-placement NAND artifact."""
    Timings: dict[str, float] = {}
    WorkDirectory = CaseDirectory / "Frontend"
    NandPath = CaseDirectory / f"{Case.Name}.Nand.json"

    StartedAt = monotonic()
    Netlist = Sv.ParseSvToNetlist(
        InputPath=RepositoryRoot / Case.ExamplePath,
        TopModule=Case.TopModule,
        Workdir=WorkDirectory,
    )
    Timings["ParseSeconds"] = monotonic() - StartedAt

    StartedAt = monotonic()
    Optimized = OptimizeLogic(Netlist)
    Timings["LogicOptimizationSeconds"] = monotonic() - StartedAt

    StartedAt = monotonic()
    NandIR = ToNandOnly(Optimized)
    ValidateNandOnlyDesign(NandIR)
    Timings["NandTransformAndValidationSeconds"] = monotonic() - StartedAt

    StartedAt = monotonic()
    WriteNandDiagram(NandIR, NandPath)
    Timings["NandArtifactWriteSeconds"] = monotonic() - StartedAt
    Timings["TotalNandPreparationSeconds"] = sum(Timings.values())
    Payload = json.loads(NandPath.read_text())
    return Payload, Timings, NandPath


def GateLevels(Gates: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Return stable feed-forward levels without interpreting generated names."""
    Producers = {
        Signal: str(Gate["Name"])
        for Gate in Gates
        for Signal in Gate.get("Outputs", [])
    }
    Levels = {
        str(Gate["Name"]): 0
        for Gate in Gates
        if Gate.get("Kind") == "INPUT"
    }
    Pending = [
        Gate for Gate in Gates if str(Gate["Name"]) not in Levels
    ]
    while Pending:
        Progress = False
        Remaining = []
        for Gate in Pending:
            Dependencies = [
                Producers.get(str(Signal))
                for Signal in Gate.get("Inputs", [])
            ]
            if any(
                Producer is None or Producer not in Levels
                for Producer in Dependencies
            ):
                Remaining.append(Gate)
                continue
            Levels[str(Gate["Name"])] = 1 + max(
                (Levels[Producer] for Producer in Dependencies if Producer),
                default=0,
            )
            Progress = True
        if not Progress:
            Names = ", ".join(str(Gate["Name"]) for Gate in Remaining)
            raise ValueError(
                "NAND graph is cyclic or has an undriven dependency: " + Names
            )
        Pending = Remaining
    return Levels


def PinOffset(Kind: str, PinNumber: int) -> tuple[int, int]:
    """Return deterministic synthetic terminal geometry in micrometers."""
    if Kind == "NAND":
        Offsets = {
            1: (-1_000, -600),
            2: (-1_000, 600),
            3: (1_000, 0),
        }
        return Offsets[PinNumber]
    if PinNumber != 1:
        raise ValueError(f"{Kind} component has no pin {PinNumber}")
    return (0, 0)


def BuildExternalProblem(NandPayload: dict[str, Any]) -> ExternalProblem:
    """Map an exact NAND hypergraph onto deterministic synthetic PCB geometry."""
    Gates = list(NandPayload["Gates"])
    Levels = GateLevels(Gates)
    GatesByLevel: dict[int, list[dict[str, Any]]] = {}
    for Gate in Gates:
        GatesByLevel.setdefault(Levels[str(Gate["Name"])], []).append(Gate)
    MaximumRows = max(len(LevelGates) for LevelGates in GatesByLevel.values())
    MaximumLevel = max(GatesByLevel)
    BoardWidth = (
        2 * BoardMarginMicrometers
        + (MaximumLevel + 1) * ColumnPitchMicrometers
    )
    BoardHeight = (
        2 * BoardMarginMicrometers + MaximumRows * RowPitchMicrometers
    )

    Components = []
    GateByName = {str(Gate["Name"]): Gate for Gate in Gates}
    ComponentByGate: dict[str, ExternalComponent] = {}
    GateIndex = {str(Gate["Name"]): Index for Index, Gate in enumerate(Gates)}
    for Level in sorted(GatesByLevel):
        LevelGates = GatesByLevel[Level]
        VerticalOffset = (MaximumRows - len(LevelGates)) * RowPitchMicrometers // 2
        for Row, Gate in enumerate(LevelGates):
            Kind = str(Gate["Kind"])
            ImageName = {
                "INPUT": "InputTerminal",
                "NAND": "NandGate",
                "OUTPUT": "OutputTerminal",
            }.get(Kind)
            if ImageName is None:
                raise ValueError(f"unsupported NAND artifact gate kind: {Kind}")
            Component = ExternalComponent(
                Reference=f"U{GateIndex[str(Gate['Name'])] + 1:04d}",
                GateName=str(Gate["Name"]),
                Kind=Kind,
                ImageName=ImageName,
                Level=Level,
                XMicrometers=(
                    BoardMarginMicrometers
                    + Level * ColumnPitchMicrometers
                    + ColumnPitchMicrometers // 2
                ),
                YMicrometers=(
                    BoardMarginMicrometers
                    + VerticalOffset
                    + Row * RowPitchMicrometers
                    + RowPitchMicrometers // 2
                ),
            )
            Components.append(Component)
            ComponentByGate[Component.GateName] = Component

    Producers: dict[str, tuple[str, int]] = {}
    for Gate in Gates:
        Kind = str(Gate["Kind"])
        OutputPin = 3 if Kind == "NAND" else 1
        for Signal in Gate.get("Outputs", []):
            Producers[str(Signal)] = (str(Gate["Name"]), OutputPin)

    Consumers: dict[str, list[tuple[str, int]]] = {}
    for Gate in Gates:
        for PinIndex, Signal in enumerate(Gate.get("Inputs", []), start=1):
            Consumers.setdefault(str(Signal), []).append(
                (str(Gate["Name"]), PinIndex)
            )

    Nets = []
    for NetIndex, (Signal, Sinks) in enumerate(Consumers.items(), start=1):
        Producer = Producers.get(Signal)
        if Producer is None:
            raise ValueError(f"NAND signal has no producer: {Signal}")
        ProducerGate, ProducerPin = Producer
        Pins = [
            f"{ComponentByGate[ProducerGate].Reference}-{ProducerPin}"
        ]
        PinCoordinates = []
        ProducerComponent = ComponentByGate[ProducerGate]
        ProducerOffset = PinOffset(
            ProducerComponent.Kind,
            ProducerPin,
        )
        PinCoordinates.append((
            ProducerComponent.XMicrometers + ProducerOffset[0],
            ProducerComponent.YMicrometers + ProducerOffset[1],
        ))
        for ConsumerGate, ConsumerPin in Sinks:
            ConsumerComponent = ComponentByGate[ConsumerGate]
            Pins.append(f"{ConsumerComponent.Reference}-{ConsumerPin}")
            ConsumerOffset = PinOffset(
                ConsumerComponent.Kind,
                ConsumerPin,
            )
            PinCoordinates.append((
                ConsumerComponent.XMicrometers + ConsumerOffset[0],
                ConsumerComponent.YMicrometers + ConsumerOffset[1],
            ))
        XValues = [Point[0] for Point in PinCoordinates]
        YValues = [Point[1] for Point in PinCoordinates]
        Nets.append(ExternalNet(
            Name=f"N{NetIndex:04d}",
            Signal=Signal,
            Pins=tuple(Pins),
            SinkCount=len(Sinks),
            HpwlMicrometers=(
                max(XValues) - min(XValues) + max(YValues) - min(YValues)
            ),
        ))
    if len(ComponentByGate) != len(GateByName):
        raise ValueError("component mapping did not preserve every NAND gate")
    return ExternalProblem(
        Module=str(NandPayload["Module"]),
        Components=tuple(sorted(Components, key=lambda Item: Item.Reference)),
        Nets=tuple(Nets),
        BoardWidthMicrometers=BoardWidth,
        BoardHeightMicrometers=BoardHeight,
        MaximumLevel=MaximumLevel,
    )


def BuildDsn(Problem: ExternalProblem) -> str:
    """Render a conservative four-signal-layer Specctra DSN problem."""
    Lines = [
        f"(pcb {Problem.Module}_FreeroutingBenchmark",
        "  (parser",
        '    (string_quote ")',
        "    (space_in_quoted_tokens on)",
        '    (host_cad "RedstoneCompiler NAND benchmark adapter")',
        f'    (host_version "{AdapterVersion}")',
        "  )",
        f"  (resolution um {ResolutionUnitsPerMicrometer})",
        "  (unit um)",
        "  (structure",
    ]
    for Index, LayerName in enumerate(LayerNames):
        Lines.extend([
            f"    (layer {LayerName}",
            "      (type signal)",
            f"      (property (index {Index}))",
            "    )",
        ])
    Lines.extend([
        "    (boundary",
        "      (rect pcb 0 0 "
        f"{Problem.BoardWidthMicrometers} {Problem.BoardHeightMicrometers})",
        "    )",
        "    (via Via_Default)",
        "    (rule",
        f"      (width {TraceWidthMicrometers})",
        f"      (clearance {ClearanceMicrometers})",
        "    )",
        "    (snap_angle ninety_degree)",
        "    (control (via_at_smd on))",
        "  )",
        "  (placement",
    ])
    for ImageName in ("InputTerminal", "NandGate", "OutputTerminal"):
        ImageComponents = [
            Component
            for Component in Problem.Components
            if Component.ImageName == ImageName
        ]
        if not ImageComponents:
            continue
        Lines.append(f"    (component {ImageName}")
        for Component in ImageComponents:
            Lines.append(
                f"      (place {Component.Reference} "
                f"{Component.XMicrometers} {Component.YMicrometers} "
                f"front 0 (PN {Component.Reference}))"
            )
        Lines.append("    )")
    Lines.extend([
        "  )",
        "  (library",
        "    (image InputTerminal",
        "      (outline (rect signal -700 -700 700 700))",
        "      (pin ThroughPin 1 0 0)",
        "    )",
        "    (image NandGate",
        "      (outline (rect signal -1400 -1000 1400 1000))",
        "      (pin ThroughPin 1 -1000 -600)",
        "      (pin ThroughPin 2 -1000 600)",
        "      (pin ThroughPin 3 1000 0)",
        "    )",
        "    (image OutputTerminal",
        "      (outline (rect signal -700 -700 700 700))",
        "      (pin ThroughPin 1 0 0)",
        "    )",
        "    (padstack ThroughPin",
    ])
    for LayerName in LayerNames:
        Lines.append(f"      (shape (circle {LayerName} 600))")
    Lines.extend([
        "      (attach off)",
        "    )",
        "    (padstack Via_Default",
    ])
    for LayerName in LayerNames:
        Lines.append(f"      (shape (circle {LayerName} 600))")
    Lines.extend([
        "      (attach off)",
        "    )",
        "  )",
        "  (network",
    ])
    for Net in Problem.Nets:
        Lines.extend([
            f"    (net {Net.Name}",
            "      (pins " + " ".join(Net.Pins) + ")",
            "    )",
        ])
    Lines.extend([
        "    (class Default \"\"",
        "      " + " ".join(Net.Name for Net in Problem.Nets),
        "      (circuit",
        "        (use_via Via_Default)",
        "        (use_layer " + " ".join(LayerNames) + ")",
        "      )",
        "      (rule",
        f"        (width {TraceWidthMicrometers})",
        f"        (clearance {ClearanceMicrometers})",
        "      )",
        "    )",
        "  )",
        "  (wiring)",
        ")",
    ])
    return "\n".join(Lines) + "\n"


def ProblemMetrics(
    NandPayload: dict[str, Any],
    Problem: ExternalProblem,
) -> dict[str, object]:
    """Describe graph and synthetic-placement scale without overstating parity."""
    GateKinds: dict[str, int] = {}
    for Gate in NandPayload["Gates"]:
        Kind = str(Gate["Kind"])
        GateKinds[Kind] = GateKinds.get(Kind, 0) + 1
    return {
        "GateCount": len(NandPayload["Gates"]),
        "GateKinds": GateKinds,
        "RoutableNetCount": len(Problem.Nets),
        "SinkConnectionCount": sum(Net.SinkCount for Net in Problem.Nets),
        "MaximumFanout": max(Net.SinkCount for Net in Problem.Nets),
        "TotalHpwlMillimeters": (
            sum(Net.HpwlMicrometers for Net in Problem.Nets) / 1000.0
        ),
        "SyntheticBoardWidthMillimeters": (
            Problem.BoardWidthMicrometers / 1000.0
        ),
        "SyntheticBoardHeightMillimeters": (
            Problem.BoardHeightMicrometers / 1000.0
        ),
        "SyntheticPlacementLevels": Problem.MaximumLevel + 1,
        "RoutingLayers": list(LayerNames),
    }


def ParseSExpression(InputText: str) -> list[Any]:
    """Parse the small S-expression subset emitted by Freerouting SES files."""
    Tokens = SExpressionTokenPattern.findall(InputText)
    Stack: list[list[Any]] = []
    Roots: list[Any] = []
    for Token in Tokens:
        if Token == "(":
            NewList: list[Any] = []
            if Stack:
                Stack[-1].append(NewList)
            else:
                Roots.append(NewList)
            Stack.append(NewList)
        elif Token == ")":
            if not Stack:
                raise ValueError("unexpected closing parenthesis in SES")
            Stack.pop()
        else:
            Value = Token[1:-1] if Token.startswith('"') else Token
            if not Stack:
                raise ValueError("atom outside an S-expression")
            Stack[-1].append(Value)
    if Stack:
        raise ValueError("unterminated S-expression in SES")
    return Roots


def WalkForms(Value: Any) -> Iterable[list[Any]]:
    """Yield every list form in a parsed S-expression tree."""
    if not isinstance(Value, list):
        return
    yield Value
    for Child in Value:
        if isinstance(Child, list):
            yield from WalkForms(Child)


def ParseSesMetrics(SesPath: Path) -> dict[str, object]:
    """Extract normalized geometry, length, bends, layers, and vias from SES."""
    Roots = ParseSExpression(SesPath.read_text())
    Resolution = ResolutionUnitsPerMicrometer
    for Form in WalkForms(Roots):
        if len(Form) >= 3 and Form[0] == "resolution":
            if Form[1] != "um":
                raise ValueError(f"unsupported SES coordinate unit: {Form[1]}")
            Resolution = int(float(Form[2]))
            break

    NetworkOut = next(
        (
            Form
            for Form in WalkForms(Roots)
            if Form and Form[0] == "network_out"
        ),
        None,
    )
    if NetworkOut is None:
        raise ValueError("SES contains no network_out form")

    Segments = []
    Vias = []
    Layers = set()
    PerNetLengthUnits: dict[str, float] = {}
    WireCount = 0
    BendCount = 0
    RoutedNets = set()
    for NetForm in NetworkOut[1:]:
        if not isinstance(NetForm, list) or not NetForm or NetForm[0] != "net":
            continue
        NetName = str(NetForm[1])
        for Form in WalkForms(NetForm[2:]):
            if Form and Form[0] == "path" and len(Form) >= 7:
                LayerName = str(Form[1])
                Width = str(Form[2])
                Coordinates = [float(Value) for Value in Form[3:]]
                if len(Coordinates) % 2:
                    raise ValueError(f"odd SES coordinate count for {NetName}")
                Points = list(zip(Coordinates[0::2], Coordinates[1::2]))
                WireCount += 1
                Layers.add(LayerName)
                RoutedNets.add(NetName)
                PriorDirection = None
                for Start, End in zip(Points, Points[1:]):
                    Direction = (End[0] - Start[0], End[1] - Start[1])
                    SegmentLength = hypot(Direction[0], Direction[1])
                    PerNetLengthUnits[NetName] = (
                        PerNetLengthUnits.get(NetName, 0.0) + SegmentLength
                    )
                    CanonicalEndpoints = sorted((Start, End))
                    Segments.append((
                        NetName,
                        LayerName,
                        Width,
                        CanonicalEndpoints[0],
                        CanonicalEndpoints[1],
                    ))
                    if PriorDirection is not None:
                        CrossProduct = (
                            PriorDirection[0] * Direction[1]
                            - PriorDirection[1] * Direction[0]
                        )
                        if not isclose(CrossProduct, 0.0, abs_tol=1e-9):
                            BendCount += 1
                    PriorDirection = Direction
            elif Form and Form[0] == "via" and len(Form) >= 4:
                ViaRecord = (
                    NetName,
                    str(Form[1]),
                    float(Form[2]),
                    float(Form[3]),
                )
                Vias.append(ViaRecord)
                RoutedNets.add(NetName)

    UnitScale = float(Resolution * 1000)
    TotalLengthMillimeters = sum(PerNetLengthUnits.values()) / UnitScale
    PerNetLengthMillimeters = {
        NetName: Length / UnitScale
        for NetName, Length in sorted(PerNetLengthUnits.items())
    }
    SemanticPayload = {
        "Segments": sorted(Segments),
        "Vias": sorted(Vias),
    }
    SemanticSha256 = sha256(
        json.dumps(SemanticPayload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "RawSesSha256": Sha256File(SesPath),
        "SemanticRouteSha256": SemanticSha256,
        "SesBytes": SesPath.stat().st_size,
        "WireCount": WireCount,
        "SegmentCount": len(Segments),
        "BendCount": BendCount,
        "ViaCount": len(Vias),
        "LayersUsed": sorted(Layers),
        "RoutedNetCount": len(RoutedNets),
        "TotalLengthMillimeters": TotalLengthMillimeters,
        "PerNetLengthMillimeters": PerNetLengthMillimeters,
        "MaximumNetLengthShare": (
            max(PerNetLengthMillimeters.values()) / TotalLengthMillimeters
            if TotalLengthMillimeters > 0.0
            else 0.0
        ),
    }


def ParseRouterLog(LogText: str) -> dict[str, object]:
    """Parse pass-level and final typed status from Freerouting output."""
    Passes = [
        {
            "Pass": int(Match.group("Pass")),
            "Seconds": float(Match.group("Seconds")),
            "UnroutedItems": int(Match.group("Unrouted")),
            "Violations": int(Match.group("Violations")),
        }
        for Match in PassLinePattern.finditer(LogText)
    ]
    Matches = list(FinalRouterLinePattern.finditer(LogText))
    if not Matches:
        return {"FinalStatusFound": False, "Passes": Passes}
    Final = Matches[-1]
    JobMatches = list(JobLinePattern.finditer(LogText))
    Job = JobMatches[-1] if JobMatches else None
    return {
        "FinalStatusFound": True,
        "InitialUnroutedItems": int(Final.group("Initial")),
        "RouterStageSeconds": float(Final.group("Seconds")),
        "FinalScore": float(Final.group("Score")),
        "FinalUnroutedItems": int(Final.group("Unrouted")),
        "RouterReportedViolations": int(Final.group("Violations")),
        "RouterReportedCpuSeconds": float(Final.group("Cpu")),
        "RouterReportedAllocatedGigabytes": float(Final.group("Allocated")),
        "RouterReportedPeakHeapMegabytes": float(Final.group("Heap")),
        "JobState": Job.group("State") if Job else None,
        "JobElapsedSeconds": float(Job.group("Seconds")) if Job else None,
        "PassCount": len(Passes),
        "Passes": Passes,
    }


def ParseElapsedTime(Value: str) -> float:
    """Parse GNU time's h:mm:ss or m:ss elapsed representation."""
    Parts = Value.split(":")
    if len(Parts) == 2:
        return float(Parts[0]) * 60.0 + float(Parts[1])
    if len(Parts) == 3:
        return (
            float(Parts[0]) * 3600.0
            + float(Parts[1]) * 60.0
            + float(Parts[2])
        )
    return float(Value)


def ParseGnuTime(TimePath: Path) -> dict[str, object]:
    """Parse selected process resource metrics from GNU time -v."""
    if not TimePath.is_file():
        return {}
    Fields = {}
    for Line in TimePath.read_text().splitlines():
        if ": " not in Line:
            continue
        Key, Value = Line.strip().split(": ", 1)
        Fields[Key] = Value
    Result: dict[str, object] = {}
    NumericFields = {
        "User time (seconds)": "UserCpuSeconds",
        "System time (seconds)": "SystemCpuSeconds",
        "Maximum resident set size (kbytes)": "MaximumResidentSetKilobytes",
        "Voluntary context switches": "VoluntaryContextSwitches",
        "Involuntary context switches": "InvoluntaryContextSwitches",
        "File system inputs": "FileSystemInputs",
        "File system outputs": "FileSystemOutputs",
    }
    for SourceName, ResultName in NumericFields.items():
        Value = Fields.get(SourceName)
        if Value is None:
            continue
        Result[ResultName] = (
            float(Value) if "Seconds" in ResultName else int(Value)
        )
    Elapsed = next(
        (
            Value
            for Key, Value in Fields.items()
            if Key.startswith("Elapsed (wall clock) time")
        ),
        None,
    )
    if Elapsed is not None:
        Result["GnuTimeWallSeconds"] = ParseElapsedTime(Elapsed)
    Percent = Fields.get("Percent of CPU this job got")
    if Percent and Percent.endswith("%"):
        Result["CpuUtilizationPercent"] = float(Percent[:-1])
    return Result


def RouterCommand(
    JavaExecutable: str,
    JarPath: Path,
    InputDsnPath: Path,
    OutputSesPath: Path,
    UserDataPath: Path,
    RuntimeCeilingSeconds: float,
    MaximumPasses: int,
) -> list[str]:
    """Build the fully pinned, headless, route-only Freerouting command."""
    TimeoutSeconds = max(1, int(RuntimeCeilingSeconds))
    Hours, Remainder = divmod(TimeoutSeconds, 3600)
    Minutes, Seconds = divmod(Remainder, 60)
    return [
        JavaExecutable,
        "-Djava.awt.headless=true",
        "-XX:-UsePerfData",
        "-jar",
        str(JarPath),
        "--gui.enabled=false",
        "--api_server.enabled=false",
        "-da",
        "--router.fanout.enabled=false",
        "--router.optimizer.enabled=false",
        "--router.strict_drc=true",
        f"--router.job_timeout={Hours:02d}:{Minutes:02d}:{Seconds:02d}",
        f"--user_data_path={UserDataPath}",
        "--logging.file.enabled=false",
        "-mp",
        str(MaximumPasses),
        "-mt",
        str(OptimizerThreads),
        "-us",
        "greedy",
        "-is",
        "sequential",
        "-ll",
        "INFO",
        "-de",
        str(InputDsnPath),
        "-do",
        str(OutputSesPath),
    ]


def DrcCommand(
    JavaExecutable: str,
    JarPath: Path,
    InputDsnPath: Path,
    InputSesPath: Path,
    DrcReportPath: Path,
    UserDataPath: Path,
) -> list[str]:
    """Build the separate DSN+SES validation command required by v2.3.0."""
    return [
        JavaExecutable,
        "-Djava.awt.headless=true",
        "-XX:-UsePerfData",
        "-jar",
        str(JarPath),
        "--gui.enabled=false",
        "--api_server.enabled=false",
        "-da",
        f"--user_data_path={UserDataPath}",
        "--logging.file.enabled=false",
        "-ll",
        "INFO",
        "-de",
        str(InputDsnPath),
        str(InputSesPath),
        "-drc",
        str(DrcReportPath),
    ]


def RunTimedProcess(
    Command: Sequence[str],
    LogPath: Path,
    TimePath: Path,
    TimeoutSeconds: float,
) -> tuple[int | None, bool, float]:
    """Run one isolated process group so timeouts cannot orphan the JVM."""
    FullCommand = [
        "/usr/bin/time",
        "-v",
        "-o",
        str(TimePath),
        *Command,
    ]
    StartedAt = monotonic()
    Process = subprocess.Popen(
        FullCommand,
        cwd=RepositoryRoot,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    TimedOut = False
    try:
        Output, _ = Process.communicate(timeout=TimeoutSeconds)
    except subprocess.TimeoutExpired:
        TimedOut = True
        try:
            os.killpg(Process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            Output, _ = Process.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(Process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            Output, _ = Process.communicate()
    WallSeconds = monotonic() - StartedAt
    LogPath.write_text(Output or "")
    return Process.returncode, TimedOut, WallSeconds


def ParseDrcReport(
    DrcReportPath: Path,
    DrcLogText: str,
) -> dict[str, object]:
    """Judge Freerouting DRC JSON; its process exits zero even on violations."""
    if not DrcReportPath.is_file() or DrcReportPath.stat().st_size == 0:
        return {"ReportFound": False}
    Payload = json.loads(DrcReportPath.read_text())
    UnconnectedItems = Payload.get("unconnected_items", [])
    Violations = Payload.get("violations", [])
    SchematicParity = Payload.get("schematic_parity", [])
    ImportMatch = DrcSesImportPattern.search(DrcLogText)
    CanonicalPayload = dict(Payload)
    CanonicalPayload.pop("date", None)
    return {
        "ReportFound": True,
        "FreeroutingVersion": Payload.get("freerouting_version"),
        "CoordinateUnits": Payload.get("coordinate_units"),
        "Source": Payload.get("source"),
        "QualityScore": Payload.get("quality_score"),
        "UnconnectedItemGroups": len(UnconnectedItems),
        "ViolationCount": len(Violations),
        "SchematicParityCount": len(SchematicParity),
        "SessionImportVerified": ImportMatch is not None,
        "ImportedWireCount": (
            int(ImportMatch.group("Wires")) if ImportMatch else None
        ),
        "ImportedViaCount": (
            int(ImportMatch.group("Vias")) if ImportMatch else None
        ),
        "CanonicalReportSha256": sha256(
            json.dumps(
                CanonicalPayload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def ClassifyRun(
    ExitCode: int | None,
    TimedOut: bool,
    SesPath: Path,
    RouterMetrics: dict[str, object],
    DrcExitCode: int | None,
    DrcTimedOut: bool,
    DrcMetrics: dict[str, object],
) -> str:
    """Return an explicit external-router outcome instead of trusting exit zero."""
    if TimedOut:
        return "TIMEOUT"
    if ExitCode != 0:
        return "PROCESS_FAILURE"
    if not SesPath.is_file() or SesPath.stat().st_size == 0:
        return "MISSING_ROUTE_ARTIFACT"
    if not RouterMetrics.get("FinalStatusFound"):
        return "UNVERIFIED_ROUTER_RESULT"
    if RouterMetrics.get("JobState") != "COMPLETED":
        return "ROUTER_JOB_NOT_COMPLETED"
    if int(RouterMetrics["FinalUnroutedItems"]) > 0:
        return "INCOMPLETE"
    if int(RouterMetrics["RouterReportedViolations"]) > 0:
        return "PCB_DRC_VIOLATION"
    if DrcTimedOut:
        return "DRC_TIMEOUT"
    if DrcExitCode != 0:
        return "DRC_PROCESS_FAILURE"
    if not DrcMetrics.get("ReportFound"):
        return "MISSING_DRC_REPORT"
    if not DrcMetrics.get("SessionImportVerified"):
        return "DRC_SESSION_IMPORT_UNVERIFIED"
    if int(DrcMetrics["UnconnectedItemGroups"]) > 0:
        return "INCOMPLETE"
    if int(DrcMetrics["ViolationCount"]) > 0:
        return "PCB_DRC_VIOLATION"
    if int(DrcMetrics["SchematicParityCount"]) > 0:
        return "PCB_SCHEMATIC_PARITY_FAILURE"
    return "PCB_DRC_CLEAN"


def ValidateCrossArtifactEvidence(
    Status: str,
    ProblemMetricsValue: dict[str, object],
    RouteMetrics: dict[str, object],
    DrcMetrics: dict[str, object],
) -> str:
    """Require parsed SES geometry to agree with graph and DRC import evidence."""
    if Status != "PCB_DRC_CLEAN":
        return Status
    if int(RouteMetrics.get("RoutedNetCount", -1)) != int(
        ProblemMetricsValue["RoutableNetCount"]
    ):
        return "ROUTED_NET_COUNT_MISMATCH"
    if int(RouteMetrics.get("WireCount", -1)) != int(
        DrcMetrics.get("ImportedWireCount", -2)
    ):
        return "DRC_IMPORTED_WIRE_COUNT_MISMATCH"
    if int(RouteMetrics.get("ViaCount", -1)) != int(
        DrcMetrics.get("ImportedViaCount", -2)
    ):
        return "DRC_IMPORTED_VIA_COUNT_MISMATCH"
    if DrcMetrics.get("FreeroutingVersion") != (
        f"Freerouting {FreeroutingVersion}"
    ):
        return "DRC_ROUTER_VERSION_MISMATCH"
    return Status


def RunOneBenchmark(
    Case: BenchmarkCase,
    ProblemMetricsValue: dict[str, object],
    DsnPath: Path,
    JarPath: Path,
    JavaExecutable: str,
    RunDirectory: Path,
    RunNumber: int,
    MaximumPasses: int,
) -> dict[str, object]:
    """Execute, time, validate, and summarize one external routing run."""
    RunDirectory.mkdir(parents=True, exist_ok=True)
    UserDataPath = RunDirectory / "UserData"
    UserDataPath.mkdir(parents=True, exist_ok=True)
    SesPath = RunDirectory / f"{Case.Name}.ses"
    LogPath = RunDirectory / "Freerouting.log"
    TimePath = RunDirectory / "GnuTime.txt"
    Command = RouterCommand(
        JavaExecutable=JavaExecutable,
        JarPath=JarPath,
        InputDsnPath=DsnPath,
        OutputSesPath=SesPath,
        UserDataPath=UserDataPath,
        RuntimeCeilingSeconds=Case.RuntimeCeilingSeconds,
        MaximumPasses=MaximumPasses,
    )
    ExitCode, TimedOut, WallSeconds = RunTimedProcess(
        Command=Command,
        LogPath=LogPath,
        TimePath=TimePath,
        TimeoutSeconds=Case.RuntimeCeilingSeconds + 2.0,
    )
    RouterMetrics = ParseRouterLog(LogPath.read_text())
    DrcReportPath = RunDirectory / f"{Case.Name}.Drc.json"
    DrcLogPath = RunDirectory / "FreeroutingDrc.log"
    DrcTimePath = RunDirectory / "GnuTimeDrc.txt"
    DrcExitCode: int | None = None
    DrcTimedOut = False
    DrcWallSeconds = 0.0
    DrcMetrics: dict[str, object] = {"ReportFound": False}
    DrcCommandValue: list[str] | None = None
    if SesPath.is_file() and SesPath.stat().st_size > 0:
        DrcUserDataPath = RunDirectory / "DrcUserData"
        DrcUserDataPath.mkdir(parents=True, exist_ok=True)
        DrcCommandValue = DrcCommand(
            JavaExecutable=JavaExecutable,
            JarPath=JarPath,
            InputDsnPath=DsnPath,
            InputSesPath=SesPath,
            DrcReportPath=DrcReportPath,
            UserDataPath=DrcUserDataPath,
        )
        DrcExitCode, DrcTimedOut, DrcWallSeconds = RunTimedProcess(
            Command=DrcCommandValue,
            LogPath=DrcLogPath,
            TimePath=DrcTimePath,
            TimeoutSeconds=Case.RuntimeCeilingSeconds + 2.0,
        )
        try:
            DrcMetrics = ParseDrcReport(
                DrcReportPath=DrcReportPath,
                DrcLogText=DrcLogPath.read_text(),
            )
        except (json.JSONDecodeError, OSError, ValueError) as Error:
            DrcMetrics = {"ReportFound": False, "ParseError": str(Error)}
    Status = ClassifyRun(
        ExitCode=ExitCode,
        TimedOut=TimedOut,
        SesPath=SesPath,
        RouterMetrics=RouterMetrics,
        DrcExitCode=DrcExitCode,
        DrcTimedOut=DrcTimedOut,
        DrcMetrics=DrcMetrics,
    )
    RouteMetrics: dict[str, object] = {}
    RouteParseSeconds = 0.0
    if SesPath.is_file() and SesPath.stat().st_size > 0:
        StartedAt = monotonic()
        try:
            RouteMetrics = ParseSesMetrics(SesPath)
        except (ValueError, OSError) as Error:
            RouteMetrics = {"ParseError": str(Error)}
            if Status == "PCB_DRC_CLEAN":
                Status = "ROUTE_ARTIFACT_PARSE_FAILURE"
        RouteParseSeconds = monotonic() - StartedAt

    if (
        RouterMetrics.get("FinalStatusFound")
        and RouterMetrics.get("InitialUnroutedItems")
        != ProblemMetricsValue["SinkConnectionCount"]
    ):
        Status = "INPUT_CONNECTION_COUNT_MISMATCH"
    Status = ValidateCrossArtifactEvidence(
        Status=Status,
        ProblemMetricsValue=ProblemMetricsValue,
        RouteMetrics=RouteMetrics,
        DrcMetrics=DrcMetrics,
    )

    Result = {
        "Run": RunNumber,
        "Status": Status,
        "Passed": Status == "PCB_DRC_CLEAN",
        "ExitCode": ExitCode,
        "TimedOut": TimedOut,
        "DrcExitCode": DrcExitCode,
        "DrcTimedOut": DrcTimedOut,
        "WallSeconds": WallSeconds,
        "DrcWallSeconds": DrcWallSeconds,
        "TotalRouteAndDrcWallSeconds": WallSeconds + DrcWallSeconds,
        "RouteArtifactParseSeconds": RouteParseSeconds,
        "RuntimeCeilingSeconds": Case.RuntimeCeilingSeconds,
        "WithinNativeWallCeiling": WallSeconds <= Case.RuntimeCeilingSeconds,
        "Command": Command,
        "DrcCommand": DrcCommandValue,
        "LogPath": str(LogPath),
        "TimePath": str(TimePath),
        "DrcLogPath": str(DrcLogPath) if DrcLogPath.exists() else None,
        "DrcTimePath": str(DrcTimePath) if DrcTimePath.exists() else None,
        "DrcReportPath": (
            str(DrcReportPath) if DrcReportPath.exists() else None
        ),
        "SesPath": str(SesPath) if SesPath.exists() else None,
        "RouterMetrics": RouterMetrics,
        "ProcessMetrics": ParseGnuTime(TimePath),
        "DrcProcessMetrics": ParseGnuTime(DrcTimePath),
        "DrcMetrics": DrcMetrics,
        "RouteMetrics": RouteMetrics,
    }
    WriteJson(RunDirectory / "RunResult.json", Result)
    return Result


def NumericSummary(Values: Sequence[float]) -> dict[str, float]:
    """Return stable descriptive statistics for repeated timings."""
    if not Values:
        return {}
    return {
        "Minimum": min(Values),
        "Maximum": max(Values),
        "Mean": mean(Values),
        "Median": median(Values),
        "PopulationStandardDeviation": pstdev(Values),
    }


def SummarizeRuns(Runs: Sequence[dict[str, object]]) -> dict[str, object]:
    """Aggregate repeated route results and determinism evidence."""
    WallSeconds = [float(Run["WallSeconds"]) for Run in Runs]
    DrcWallSeconds = [float(Run["DrcWallSeconds"]) for Run in Runs]
    TotalWallSeconds = [
        float(Run["TotalRouteAndDrcWallSeconds"]) for Run in Runs
    ]
    RouterSeconds = [
        float(Run["RouterMetrics"]["RouterStageSeconds"])
        for Run in Runs
        if Run["RouterMetrics"].get("FinalStatusFound")
    ]
    UserCpuSeconds = [
        float(Run["ProcessMetrics"]["UserCpuSeconds"])
        for Run in Runs
        if "UserCpuSeconds" in Run["ProcessMetrics"]
    ]
    DrcUserCpuSeconds = [
        float(Run["DrcProcessMetrics"]["UserCpuSeconds"])
        for Run in Runs
        if "UserCpuSeconds" in Run["DrcProcessMetrics"]
    ]
    ResidentSetSizes = [
        int(Run["ProcessMetrics"]["MaximumResidentSetKilobytes"])
        for Run in Runs
        if "MaximumResidentSetKilobytes" in Run["ProcessMetrics"]
    ]
    DrcResidentSetSizes = [
        int(Run["DrcProcessMetrics"]["MaximumResidentSetKilobytes"])
        for Run in Runs
        if "MaximumResidentSetKilobytes" in Run["DrcProcessMetrics"]
    ]
    SemanticHashes = [
        str(Run["RouteMetrics"]["SemanticRouteSha256"])
        for Run in Runs
        if "SemanticRouteSha256" in Run["RouteMetrics"]
    ]
    return {
        "RequiredRuns": len(Runs),
        "PassedRuns": sum(bool(Run["Passed"]) for Run in Runs),
        "AllPassed": all(bool(Run["Passed"]) for Run in Runs),
        "Statuses": [str(Run["Status"]) for Run in Runs],
        "WallSeconds": NumericSummary(WallSeconds),
        "DrcWallSeconds": NumericSummary(DrcWallSeconds),
        "TotalRouteAndDrcWallSeconds": NumericSummary(TotalWallSeconds),
        "RouterStageSeconds": NumericSummary(RouterSeconds),
        "UserCpuSeconds": NumericSummary(UserCpuSeconds),
        "DrcUserCpuSeconds": NumericSummary(DrcUserCpuSeconds),
        "MaximumResidentSetKilobytes": max(ResidentSetSizes, default=0),
        "DrcMaximumResidentSetKilobytes": max(
            DrcResidentSetSizes,
            default=0,
        ),
        "SemanticRouteHashes": SemanticHashes,
        "DeterministicSemanticRoute": (
            bool(SemanticHashes)
            and len(SemanticHashes) == len(Runs)
            and len(set(SemanticHashes)) == 1
        ),
    }


def ReadCommandOutput(Command: Sequence[str]) -> str:
    """Return compact diagnostic output without making it benchmark timing."""
    Completed = subprocess.run(
        Command,
        cwd=RepositoryRoot,
        capture_output=True,
        text=True,
        check=False,
    )
    return (Completed.stdout + Completed.stderr).strip()


def HostMetadata(JavaExecutable: str) -> dict[str, object]:
    """Capture drift-prone host and checkout facts beside the measurements."""
    GovernorPath = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    GitHead = ReadCommandOutput(["git", "rev-parse", "HEAD"])
    GitStatus = ReadCommandOutput(["git", "status", "--short"])
    return {
        "RecordedAtUtc": datetime.now(timezone.utc).isoformat(),
        "Platform": platform.platform(),
        "Python": platform.python_version(),
        "Java": ReadCommandOutput([JavaExecutable, "-version"]),
        "LogicalCpuCount": os.cpu_count(),
        "LoadAverage": list(os.getloadavg()),
        "CpuGovernor": (
            GovernorPath.read_text().strip()
            if GovernorPath.is_file()
            else None
        ),
        "GitHead": GitHead,
        "TrackedWorktreeDirty": bool(GitStatus),
        "GitStatusAtStart": GitStatus.splitlines(),
    }


def BuildMarkdownReport(Manifest: dict[str, Any]) -> str:
    """Render the machine-readable manifest into a concise audit report."""
    Lines = [
        "# Freerouting v2.3.0 logical-suite benchmark",
        "",
        f"Recorded: `{Manifest['Host']['RecordedAtUtc']}`",
        "",
        "Freerouting was run sequentially against the exact NAND hypergraph for "
        "each native acceptance circuit. The adapter supplied a deterministic "
        "four-layer synthetic PCB placement. `PCB_DRC_CLEAN` means Freerouting "
        "reported zero unrouted items and zero violations; it is not Redstone "
        "physical or truth-table acceptance.",
        "",
        "| Case | Runs | Result | Route wall mean (range) | Router core mean | DRC wall mean | Peak RSS | Route length | Bends | Vias | Deterministic |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for CaseName, CaseResult in Manifest["Cases"].items():
        Summary = CaseResult["Summary"]
        Wall = Summary["WallSeconds"]
        Router = Summary["RouterStageSeconds"]
        DrcWall = Summary["DrcWallSeconds"]
        LastRun = CaseResult["Runs"][-1]
        Route = LastRun["RouteMetrics"]
        Lines.append(
            f"| {CaseName} | {Summary['PassedRuns']}/{Summary['RequiredRuns']} "
            f"| {', '.join(sorted(set(Summary['Statuses'])))} "
            f"| {Wall.get('Mean', 0.0):.3f}s "
            f"({Wall.get('Minimum', 0.0):.3f}-{Wall.get('Maximum', 0.0):.3f}) "
            f"| {Router.get('Mean', 0.0):.3f}s "
            f"| {DrcWall.get('Mean', 0.0):.3f}s "
            f"| {Summary['MaximumResidentSetKilobytes'] / 1024.0:.1f} MiB "
            f"| {Route.get('TotalLengthMillimeters', 0.0):.1f} mm "
            f"| {Route.get('BendCount', 0)} "
            f"| {Route.get('ViaCount', 0)} "
            f"| {'yes' if Summary['DeterministicSemanticRoute'] else 'no'} |"
        )
    Lines.extend([
        "",
        "## Comparison boundary",
        "",
        "- Inputs are the same optimized NAND graphs, not the same physical search graph.",
        "- Placement, layers, pin geometry, and clearances are synthetic PCB contracts.",
        "- The CLA4 result is synthetic-placement-only because the native flow currently fails during placement before routing.",
        "- Freerouting does not validate Redstone support, required air, dust adjacency, repeater direction/power, materialization, or Minecraft simulation.",
        "- Route-only mode disables Freerouting fanout and optimization; JVM startup remains included in process wall time.",
        "",
    ])
    return "\n".join(Lines)


def ParseArguments(Arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse benchmark controls while keeping the official matrix as default."""
    Parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --case FullAdder --runs 1\n"
            "  %(prog)s --output-dir Output/Benchmarks/Freerouting/manual\n\n"
            "This compares abstract PCB routing only; it is not Minecraft or "
            "Redstone validation."
        ),
    )
    Parser.add_argument(
        "--jar",
        type=Path,
        default=DefaultJarPath,
        help="path to the pinned Freerouting v2.3.0 JAR",
    )
    Parser.add_argument("--java", default="java")
    Parser.add_argument(
        "--output-dir",
        type=Path,
        help="artifact directory; defaults to a timestamped Output/Benchmarks path",
    )
    Parser.add_argument(
        "--case",
        action="append",
        choices=[Case.Name for Case in BenchmarkCases],
        help="run only the named case; may be repeated",
    )
    Parser.add_argument(
        "--runs",
        type=int,
        help="override each selected case's official repetition count",
    )
    Parser.add_argument(
        "--maximum-passes",
        type=int,
        default=MaximumAutoroutePasses,
    )
    Parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the selected synthetic PCB benchmark plan without running Java",
    )
    Parsed = Parser.parse_args(Arguments)
    if Parsed.runs is not None and Parsed.runs <= 0:
        Parser.error("--runs must be positive")
    if Parsed.maximum_passes <= 0:
        Parser.error("--maximum-passes must be positive")
    return Parsed


def GuidedArguments() -> list[str]:
    """Guide a no-flag invocation to a non-destructive benchmark preview."""
    print("RedstoneCompiler Freerouting benchmark")
    print("1) Preview FullAdder benchmark (recommended)\n2) Run FullAdder once\n3) Choose another case")
    Choice = input("Choose a mode [1]: ").strip() or "1"
    if Choice == "1":
        return ["--case", "FullAdder", "--runs", "1", "--dry-run"]
    if Choice == "2":
        return ["--case", "FullAdder", "--runs", "1"]
    if Choice == "3":
        Names = ", ".join(Case.Name for Case in BenchmarkCases)
        Name = input(f"Case ({Names}): ").strip()
        if not Name:
            raise ValueError("a benchmark case is required")
        return ["--case", Name, "--runs", "1", "--dry-run"]
    raise ValueError("choose 1, 2, or 3")


def Main(Arguments: Sequence[str] | None = None) -> int:
    """Run the selected benchmark matrix and publish JSON plus Markdown evidence."""
    RawArguments = list(sys.argv[1:] if Arguments is None else Arguments)
    if not RawArguments:
        try:
            RawArguments = GuidedArguments()
        except (EOFError, KeyboardInterrupt):
            print("No benchmark mode selected. Run with --help for explicit commands.")
            return 2
        except ValueError as Error:
            raise SystemExit(str(Error)) from Error
    Options = ParseArguments(RawArguments)
    SelectedNames = set(Options.case or [Case.Name for Case in BenchmarkCases])
    SelectedCases = [Case for Case in BenchmarkCases if Case.Name in SelectedNames]
    if Options.dry_run:
        print("Synthetic PCB benchmark plan (not Redstone validation):")
        for Case in SelectedCases:
            print(f"- {Case.Name}: {Options.runs or Case.RequiredRuns} run(s)")
        print(f"Jar: {Options.jar}")
        return 0
    JarPath = Options.jar.resolve()
    Router = VerifyFreeroutingInstall(JarPath)
    Timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OutputDirectory = (
        Options.output_dir.resolve()
        if Options.output_dir
        else RepositoryRoot
        / "Output/Benchmarks/Freerouting"
        / Timestamp
    )
    if OutputDirectory.is_dir() and any(OutputDirectory.iterdir()):
        raise FileExistsError(
            "benchmark output directory is not empty; choose a fresh path: "
            f"{OutputDirectory}"
        )
    OutputDirectory.mkdir(parents=True, exist_ok=True)
    Manifest: dict[str, Any] = {
        "SchemaVersion": SchemaVersion,
        "AdapterVersion": AdapterVersion,
        "Mode": "LOGICAL_SUITE_SYNTHETIC_PCB_PLACEMENT",
        "Router": Router,
        "SourceProvenance": {
            "AdapterScript": {
                "Path": str(AdapterScriptPath.relative_to(RepositoryRoot)),
                "Sha256": Sha256File(AdapterScriptPath),
            },
            "NativeAcceptanceMatrix": {
                "Path": str(
                    NativeAcceptanceScriptPath.relative_to(RepositoryRoot)
                ),
                "Sha256": Sha256File(NativeAcceptanceScriptPath),
            },
            "UpstreamMetadata": {
                "Path": str(UpstreamMetadataPath.relative_to(RepositoryRoot)),
                "Sha256": Sha256File(UpstreamMetadataPath),
            },
        },
        "Host": HostMetadata(Options.java),
        "Configuration": {
            "SequentialRuns": True,
            "MaximumAutoroutePasses": Options.maximum_passes,
            "OptimizerThreads": OptimizerThreads,
            "FreeroutingFanoutEnabled": False,
            "FreeroutingOptimizerEnabled": False,
            "FreeroutingStrictDrc": True,
            "SeparateDsnPlusSesDrcValidation": True,
            "SyntheticRoutingLayers": list(LayerNames),
            "TraceWidthMicrometers": TraceWidthMicrometers,
            "ClearanceMicrometers": ClearanceMicrometers,
            "Placement": "deterministic topological columns",
        },
        "ComparisonBoundary": {
            "Preserved": [
                "SystemVerilog source",
                "optimized NAND gate graph",
                "signal hyperedges and fanout",
                "official case names and repetition counts",
                "native per-case wall ceilings",
            ],
            "NotPreserved": [
                "native Redstone placement",
                "Minecraft resource graph and capacity",
                "cell claims, supports, and required air",
                "dust adjacency and repeater power semantics",
                "materialization and physical truth-table simulation",
            ],
            "Cla4": "SYNTHETIC_PLACEMENT_ONLY",
        },
        "Cases": {},
    }

    for Case in SelectedCases:
        CaseDirectory = OutputDirectory / Case.Name
        CaseDirectory.mkdir(parents=True, exist_ok=True)
        Payload, NandTimings, NandPath = BuildNandPayload(
            Case=Case,
            CaseDirectory=CaseDirectory,
        )
        StartedAt = monotonic()
        Problem = BuildExternalProblem(Payload)
        DsnText = BuildDsn(Problem)
        DsnPath = CaseDirectory / f"{Case.Name}.dsn"
        DsnPath.write_text(DsnText)
        AdapterSeconds = monotonic() - StartedAt
        Metrics = ProblemMetrics(Payload, Problem)
        RunCount = Options.runs or Case.RequiredRuns
        Runs = []
        for RunNumber in range(1, RunCount + 1):
            print(
                f"[{Case.Name}] Freerouting run {RunNumber}/{RunCount}",
                flush=True,
            )
            Run = RunOneBenchmark(
                Case=Case,
                ProblemMetricsValue=Metrics,
                DsnPath=DsnPath,
                JarPath=JarPath,
                JavaExecutable=Options.java,
                RunDirectory=CaseDirectory / f"Run{RunNumber:02d}",
                RunNumber=RunNumber,
                MaximumPasses=Options.maximum_passes,
            )
            Runs.append(Run)
            print(
                f"[{Case.Name}] run {RunNumber}: {Run['Status']} "
                f"in {Run['WallSeconds']:.3f}s",
                flush=True,
            )
        CaseResult = {
            "Case": {
                **asdict(Case),
                "ExamplePath": str(Case.ExamplePath),
            },
            "NandArtifact": {
                "Path": str(NandPath),
                "Sha256": Sha256File(NandPath),
            },
            "SystemVerilogSource": {
                "Path": str(Case.ExamplePath),
                "Sha256": Sha256File(RepositoryRoot / Case.ExamplePath),
            },
            "DsnArtifact": {
                "Path": str(DsnPath),
                "Bytes": DsnPath.stat().st_size,
                "Sha256": Sha256File(DsnPath),
            },
            "PreparationTimings": {
                **NandTimings,
                "AdapterAndDsnWriteSeconds": AdapterSeconds,
            },
            "ProblemMetrics": Metrics,
            "Runs": Runs,
            "Summary": SummarizeRuns(Runs),
        }
        Manifest["Cases"][Case.Name] = CaseResult
        WriteJson(OutputDirectory / "BenchmarkManifest.partial.json", Manifest)

    Manifest["Overall"] = {
        "CaseCount": len(SelectedCases),
        "RunCount": sum(
            len(CaseResult["Runs"])
            for CaseResult in Manifest["Cases"].values()
        ),
        "PassedRunCount": sum(
            sum(bool(Run["Passed"]) for Run in CaseResult["Runs"])
            for CaseResult in Manifest["Cases"].values()
        ),
        "AllRunsPassed": all(
            CaseResult["Summary"]["AllPassed"]
            for CaseResult in Manifest["Cases"].values()
        ),
    }
    ManifestPath = OutputDirectory / "BenchmarkManifest.json"
    ReportPath = OutputDirectory / "Report.md"
    WriteJson(ManifestPath, Manifest)
    ReportPath.write_text(BuildMarkdownReport(Manifest))
    print(f"Manifest: {ManifestPath}")
    print(f"Report: {ReportPath}")
    return 0 if Manifest["Overall"]["AllRunsPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(Main())

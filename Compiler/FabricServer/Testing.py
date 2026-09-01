"""Imported-schematic test planning for the authenticated Fabric harness."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from Compiler.Ir.Models import Gate, GateKind, ModuleIR
from SVDecoder import Sv

from .Fixture import FabricFixtureArtifact
from .Validation import BuildExpectedVectors


def ReadFabricFixture(
    PathValue: Path,
) -> tuple[FabricFixtureArtifact, dict[str, object]]:
    """Read a previously imported fixture without changing its byte identity."""
    ResolvedPath = Path(PathValue).expanduser().resolve()
    Encoded = ResolvedPath.read_bytes()
    try:
        Fixture = json.loads(Encoded)
    except json.JSONDecodeError as Error:
        raise ValueError(f"Fabric fixture is not valid JSON: {ResolvedPath}") from Error
    if not isinstance(Fixture, dict):
        raise ValueError(f"Fabric fixture must be a JSON object: {ResolvedPath}")
    for Field in ("Blocks", "Inputs", "Outputs"):
        if not isinstance(Fixture.get(Field), list):
            raise ValueError(f"Fabric fixture has no {Field} list: {ResolvedPath}")
    Artifact = FabricFixtureArtifact(
        Path=ResolvedPath,
        Sha256=sha256(Encoded).hexdigest(),
        BlockCount=len(Fixture["Blocks"]),
        InputCount=len(Fixture["Inputs"]),
        OutputCount=len(Fixture["Outputs"]),
    )
    return Artifact, Fixture


def ReadNandModule(PathValue: Path) -> ModuleIR:
    """Recreate the logic oracle from the compiler's serialized NAND module."""
    ResolvedPath = Path(PathValue).expanduser().resolve()
    try:
        Document = json.loads(ResolvedPath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as Error:
        raise ValueError(f"NAND artifact is not valid JSON: {ResolvedPath}") from Error
    if not isinstance(Document, dict):
        raise ValueError(f"NAND artifact must be a JSON object: {ResolvedPath}")
    ModuleName = Document.get("Module")
    Inputs = Document.get("Inputs")
    Outputs = Document.get("Outputs")
    GateDocuments = Document.get("Gates")
    if not isinstance(ModuleName, str) or not ModuleName:
        raise ValueError(f"NAND artifact has no Module name: {ResolvedPath}")
    if (
        not isinstance(Inputs, list)
        or not isinstance(Outputs, list)
        or not all(isinstance(Value, str) and Value for Value in [*Inputs, *Outputs])
    ):
        raise ValueError(f"NAND artifact has invalid Inputs or Outputs: {ResolvedPath}")
    if not isinstance(GateDocuments, list):
        raise ValueError(f"NAND artifact has no Gates list: {ResolvedPath}")
    Gates: list[Gate] = []
    for GateDocument in GateDocuments:
        if not isinstance(GateDocument, dict):
            raise ValueError(f"NAND artifact contains a non-object gate: {ResolvedPath}")
        Name = GateDocument.get("Name")
        KindText = GateDocument.get("Kind")
        GateInputs = GateDocument.get("Inputs")
        GateOutputs = GateDocument.get("Outputs")
        if (
            not isinstance(Name, str)
            or not isinstance(KindText, str)
            or not isinstance(GateInputs, list)
            or not isinstance(GateOutputs, list)
            or not all(isinstance(Value, str) for Value in [*GateInputs, *GateOutputs])
        ):
            raise ValueError(f"NAND artifact has an invalid gate: {ResolvedPath}")
        try:
            Kind = GateKind(KindText)
        except ValueError as Error:
            raise ValueError(f"NAND artifact has unknown gate kind {KindText!r}") from Error
        Gates.append(Gate(Name=Name, Kind=Kind, Inputs=GateInputs, Outputs=GateOutputs))
    return ModuleIR(
        Name=ModuleName,
        Inputs=list(Inputs),
        Outputs=list(Outputs),
        Gates=Gates,
        SourcePath=ResolvedPath,
    )


def ReadSvModule(
    PathValue: Path,
    *,
    TopModule: str | None = None,
) -> ModuleIR:
    """Parse a SystemVerilog source file into the live-test logic oracle."""
    ResolvedPath = Path(PathValue).expanduser().resolve()
    Netlist = Sv.ParseSvToNetlist(
        InputPath=ResolvedPath,
        TopModule=TopModule,
    )
    return Netlist.Modules[Netlist.Top]


def FixturePortNames(Fixture: dict[str, object], Field: str) -> list[str]:
    """Return the validated, deterministic signal names for a fixture port kind."""
    Values = Fixture.get(Field)
    if not isinstance(Values, list) or not Values:
        raise ValueError(f"Fabric fixture has no testable {Field.lower()}")
    Names = []
    for Value in Values:
        if not isinstance(Value, dict) or not isinstance(Value.get("Name"), str):
            raise ValueError(f"Fabric fixture has an invalid {Field.lower()} port")
        Names.append(Value["Name"])
    if len(set(Names)) != len(Names):
        raise ValueError(f"Fabric fixture has duplicate {Field.lower()} port names")
    return Names


def BuildImportedSchematicVectors(
    Fixture: dict[str, object],
    Module: ModuleIR,
) -> list[dict[str, object]]:
    """Build a real physical test plan from the imported port contract."""
    InputNames = FixturePortNames(Fixture, "Inputs")
    OutputNames = FixturePortNames(Fixture, "Outputs")
    if set(InputNames) != set(Module.Inputs):
        raise ValueError(
            "fixture inputs do not match the logic oracle: "
            f"fixture={sorted(InputNames)}, oracle={sorted(Module.Inputs)}",
        )
    try:
        return BuildExpectedVectors(Module, InputNames, OutputNames)
    except KeyError as Error:
        raise ValueError(
            f"fixture output {Error.args[0]!r} is not produced by the logic oracle",
        ) from Error

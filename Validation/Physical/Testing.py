"""Readers for backend-neutral physical-validation artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from Compilation.Ir.Models import Gate, GateKind, ModuleIR


def ReadNandModule(PathValue: Path) -> ModuleIR:
    """Recreate the logic oracle from a serialized NAND module."""
    ResolvedPath = Path(PathValue).expanduser().resolve()
    try:
        Document = json.loads(ResolvedPath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as Error:
        raise ValueError(
            f"NAND artifact is not valid JSON: {ResolvedPath}",
        ) from Error
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
        or not all(
            isinstance(Value, str) and Value
            for Value in [*Inputs, *Outputs]
        )
    ):
        raise ValueError(
            f"NAND artifact has invalid Inputs or Outputs: {ResolvedPath}",
        )
    if not isinstance(GateDocuments, list):
        raise ValueError(f"NAND artifact has no Gates list: {ResolvedPath}")
    Gates: list[Gate] = []
    for GateDocument in GateDocuments:
        if not isinstance(GateDocument, dict):
            raise ValueError(
                f"NAND artifact contains a non-object gate: {ResolvedPath}",
            )
        Name = GateDocument.get("Name")
        KindText = GateDocument.get("Kind")
        GateInputs = GateDocument.get("Inputs")
        GateOutputs = GateDocument.get("Outputs")
        if (
            not isinstance(Name, str)
            or not isinstance(KindText, str)
            or not isinstance(GateInputs, list)
            or not isinstance(GateOutputs, list)
            or not all(
                isinstance(Value, str)
                for Value in [*GateInputs, *GateOutputs]
            )
        ):
            raise ValueError(f"NAND artifact has an invalid gate: {ResolvedPath}")
        try:
            Kind = GateKind(KindText)
        except ValueError as Error:
            raise ValueError(
                f"NAND artifact has unknown gate kind {KindText!r}",
            ) from Error
        Gates.append(Gate(
            Name=Name,
            Kind=Kind,
            Inputs=GateInputs,
            Outputs=GateOutputs,
        ))
    return ModuleIR(
        Name=ModuleName,
        Inputs=list(Inputs),
        Outputs=list(Outputs),
        Gates=Gates,
        SourcePath=ResolvedPath,
    )

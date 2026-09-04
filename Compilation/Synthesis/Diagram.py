"""NAND-only machine-readable artifact writer."""

from __future__ import annotations

import json
from pathlib import Path

from ..Ir.Models import NetlistIR


def WriteNandDiagram(Netlist: NetlistIR, DiagramPath: Path) -> Path:
    """Write the machine-readable NAND design as JSON."""
    Module = Netlist.Modules[Netlist.Top]
    DiagramPath.parent.mkdir(parents=True, exist_ok=True)
    Payload = {
        "Module": Module.Name,
        "Inputs": Module.Inputs,
        "Outputs": Module.Outputs,
        "Gates": [
            {
                "Name": Gate.Name,
                "Kind": Gate.Kind.value,
                "Inputs": Gate.Inputs,
                "Outputs": Gate.Outputs,
            }
            for Gate in Module.Gates
        ],
    }
    DiagramPath.write_text(json.dumps(Payload, indent=2) + "\n")
    return DiagramPath

"""NAND-only diagram artifact writers."""

from __future__ import annotations

import json
from pathlib import Path

from ..Ir.Models import NetlistIR


def WriteNandDiagram(Netlist: NetlistIR, DiagramPath: Path) -> Path:
    """Write machine-readable JSON and a neighboring Graphviz DOT diagram."""
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

    DotPath = DiagramPath.with_suffix(".dot")
    Producers = {
        Output: Gate.Name
        for Gate in Module.Gates
        for Output in Gate.Outputs
    }
    Lines = [
        "digraph NandDiagram {",
        '  rankdir="LR";',
        '  node [fontname="monospace", shape=box];',
    ]
    for Gate in Module.Gates:
        Lines.append(
            f'  "{Gate.Name}" [label="{Gate.Name}\\n{Gate.Kind.value}"];'
        )
    for Gate in Module.Gates:
        for Signal in Gate.Inputs:
            Source = Producers.get(Signal)
            if Source is not None:
                Lines.append(f'  "{Source}" -> "{Gate.Name}" [label="{Signal}"];')
    Lines.append("}")
    DotPath.write_text("\n".join(Lines) + "\n")
    return DotPath

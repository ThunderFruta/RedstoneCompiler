"""Hard invariants for the NAND-only physical backend."""

from __future__ import annotations

from typing import Any

from ..Ir.Models import GateKind


AllowedNandOnlyKinds = frozenset(
    (GateKind.INPUT.value, GateKind.NAND.value, GateKind.OUTPUT.value)
)


def _DesignGates(Design: Any) -> list[Any]:
    """Return gates from any supported compiler stage contract."""
    if hasattr(Design, "Modules"):
        Module = Design.Modules[Design.Top]
        return Module.Gates
    elif hasattr(Design, "PlacedGates"):
        return Design.PlacedGates
    elif hasattr(Design, "Gates"):
        return Design.Gates
    raise TypeError("NAND-only validation requires a netlist, module, or placement")


def ValidateNandOnlyDesign(Design: Any, ReferenceDesign: Any | None = None) -> None:
    """Reject non-NAND logic and loss/duplication across physical lowering."""
    Gates = _DesignGates(Design)

    Invalid = []
    for Gate in Gates:
        KindValue = Gate.Kind.value if hasattr(Gate.Kind, "value") else str(Gate.Kind)
        if KindValue not in AllowedNandOnlyKinds:
            Invalid.append(f"{Gate.Name}:{KindValue}")
    if Invalid:
        raise ValueError(
            "NAND-only physical backend rejected non-NAND logic cells: "
            + ", ".join(sorted(Invalid))
        )

    LogicalNands = [
        Gate.Name
        for Gate in Gates
        if (Gate.Kind.value if hasattr(Gate.Kind, "value") else str(Gate.Kind))
        == GateKind.NAND.value
    ]
    if len(LogicalNands) != len(set(LogicalNands)):
        raise ValueError("NAND-only physical backend requires unique NAND cell names")
    if ReferenceDesign is not None:
        ReferenceNands = {
            Gate.Name
            for Gate in _DesignGates(ReferenceDesign)
            if (
                Gate.Kind.value
                if hasattr(Gate.Kind, "value")
                else str(Gate.Kind)
            )
            == GateKind.NAND.value
        }
        ActualNands = set(LogicalNands)
        if ActualNands != ReferenceNands:
            Missing = sorted(ReferenceNands - ActualNands)
            Unexpected = sorted(ActualNands - ReferenceNands)
            raise ValueError(
                "Placed NAND cells do not map one-to-one to lowered NAND IR: "
                f"missing={Missing}, unexpected={Unexpected}"
            )

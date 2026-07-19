"""Transform scalar logic gates to NAND-only equivalents."""

from __future__ import annotations

from collections import Counter

from ..Ir.Models import Gate, GateKind, ModuleIR, NetIR, NetlistIR


def ToNandOnly(Netlist: NetlistIR) -> NetlistIR:
    """Expand and structurally optimize logic into two-input NAND gates."""
    SourceModule = Netlist.Modules[Netlist.Top]
    Module = ModuleIR(
        Name=SourceModule.Name,
        Ports=dict(SourceModule.Ports),
        Inputs=list(SourceModule.Inputs),
        Outputs=list(SourceModule.Outputs),
        Nets=dict(SourceModule.Nets),
        SourcePath=SourceModule.SourcePath,
    )
    GateIndex = 0
    NetIndex = 0
    Aliases: dict[str, str] = {}
    MemoizedNands: dict[tuple[str, str], str] = {}
    Inversions: dict[str, str] = {}
    Producers = {
        SourceGate.Output: SourceGate
        for SourceGate in SourceModule.Gates
    }
    Consumers = Counter(
        Input
        for SourceGate in SourceModule.Gates
        for Input in SourceGate.Inputs
    )
    FusedAndOutputs = {
        Input
        for SourceGate in SourceModule.Gates
        if SourceGate.Kind == GateKind.OR
        for Input in SourceGate.Inputs
        if Input in Producers
        and Producers[Input].Kind == GateKind.AND
        and Consumers[Input] == 1
    }

    def Resolve(Signal: str) -> str:
        Trail = []
        while Signal in Aliases:
            Trail.append(Signal)
            Signal = Aliases[Signal]
        for Alias in Trail:
            Aliases[Alias] = Signal
        return Signal

    def AddNand(Inputs: list[str], Output: str | None = None) -> str:
        nonlocal GateIndex, NetIndex
        Inputs = [Resolve(Input) for Input in Inputs]
        if len(Inputs) != 2:
            raise ValueError("NAND gates require exactly two inputs")

        if Inputs[0] == Inputs[1] and Inputs[0] in Inversions:
            Existing = Resolve(Inversions[Inputs[0]])
            if Output is not None:
                Aliases[Output] = Existing
            return Existing

        Key = tuple(sorted(Inputs))
        if Key in MemoizedNands:
            Existing = Resolve(MemoizedNands[Key])
            if Output is not None:
                Aliases[Output] = Existing
            return Existing

        if Output is None:
            Output = f"NandNet{NetIndex}"
            NetIndex += 1
        Module.Nets.setdefault(Output, NetIR(Name=Output))
        Module.Gates.append(
            Gate(
                Name=f"NandGate{GateIndex}",
                Kind=GateKind.NAND,
                Outputs=[Output],
                Inputs=Inputs,
            )
        )
        MemoizedNands[Key] = Output
        if Inputs[0] == Inputs[1]:
            Inversions[Inputs[0]] = Output
            Inversions[Output] = Inputs[0]
        GateIndex += 1
        return Output

    for Name in Module.Inputs:
        Module.Gates.append(
            Gate(Name=f"Input{Name}", Kind=GateKind.INPUT, Outputs=[Name])
        )

    for SourceGate in SourceModule.Gates:
        if (
            SourceGate.Kind == GateKind.AND
            and SourceGate.Output in FusedAndOutputs
        ):
            continue

        Inputs = [Resolve(Input) for Input in SourceGate.Inputs]
        Output = SourceGate.Output
        if SourceGate.Kind == GateKind.NAND:
            AddNand(Inputs, Output)
        elif SourceGate.Kind == GateKind.NOT:
            AddNand([Inputs[0], Inputs[0]], Output)
        elif SourceGate.Kind == GateKind.AND:
            Inverted = AddNand(Inputs)
            AddNand([Inverted, Inverted], Output)
        elif SourceGate.Kind == GateKind.OR:
            NegatedInputs = []
            for OriginalInput in SourceGate.Inputs:
                Producer = Producers.get(OriginalInput)
                if OriginalInput in FusedAndOutputs and Producer is not None:
                    NegatedInputs.append(
                        AddNand(
                            [Resolve(Input) for Input in Producer.Inputs]
                        )
                    )
                else:
                    Input = Resolve(OriginalInput)
                    NegatedInputs.append(AddNand([Input, Input]))
            AddNand(NegatedInputs, Output)
        elif SourceGate.Kind == GateKind.XOR:
            Shared = AddNand(Inputs)
            Left = AddNand([Inputs[0], Shared])
            Right = AddNand([Inputs[1], Shared])
            AddNand([Left, Right], Output)
        elif SourceGate.Kind == GateKind.BUFFER:
            Aliases[Output] = Inputs[0]
        else:
            raise NotImplementedError(f"Unsupported logic gate: {SourceGate.Kind}")

    for Name in Module.Outputs:
        Module.Gates.append(
            Gate(
                Name=f"Output{Name}",
                Kind=GateKind.OUTPUT,
                Outputs=[f"{Name}$Output"],
                Inputs=[Resolve(Name)],
            )
        )

    return NetlistIR(Top=Netlist.Top, Modules={Netlist.Top: Module})

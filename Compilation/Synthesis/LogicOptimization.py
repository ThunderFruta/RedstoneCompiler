"""Boolean simplification before NAND-only technology mapping."""

from __future__ import annotations

from dataclasses import dataclass

from ..Ir.Models import Gate, GateKind, ModuleIR, NetIR, NetlistIR


CommutativeKinds = {
    GateKind.NAND,
    GateKind.AND,
    GateKind.OR,
    GateKind.XOR,
}


@dataclass(frozen=True)
class Implicant:
    """One Quine-McCluskey cube represented by value and cared-bit masks."""

    Value: int
    CareMask: int

    def Covers(self, Minterm: int) -> bool:
        return (Minterm & self.CareMask) == (self.Value & self.CareMask)

    @property
    def LiteralCount(self) -> int:
        return self.CareMask.bit_count()


def CountNands(Netlist: NetlistIR) -> int:
    """Technology-map a candidate and count its physical NAND cells."""
    from .NandTransform import ToNandOnly

    NandNetlist = ToNandOnly(Netlist)
    return sum(
        GateValue.Kind == GateKind.NAND
        for GateValue in NandNetlist.Modules[NandNetlist.Top].Gates
    )


def StructurallySimplifyLogic(Netlist: NetlistIR) -> NetlistIR:
    """Apply aliases, identities, common subexpressions, and dead-code removal."""
    Source = Netlist.Modules[Netlist.Top]
    Module = ModuleIR(
        Name=Source.Name,
        Ports=dict(Source.Ports),
        Inputs=list(Source.Inputs),
        Outputs=list(Source.Outputs),
        Nets=dict(Source.Nets),
        SourcePath=Source.SourcePath,
    )
    Aliases: dict[str, str] = {}
    Expressions: dict[tuple[GateKind, tuple[str, ...]], str] = {}
    Inversions: dict[str, str] = {}

    def Resolve(Signal: str) -> str:
        Trail = []
        while Signal in Aliases:
            Trail.append(Signal)
            Signal = Aliases[Signal]
        for Alias in Trail:
            Aliases[Alias] = Signal
        return Signal

    for SourceGate in Source.Gates:
        Inputs = [Resolve(Input) for Input in SourceGate.Inputs]
        Output = SourceGate.Output
        Result: str | None = None

        if SourceGate.Kind == GateKind.BUFFER:
            Result = Inputs[0]
        elif (
            SourceGate.Kind in (GateKind.AND, GateKind.OR)
            and Inputs[0] == Inputs[1]
        ):
            Result = Inputs[0]
        elif SourceGate.Kind == GateKind.NOT and Inputs[0] in Inversions:
            Result = Resolve(Inversions[Inputs[0]])

        SignatureInputs = (
            tuple(sorted(Inputs))
            if SourceGate.Kind in CommutativeKinds
            else tuple(Inputs)
        )
        Signature = SourceGate.Kind, SignatureInputs
        if Result is None and Signature in Expressions:
            Result = Resolve(Expressions[Signature])

        if Result is not None:
            Aliases[Output] = Result
            continue

        GateValue = Gate(
            Name=SourceGate.Name,
            Kind=SourceGate.Kind,
            Outputs=[Output],
            Inputs=Inputs,
            Attrs=dict(SourceGate.Attrs),
        )
        Module.Gates.append(GateValue)
        Expressions[Signature] = Output
        if SourceGate.Kind == GateKind.NOT:
            Inversions[Inputs[0]] = Output
            Inversions[Output] = Inputs[0]

    for Output in Module.Outputs:
        Resolved = Resolve(Output)
        if Resolved == Output:
            continue
        Module.Gates.append(
            Gate(
                Name=f"OptimizedOutput{Output}",
                Kind=GateKind.BUFFER,
                Outputs=[Output],
                Inputs=[Resolved],
            )
        )

    Producers = {
        GateValue.Output: GateValue
        for GateValue in Module.Gates
    }
    RequiredSignals = list(Module.Outputs)
    RequiredGates: set[str] = set()
    while RequiredSignals:
        Signal = RequiredSignals.pop()
        Producer = Producers.get(Signal)
        if Producer is None or Producer.Name in RequiredGates:
            continue
        RequiredGates.add(Producer.Name)
        RequiredSignals.extend(Producer.Inputs)
    Module.Gates = [
        GateValue
        for GateValue in Module.Gates
        if GateValue.Name in RequiredGates
    ]
    return NetlistIR(Top=Netlist.Top, Modules={Netlist.Top: Module})


def EvaluateModuleOutputs(
    Module: ModuleIR,
    Assignment: int,
) -> dict[str, bool]:
    """Evaluate all outputs for one packed primary-input assignment."""
    Values = {
        Name: bool((Assignment >> Index) & 1)
        for Index, Name in enumerate(Module.Inputs)
    }
    for GateValue in Module.Gates:
        Inputs = [Values[Signal] for Signal in GateValue.Inputs]
        if GateValue.Kind == GateKind.NOT:
            Value = not Inputs[0]
        elif GateValue.Kind == GateKind.AND:
            Value = all(Inputs)
        elif GateValue.Kind == GateKind.NAND:
            Value = not all(Inputs)
        elif GateValue.Kind == GateKind.OR:
            Value = any(Inputs)
        elif GateValue.Kind == GateKind.XOR:
            Value = Inputs[0] != Inputs[1]
        elif GateValue.Kind == GateKind.BUFFER:
            Value = Inputs[0]
        else:
            raise ValueError(
                f"Cannot truth-table optimize gate kind {GateValue.Kind.value}"
            )
        Values[GateValue.Output] = Value
    return {
        Output: Values[Output]
        for Output in Module.Outputs
    }


def GeneratePrimeImplicants(
    Minterms: set[int],
    VariableCount: int,
) -> set[Implicant]:
    """Generate prime implicants with the Quine-McCluskey combination pass."""
    FullMask = (1 << VariableCount) - 1
    Current = {
        Implicant(Minterm, FullMask)
        for Minterm in Minterms
    }
    Primes: set[Implicant] = set()

    while Current:
        Combined: set[Implicant] = set()
        Used: set[Implicant] = set()
        CurrentList = sorted(Current, key=lambda Cube: (Cube.CareMask, Cube.Value))
        for Index, First in enumerate(CurrentList):
            for Second in CurrentList[Index + 1 :]:
                if First.CareMask != Second.CareMask:
                    continue
                Difference = (First.Value ^ Second.Value) & First.CareMask
                if Difference.bit_count() != 1:
                    continue
                Used.add(First)
                Used.add(Second)
                NewMask = First.CareMask & ~Difference
                Combined.add(Implicant(First.Value & NewMask, NewMask))
        Primes.update(Current - Used)
        Current = Combined
    return Primes


def SelectMinimumCover(
    Primes: set[Implicant],
    Minterms: set[int],
) -> tuple[Implicant, ...]:
    """Select an exact minimum prime cover by literals, then term count."""
    Covering = {
        Minterm: [
            Prime
            for Prime in Primes
            if Prime.Covers(Minterm)
        ]
        for Minterm in Minterms
    }
    Selected: set[Implicant] = set()
    Remaining = set(Minterms)

    for Minterm, Options in Covering.items():
        if len(Options) == 1:
            Selected.add(Options[0])
    for Prime in Selected:
        Remaining = {
            Minterm
            for Minterm in Remaining
            if not Prime.Covers(Minterm)
        }
    if not Remaining:
        return tuple(sorted(Selected, key=lambda Cube: (Cube.CareMask, Cube.Value)))

    Best: tuple[tuple[int, int], set[Implicant]] | None = None

    def Search(
        Uncovered: set[int],
        Chosen: set[Implicant],
    ) -> None:
        nonlocal Best
        if not Uncovered:
            Complete = Selected | Chosen
            Score = (
                sum(Prime.LiteralCount for Prime in Complete),
                len(Complete),
            )
            if Best is None or Score < Best[0]:
                Best = Score, set(Complete)
            return
        if Best is not None:
            LiteralLowerBound = sum(
                Prime.LiteralCount
                for Prime in Selected | Chosen
            )
            if (
                LiteralLowerBound > Best[0][0]
                or (
                    LiteralLowerBound == Best[0][0]
                    and len(Selected | Chosen) >= Best[0][1]
                )
            ):
                return

        Minterm = min(
            Uncovered,
            key=lambda Value: len(
                [Prime for Prime in Covering[Value] if Prime not in Selected]
            ),
        )
        Options = sorted(
            (
                Prime
                for Prime in Covering[Minterm]
                if Prime not in Selected
            ),
            key=lambda Prime: (
                -sum(Prime.Covers(Value) for Value in Uncovered),
                Prime.LiteralCount,
                Prime.Value,
            ),
        )
        for Prime in Options:
            Search(
                {
                    Value
                    for Value in Uncovered
                    if not Prime.Covers(Value)
                },
                Chosen | {Prime},
            )

    Search(Remaining, set())
    if Best is None:
        raise ValueError("Quine-McCluskey could not cover the output minterms")
    return tuple(sorted(Best[1], key=lambda Cube: (Cube.CareMask, Cube.Value)))


def MinimizeOutputTruthTable(
    TrueMinterms: set[int],
    VariableCount: int,
) -> tuple[tuple[Implicant, ...], bool] | None:
    """Choose a compact SOP for the output or its complement."""
    Universe = set(range(1 << VariableCount))
    FalseMinterms = Universe - TrueMinterms
    if not TrueMinterms or not FalseMinterms:
        return None

    Choices = []
    for Minterms, Complement in (
        (TrueMinterms, False),
        (FalseMinterms, True),
    ):
        Primes = GeneratePrimeImplicants(Minterms, VariableCount)
        Cubes = SelectMinimumCover(Primes, Minterms)
        Score = (
            sum(Cube.LiteralCount for Cube in Cubes)
            + max(0, len(Cubes) - 1)
            + int(Complement),
            len(Cubes),
        )
        Choices.append((Score, Cubes, Complement))
    _, Cubes, Complement = min(Choices, key=lambda Choice: Choice[0])
    return Cubes, Complement


def BuildTruthTableCandidate(
    Source: ModuleIR,
    MinimizedOutputs: dict[str, tuple[tuple[Implicant, ...], bool]],
) -> NetlistIR:
    """Rebuild minimized output equations with shared literals and terms."""
    Module = ModuleIR(
        Name=Source.Name,
        Ports=dict(Source.Ports),
        Inputs=list(Source.Inputs),
        Outputs=list(Source.Outputs),
        Nets={
            Name: NetIR(
                Name=Net.Name,
                Width=Net.Width,
                IsConstant=Net.IsConstant,
                Value=Net.Value,
            )
            for Name, Net in Source.Nets.items()
            if Name in Source.Inputs or Name in Source.Outputs
        },
        SourcePath=Source.SourcePath,
    )
    Expressions: dict[tuple[GateKind, tuple[str, ...]], str] = {}
    GateIndex = 0
    NetIndex = 0

    def Emit(Kind: GateKind, Inputs: list[str]) -> str:
        nonlocal GateIndex, NetIndex
        KeyInputs = (
            tuple(sorted(Inputs))
            if Kind in CommutativeKinds
            else tuple(Inputs)
        )
        Key = Kind, KeyInputs
        if Key in Expressions:
            return Expressions[Key]
        Output = f"MinimizedNet{NetIndex}"
        NetIndex += 1
        Module.Nets[Output] = NetIR(Name=Output)
        Module.Gates.append(
            Gate(
                Name=f"Minimized{Kind.value.title()}Gate{GateIndex}",
                Kind=Kind,
                Outputs=[Output],
                Inputs=Inputs,
            )
        )
        GateIndex += 1
        Expressions[Key] = Output
        return Output

    def Combine(Kind: GateKind, Signals: list[str]) -> str:
        Result = Signals[0]
        for Signal in Signals[1:]:
            Result = Emit(Kind, [Result, Signal])
        return Result

    for Output in Source.Outputs:
        Cubes, Complement = MinimizedOutputs[Output]
        Terms = []
        for Cube in Cubes:
            Literals = []
            for Index, Input in enumerate(Source.Inputs):
                Bit = 1 << Index
                if not Cube.CareMask & Bit:
                    continue
                Literals.append(
                    Input
                    if Cube.Value & Bit
                    else Emit(GateKind.NOT, [Input])
                )
            if not Literals:
                raise ValueError("Constant implicants are not supported")
            Terms.append(Combine(GateKind.AND, Literals))
        Result = Combine(GateKind.OR, Terms)
        if Complement:
            Result = Emit(GateKind.NOT, [Result])
        Module.Gates.append(
            Gate(
                Name=f"MinimizedOutput{Output}",
                Kind=GateKind.BUFFER,
                Outputs=[Output],
                Inputs=[Result],
            )
        )
    return NetlistIR(Top=Source.Name, Modules={Source.Name: Module})


def OptimizeLogic(
    Netlist: NetlistIR,
    MaximumTruthTableInputs: int = 6,
) -> NetlistIR:
    """Reduce logic before NAND mapping, retaining only cheaper equivalents."""
    Structural = StructurallySimplifyLogic(Netlist)
    Module = Structural.Modules[Structural.Top]
    if len(Module.Inputs) > MaximumTruthTableInputs:
        return Structural

    TruthMinterms = {
        Output: set()
        for Output in Module.Outputs
    }
    for Assignment in range(1 << len(Module.Inputs)):
        Values = EvaluateModuleOutputs(Module, Assignment)
        for Output, Value in Values.items():
            if Value:
                TruthMinterms[Output].add(Assignment)

    MinimizedOutputs = {}
    for Output, Minterms in TruthMinterms.items():
        Minimized = MinimizeOutputTruthTable(
            Minterms,
            len(Module.Inputs),
        )
        if Minimized is None:
            return Structural
        MinimizedOutputs[Output] = Minimized

    Candidate = BuildTruthTableCandidate(Module, MinimizedOutputs)
    return Candidate if CountNands(Candidate) < CountNands(Structural) else Structural

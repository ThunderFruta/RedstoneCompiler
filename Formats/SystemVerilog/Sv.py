"""Small SystemVerilog frontend for scalar combinational modules."""

from __future__ import annotations

from pathlib import Path
import re

from Compilation.Ir.Models import GateKind, Gate, ModuleIR, NetIR, NetlistIR


class ExpressionParser:
    """Parse a scalar bitwise expression and emit logic gates."""

    def __init__(self, Text: str, Module: ModuleIR) -> None:
        self.Tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*|[()~&|^]", Text)
        CompactText = re.sub(r"\s+", "", Text)
        if "".join(self.Tokens) != CompactText:
            raise ValueError(f"Unsupported expression syntax: {Text.strip()}")
        self.Index = 0
        self.Module = Module
        self.TempIndex = 0

    def Parse(self) -> str:
        Result = self.ParseOr()
        if self.Index != len(self.Tokens):
            raise ValueError(f"Unexpected token: {self.Tokens[self.Index]}")
        return Result

    def ParseOr(self) -> str:
        Left = self.ParseXor()
        while self.Match("|"):
            Left = self.Emit(GateKind.OR, [Left, self.ParseXor()])
        return Left

    def ParseXor(self) -> str:
        Left = self.ParseAnd()
        while self.Match("^"):
            Left = self.Emit(GateKind.XOR, [Left, self.ParseAnd()])
        return Left

    def ParseAnd(self) -> str:
        Left = self.ParseUnary()
        while self.Match("&"):
            Left = self.Emit(GateKind.AND, [Left, self.ParseUnary()])
        return Left

    def ParseUnary(self) -> str:
        if self.Match("~"):
            return self.Emit(GateKind.NOT, [self.ParseUnary()])
        if self.Match("("):
            Result = self.ParseOr()
            self.Expect(")")
            return Result
        if self.Index >= len(self.Tokens):
            raise ValueError("Expected a signal name")
        Token = self.Tokens[self.Index]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", Token):
            raise ValueError(f"Expected a signal name, found {Token}")
        self.Index += 1
        if Token not in self.Module.Nets:
            raise ValueError(f"Unknown signal in expression: {Token}")
        return Token

    def Emit(self, Kind: GateKind, Inputs: list[str]) -> str:
        Output = f"LogicNet{len(self.Module.Gates)}_{self.TempIndex}"
        self.TempIndex += 1
        self.Module.Nets[Output] = NetIR(Name=Output)
        self.Module.Gates.append(
            Gate(
                Name=f"{Kind.value.title()}Gate{len(self.Module.Gates)}",
                Kind=Kind,
                Outputs=[Output],
                Inputs=Inputs,
            )
        )
        return Output

    def Match(self, Token: str) -> bool:
        if self.Index < len(self.Tokens) and self.Tokens[self.Index] == Token:
            self.Index += 1
            return True
        return False

    def Expect(self, Token: str) -> None:
        if not self.Match(Token):
            Found = self.Tokens[self.Index] if self.Index < len(self.Tokens) else "end of expression"
            raise ValueError(f"Expected {Token}, found {Found}")


def StripComments(Text: str) -> str:
    Text = re.sub(r"/\*.*?\*/", "", Text, flags=re.DOTALL)
    return re.sub(r"//.*?$", "", Text, flags=re.MULTILINE)


def ParseNames(Declaration: str) -> list[str]:
    if "[" in Declaration or "]" in Declaration:
        raise ValueError("Only scalar signals are supported in the first compiler milestone")
    Cleaned = re.sub(r"\b(?:wire|logic|reg|signed|unsigned|var)\b", " ", Declaration)
    Names = []
    for Part in Cleaned.split(","):
        Match = re.search(r"([A-Za-z_][A-Za-z0-9_$]*)\s*$", Part.strip())
        if Match:
            Names.append(Match.group(1))
    return Names


def ParsePorts(Header: str, Body: str) -> tuple[list[str], list[str], list[str]]:
    Inputs: list[str] = []
    Outputs: list[str] = []
    Wires: list[str] = []
    Direction: str | None = None

    for Part in Header.split(","):
        Match = re.match(r"\s*(input|output|inout)\b(.*)", Part, flags=re.DOTALL)
        if Match:
            Direction = Match.group(1)
            Declaration = Match.group(2)
        else:
            Declaration = Part
        if Direction == "inout":
            raise ValueError("inout ports are not supported")
        Names = ParseNames(Declaration)
        if Direction == "input":
            Inputs.extend(Names)
        elif Direction == "output":
            Outputs.extend(Names)

    for Match in re.finditer(r"\b(input|output|inout|wire|logic)\b\s+([^;]+);", Body):
        Kind = Match.group(1)
        Names = ParseNames(Match.group(2))
        if Kind == "input":
            Inputs.extend(Names)
        elif Kind == "output":
            Outputs.extend(Names)
        elif Kind == "inout":
            raise ValueError("inout ports are not supported")
        else:
            Wires.extend(Names)

    return list(dict.fromkeys(Inputs)), list(dict.fromkeys(Outputs)), list(dict.fromkeys(Wires))


def ParseSvToNetlist(
    *, InputPath: Path, TopModule: str | None = None, Workdir: Path | None = None
) -> NetlistIR:
    """Parse one scalar combinational module into the compiler IR."""
    del Workdir

    InputPath = InputPath.expanduser().resolve()
    if not InputPath.is_file():
        raise FileNotFoundError(f"SystemVerilog input does not exist: {InputPath}")

    Text = StripComments(InputPath.read_text())
    ModulePattern = re.compile(
        r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*;(.*?)\bendmodule\b",
        flags=re.DOTALL,
    )
    Matches = list(ModulePattern.finditer(Text))
    if not Matches:
        raise ValueError("No supported module declaration was found")

    Selected = None
    if TopModule is None:
        if len(Matches) > 1:
            Names = ", ".join(Match.group(1) for Match in Matches)
            raise ValueError(f"Multiple modules found ({Names}); select one with --top")
        Selected = Matches[0]
    else:
        Selected = next((Match for Match in Matches if Match.group(1) == TopModule), None)
        if Selected is None:
            raise ValueError(f"Top module {TopModule!r} was not found")

    Top = Selected.group(1)
    Header = Selected.group(2)
    Body = Selected.group(3)
    Inputs, Outputs, Wires = ParsePorts(Header, Body)
    if not Inputs:
        raise ValueError(f"Module {Top} has no scalar input ports")
    if not Outputs:
        raise ValueError(f"Module {Top} has no scalar output ports")

    Module = ModuleIR(
        Name=Top,
        SourcePath=InputPath,
        Inputs=Inputs,
        Outputs=Outputs,
    )
    for Name in Inputs + Outputs + Wires:
        Module.Ports[Name] = 1
        Module.Nets[Name] = NetIR(Name=Name)

    Assigned: set[str] = set()
    for Match in re.finditer(
        r"\bassign\s+([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(.*?);",
        Body,
        flags=re.DOTALL,
    ):
        Target = Match.group(1)
        if Target not in Module.Nets:
            raise ValueError(f"Assignment target was not declared: {Target}")
        if Target in Assigned:
            raise ValueError(f"Signal has multiple continuous assignments: {Target}")
        Source = ExpressionParser(Match.group(2), Module).Parse()
        if (
            Module.Gates
            and Module.Gates[-1].Output == Source
            and Source.startswith("LogicNet")
        ):
            Module.Gates[-1].Outputs = [Target]
            Module.Nets.pop(Source, None)
        else:
            Module.Gates.append(
                Gate(
                    Name=f"BufferGate{len(Module.Gates)}",
                    Kind=GateKind.BUFFER,
                    Outputs=[Target],
                    Inputs=[Source],
                )
            )
        Assigned.add(Target)

    MissingOutputs = [Name for Name in Outputs if Name not in Assigned]
    if MissingOutputs:
        raise ValueError(f"Output ports are not assigned: {', '.join(MissingOutputs)}")
    if re.search(r"\balways(?:_comb|_ff|_latch)?\b", Body):
        raise ValueError("always blocks are not supported yet; use continuous assign statements")

    return NetlistIR(Top=Top, Modules={Top: Module})

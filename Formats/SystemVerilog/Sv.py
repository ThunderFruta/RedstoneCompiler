"""Fully consumed scalar combinational SystemVerilog; see Docs/Formats/SystemVerilog.md."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from Compilation.Ir.Models import GateKind, Gate, ModuleIR, NetIR, NetlistIR


# Reserved SystemVerilog words cannot acquire identifier semantics in this subset.
Keywords = frozenset("""
accept_on alias always always_comb always_ff always_latch and assert assign assume
automatic before begin bind bins binsof bit break buf bufif0 bufif1 byte case casex
casez cell chandle checker class clocking cmos config const constraint context
continue cover covergroup coverpoint cross deassign default defparam design disable
dist do edge else end endcase endchecker endclass endclocking endconfig endfunction
endgenerate endgroup endinterface endmodule endpackage endprimitive endprogram
endproperty endsequence endspecify endtable endtask enum event eventually expect
export extends extern final first_match for force foreach forever fork forkjoin
function generate genvar global highz0 highz1 if iff ifnone ignore_bins illegal_bins
implements implies import incdir include initial inout input inside instance int
integer interconnect interface intersect join join_any join_none large let liblist
library local localparam logic longint macromodule matches medium modport module
nand negedge nettype new nexttime nmos nor noshowcancelled not notif0 notif1 null or
output package packed parameter pmos posedge primitive priority program property
protected pull0 pull1 pulldown pullup pulsestyle_ondetect pulsestyle_onevent pure
rand randc randcase randsequence rcmos real realtime ref reg reject_on release
repeat restrict return rnmos rpmos rtran rtranif0 rtranif1 s_always s_eventually
s_nexttime s_until s_until_with scalared sequence shortint shortreal showcancelled
signed small soft solve specify specparam static string strong strong0 strong1
struct super supply0 supply1 sync_accept_on sync_reject_on table tagged task this
throughout time timeprecision timeunit tran tranif0 tranif1 tri tri0 tri1 triand
trior trireg type typedef union unique unique0 unsigned until until_with untyped
use uwire var vectored virtual void wait wait_order wand weak weak0 weak1 while
wildcard wire with within wor xnor xor
""".split())
IdentifierPattern = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
TriviaPattern = re.compile(r"[ \t\r\n]+|//[^\n]*|/\*.*?\*/", re.DOTALL)


@dataclass(frozen=True)
class Token:
    Text: str
    Offset: int


@dataclass(frozen=True)
class Expression:
    Token: Token
    Operands: tuple[Expression, ...] = ()

    def References(self) -> list[Token]:
        if not self.Operands:
            return [self.Token]
        return [Reference for Operand in self.Operands for Reference in Operand.References()]


class SourceParser:
    """Consume every token, then validate and elaborate the acyclic scalar graph."""

    def __init__(self, Text: str, InputPath: Path) -> None:
        self.Text = Text
        self.InputPath = InputPath
        self.Index = 0
        self.Tokens = self.Tokenize()

    def Error(self, At: Token, Message: str) -> ValueError:
        Line = self.Text.count("\n", 0, At.Offset) + 1
        Column = At.Offset - self.Text.rfind("\n", 0, At.Offset)
        return ValueError(f"{self.InputPath}:{Line}:{Column}: {Message}")

    def Tokenize(self) -> list[Token]:
        Tokens = []
        Offset = 0
        while Offset < len(self.Text):
            Match = TriviaPattern.match(self.Text, Offset)
            if Match:
                Offset = Match.end()
                continue
            At = Token(self.Text[Offset], Offset)
            if self.Text.startswith("/*", Offset):
                raise self.Error(At, "Unterminated block comment")
            if self.Text.startswith("(*", Offset):
                raise self.Error(At, "SystemVerilog attributes are not supported")
            if At.Text == "`":
                raise self.Error(At, "SystemVerilog compiler directives and macros are not supported")
            Match = IdentifierPattern.match(self.Text, Offset)
            if Match:
                At = Token(Match.group(), Offset)
            Tokens.append(At)
            Offset += len(At.Text)
        Tokens.append(Token("", len(self.Text)))
        return Tokens

    @property
    def Current(self) -> Token:
        return self.Tokens[self.Index]

    def Take(self) -> Token:
        At = self.Current
        if At.Text:
            self.Index += 1
        return At

    def Match(self, Text: str) -> bool:
        if self.Current.Text == Text:
            self.Take()
            return True
        return False

    def Expect(self, Text: str) -> Token:
        At = self.Current
        if not self.Match(Text):
            raise self.Error(At, f"Expected {Text!r}, found {At.Text or 'end of source'!r}")
        return At

    def Name(self) -> Token:
        At = self.Current
        if At.Text in ("[", "]"):
            raise self.Error(At, "Only scalar signals are supported")
        if not IdentifierPattern.fullmatch(At.Text) or At.Text in Keywords:
            raise self.Error(At, f"Expected an identifier, found {At.Text or 'end of source'!r}")
        return self.Take()

    def Type(self) -> bool:
        Start = self.Index
        if self.Match("wire"):
            self.Match("logic")
        else:
            self.Match("var")
            if not self.Match("logic"):
                self.Match("reg")
        if not self.Match("signed"):
            self.Match("unsigned")
        return self.Index != Start

    def ParseExpression(self, MinimumPrecedence: int = 0) -> Expression:
        At = self.Current
        if self.Match("~"):
            Left = Expression(At, (self.ParseExpression(4),))
        elif self.Match("("):
            Left = self.ParseExpression()
            self.Expect(")")
        else:
            Left = Expression(self.Name())
        Precedence = {"|": 1, "^": 2, "&": 3}
        while Precedence.get(self.Current.Text, -1) >= MinimumPrecedence:
            Operator = self.Take()
            Right = self.ParseExpression(Precedence[Operator.Text] + 1)
            Left = Expression(Operator, (Left, Right))
        return Left

    def ParseModule(self) -> ModuleIR:
        self.Expect("module")
        Name = self.Name()
        self.Expect("(")
        Header: dict[str, Token] = {}
        Declarations: dict[str, tuple[str, Token]] = {}
        Ansi = self.Current.Text in ("input", "output")
        Direction = ""
        ExplicitPortTypes: set[str] = set()
        SplitDeclarations: set[str] = set()

        def Declare(At: Token, Kind: str, ExplicitType: bool = False) -> None:
            if At.Text in Declarations:
                Previous = Declarations[At.Text][0]
                IsSplit = (
                    not Ansi and At.Text in Header and At.Text not in SplitDeclarations
                    and ((Previous in ("input", "output") and Kind in ("wire", "logic"))
                         or (Kind in ("input", "output") and Previous in ("wire", "logic")))
                    and At.Text not in ExplicitPortTypes and not ExplicitType
                )
                if not IsSplit:
                    raise self.Error(At, f"Signal was declared more than once: {At.Text}")
                SplitDeclarations.add(At.Text)
                if Previous in ("input", "output"):
                    return
            Declarations[At.Text] = Kind, At
            if Kind in ("input", "output") and ExplicitType:
                ExplicitPortTypes.add(At.Text)

        if self.Current.Text != ")":
            while True:
                if Ansi:
                    if self.Current.Text in ("input", "output"):
                        Direction = self.Take().Text
                        self.Type()
                At = self.Name()
                if At.Text in Header:
                    raise self.Error(At, f"Duplicate port in module header: {At.Text}")
                Header[At.Text] = At
                if Ansi:
                    Declare(At, Direction)
                if not self.Match(","):
                    break
        self.Expect(")")
        self.Expect(";")
        Assignments: dict[str, tuple[Token, Expression]] = {}
        while self.Current.Text != "endmodule":
            At = self.Take()
            if At.Text in ("wire", "logic", "input", "output"):
                ExplicitType = False
                if At.Text in ("input", "output"):
                    if Ansi:
                        raise self.Error(At, "Body port declarations cannot be mixed with ANSI ports")
                    ExplicitType = self.Type()
                else:
                    if At.Text == "wire":
                        self.Match("logic")
                    if not self.Match("signed"):
                        self.Match("unsigned")
                while True:
                    Signal = self.Name()
                    if At.Text in ("input", "output") and Signal.Text not in Header:
                        raise self.Error(Signal, f"Port is absent from module header: {Signal.Text}")
                    Declare(Signal, At.Text, ExplicitType)
                    if not self.Match(","):
                        break
                self.Expect(";")
            elif At.Text == "assign":
                Target = self.Name()
                self.Expect("=")
                Value = self.ParseExpression()
                self.Expect(";")
                if Target.Text in Assignments:
                    raise self.Error(Target, f"Signal has multiple continuous assignments: {Target.Text}")
                Assignments[Target.Text] = Target, Value
            else:
                raise self.Error(At, f"Unsupported module item: {At.Text or 'end of source'}")
        self.Expect("endmodule")
        for Signal, At in Header.items():
            if Signal not in Declarations or Declarations[Signal][0] not in ("input", "output"):
                raise self.Error(At, f"Port has no input/output declaration: {Signal}")
        Inputs = [Signal for Signal in Header if Declarations[Signal][0] == "input"]
        Outputs = [Signal for Signal in Header if Declarations[Signal][0] == "output"]
        if not Inputs or not Outputs:
            raise self.Error(Name, f"Module {Name.Text} must have scalar input and output ports")
        for Output in Outputs:
            BoundaryName = f"{Output}$Output"
            if BoundaryName in Declarations:
                raise self.Error(Declarations[BoundaryName][1],
                                 f"Signal conflicts with reserved output-boundary name: {BoundaryName}")
        for Target, Value in Assignments.values():
            if Target.Text not in Declarations:
                raise self.Error(Target, f"Assignment target was not declared: {Target.Text}")
            if Target.Text in Inputs:
                raise self.Error(Target, f"Cannot assign to input port: {Target.Text}")
            for Reference in Value.References():
                if Reference.Text not in Declarations:
                    raise self.Error(Reference, f"Unknown signal in expression: {Reference.Text}")
                if Reference.Text not in Inputs and Reference.Text not in Assignments:
                    raise self.Error(Reference, f"Signal has no continuous assignment: {Reference.Text}")
        Missing = [Signal for Signal in Outputs if Signal not in Assignments]
        if Missing:
            raise self.Error(Header[Missing[0]], f"Output ports are not assigned: {', '.join(Missing)}")

        # Source statement order has no hardware meaning. Emit dependency order for
        # the optimizer/lowerer, and reject feedback instead of inventing a value.
        Ordered: list[tuple[Token, Expression]] = []
        Available = set(Inputs)
        Pending = list(Assignments.values())
        while Pending:
            Remaining = []
            for Target, Value in Pending:
                if all(Reference.Text in Available for Reference in Value.References()):
                    Ordered.append((Target, Value))
                    Available.add(Target.Text)
                else:
                    Remaining.append((Target, Value))
            if len(Remaining) == len(Pending):
                raise self.Error(Remaining[0][0], "Combinational cycle in continuous assignments")
            Pending = Remaining

        Module = ModuleIR(Name=Name.Text, SourcePath=self.InputPath, Inputs=Inputs, Outputs=Outputs)
        for Signal in Declarations:
            Module.Ports[Signal] = 1
            Module.Nets[Signal] = NetIR(Name=Signal)
        Kinds = {"~": GateKind.NOT, "&": GateKind.AND, "^": GateKind.XOR, "|": GateKind.OR}
        TempIndex = 0

        def Emit(Value: Expression) -> str:
            nonlocal TempIndex
            if not Value.Operands:
                return Value.Token.Text
            GateInputs = [Emit(Operand) for Operand in Value.Operands]
            while True:
                Output = f"LogicNet{len(Module.Gates)}_{TempIndex}"
                TempIndex += 1
                if Output not in Module.Nets:
                    break
            Module.Nets[Output] = NetIR(Name=Output)
            Kind = Kinds[Value.Token.Text]
            Module.Gates.append(Gate(Name=f"{Kind.value.title()}Gate{len(Module.Gates)}",
                                     Kind=Kind, Outputs=[Output], Inputs=GateInputs))
            return Output

        for Target, Value in Ordered:
            TempIndex = 0
            Source = Emit(Value)
            if Value.Operands:
                Module.Gates[-1].Outputs = [Target.Text]
                Module.Nets.pop(Source)
            else:
                Module.Gates.append(Gate(Name=f"BufferGate{len(Module.Gates)}",
                                         Kind=GateKind.BUFFER, Outputs=[Target.Text], Inputs=[Source]))
        return Module

    def Parse(self, TopModule: str | None) -> NetlistIR:
        Modules: dict[str, ModuleIR] = {}
        while self.Current.Text:
            At = self.Current
            Module = self.ParseModule()
            if Module.Name in Modules:
                raise self.Error(At, f"Duplicate module declaration: {Module.Name}")
            Modules[Module.Name] = Module
        if not Modules:
            raise self.Error(self.Current, "No supported module declaration was found")
        if TopModule is None:
            if len(Modules) > 1:
                raise self.Error(self.Current, f"Multiple modules found ({', '.join(Modules)}); select one with --top")
            TopModule = next(iter(Modules))
        elif TopModule not in Modules:
            raise self.Error(self.Current, f"Top module {TopModule!r} was not found")
        return NetlistIR(Top=TopModule, Modules={TopModule: Modules[TopModule]})


def ParseSvToNetlist(
    *, InputPath: Path, TopModule: str | None = None, Workdir: Path | None = None
) -> NetlistIR:
    """Validate the entire file and return the selected scalar combinational module."""
    del Workdir
    InputPath = InputPath.expanduser().resolve()
    if not InputPath.is_file():
        raise FileNotFoundError(f"SystemVerilog input does not exist: {InputPath}")
    Parser = SourceParser(InputPath.read_text(encoding="utf-8"), InputPath)
    try:
        return Parser.Parse(TopModule)
    except RecursionError as Error:
        raise Parser.Error(Parser.Current, "Expression nesting exceeds the supported parser depth") from Error

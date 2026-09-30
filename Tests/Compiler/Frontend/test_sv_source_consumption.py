"""The complete source must satisfy the scalar grammar, with located failures."""

from itertools import product
from pathlib import Path
import re

import pytest

from Compilation.Synthesis.LogicEvaluation import EvaluateLogicModule
from Compilation.Synthesis.LogicOptimization import OptimizeLogic
from Compilation.Synthesis.NandTransform import ToNandOnly
from Formats.SystemVerilog.Sv import ParseSvToNetlist


Valid = "module demo(input a, output y); assign y = a; endmodule"
Fixtures = Path(__file__).resolve().parents[2] / "Fixtures/SystemVerilog/Unsupported"


def Parse(tmp_path: Path, Source: str, Top: str | None = None):
    SourcePath = tmp_path / "Source.sv"
    SourcePath.write_text(Source, encoding="utf-8")
    return ParseSvToNetlist(InputPath=SourcePath, TopModule=Top)


@pytest.mark.parametrize("SourcePath", sorted(Fixtures.glob("*.sv")), ids=lambda Path: Path.stem)
def test_minimized_unsupported_fixtures_fail_with_source_location(SourcePath: Path) -> None:
    with pytest.raises(ValueError, match=rf"{re.escape(str(SourcePath))}:\d+:\d+: "):
        ParseSvToNetlist(InputPath=SourcePath)


@pytest.mark.parametrize("Item", [
    "initial force y = a;", "final y = a;", "always y = a;",
    "always_comb y = a;", "always_ff y <= a;", "always_latch y = a;",
    "not (y, a);", "child u(a, y);", "generate endgenerate", "if (a) assign y = a;",
    "parameter N = 1;", "localparam N = 1;", "function f; endfunction", ";",
    "assign y = a, a = y;", "assign #1 y = a;", "defparam u.N = 1;", "garbage",
])
def test_unsupported_items_cannot_hide_next_to_valid_assignment(tmp_path: Path, Item: str) -> None:
    Source = Valid.replace("endmodule", Item + " endmodule")
    with pytest.raises(ValueError, match=r"Source\.sv:1:\d+:"):
        Parse(tmp_path, Source)


@pytest.mark.parametrize("Source", [
    "garbage " + Valid, Valid + " garbage", Valid + ";", Valid + " endmodule",
    Valid + " module broken(input a, output y);", Valid.replace("endmodule", ""),
    Valid.replace("; assign", " assign"), Valid.replace("a; endmodule", "a endmodule"),
    Valid.replace("input a", "input a,"), Valid.replace("output y", "output y,"),
    Valid.replace("input a", "input a a"), Valid.replace("input a", "input integer a"),
    Valid.replace("input a", "input signed unsigned a"), Valid.replace("input a", "input [0:0] a"),
    Valid.replace("input a", "input a [0:0]"), Valid.replace("input a", "input a = x"),
    Valid.replace("output y", "inout y"), Valid.replace("assign", "wire x = a; assign"),
    Valid.replace("assign", "wire x,; assign"), Valid.replace("assign", "wire x x; assign"),
    Valid.replace("assign", "wire x[0]; assign"), Valid.replace("assign", "logic x(a); assign"),
    Valid.replace("assign y = a", "assign y ="), Valid.replace("assign y = a", "assign y = a a"),
    Valid.replace("assign y = a", "assign y = a && a"),
    Valid.replace("assign y = a", "assign y = a ? a : a"),
    Valid.replace("assign y = a", "assign y = 1'b0"),
    Valid.replace("assign y = a", "assign y = ~"),
    Valid.replace("assign y = a", "assign y = (a"),
    Valid.replace("assign y = a", "assign y = a)"),
    Valid.replace("input a", "input initial"), Valid.replace("input a", "input \\a "),
    Valid + " /* unterminated", "/* unterminated " + Valid,
])
def test_malformed_or_trailing_syntax_is_not_partially_parsed(tmp_path: Path, Source: str) -> None:
    with pytest.raises(ValueError, match=r"Source\.sv:\d+:\d+:"):
        Parse(tmp_path, Source)


@pytest.mark.parametrize("Text,Message", [
    ("`timescale 1ns/1ps\n", "compiler directives and macros"),
    ("`default_nettype none\n", "compiler directives and macros"),
    ("`define VALUE a\n", "compiler directives and macros"),
    ('(* keep = "true" *) ', "attributes"),
])
@pytest.mark.parametrize("Position", ["prefix", "body", "suffix"])
def test_directives_and_attributes_are_explicitly_rejected(tmp_path: Path, Text: str, Message: str, Position: str) -> None:
    Source = {"prefix": Text + Valid, "body": Valid.replace("assign", Text + "assign"),
              "suffix": Valid + Text}[Position]
    with pytest.raises(ValueError, match=Message):
        Parse(tmp_path, Source)


def test_comments_preserve_source_coordinates_and_do_not_join_tokens(tmp_path: Path) -> None:
    Source = "/* two\nlines */\nmodule demo(input a, output y);\n  assign y = a;\n  initial force y = a;\nendmodule"
    with pytest.raises(ValueError, match=r"Source\.sv:5:3: Unsupported module item: initial"):
        Parse(tmp_path, Source)
    with pytest.raises(ValueError):
        Parse(tmp_path, Valid.replace("assign", "as/* not token glue */sign"))
    Parsed = Parse(tmp_path, "// `include and (* ignored *)\n" + Valid.replace("= a", "= /* comment */ a"))
    assert Parsed.Top == "demo"


@pytest.mark.parametrize("Source,Message", [
    (Valid.replace("input a", "input a, a"), "Duplicate port"),
    (Valid.replace("assign", "wire a; assign"), "declared more than once"),
    (Valid.replace("assign", "input a; assign"), "cannot be mixed"),
    (Valid.replace("assign y = a", "assign a = y; assign y = a"), "Cannot assign to input"),
    (Valid.replace("assign y = a", "assign z = a; assign y = a"), "target was not declared"),
    (Valid.replace("assign y = a", "assign y = z"), "Unknown signal"),
    (Valid.replace("assign y = a", "wire z; assign y = z"), "no continuous assignment"),
    (Valid.replace("assign y = a", "assign y = y"), "Combinational cycle"),
    (Valid.replace("assign y = a", "wire z; assign z = y; assign y = z"), "Combinational cycle"),
    (Valid.replace("assign y = a;", ""), "Output ports are not assigned"),
    ("module demo(a,y); input a; output z; assign z=a; endmodule", "absent from module header"),
    ("module demo(a,y); input a; wire y; assign y=a; endmodule", "no input/output declaration"),
    (Valid + "\n" + Valid, "Duplicate module"),
])
def test_invalid_scalar_connectivity_fails_closed(tmp_path: Path, Source: str, Message: str) -> None:
    with pytest.raises(ValueError, match=Message):
        Parse(tmp_path, Source)


def test_top_selection_does_not_hide_invalid_source(tmp_path: Path) -> None:
    Other = Valid.replace("demo", "other")
    assert Parse(tmp_path, Valid + "\n" + Other, "other").Top == "other"
    with pytest.raises(ValueError, match="Multiple modules"):
        Parse(tmp_path, Valid + "\n" + Other)
    with pytest.raises(ValueError, match="Unsupported module item: initial"):
        Parse(tmp_path, Valid + "\n" + Other.replace("endmodule", "initial y = a; endmodule"), "demo")


@pytest.mark.parametrize("Header,Declarations", [
    ("input wire logic a, b, c, output logic y, z", "wire x, unused;"),
    ("a,b,c,y,z", "input a,b,c; output wire y,z; logic x, unused;"),
])
def test_supported_scalar_grammar_and_forward_dependencies(tmp_path: Path, Header: str, Declarations: str) -> None:
    Source = f"""module demo({Header});
        assign y = ~(x | c) ^ a & b;
        assign z = x;
        {Declarations}
        assign x = a ^ b & c;
        endmodule"""
    Parsed = Parse(tmp_path, Source)
    Lowered = ToNandOnly(OptimizeLogic(Parsed))
    for a, b, c in product((False, True), repeat=3):
        Inputs = dict(a=a, b=b, c=c)
        x = a ^ (b & c)
        Expected = dict(y=(not (x | c)) ^ (a & b), z=x)
        for Netlist, Suffix in ((Parsed, ""), (Lowered, "$Output")):
            Values = EvaluateLogicModule(Netlist.Modules[Netlist.Top], Inputs)
            assert {Output: Values[Output + Suffix] for Output in Expected} == Expected


def test_generated_parser_nets_do_not_alias_user_names(tmp_path: Path) -> None:
    Parsed = Parse(tmp_path, """module demo(input a,b,LogicNet0_0, output y,z);
        assign y = (a & b) | LogicNet0_0;
        assign z = LogicNet0_0;
        endmodule""")
    Module = Parsed.Modules[Parsed.Top]
    for a, b, c in product((False, True), repeat=3):
        Values = EvaluateLogicModule(Module, dict(a=a, b=b, LogicNet0_0=c))
        assert Values["y"] == ((a & b) | c)
        assert Values["z"] == c


def test_source_name_cannot_collide_with_fixed_output_boundary(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"Source\.sv:1:19: Signal conflicts with reserved output-boundary name"):
        Parse(tmp_path, "module demo(input y$Output, output y); assign y=~y$Output; endmodule")
    assert Parse(tmp_path, "module demo(input a$bit, output y); assign y=~a$bit; endmodule").Top == "demo"


@pytest.mark.parametrize("Declaration", ["wire", "logic"])
@pytest.mark.parametrize("Reversed", [False, True])
def test_nonansi_split_port_declaration(tmp_path: Path, Declaration: str, Reversed: bool) -> None:
    Statements = ["input a; output y;", f"{Declaration} a, y;"]
    if Reversed:
        Statements.reverse()
    Parsed = Parse(tmp_path, "module demo(a,y); " + " ".join(Statements) + " assign y=~a; endmodule")
    for Value in (False, True):
        assert EvaluateLogicModule(Parsed.Modules[Parsed.Top], {"a": Value})["y"] is not Value


@pytest.mark.parametrize("Qualifier", ["reg", "signed", "unsigned", "var", "var logic signed", "reg unsigned", "wire logic signed"])
def test_scalar_port_qualifiers_preserve_bitwise_semantics(tmp_path: Path, Qualifier: str) -> None:
    Parsed = Parse(tmp_path, f"module demo(input {Qualifier} a, output {Qualifier} y); assign y=~a; endmodule")
    for Value in (False, True):
        assert EvaluateLogicModule(Parsed.Modules[Parsed.Top], {"a": Value})["y"] is not Value


@pytest.mark.parametrize("Body", [
    "input a; input a; output y;", "input a; output y; wire a; wire a;",
    "input logic a; wire a; output y;", "wire a; input logic a; output y;",
])
def test_split_declaration_cannot_mask_duplicate_or_conflicting_types(tmp_path: Path, Body: str) -> None:
    with pytest.raises(ValueError, match="declared more than once"):
        Parse(tmp_path, f"module demo(a,y); {Body} assign y=a; endmodule")


@pytest.mark.parametrize("Whitespace", ["\u00a0", "\u2003", "\x1c", "\v", "\f"])
def test_non_grammar_whitespace_is_not_silently_accepted(tmp_path: Path, Whitespace: str) -> None:
    with pytest.raises(ValueError, match=r"Source\.sv:1:7:"):
        Parse(tmp_path, Valid.replace("module ", "module" + Whitespace))


def test_ascii_whitespace_and_crlf_are_supported(tmp_path: Path) -> None:
    assert Parse(tmp_path, Valid.replace(" ", "\t\r\n")).Top == "demo"

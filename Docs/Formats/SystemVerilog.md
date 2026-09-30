# Supported SystemVerilog source

The production frontend accepts a deliberately small scalar, two-state,
combinational subset. It consumes the **entire file**, including modules not
selected by `--top`. Unsupported or malformed material fails with
`path:line:column: message` (one-based positions), rather than disappearing before
IR construction. Selection chooses the returned module, not which source is
validated. There is no preprocessor or hierarchy elaborator.

## Grammar

ASCII spaces, tabs, CR/LF line endings, `//` comments, and terminated
`/* ... */` comments separate tokens. Other whitespace characters are rejected.
Comments do not join the tokens on either side. Identifiers are case-sensitive
ASCII `[A-Za-z_][A-Za-z0-9_$]*`, excluding SystemVerilog reserved words. Escaped
identifiers are unsupported. The declared name `<output>$Output` is reserved
for each output because it identifies that output in the downstream IR; a source
declaration colliding with that exact name is rejected with its location. Other
identifiers containing `$`, and names resembling temporary compiler nets, are
allowed. Temporary net allocation skips existing source names.

```text
file         = module+ EOF
module       = "module" identifier "(" ports ")" ";" item* "endmodule"
ports        = ansi_ports | identifier ("," identifier)*
ansi_ports   = direction qualifiers identifier ("," (direction qualifiers)? identifier)*
direction    = "input" | "output"
qualifiers   = ("wire" "logic"? | "var" ("logic" | "reg")? | "logic" | "reg")? signing?
signing      = "signed" | "unsigned"
internal_type = ("wire" "logic"? | "logic") signing?
item         = declaration | "assign" identifier "=" expression ";"
declaration  = (direction qualifiers | internal_type) identifier ("," identifier)* ";"
expression   = or_expression
or_expression  = xor_expression ("|" xor_expression)*
xor_expression = and_expression ("^" and_expression)*
and_expression = unary_expression ("&" unary_expression)*
unary_expression = identifier | "~" unary_expression | "(" expression ")"
```

Every module requires at least one input and one output. ANSI ports carry their
direction in the header; grouped names inherit the preceding direction/type.
Non-ANSI headers list names and require exactly one body input/output declaration
per port. The two styles cannot be mixed. Body `wire`/`logic` declarations declare
internal scalar signals. A non-ANSI port with an unqualified direction declaration
may have one separate `wire`/`logic` declaration, in either order (for example,
`input a; wire a;`). Repeated declarations, combining explicit types on both
port declarations, and declaration initializers are rejected.

Scalar `reg`, `var`, `signed`, and `unsigned` port qualifiers are retained; they
do not change two-state single-bit bitwise evaluation. Their order is constrained
by the grammar, so unrelated words or repeated qualifiers cannot disappear.
`wire logic` is accepted as an explicit scalar net with logic data type. Other
types, including `bit`, and body `reg` declarations remain outside this grammar.

`~` binds most tightly, then `&`, then `^`, then `|`. Binary operators associate
left-to-right. Each declaration and continuous assignment ends with `;`.
Declarations and assignments may appear in any source order. The frontend emits
dependency-ordered IR so forward references have the same combinational meaning.

All referenced signals must be declared. Inputs cannot be assignment targets.
Every output and every referenced internal signal must have exactly one
continuous driver. Feedback cycles are rejected, even in unused assignments.
Unused, unassigned internal signals are allowed. Boolean inputs are restricted
to 0/1; this compiler does not model Verilog X/Z, strengths, delays, or resolution.

## Explicit rejection policy

- Reject all attributes (`(* ... *)`), compiler directives and macros (backtick
  syntax), including `timescale`, `default_nettype`, `include`, and conditional
  compilation. They are never stripped or implicitly applied
- Reject module/primitive instances, generate/initial/final/procedural constructs,
  parameters, functions, tasks, imports, packages and unsupported declarations
- Reject constants, vectors/ranges/selects, arrays, concatenation, arithmetic,
  comparisons, ternaries, delays, strengths and unsupported expression syntax
- Reject unmatched delimiters, missing terminators, extra module-end labels,
  duplicate module names and unexpected material before/between/after modules
- A directive or attribute inside a comment remains comment text

This is a fail-closed source boundary, not expanded HDL support. Existing NAND-only
mapping and physical acceptance requirements are unchanged. Syntax/connectivity
rejection occurs before the selected netlist is returned.

## Verification

`Tests/Compiler/Frontend/test_sv_source_consumption.py` exercises valid ANSI and
non-ANSI forms, precedence, forward dependencies, source coordinates, whole-file
selection, and unsupported syntax. Minimized repros are checked in under
`Tests/Fixtures/SystemVerilog/Unsupported/`. Existing example arithmetic and
optimization tests continue through the real frontend.

The source-to-NAND differential suite in `Tests/Compiler/Synthesis/` starts from
SystemVerilog source and uses an independent pinned HDL tool as its semantic
oracle. See [oracle instructions](../../Tests/Compiler/Synthesis/SourceOracle.md)
for installation, exact bounds, outcomes and retained counterexample evidence. Passing these Boolean checks does
not establish physical routing or Minecraft acceptance.

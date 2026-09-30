"""Deterministic valid HDL sources, generated independently of compiler IR."""

from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class SourceCase:
    Name: str
    Source: str
    Top: str
    Inputs: tuple[str, ...]
    Outputs: tuple[str, ...]
    Seed: int | None = None


def GenerateSource(Seed: int) -> SourceCase:
    Generator = Random(Seed)
    Available = ["a", "b", "c", "d"]

    def Expression(Depth: int) -> str:
        if Depth == 0 or Generator.randrange(4) == 0:
            return Generator.choice(Available)
        Left = Expression(Depth - 1)
        if Generator.randrange(4) == 0:
            return f"~({Left})"
        Right = Left if Generator.randrange(4) == 0 else Expression(Depth - 1)
        return f"({Left} {Generator.choice(('&', '^', '|'))} {Right})"

    Assignments = []
    for Index in range(8):
        Name = f"t{Index}"
        Assignments.append(f"assign {Name} = {Expression(3)};")
        Available.append(Name)
    Assignments.extend([
        "assign y = t7 ^ (t5 & t6) | a & b;",
        "assign z = ~(t7 | t6) ^ (a ^ a);",
        "assign alias_out = t7;",
        "assign dead = ~(a & b);",
    ])
    Generator.shuffle(Assignments)
    if Seed % 2:
        Header = "module Generated(a,b,c,d,unused,y,z,alias_out);\ninput wire a,b,c,d,unused;\noutput logic y,z,alias_out;"
    else:
        Header = "module Generated(input logic a,b,c,d,unused, output wire y,z,alias_out);"
    Source = "\n".join([
        Header, "wire t0,t1,t2,t3,t4,t5,t6,t7,dead,unused_wire;",
        *Assignments, "endmodule", "",
    ])
    return SourceCase(
        f"generated_{Seed}", Source, "Generated",
        ("a", "b", "c", "d", "unused"), ("y", "z", "alias_out"), Seed,
    )


DirectedCases = (
    SourceCase(
        "precedence_nested_reconvergent",
        """module Precedence(input a,b,c,d,unused, output y,z,alias_out);
wire shared, alias1, alias2, dead, unused_wire;
assign y = ~a & b ^ c | d;
assign z = ~(shared ^ (alias2 | (~c & d)));
assign alias_out = alias2;
assign dead = ~(a ^ b);
assign alias2 = alias1;
assign alias1 = shared;
assign shared = (a & b) | (c & d);
endmodule
""", "Precedence", ("a", "b", "c", "d", "unused"), ("y", "z", "alias_out"),
    ),
    SourceCase(
        "nonansi_top_comments_identifiers",
        """// The first module must not be selected by accident.
module Unselected(input a, output y); assign y = ~a; endmodule
/* Module names in comments: module Fake(input a, output y); endmodule */
module Selected(y, a, repeat_out, b, dollar$out);
output logic y, repeat_out, dollar$out;
input wire a, b;
logic LogicNet0_0, alias_name;
assign y = ~(LogicNet0_0 ^ alias_name);
assign repeat_out = a ^ a;
assign dollar$out = LogicNet0_0;
assign alias_name = b;
assign LogicNet0_0 = a & a;
endmodule
""", "Selected", ("a", "b"), ("y", "repeat_out", "dollar$out"),
    ),
    SourceCase(
        "associativity_repeated_operands",
        """module Repeated(input a,b,c, output y,z,p,q);
assign y = a ^ b ^ c;
assign z = (a | a) & (b ^ b) | ~~c;
assign p = ~a & b | a & ~b;
assign q = ~(a & a) ^ ((b | b) & (c | c));
endmodule
""", "Repeated", ("a", "b", "c"), ("y", "z", "p", "q"),
    ),
)


SourceCases = (*DirectedCases, *(GenerateSource(Seed) for Seed in range(32)))

# Valid source names must not alias compiler-created temporary signals.
NamespaceCases = (
    SourceCase(
        "input_nandnet_collision",
        "module Collision(input NandNet0,b, output y); assign y=NandNet0 ^ b; endmodule\n",
        "Collision", ("NandNet0", "b"), ("y",),
    ),
    SourceCase(
        "wire_nandnet_collision",
        "module Collision(input a,b,c, output y,z); wire NandNet0; assign NandNet0=a & b; assign y=NandNet0 ^ c; assign z=a | b; endmodule\n",
        "Collision", ("a", "b", "c"), ("y", "z"),
    ),
    SourceCase(
        "input_minimizednet_collision",
        "module Collision(input MinimizedNet0,b, output y); assign y=(~MinimizedNet0 & b) | (~MinimizedNet0 & ~b); endmodule\n",
        "Collision", ("MinimizedNet0", "b"), ("y",),
    ),
)


ExampleInterfaces = {
    "HalfAdder": (("A", "B"), ("Sum", "Carry")),
    "FullAdder": (("A", "B", "CarryIn"), ("Sum", "CarryOut")),
    "TFlipFlopLatch": (("Enable", "Toggle", "StateIn"), ("StateOut",)),
    "DecimalToBinary4": (
        tuple(f"Digit{Index}" for Index in range(10)),
        tuple(f"Binary{Index}" for Index in range(4)),
    ),
    **{
        Name: (
            (*tuple(f"A{Index}" for Index in range(Width)),
             *tuple(f"B{Index}" for Index in range(Width)), "CarryIn"),
            (*tuple(f"Sum{Index}" for Index in range(Width)), "CarryOut"),
        )
        for Name, Width in (("RippleCarryAdder4", 4), ("RippleCarryAdder8", 8), ("CarryLookaheadAdder4", 4))
    },
}


ObservableOutputCases = (
    SourceCase(
        "externally_observed_and",
        "module Demo(input a,b,c, output y,z); assign y=a&b; assign z=y|c; endmodule\n",
        "Demo", ("a", "b", "c"), ("y", "z"),
    ),
    SourceCase(
        "externally_observed_and_without_minimization",
        "module Demo(input a,b,c,d,e,f,g, output y,z); assign y=a&b; assign z=y|c; endmodule\n",
        "Demo", ("a", "b", "c", "d", "e", "f", "g"), ("y", "z"),
    ),
)


QualifierCases = (
    SourceCase(
        "ansi_signed_unsigned_reg",
        "module Qualified(input signed a, input unsigned b, output reg signed y, output reg unsigned z); assign y=a^b; assign z=~a; endmodule\n",
        "Qualified", ("a", "b"), ("y", "z"),
    ),
    SourceCase(
        "ansi_var_logic",
        "module Qualified(input var logic signed a, input var logic unsigned b, output var logic y); assign y=a^b; endmodule\n",
        "Qualified", ("a", "b"), ("y",),
    ),
    SourceCase(
        "ansi_wire_logic",
        "module Qualified(input wire logic signed a, input wire logic unsigned b, output wire logic y); assign y=a^b; endmodule\n",
        "Qualified", ("a", "b"), ("y",),
    ),
    SourceCase(
        "nonansi_split_wire",
        "module Qualified(a,b,y); input a,b; output y; wire a,b,y; assign y=a^b; endmodule\n",
        "Qualified", ("a", "b"), ("y",),
    ),
)

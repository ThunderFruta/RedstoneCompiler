module FullAdder (
    input logic A,
    input logic B,
    input logic CarryIn,
    output logic Sum,
    output logic CarryOut
);
    wire Propagate;
    wire Generate;
    wire PropagateCarry;

    assign Propagate = A ^ B;
    assign Generate = A & B;
    assign Sum = Propagate ^ CarryIn;
    assign PropagateCarry = Propagate & CarryIn;
    assign CarryOut = Generate | PropagateCarry;
endmodule

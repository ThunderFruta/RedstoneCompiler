module RippleCarryAdder4 (
    input A0,
    input A1,
    input A2,
    input A3,
    input B0,
    input B1,
    input B2,
    input B3,
    input CarryIn,
    output Sum0,
    output Sum1,
    output Sum2,
    output Sum3,
    output CarryOut
);
    wire Propagate0;
    wire Propagate1;
    wire Propagate2;
    wire Propagate3;
    wire Generate0;
    wire Generate1;
    wire Generate2;
    wire Generate3;
    wire Carry0;
    wire Carry1;
    wire Carry2;

    assign Propagate0 = A0 ^ B0;
    assign Sum0 = Propagate0 ^ CarryIn;
    assign Generate0 = A0 & B0;
    assign Carry0 = Generate0 | (Propagate0 & CarryIn);

    assign Propagate1 = A1 ^ B1;
    assign Sum1 = Propagate1 ^ Carry0;
    assign Generate1 = A1 & B1;
    assign Carry1 = Generate1 | (Propagate1 & Carry0);

    assign Propagate2 = A2 ^ B2;
    assign Sum2 = Propagate2 ^ Carry1;
    assign Generate2 = A2 & B2;
    assign Carry2 = Generate2 | (Propagate2 & Carry1);

    assign Propagate3 = A3 ^ B3;
    assign Sum3 = Propagate3 ^ Carry2;
    assign Generate3 = A3 & B3;
    assign CarryOut = Generate3 | (Propagate3 & Carry2);
endmodule

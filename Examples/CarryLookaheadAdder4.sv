module CarryLookaheadAdder4 (
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

    wire Carry1;
    wire Carry2;
    wire Carry3;

    wire Carry1Input;

    wire Carry2Generate0;
    wire Carry2Propagate10;
    wire Carry2Input;
    wire Carry2Partial;

    wire Carry3Generate1;
    wire Carry3Propagate21;
    wire Carry3Generate0;
    wire Carry3Propagate210;
    wire Carry3Input;
    wire Carry3Partial0;
    wire Carry3Partial1;
    wire Carry3Partial2;

    wire CarryOutGenerate2;
    wire CarryOutPropagate32;
    wire CarryOutGenerate1;
    wire CarryOutPropagate321;
    wire CarryOutGenerate0;
    wire CarryOutPropagate3210;
    wire CarryOutInput;
    wire CarryOutPartial0;
    wire CarryOutPartial1;
    wire CarryOutPartial2;
    wire CarryOutPartial3;

    assign Propagate0 = A0 ^ B0;
    assign Propagate1 = A1 ^ B1;
    assign Propagate2 = A2 ^ B2;
    assign Propagate3 = A3 ^ B3;

    assign Generate0 = A0 & B0;
    assign Generate1 = A1 & B1;
    assign Generate2 = A2 & B2;
    assign Generate3 = A3 & B3;

    assign Carry1Input = Propagate0 & CarryIn;
    assign Carry1 = Generate0 | Carry1Input;

    assign Carry2Generate0 = Propagate1 & Generate0;
    assign Carry2Propagate10 = Propagate1 & Propagate0;
    assign Carry2Input = Carry2Propagate10 & CarryIn;
    assign Carry2Partial = Generate1 | Carry2Generate0;
    assign Carry2 = Carry2Partial | Carry2Input;

    assign Carry3Generate1 = Propagate2 & Generate1;
    assign Carry3Propagate21 = Propagate2 & Propagate1;
    assign Carry3Generate0 = Carry3Propagate21 & Generate0;
    assign Carry3Propagate210 = Carry3Propagate21 & Propagate0;
    assign Carry3Input = Carry3Propagate210 & CarryIn;
    assign Carry3Partial0 = Generate2 | Carry3Generate1;
    assign Carry3Partial1 = Carry3Partial0 | Carry3Generate0;
    assign Carry3 = Carry3Partial1 | Carry3Input;

    assign CarryOutGenerate2 = Propagate3 & Generate2;
    assign CarryOutPropagate32 = Propagate3 & Propagate2;
    assign CarryOutGenerate1 = CarryOutPropagate32 & Generate1;
    assign CarryOutPropagate321 = CarryOutPropagate32 & Propagate1;
    assign CarryOutGenerate0 = CarryOutPropagate321 & Generate0;
    assign CarryOutPropagate3210 = CarryOutPropagate321 & Propagate0;
    assign CarryOutInput = CarryOutPropagate3210 & CarryIn;
    assign CarryOutPartial0 = Generate3 | CarryOutGenerate2;
    assign CarryOutPartial1 = CarryOutPartial0 | CarryOutGenerate1;
    assign CarryOutPartial2 = CarryOutPartial1 | CarryOutGenerate0;
    assign CarryOut = CarryOutPartial2 | CarryOutInput;

    assign Sum0 = Propagate0 ^ CarryIn;
    assign Sum1 = Propagate1 ^ Carry1;
    assign Sum2 = Propagate2 ^ Carry2;
    assign Sum3 = Propagate3 ^ Carry3;
endmodule

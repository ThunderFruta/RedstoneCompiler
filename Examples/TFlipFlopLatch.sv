module TFlipFlopLatch (
    input logic Enable,
    input logic Toggle,
    input logic StateIn,
    output logic StateOut
);
    wire EnableInactive;
    wire ToggleNext;
    wire LatchHold;
    wire ToggleAdvance;

    assign EnableInactive = ~Enable;
    assign ToggleNext = StateIn ^ Toggle;
    assign LatchHold = EnableInactive & StateIn;
    assign ToggleAdvance = Enable & ToggleNext;
    assign StateOut = LatchHold | ToggleAdvance;
endmodule

module DecimalToBinary4 (
    input logic Digit0,
    input logic Digit1,
    input logic Digit2,
    input logic Digit3,
    input logic Digit4,
    input logic Digit5,
    input logic Digit6,
    input logic Digit7,
    input logic Digit8,
    input logic Digit9,
    output logic Binary0,
    output logic Binary1,
    output logic Binary2,
    output logic Binary3
);
    // One-hot decimal digit inputs map to a 4-bit binary output (0-9).
    // Example: Digit7 = 1 -> Binary3-Binary0 = 0111.
    assign Binary0 = Digit1 | Digit3 | Digit5 | Digit7 | Digit9;
    assign Binary1 = Digit2 | Digit3 | Digit6 | Digit7;
    assign Binary2 = Digit4 | Digit5 | Digit6 | Digit7;
    assign Binary3 = Digit8 | Digit9;
endmodule

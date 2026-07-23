# Truth-table text

`<Name>.TruthTable.txt` is a human-readable exhaustive comparison produced by
the Redstone simulator. It records the module, ordered inputs and outputs, one
row per input combination, expected outputs, simulated outputs, row result,
overall result, and row count.

FullAdder contains 8 rows; the four-bit arithmetic acceptance circuits contain
512. Acceptance requires every row to report `PASS` and the footer to report
`Overall: PASS`.

This text is convenient evidence, but acceptance also requires the matching
physical design JSON, zero legal conflicts, and repeated-run determinism.

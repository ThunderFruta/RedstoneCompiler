# NAND JSON

`<Name>.Nand.json` is the logical NAND-only design published before physical
routing. Its top-level fields are:

- `Module`: selected top module;
- `Inputs` and `Outputs`: ordered public signal names; and
- `Gates`: ordered objects with `Name`, `Kind`, `Inputs`, and `Outputs`.

Gate kinds include `INPUT`, `NAND`, and `OUTPUT`. Generated gate and net names
are stable diagnostics, not semantic routing classes. Consumers should follow
connectivity and declared order rather than parse numeric suffixes.

The file proves synthesis shape only. It does not prove placement, routing,
Redstone timing, or truth-table correctness.

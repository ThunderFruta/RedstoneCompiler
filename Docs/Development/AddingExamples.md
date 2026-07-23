# Adding examples

Place supported scalar combinational SystemVerilog examples under `Examples/`.
Use one module per file unless the test explicitly exercises `--top`.

Add a lightweight logical test first. If the example is intended as a physical
benchmark, define its expected truth-row count, runtime class, and acceptance
role in [Benchmarks](../Testing/Benchmarks.md); do not make its name select a
routing heuristic.

Generated files belong under `Output/<ExampleName>/` and should include NAND
JSON, truth table, physical design JSON, diagram, and litematic only after a
successful compile. Large generated artifacts and ad hoc logs are evidence,
not source fixtures, unless a test explicitly owns them.

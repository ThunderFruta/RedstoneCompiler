# Compiler pipeline

## Stage ownership

1. `SVDecoder/` parses and elaborates the supported SystemVerilog subset.
2. `Compiler/Synthesis/` simplifies logic and maps it to NAND-only form.
3. `Compiler/Placement/` places standard-cell templates and captures legal
   local routes.
4. `Compiler/Routing/` plans coarse capacity, negotiates detailed route trees,
   materializes repeaters, and validates claims.
5. `Compiler/Simulation/` verifies the materialized circuit truth table.
6. `SchemEncoder/` writes the accepted design as a litematic.

`Compiler/Pipeline.py` owns end-to-end orchestration.
`Compiler/Placement/PcbFlow.py` owns the placement/routing feedback loop.

## Publication boundary

No schematic is accepted before exact physical claims, repeater legality, DRC,
and truth-table simulation pass. Typed failures publish diagnostics and exit
nonzero. `new-router-first` is the only production strategy and never invokes
a silent compatibility fallback.

## Runtime boundary

One absolute deadline covers placement generation, routing, validation,
simulation, and diagnostic publication. Nested stages receive remaining time;
they do not reset it.


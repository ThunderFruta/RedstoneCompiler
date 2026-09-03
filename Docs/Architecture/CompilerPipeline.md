# Compiler pipeline

## Stage ownership

1. `Compiler/Frontend/` parses and elaborates the supported SystemVerilog subset.
2. `Compiler/Synthesis/` simplifies logic and maps it to NAND-only form.
3. `PhysicalDesign/Placement/` places standard-cell templates and captures legal
   local routes.
4. `PhysicalDesign/Routing/` plans coarse capacity, negotiates detailed route trees,
   materializes repeaters, and validates claims.
5. `PhysicalDesign/Rendering/` writes a neutral-state staging litematic and audits its
   rendered orientation contract.
6. `Validation/Mchprs/` validates the physical fixture exhaustively
   through 20 inputs with the pinned MCHPRS/Redpiler engine.
7. `Validation/Fabric/` runs the required single-fixture canary set on
   Minecraft 26.2, resets every input low, waits for settlement, and publishes
   the observed all-zero server state as the final `.litematic`.

`Compiler/Pipeline.py` owns end-to-end orchestration.
`PhysicalDesign/Flow/` owns the placement/routing feedback loop, with the
narrow public entrypoint in `PhysicalDesign/Flow/Runner.py`.

## Publication boundary

Routing publication requires exact physical claims, repeater legality, and DRC.
Every published schematic without a configured Fabric server is marked
`infrastructure-failure`; it must not be treated as Minecraft behavioral
acceptance until the server stage returns an authoritative result.
The neutral staging litematic is private to publication. A live-world snapshot
failure clears every success artifact rather than publishing stale torch,
repeater, dust, or lamp properties.
Typed routing failures publish diagnostics and exit nonzero.

## Runtime boundary

One absolute deadline covers placement generation, routing, structural
validation, and diagnostic publication. The Fabric-server stage will own a
separate server lifecycle and request deadline once connected.

# Redstone Compiler

This repository compiles a small scalar combinational subset of SystemVerilog
into a NAND-only logic diagram and a Minecraft `.litematic` containing the
provided Input, Nand, and Output redstone templates.

The physical flow now performs:

- Structural simplification and exact Quine-McCluskey minimization before NAND
  technology mapping, with NAND-count comparison before accepting a rewrite.
- Fixed standard-cell macro geometry shared by placement, routing, and writing.
- Typed, serializable placement, routing, and repair policy plus one versioned
  owner for redstone connectivity, isolation, layer, and repeater rules.
- Clustered PCB-style placement followed by a deterministic zero-overflow
  global channel plan and exact layer/track assignment.
- Authoritative PCB routing with clustered placement, deterministic global planning,
  bounded guided-routing passes, and route-tree cleanup.
- One authoritative strict guided-routing attempt. Failure is reported directly;
  the router does not retry expanded, wide, or reversed fallback configurations.
- Strict logical-to-physical pin cardinality and named standard-cell access
  patterns; invalid macro geometry fails before routing.
- Four-way rotation plus horizontal mirroring of NAND macros.
- Route-tree cleanup that removes cycles, dead branches, and unused dust.

## Current SystemVerilog subset

- One module per compile, or select a module with `--top`.
- Scalar `input`, `output`, `wire`, and `logic` declarations.
- Continuous `assign` statements.
- Parentheses and the bitwise operators `~`, `&`, `^`, and `|`.

Vectors, constants, sequential logic, module instances, and `always` blocks are
not supported yet.

## Guided usage

```bash
pip install -e .
redstone-compiler
```

## Argument-driven usage

```bash
redstone-compiler \
  --input Examples/ExampleAnd.sv \
  --output Output/ExampleAnd/ExampleAnd.litematic \
  --diagram Output/ExampleAnd/ExampleAnd.Nand.json
```

Default and guided compiles group every generated artifact under
`Output/<OutputName>/`.

The CLI always uses the organized NAND router
(`physical-design-v4-organized-nand`). The retired compatibility router is not
exposed as a CLI mode; it remains an internal regression/fallback implementation
for API-level validation only.

Each compile also writes a Graphviz `.dot` file beside the NAND JSON diagram
and a `.PhysicalDesign.json` file beside the litematic. The latter records the
effective policy, technology version, global signal order, assigned layers,
resource count, and any global overflow. The graph is diagnostic only; physical
placement uses NAND connectivity and does not reproduce the drawing.

The physical router plans coarse corridors before detailed search. Nets are
ordered deterministically by span, fanout, and retry count; long or high-fanout
nets reserve trunks first. Every accepted global plan has concrete per-layer
resource ownership and zero overflow. Routing starts with strict
guide, bend, and via costs, then uses bounded conflict-cut repair and removes
cycles and dead branches. This PCB flow is the sole physical backend.

Completed compiles expose `CompileResult.RoutingMetrics` and print route length,
bends, vias, rerouted-net count, cumulative conflicts, and corridor overflow.
The metrics object also retains per-iteration routing measurements.

## Routing behavior and current limitations

The progress display reports routing passes, conflicted signals,
and the active named policy. Dense designs can take several minutes because
each accepted route must satisfy physical connectivity, electrical isolation,
signal-length/repeater, and truth-table checks.

FullAdder is the checked acceptance example. The RCA4 and CLA4 organized-router
regressions are still open, so their slower diagnostic tests remain opt-in:

```bash
RC_RUN_SCALE_TESTS=1 python3 -m unittest Tests.test_scale_routing -v
```

A failure such as:

```text
PCB routing failed: ValueError: Could not route net NandNet1 to a target after 8 passes
```

means that SystemVerilog parsing and NAND synthesis succeeded, but the detailed
router could not realize the authoritative global plan within the bounded
policy. No `.litematic` is written after this failure. Typed routing failures
retain the affected nets, saturated resources, and suggested repair actions.

## Package layout

- `Main.py` -- project-root CLI entrypoint (preferred).
- `Compiler/Main.py` -- command-line implementation and guided/argument workflow.
- `Compiler/Pipeline.py` -- end-to-end orchestration.
- `SVDecoder/` -- SystemVerilog parser/adaptor.
- `Compiler/Ir/` -- compiler IR definitions.
- `Compiler/Synthesis/` -- NAND normalization transforms.
- `Compiler/Cells/` -- authoritative standard-cell macro definitions.
- `Compiler/Placement/PcbFlow.py` -- sole physical-flow orchestrator.
- `Compiler/Placement/Pcb.py` -- clustered PCB gate placement.
- `Compiler/Placement/Geometry.py` -- shared placed-cell geometry.
- `Compiler/Routing/Pcb.py` -- PCB routing search and retries.
- `Compiler/Routing/Models.py` -- shared routing-stage data contracts.
- `Compiler/Routing/Actions/` -- focused geometry, validation, cleanup, and
  authoritative repeater operations.
- `Compiler/Routing/Workers/` -- pin-access and detailed-routing stage
  orchestration.
- `Compiler/Routing/Core.py` -- compact routing helper facade.
- `Compiler/Routing/Technology.py` -- authoritative redstone design rules.
- `Compiler/Routing/Policy.py` -- serializable physical-design policy.
- `Compiler/Routing/TrackAssignment.py` -- exact global track ownership types.
- `Templates/` -- simple lego blueprints (`Input`, `Output`, `Nand`) for cell placement.
- `SchemEncoder/` -- self-contained Litematica NBT writer.

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
  graph-anchored terminal access, placement-owned base claims, bounded
  guided-routing passes, and route-tree cleanup.
- One production routing strategy (`new-router-first`) with bounded internal
  placement/routing work and typed hard failure; there is no automatic
  compatibility or hybrid fallback.
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

The CLI exposes only `new-router-first`. The 2026-07-21
`physical-design-v9-openroad-style-relaxed` baseline is not accepted; the
current recovery target is `physical-design-v10-routability-feedback`. The
frozen compatibility implementation is an explicit internal regression oracle,
not a production fallback, and cannot satisfy new-router acceptance. See the
[router reliability guide](Docs/Routing/RouterReliabilityGuide.md) for the live
verdict and reproducible commands.

The current v10 checkpoint is still **NOT ACCEPTED**. The durable RRF-073
physical matrix verifies FullAdder 5/5 with identical placement fingerprint
`a8dc7c20513bcfc3`, ownership, route metrics, and emitted design. RCA4 and CLA4
self-exit with typed failures inside their immutable wall ceilings but remain
unrouted 0/2, so bounded completion does not satisfy either scale gate. The
current evidence source is
`Output/Acceptance/2026-07-21/RouterV10Recovery/AcceptanceManifest.json`.
The final lightweight checkpoint passes the explicit scale-excluded Python
suite 159/159 and the exact eight-file Rust release gate 25/25.

Each compile also writes a Graphviz `.dot` file beside the NAND JSON diagram
and a `.PhysicalDesign.json` file beside the litematic. The latter records the
effective policy, technology version, global signal order, assigned layers,
resource count, and any global overflow. Successful v10 metadata also carries a
`RouterReliability` envelope with placement/resource fingerprints and native
work evidence. Typed failures write the parallel reproduction and partial-work
evidence to `.RoutingFailure.json`. The graph is diagnostic only; physical
placement uses NAND connectivity and does not reproduce the drawing.

The physical router uses the template PCB backend and exact capacity-one
resource ownership. The v10 recovery makes placement electrical legality,
retained placement alternatives, boundary capacity, meaningful escalation, and
one absolute Python/native deadline explicit. Repeated reserved portal work may
advance once to bounded unreserved portals on that same deadline; this remains
the production new router, not compatibility routing. Frozen routes remain
routing obstacles but are not treated as standard-cell template geometry. The
normative behavior is in the
[router reliability design](Docs/Routing/RouterReliabilityDesignDoc.md); its
implementation status is recorded in the
[append-only notes](Docs/Routing/RouterReliabilityImplementationNotes.md).

Completed compiles expose `CompileResult.RoutingMetrics` and print route length,
bends, vias, rerouted-net count, cumulative conflicts, and corridor overflow.
The metrics object also retains per-iteration routing measurements.

## Routing behavior and current limitations

The progress display reports routing passes, conflicted signals,
and the active named policy. Dense designs can take several minutes because
each accepted route must satisfy physical connectivity, electrical isolation,
signal-length/repeater, and truth-table checks.

The current router is not accepted. The v9 FullAdder, RCA4, and CLA4 baseline
has bounded-runtime failures. The v10 RRF-073 matrix verifies the FullAdder
sub-gate and the 10/25/120-second process envelope, but RCA4 and CLA4 still
produce no qualifying routed artifacts. A historical v8 FullAdder result
remains a regression oracle but is not proof of complete v10 correctness. The
slower RCA4 and CLA4 physical tests remain opt-in:

```bash
RC_RUN_SCALE_TESTS=1 python3 -m unittest Tests.test_scale_routing -v
```

The canonical physical acceptance owner runs the fixed 5+2+2 matrix
sequentially and writes a durable evidence manifest. RRF-073 completed that
matrix with 8/23/118-second router deadlines reserved inside immutable
10/25/120-second wall ceilings. Its current overall result is failed because
RCA4 and CLA4 routed 0/2:

```bash
python3 Scripts/RunRouterAcceptance.py --date 2026-07-21 \
  --output-root Output/Acceptance --python /usr/bin/python3 --dry-run
python3 Scripts/RunRouterAcceptance.py --date 2026-07-21 \
  --output-root Output/Acceptance --python /usr/bin/python3
```

A typed failure such as:

```text
Portal:RuntimeBudgetExceeded: adaptive runtime budget exhausted
```

means that SystemVerilog parsing and NAND synthesis succeeded, but the detailed
router could not realize an authoritative physical design within the bounded
policy. The command exits nonzero and no final `.litematic` is accepted. The
v10 diagnostics contract adds a `.RoutingFailure.json` containing the typed
failure, attempted placements and escalation states, fingerprints, timings,
deadline state, and affected resources without converting the failure to
success.

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
- `Compiler/Routing/AuthoritativePlanner.py` -- production portal, route-tree,
  base-ownership, exact-assignment, and escalation orchestration.
- `Compiler/Routing/Reliability.py` -- shared deadline, fingerprint, placement,
  failure, and evidence contracts.
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
- `RustRouting/Src/` -- eight-file native router split: `Lib.rs`, `Models.rs`,
  `Deadline.rs`, `PathRouting.rs`, `Generation.rs`, `Assignment.rs`,
  `AssignmentPlanning.rs`, and `Bindings.rs`.
- `Scripts/RunRouterAcceptance.py` -- sequential physical acceptance and
  evidence-manifest harness.
- `Templates/` -- simple lego blueprints (`Input`, `Output`, `Nand`) for cell placement.
- `SchemEncoder/` -- self-contained Litematica NBT writer.

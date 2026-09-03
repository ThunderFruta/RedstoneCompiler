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

The CLI exposes only `new-router-first`. The frozen compatibility
implementation is an explicit internal regression oracle, not a production
fallback, and cannot satisfy new-router acceptance. The active redesign is the
[negotiated route-tree router](Docs/Routing/Active/NegotiatedRouteTreeRouter.md),
with its algorithmic sources in
[router research and inspiration](Docs/Routing/Active/RouterResearchAndInspiration.md).
The [routing documentation index](Docs/Routing/Readme.md),
[physical-design architecture review](Docs/Architecture/PhysicalDesignArchitectureReview.md),
[failure catalog](Docs/Routing/Active/FailureCatalog.md), and
[testing instructions](Docs/Testing/RunningTests.md) are the current references.

Each compile also writes a NAND JSON diagram and a `.PhysicalDesign.json` file
beside the litematic. The latter records the
effective policy, technology version, global signal order, assigned layers,
resource count, and any global overflow. Typed failures write parallel
reproduction and partial-work evidence to `.RoutingFailure.json`.

The physical router uses the template PCB backend and exact capacity-one
resource ownership. Placement electrical legality, boundary capacity,
escalation, and deadline behavior are defined by the active routing design and
validated through the current testing gates. Frozen routes remain routing
obstacles but are not treated as standard-cell template geometry.

Completed compiles expose `CompileResult.RoutingMetrics` and print route length,
bends, vias, rerouted-net count, cumulative conflicts, and corridor overflow.
The metrics object also retains per-iteration routing measurements.

## Routing behavior and current limitations

The progress display reports routing passes, conflicted signals,
and the active named policy. Dense designs can take several minutes because
each accepted route must satisfy physical connectivity, electrical isolation,
signal-length/repeater, and truth-table checks.

Acceptance status is established only by a fresh acceptance manifest and the
matching output artifacts for the checkout under test. The slower RCA4 and
CLA4 physical tests remain opt-in:

```bash
RC_RUN_SCALE_TESTS=1 python3 -m unittest Tests.test_scale_routing -v
```

The canonical physical acceptance owner runs its configured matrix sequentially
and writes a durable evidence manifest:

```bash
python3 Tools/Routing/RunRouterAcceptance.py \
  --output-root Output/Acceptance --python /usr/bin/python3 --dry-run
python3 Tools/Routing/RunRouterAcceptance.py \
  --output-root Output/Acceptance --python /usr/bin/python3
```

A typed failure such as:

```text
Portal:RuntimeBudgetExceeded: adaptive runtime budget exhausted
```

means that SystemVerilog parsing and NAND synthesis succeeded, but the detailed
router could not realize an authoritative physical design within the bounded
policy. The command exits nonzero and no final `.litematic` is accepted. The
diagnostics contract adds a `.RoutingFailure.json` containing the typed failure,
attempted placements and escalation states, fingerprints, timings, deadline
state, and affected resources without converting the failure to success.

## Package layout

- `Main.py` -- project-root CLI entrypoint (preferred).
- `App/CompilerCli.py` -- command-line implementation and guided/argument workflow.
- `Compiler/Pipeline.py` -- end-to-end orchestration.
- `Compiler/Frontend/` -- SystemVerilog parser/adaptor.
- `Compiler/Ir/` -- compiler IR definitions.
- `Compiler/Synthesis/` -- NAND normalization transforms.
- `PhysicalDesign/Cells/` -- authoritative standard-cell macro definitions.
- `PhysicalDesign/Placement/Access/`, `Core/`, and `Flow/` -- placement access
  geometry, search/commit, and the physical-flow orchestrator.
- `PhysicalDesign/Geometry/Placement.py` and `Rotation.py` -- shared placed-cell
  geometry primitives.
- `PhysicalDesign/Routing/Global/` -- production portal, route-tree,
  base-ownership, exact-assignment, and escalation orchestration.
- `PhysicalDesign/Execution/Reliability.py` -- shared deadline, fingerprint, placement,
  failure, and evidence contracts.
- `PhysicalDesign/Routing/Pcb.py` -- PCB routing search and retries.
- `PhysicalDesign/Contracts/` -- shared immutable routing-stage data
  contracts.
- `PhysicalDesign/Redstone/Actions/` -- focused geometry, validation, cleanup, and
  authoritative repeater operations.
- `PhysicalDesign/Routing/Workers/` -- pin-access and detailed-routing stage
  orchestration.
- `PhysicalDesign/Redstone/Technology.py` -- authoritative redstone design rules.
- `PhysicalDesign/Policy.py` -- serializable physical-design policy.
- `PhysicalDesign/Routing/Assignment/TrackAssignment.py` -- exact global track ownership types.
- `Native/Routing/Src/` -- native router domains for runtime, geometry, path
  search, assignment, escape planning, generation, and Python
  bindings; `Lib.rs` is the PyO3 registration entrypoint.
- `Tools/Routing/RunRouterAcceptance.py` -- sequential physical acceptance and
  evidence-manifest harness.
- `Validation/Mchprs/` -- fast exhaustive physical redstone
  validation through the pinned MCHPRS/Redpiler engine.
- `Validation/Fabric/` and `Validation/Fabric/Harness/` -- the required
  single-fixture Minecraft 26.2 canary check, validation backends, and tracked
  harness source;
  `ValidationServerHarness/Server/` is the ignored local runtime.
- `Assets/Templates/` -- simple lego blueprints (`Input`, `Output`, `Nand`) for cell placement.
- `PhysicalDesign/Rendering/SchemWriter.py` -- self-contained Litematica NBT writer.

The complete current ownership map, retired-path list, and structural gates are
in [Docs/Reference/ProjectTreeDesignDoc.md](Docs/Reference/ProjectTreeDesignDoc.md).

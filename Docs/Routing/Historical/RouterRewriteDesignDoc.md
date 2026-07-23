# Router/Placement Rewrite Design Document

> **Historical design notice (2026-07-21):** This document preserves earlier
> rewrite phases and measurements. The current normative contract is the
> [router reliability design](../Active/RouterReliabilityDesignDoc.md), and the current
> verdict is maintained in the [reliability guide](../Active/RouterReliabilityGuide.md).

## Objective

Replace the current router-first, single-pass flow with a two-layer, iterative routing architecture that produces compact gate-to-gate structures and minimizes long snaking runs.

Core user goal: most nets should be completed with a local, short burst of redstone dust between neighboring gates, with longer trunks used only when absolutely necessary for long fanout or module boundaries.

## Current Baseline (Observed)

- Input is compiled to NAND IR and sent into an authoritative resource-graph router.
- Current FullAdder sample output: `length=563`, `bends=103`, `vias=126`, footprint `1596` and output size `693 blocks`.
- Results indicate routing dominates runtime and dominates physical quality.
- Routing path selection currently reacts to local occupancy and assignment rather than global congestion-aware planning.

## Target Architecture Overview

1. Stage 0: Gate clustering and interface extraction
   - Identify repeated cells and candidate macro clusters (full adder slices, carry chain segments, bus crossbars, control islands).
   - Extract an interface contract per cluster: input/output pin count and orientation budget, side-exit windows, keepout masks, fanout pressure, and preferred escape directions.

2. Stage 1: Net-aware placement pass
   - Convert routing objective into placement objective using HPWL, estimated via count, and overflow penalties.
   - Use a cost-driven placement loop before detailed routing.
   - Optimize carry and bus adjacency first, then local glue logic placement.

### Locality-first interpretation for Stage 1

- Prefer candidate placements where immediate fanout and carry-neighbor pins are at Manhattan distance 1-2 gates equivalent.
- Reserve narrow gate-side escape windows so routing has a predictable first hop.
- Penalize placements that create unavoidable long Manhattan edges across modules.
- Track a placement score term: `LocalFanoutPenalty` = count of nets needing > 8-step local route.

3. Stage 2: Grid-based global routing
   - Build a coarse, layered routing grid with capacities and congestion costs.
   - Route all nets on this grid (multi-pin nets with Steiner-style branching or pin-order-aware tree growth).
   - Track congestion and block high-cost regions so they naturally avoid becoming long snakes.

### “Tiny dust” routing objective for Stage 2

- Add a default cap on preferred route spread:
  - Stage-local nets (same cluster): cost heavily favors staying within a near envelope around source/target clusters.
  - Inter-cluster nets: only these may consume long straights.
- Set a hard preference: if a legal route exists within the local budget, do not open a long channel.
- Default local budget examples to start tuning:
  - intra-cluster nets: <= 2 guide steps outside cluster envelope
  - cluster-to-cluster helper nets: <= 6 guide steps outside shared boundary corridor
  - all others: normal global exploration only after local attempts fail

4. Stage 3: Detailed route generation
   - Convert each global guide into constrained A* or Dijkstra detail routes.
   - Apply hard priorities: directness, bounded bend count, via control, and forbidden detour windows.
   - Do not re-route unrelated nets when one net is in progress.

5. Stage 4: Constraint solving + rip-up and reroute loop
   - After each full iteration, measure overflow and conflict score.
   - Rip up the top offenders and reroute with increased cost penalties in impacted channels.
   - Repeat until convergence or pass budget exhaustion.

6. Stage 5: Authoritative materialization
   - Materialize only after global+detailed plans are finalized.
   - Validate final occupancy with the existing redstone resource graph and simulation flow.

## Core Data Contracts

- `ClusterContract`
  - stable cluster key, pin-role schema, footprint candidates, pin windows, keepout masks.
- `PlacementSolution`
  - cluster-to-site mapping, legal orientations, channel reservations, overflow estimates.
- `GlobalGuide`
  - coarse path edges with net id, layer preference, demand contribution, and per-segment cost.
- `DetailedRoute`
  - exact path points with bend metadata and via decision per segment.
- `RipupPlan`
  - reroute candidate list with objective regression deltas.

## Placement-to-routing interface

- Placement must pass expected interface capacity into routing:
  - allowed approach directions per pin,
  - reserved pin escape boxes,
  - pre-allocated bus/carry routing corridor seeds.
- Routing must return back to placement when corridors are chronically blocked.

## Objective Function

- Primary objective: minimize weighted sum of
  - `wire length`, `bends`, `vias`, `overflows`, `local congestion`, `critical-net delay`.
- Secondary objective:
  - reduce disconnected long detours,
  - keep carry chains monotonic,
  - preserve bus ordering and parallelism.

For compactness-first behavior, assign primary weight bias:
1) `wire length`  > 2x total
2) `bends`        > 1.5x total
3) `via count`    > 1x total
4) `overflows`    > 0.8x total
5) `local congestion` > 0.5x total
Then apply repeater/cleanliness penalties only after these targets are satisfied.

## Routing Rule Set

- Preferred direction per layer (if supported): one axis as default trunking, the other for short crossovers.
- Via penalty increases with bend density and local congestion.
- Manhattan distance dominates short-route scoring before reserve-space scoring.
- Hard constraints first, soft costs second.
- Never convert a shortest legal channel allocation into a longer unrelated detour.

### Tiny route budget (default policy)

- For non-critical, non-bus nets:
  - target route span: `wire length <= 10` when a legal local route exists
  - target bends: `<= 3` for single fanout connections
  - target vias: `<= 1` unless blocked by keepout
- For carry/cascade nets:
  - target route span: keep strictly monotonic direction preference
  - allow bends only for layer transitions and obstacle bypass.
- For global buses/critical control:
  - permit longer runs, but maintain single-stripe channel continuity and no zig-zag.

## Repeater/Support Handling

- Repeater decisions come after route geometry exists.
- Use repeater-aware costs in detailed routing to avoid repeated detours for signal continuity.
- Prevent repeater-induced drift by tying insertion decisions to existing path anchors.

## Execution Phases

1. Phase 1 compatibility and observability
   - Add instrumentation for routing stage telemetry already used by the current run output: total length, bends, vias, reroutes, conflicts, overflow.
   - Keep current legacy path as fallback.

2. Phase 2 prototype global router
   - Add coarse guide router and basic congestion map.
   - Use fixed placement input and validate correctness on FullAdder first.

3. Phase 3 placement-aware optimization
   - Add placement optimizer that minimizes HPWL and congestion cost.
   - Evaluate gain from carry-chain and bus-preserving placements.

4. Phase 4 iterative reroute engine
   - Add iterative reroute controller with bounded attempts and deterministic convergence checks.

5. Phase 5 full rewrite mode
   - Introduce strategy switch: `authoritative-only`, `hybrid`, `new-router-first`.
   - Default to hybrid during validation.

## Acceptance Criteria

- FullAdder should reduce routing complexity and avoid snake-like single-channel behavior.
- Observable goals for baseline examples:
  - strong reduction in bend count,
  - reduction in via count,
  - reduced overflow peaks,
  - lower route length for equivalent logic depth,
  - no increase in failed routing probability.
- No functional regressions on truth table and authoritative simulation.

## Risks

- Runtime may increase during first implementation because of iterative convergence.
- Hard partitions may reduce routing flexibility if corridor contracts are too strict.
- Legacy design assumptions in existing callers require compatibility flags and strict output determinism.

Operational risk control:

- If local-budget mode degrades solve rate, increase local budget progressively before relaxing legal constraints.
- Always keep a bounded fallback mode that drops back to previous legal routing when reroute iterations fail.

## Out-of-scope (initial rewrite)

- Full behavioral changes to synthesis.
- New technology primitives beyond existing redstone block semantics.
- Full multi-module timing model and power estimation in this phase.

## What “routing does not dominate” means for this design

- Routing quality, not just legality, is part of placement fitness.
- Target trend on baseline:
  - reduce routing length by default while preserving gate count,
  - reduce bends and vias from current full-adder numbers,
  - keep overflow peak low enough to avoid many reroute passes.
- Practical acceptance on medium modules:
  - routing pass should stay bounded and stable,
  - no single net should be able to consume disproportionate routing area,
  - dense clusters should preserve local pin-to-pin adjacency.

## Implementation status and measured evidence (2026-07-19)

The rewrite is implemented as a versioned `physical-design-v2-local-first`
policy beside the frozen `physical-design-v1-compatibility` path. The CLI
exposes `compatibility`, `hybrid`, and `new-router-first`; hybrid attempts the
local-first route and falls back to compatibility on routing or simulation
failure. Cluster, placement, global-guide, detailed-route, and rip-up contracts
are serialized in each `.PhysicalDesign.json` artifact.

- [x] Strategy selection, compatibility policy, and hybrid fallback seam.
- [x] Locality-first placement spacing, bounded pin escape, three-layer guide
  budget, capacity-one exact assignment, and final authoritative validation.
- [x] FullAdder truth table: 8/8 rows in compatibility and rewrite modes.
- [x] FullAdder length: `563 -> 372` (`-33.9%`).
- [x] FullAdder vias: `126 -> 78` (`-38.1%`).
- [x] FullAdder footprint: `1596 -> 960` (`-39.8%`).
- [x] FullAdder overflow peak: `2 -> 1`; final conflicts and unresolved claims:
  `0`.
- [ ] FullAdder bends: `103 -> 95` (`-7.8%`), below baseline but short of the
  required `<=66`.
- [ ] FullAdder blocks: `693 -> 502`, two blocks above the required `<=500`.
- [ ] Local-first RippleCarryAdder4: exact capacity-one assignment currently
  fails on the tight profile, so placement/corridor feedback needs another
  implementation phase before the rewrite is complete for 4-bit datapaths.

Evidence command:

```bash
python Main.py --example Examples/FullAdder.sv --topmodule FullAdder \
  --output /tmp/RedstoneAcceptance/RewriteRun1 \
  --outputname FullAdderRewriteRun1 \
  --routing-strategy new-router-first
```

### Open-source staged-router port checkpoint

The subsequent guide-first implementation is recorded separately because it
changes the live rewrite metrics. Capacity-aware guides, routability-scored
placement alternatives, constrained detail candidates, localized repair, and
metric-gated hybrid fallback are now implemented. Five post-port runs were
deterministic at `length=362`, `bends=65`, `vias=86`, `overflow_peak=2`,
`footprint=1088`, `blocks=492`, and maximum-net share `17.127%`; runtimes were
`3.000-3.046s` with median `3.024s`. All passed 8/8 truth-table rows with zero
final conflicts and unresolved claims.

- [x] Explicit global-guide, placement-feedback, constrained-detail, localized
  repair, and quality-gate stages are live and serialized.
- [x] Five-run post-port determinism and runtime stability pass.
- [ ] `overflow_peak <=1`: post-port result is `2` at one three-net column.
- [x] Bend target `<=66`: post-port result is `65`.
- [x] Via target `<=88`: post-port result is `86`.
- [x] Block target `<=500`: post-port result is `492`.

The earlier `372/95/78/960/502` row remains useful as the pre-guide-port
checkpoint, not as the current executable result.

### NAND-only dense-placement checkpoint (2026-07-19)

The evolving rewrite policy is now `physical-design-v3-nand-packed`. Hard
validation runs after NAND lowering, after placement, and before writing;
`INPUT`, `NAND`, and `OUTPUT` are the only accepted placed cell kinds. Packed
clusters remain metadata over individual `Nand.litematic` instances.

- [x] Exact emitted block provenance and material counts are generated from the
  same canonical block map written to the litematic.
- [x] Deterministic pin-aligned NAND packing and frozen fully local nets are
  attempted before the proven local-first placement.
- [x] Exact-assignment failure rolls back transactionally to the proven rewrite
  placement; hybrid then applies material and routing gates before compatibility
  fallback.
- [x] Packed FullAdder routes authoritatively and passes 8/8 physical rows.
- [x] Exact size gates pass: `399` non-air blocks and footprint `510-544`
  versus required `<=500` and `<=600`.
- [x] Routing quality passes length/bend/via/overflow gates at
  `133/39/36/0` with zero final conflicts and unresolved claims.
- [ ] Component-majority density is improved but not complete:
  `67/200=33.5%` component-owned functional blocks, `133/200=66.5%`
  routing-owned, and `137/200=68.5%` raw dust.
- [ ] Maximum-net share is `21.805%`, slightly above the `20%` gate.

Evidence:

```bash
python Main.py --example Examples/FullAdder.sv --topmodule FullAdder \
  --output /tmp/NandGraphBeamFallback --outputname FullAdder \
  --routing-strategy new-router-first
```

The run passes 8/8 rows in `2.214s`, with `length=133`, `bends=39`,
`vias=36`, zero final conflicts/unresolved claims, and `overflow_peak=0`.

### Organized NAND v4 checkpoint (2026-07-19)

The active rewrite policy is now `physical-design-v4-organized-nand`. Generic
local claims are routed while packing, boundary escapes are reserved
transactionally, partial trees seed deterministic multi-source growth, and the
Rust exact assignment starts with complete local resource owners. Full-tree and
incremental extension metrics, same-signal reuse, entrance counts, layer
deviations, and candidate rejection reasons are serialized.

- [x] FullAdder correctness and legality: 8/8, zero conflicts/unresolved
  claims, `overflow_peak=0`.
- [x] FullAdder routing/size: `length=105`, `bends=28`, `vias=30`, footprint
  `476`, exact non-air blocks `344`.
- [x] Runtime: five runs `2.153-2.177s`; twenty-run p95 `2.162358s`.
- [ ] Component-majority density: component `39.0%`, routing `61.0%`, raw dust
  `65.1%`.
- [ ] Maximum-net share: `22.857%`, required `<=20%`.
- [ ] RCA4 physical simulation and CLA4 exact assignment remain blocking.

FLUTE3 source/data from the pinned OpenROAD revision are vendored but disabled
because the audited C++ still depends on OpenROAD `stt`/`utl`. The production
policy uses the deterministic native Rust topology API. By later explicit user
direction, `new-router-first` is the sole CLI mode and compatibility remains
internal. Hybrid is still not promoted while Phase C is open.

Evidence index:
`Output/Acceptance/2026-07-19/AcceptanceSummary.md`.

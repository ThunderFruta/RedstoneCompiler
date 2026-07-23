# Organized NAND Routing Goals

> **Historical goals notice (2026-07-21):** Keep the dated measurements below,
> but do not treat them as v10 acceptance. Current gates and their verdict are
> maintained in the [router reliability guide](../Active/RouterReliabilityGuide.md).

## 2026-07-19 annotation and repeater follow-up

- Final block-map construction emits one supported sign for every I/O cell;
  signs are allocated after routing and cannot be overwritten by route material.
- Repeater reservations are placed near the maximum safe dust interval and
  redundant reservations are pruned against the complete directed fanout tree.
- FullAdder retained 8/8 rows, zero conflicts, and all 5/5 I/O signs.
- RCA4 retained 512/512 rows, zero conflicts, and all 14/14 I/O signs.
- RCA8 retained 131072/131072 rows, zero conflicts, and all 26/26 I/O signs.

The retained routes still require 2, 11, and 23 routing repeaters respectively;
the safe minimizer did not delete them because each is necessary for at least
one sink under the final route geometry. This is recorded as an honest unchanged
metric rather than weakening redstone strength validation.

Retained artifacts are under
`Output/Benchmarks/2026-07-19/SignRepeaterFix/{FullAdder,Rca4Penalty,Rca8}`.
Each was generated with `Main.py --example <example> --topmodule <module>
--output <artifact-directory> --outputname <module> --routing-strategy
new-router-first`. These follow-up runs are regression evidence, not Phase C
acceptance samples, so they do not check any still-open acceptance gate below.

## Purpose

This document is the acceptance checklist for the
[Organized NAND Routing Design](./OrganizedNandRoutingDesignDoc.md). It defines
what the rework must accomplish without confusing architectural progress with
measured completion.

Unchecked goals are not complete. A goal may be checked only with a dated run,
the exact command, artifact and raw-log paths, parsed metrics, and a pass/fail
rationale. Estimates and planning scores are not acceptance evidence.

## Goal hierarchy

The work is prioritized in this order:

1. NAND-only functional correctness.
2. Exact physical legality and deterministic ownership.
3. Component-majority material composition.
4. Organized, local, reusable signal trees.
5. Footprint and total-block density.
6. Runtime stability and larger-design scalability.

A lower-priority improvement cannot compensate for a higher-priority failure.

## G0: Preserve the logical and compatibility contracts

- [x] Every internal synthesized and placed gate is `NAND`.
- [x] Every synthesized NAND maps one-to-one to an existing `Nand.litematic`
  stamp.
- [x] `INPUT` and `OUTPUT` remain the only permitted interface cells.
- [x] Routing introduces no logical gates or circuit-specific macros.
- [ ] Compatibility mode retains its frozen policy, placement, routing
  behavior, and FullAdder truth-table contents.
- [x] Forced hybrid rejection reruns compatibility and produces identical
  logical outputs for every tested row.
- [x] The implementation contains no FullAdder, carry, XOR, or generated-name
  recognition in placement or routing decisions.

Evidence required: NAND validation output, gate/template counts, compatibility
snapshot comparison, forced-fallback artifact, and focused source/test result.

## G1: Make local routes authoritative

- [x] `LocalRouteClaim` represents nodes, edges, physical resource claims,
  connected targets, continuation nodes, repeaters, and exact material.
- [x] Local claims are validated by the authoritative routing resource graph.
- [x] Same-signal claims can touch and merge without self-conflict.
- [x] Cross-signal wire, support, air, and electrical claims remain
  capacity-one and isolated.
- [x] Complete local nets bypass global candidate generation.
- [x] Partial local nets preserve their connected sinks and enter global
  routing as signal-owned seed trees.
- [x] Existing complete `FrozenNetWires` behavior remains available through a
  compatibility adapter until all callers use typed claims.

Exit gate: FullAdder passes 8/8 with zero final conflicts and unresolved claims,
and identical runs reproduce all local claims and ownership.

## G2: Eliminate duplicate fanout trunks

- [x] Each unresolved sink connects to the cheapest legal point in the
  signal's existing owned tree, not unconditionally to the producer.
- [x] Every accepted extension becomes a source for subsequent targets.
- [x] Exact assignment begins with local resource ownership preloaded.
- [x] Candidate cost reports incremental emitted material separately from the
  total merged tree.
- [x] Repeaters and signal strength are recomputed over the complete merged
  tree.
- [x] A fanout regression proves one local sink and one remote sink share a
  trunk without self-conflict or duplicated routing cells.

Exit gate: the fanout fixture uses fewer routing-owned blocks than independent
source-to-sink routing while remaining deterministic and physically valid.

## G3: Route while packing NAND islands

- [ ] Packed beam candidates include exact template voxels, selected pin
  escapes, local routes, ownership, material, and rejection diagnostics.
- [ ] Adding a NAND transactionally places the template and routes newly local
  producer-consumer edges.
- [ ] Rejected candidates roll back every voxel and resource claim.
- [ ] High-fanout producers are placed by connectivity and physical cost near
  their consumers without using signal names.
- [ ] Boundary-cut count and predicted corridor demand influence cluster
  construction.
- [ ] Candidate ranking prioritizes legality, route-owned blocks, raw dust,
  boundary demand, footprint, and support before length/bends/vias.
- [ ] At least eight legal retained candidates are available for bounded
  placement feedback when the policy requests eight.

Intermediate FullAdder gate:

- [ ] Routing-owned functional blocks `<80`.
- [ ] Component-owned functional share `>=45%`.
- [ ] Raw dust share `<=55%`.
- [x] Footprint `<=600`.
- [x] `overflow_peak <=1` with zero final conflicts/unresolved claims.
- [x] Physical truth table passes 8/8.

## G4: Produce visibly organized global routing

- [ ] Local island routes stay on the component/local plane unless a bounded
  legal escape is recorded.
- [ ] X-dominant and Z-dominant trunks use their preferred layers when legal.
- [ ] Upper layers are reserved primarily for crossings and congestion escape.
- [ ] Cluster boundary pins align to deterministic shared corridor tracks.
- [ ] Unrelated global nets do not traverse island keep-in envelopes without a
  recorded bounded escape.
- [ ] Each signal and cluster obeys a bounded global-entrance budget.
- [ ] Rerouting reopens offending extensions while preserving clean local trees
  and clean global branches.
- [x] Diagnostics report layer deviations, island crossings, entrances,
  same-signal reuse, and avoided duplicate-trunk blocks.

Exit gate: FullAdder maximum-net share is `<=18%`, `overflow_peak <=1`, and
organization diagnostics contain no unexplained policy violations.

## G5: Meet the final FullAdder density gates

All measurements must come from the canonical emitted block map.

- [ ] Component-owned functional share `>=60%`.
- [ ] Routing-owned functional share `<=40%`.
- [ ] Raw dust share `<=45%` of non-support, non-annotation functional blocks.
- [x] Footprint `<=600`.
- [x] Total exact non-air blocks `<=500`.
- [x] Routed length `<=422`.
- [x] Bend count `<=66`.
- [x] Via count `<=88`.
- [ ] Maximum-net share `<=20%`.
- [x] `overflow_peak <=1`.
- [x] Final conflicts `=0`.
- [x] Final unresolved resource claims `=0`.
- [x] FullAdder physical truth table passes all 8 rows.

With the current 67 component-owned functional blocks, the 60% share implies a
derived routing target of approximately 44 blocks or fewer. This is a planning
diagnostic, not a substitute for recomputing the exact share if template-owned
material changes.

## G6: Meet runtime and repeatability goals

- [x] Compatibility FullAdder wall time `<=8.0s`.
- [x] Rewrite FullAdder wall time `<=10.0s`.
- [x] One warm-up plus five measured rewrite runs stay within `+-5%` of their
  median.
- [x] Twenty measured rewrite runs have p95 wall time no greater than `2x` the
  measured compatibility baseline.
- [ ] Identical inputs, seed, and policy reproduce placement, local claims,
  routes, ownership, and route metrics.
- [x] Packing, guide allocation, exact assignment, and reroute loops all have
  explicit deterministic budgets and stagnation exits.

## G7: Restore larger-adder scalability

- [ ] RippleCarryAdder4 passes all 512 physical truth-table rows.
- [ ] CarryLookaheadAdder4 passes all 512 physical truth-table rows.
- [ ] Both report zero final conflicts and unresolved claims.
- [ ] Both report `overflow_peak <=1`.
- [ ] RippleCarryAdder4 completes in `<=25s`.
- [x] Neither design uses circuit-specific placement or routing rules.
- [x] Structured failures identify the blocking signal, stage, resources, and
  placement/corridor context when a bounded attempt fails.

## G8: Make acceptance evidence reproducible

- [x] `.PhysicalDesign.json` records local claims, locally/globally connected
  targets, cluster entrances, same-signal reuse, exact incremental material,
  organization deviations, and rejection reasons.
- [x] CLI output prints the stable acceptance summary.
- [x] Exact provenance totals reproduce the final litematic palette and block
  count.
- [x] Compatibility baseline, rewrite warm-up, five-run sample, twenty-run
  sample, RCA4, and CLA4 use isolated output directories.
- [x] Every evidence row includes date, run ID, command, raw-log path, artifact
  path, metrics, and pass/fail rationale.
- [ ] The design, comparison, SLO, open-source porting, and goals documents are
  updated with the same final observed values.

## Phase gates

### Phase A: ownership foundation

- [ ] G0 passes.
- [ ] G1 passes.
- [ ] G2 passes.
- [ ] FullAdder remains correct and deterministic.

### Phase B: organized component-majority trajectory

- [ ] G3 intermediate FullAdder gate passes.
- [ ] G4 passes.
- [ ] At least two of component share, routing share, dust share, footprint, or
  exact non-air blocks improve from the current rewrite artifact.

### Phase C: release candidate

- [ ] G5 passes in one acceptance artifact.
- [ ] G6 passes.
- [ ] G7 passes.
- [ ] G8 passes.
- [ ] Hybrid accepts the rewrite without fallback.

Hybrid must not become the default until every Phase C checkbox is supported by
retained evidence.

## Adaptive v5 evidence (2026-07-19)

- [x] FullAdder passes 8/8 with zero conflicts/unresolved claims and
  `overflow_peak=0`.
- [x] FullAdder five-run metrics are identical: `length=102`, `bends=26`,
  `vias=30`, `footprint=476`, and `non_air=339`.
- [x] FullAdder runtime is `1.863-1.884s`, median `1.870s`, maximum median
  deviation `0.78%`.
- [x] RCA4 passes 512/512 in `12.109s` with `length=458`, `bends=118`,
  `vias=139`, zero conflicts/unresolved claims, and `overflow_peak=0`.
- [ ] CLA4 does not yet produce an accepted route inside the 120-second
  assignment ceiling; this remains the scale blocker.
- [ ] FullAdder component/material composition remains below Phase C:
  component share `39.6%`, routing share `60.4%`, raw dust share `64.5%`, and
  maximum-net share `22.549%`.

Artifacts and raw logs are retained under
`Output/Acceptance/2026-07-19/AdaptiveV5`. The RCA artifact is in the
`RippleCarryAdder4` subdirectory. Phase C and any default-mode promotion
remain unchecked.

The subsequent Rust ownership correction preserves these FullAdder results
and keeps RCA4 passing 512/512 (`12.450s`, overflow peak `1`). CLA4 remains
unchecked. The new Rust regression proves that same-signal base claims merge
while foreign base claims remain capacity-one obstacles.

## Structural-reuse v6 evidence (2026-07-19)

- [x] Repeated-island detection is generic: the unit fixture uses arbitrary
  NAND/gate/signal names and rejects a pin-swapped non-isomorphic graph.
- [x] RCA4 automatically finds one unique nine-NAND structural template and
  three exact nine-gate reuse mappings; no adder, carry, or bit recognizer is
  present.
- [x] Reused placement candidates rebuild exact template geometry and rerun
  local routing plus authoritative validation; fallback packing remains live.
- [x] RCA4 passes 512/512 at `9.494s`, with `length=458`, `bends=122`,
  `vias=135`, `overflow_peak=1`, and zero conflicts/unresolved claims.
- [x] FullAdder remains 8/8 at `1.885s`, with zero structural reuses, proving
  the optimization is conditional on actual repetition.
- [x] 52 Python tests pass (2 opt-in scale tests skipped) and all 7 Rust tests
  pass.
- [ ] The `9.494s` RCA4 result is one retained observation, not a five-run
  stability result.
- [ ] CLA4 and the FullAdder material-share Phase C gates remain open.

Evidence: `Output/Acceptance/2026-07-19/StructuralReuseV6`.

## RCA8 experiment (2026-07-19)

- [x] Eight generic nine-NAND islands collapse to one structural template plus
  seven validated placement reuses.
- [x] Adaptive exact-assignment work grows on assignment exhaustion without
  requiring unrelated coarse-guide overflow.
- [x] RCA8 passes all 131,072 physical rows with zero conflicts/unresolved
  claims and `overflow_peak=1`.
- [x] Physical scaling is close to linear from RCA4: length `458 -> 914`,
  non-air blocks `1423 -> 2830`, and footprint `2272 -> 4672`.
- [ ] Runtime scaling is not linear: `9.686s -> 30.338s`. Candidate/placement
  setup and exhaustive truth-table enumeration remain scale targets.

Artifact:
`Output/Experiments/2026-07-19/RippleCarryAdder8AdaptiveAssignment`.

## Native batching benchmark (2026-07-19)

- [x] Portal searches from every signal, terminal, and layer execute through
  one deterministic Rayon batch.
- [x] Route-tree searches from every net execute through one deterministic
  all-net Rayon batch rather than stopping at per-net barriers.
- [x] Request-index ordering and repeated output are covered by focused tests.
- [x] RCA8 three-run runtime is `26.159-26.349s`, median `26.249s`, maximum
  median deviation `0.38%`.
- [x] RCA8 authoritative routing-stage median improved `29.7%`, and total
  runtime improved `13.5%`, with identical physical metrics and all 131072
  rows passing.
- [x] RCA4 remains 512/512 and improved from `9.686s` to `7.624s` in the
  retained comparison run.
- [x] FullAdder remains 8/8 with unchanged route metrics.
- [x] 55 Python tests pass (2 opt-in scale tests skipped); 7 Rust tests pass.
- [ ] Thirty-two SMT threads do not outperform 16 physical-core threads on
  RCA8; further gains require reducing serial placement, materialization, and
  exhaustive-simulation work.

Evidence:
`Output/Benchmarks/2026-07-19/MulticoreBatch`.

## Native parallel simulation benchmark (2026-07-19)

- [x] Reference and physically delivered programs compile to indexed,
  deterministic combinational instructions.
- [x] Exhaustive assignments execute as disjoint native Rayon tasks and return
  in the original truth-table order.
- [x] FullAdder native and Python reports are exactly equal.
- [x] RCA8 native and Python reports are exactly equal across all 131072 rows.
- [x] RCA8 isolated simulation improves `6.692038s -> 0.392390s` (`17.1x`).
- [x] RCA8 three-run end-to-end median improves to `20.027277s`, with `0.35%`
  maximum median deviation and unchanged physical metrics.
- [x] FullAdder remains 8/8 at `1.832s`; RCA4 remains 512/512 at `7.854s`.
- [x] 55 Python tests pass with 2 opt-in scale tests skipped; 8 Rust tests pass.
- [ ] Placement and local-route candidate construction remain the largest
  serializable stage at approximately 9 seconds on RCA8.

Evidence:
`Output/Benchmarks/2026-07-19/ParallelSimulation`.

## 2026-07-19 adaptive assignment and reset-safety follow-up

- [x] Rust distinguishes exact-assignment work exhaustion from exhaustive
  candidate incompatibility.
- [x] Exhaustive incompatibility regenerates layers, portals, lanes, and route
  candidates instead of replaying an identical assignment problem.
- [x] Placement attempts consume one shared runtime budget and rank measured
  routability before packed density.
- [x] FullAdder and RCA4 accepted placements contain zero template-to-template
  electrical-clearance violations.
- [x] FullAdder retains 8/8 rows and RCA4 retains 512/512 rows after electrical
  isolation legalization.
- [ ] Confirm the replacement FullAdder resets after every lever on-to-off
  transition inside Minecraft; automated steady-state simulation is not
  sufficient evidence for this item.
- [ ] CLA4 remains unrouted. The retained run stops at the candidate-stage
  shared deadline after two exhaustively incompatible assignments.

Evidence:
`Output/Benchmarks/2026-07-19/StatefulResetFix` and
`Output/Benchmarks/2026-07-19/AdaptiveClaEscalationFix/Cla4RoutabilityFirst.log`.

## 2026-07-19 length-first compaction follow-up

- [x] FullAdder route length improves `107 -> 102`, exact blocks improve
  `350 -> 340`, and footprint improves `490 -> 476`.
- [x] RCA4 route length improves `472 -> 452`, exact blocks improve
  `1455 -> 1415`, and route support improves `716 -> 696`.
- [x] Three FullAdder runs reproduce `length=102`, `bends=33`, `vias=24`, and
  all 8 rows; runtime median is `2.292s` with `1.27%` maximum deviation.
- [x] Three RCA4 runs reproduce `length=452`, `bends=136`, `vias=129`, and all
  512 rows; runtime median is `9.177s` with `0.52%` maximum deviation.
- [x] Both designs retain zero conflicts/unresolved claims and
  `overflow_peak <= 1`.
- [ ] FullAdder still misses component/routing/dust and maximum-net-share
  gates; this checkpoint does not complete Phase C.
- [ ] Manual Minecraft on-to-off reset verification remains open.

Evidence: `Output/Benchmarks/2026-07-19/CompactionV8`.

## Current measured reference

The retained 2026-07-19 v4 checkpoint is evidence for individual checked
criteria, not Phase C completion:

```text
FullAdder new-router-first physical-design-v4-organized-nand
runtime=2.153-2.177s; 20-run p95=2.162358s
truth_table=8/8
length=105 bends=28 vias=30 overflow_peak=0
conflicts=0 unresolved_claims=0 maximum_net_share=22.857%
exact_non_air_blocks=344 footprint=476
component_owned_functional=67 (39.0%)
routing_owned_functional=105 (61.0%)
raw_dust=112 (65.1% of functional blocks)
```

Artifact: `Output/Acceptance/2026-07-19/RewriteV4/FullAdderRewriteRun1.PhysicalDesign.json`.
Commands, raw logs, the twenty-run sample, hybrid proof, and negative scale
evidence are indexed in
`Output/Acceptance/2026-07-19/AcceptanceSummary.md`.

The reference passes correctness, runtime, footprint, block-count, length,
bend, via, and overflow thresholds. It does not pass component share, routing
share, raw dust share, maximum-net share, RCA4 simulation, or CLA4 routing, so
no final Phase C box is checked. By explicit user direction after this
checkpoint, the CLI now exposes only `new-router-first`; compatibility remains
internal. This does not mark Phase C or hybrid-default promotion complete.

## Non-goals

- Replacing NANDs with XOR, carry, adder, or compound generated macros.
- Hiding multiple logical NANDs inside one template stamp.
- Optimizing only the FullAdder topology or recognizing its net names.
- Restoring the retired flat negotiated detailed router.
- Importing the full OpenROAD/OpenDB/LEF/DEF runtime stack.
- Weakening electrical isolation, capacity, DRC, or simulation requirements to
  improve density.
- Checking goals using estimated blocks, projections, or a fallback artifact.

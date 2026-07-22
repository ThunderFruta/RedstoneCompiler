# Routing Rewrite Comparison Document

> **Historical comparison notice (2026-07-21):** Measurements and comparisons
> below describe the policy version that produced them. They do not override
> the current [router reliability design](RouterReliabilityDesignDoc.md) or
> establish v10 acceptance.

## Current System vs Proposed Rewrite vs Industry Patterns

## What the current system does well

- Deterministic final materialization through an authoritative resource graph.
- Correctness-oriented validation path is strong and preserved at the end.
- Works for functional outputs on sample designs.

## What is currently limiting quality

- Routing is effectively single-pass and dominated by detailed routing cost once placement is fixed.
- Long detours appear because congestion is mostly handled after local decisions.
- Nets do not receive explicit global guidance channels, so nearby gates are not guaranteed to get direct links.
- Repeated structures are not first-class in the physical planning loop, so structural regularity is often lost.

## Proposed rewrite in practical terms

- Do placement as a router-aware optimization stage rather than a separate afterthought.
- Add a coarse global-routing stage with congestion-aware channel costs.
- Convert global guides into constrained detailed routes.
- Use rip-up/reroute loops to fix the worst offenders instead of accepting first-pass artifacts.
- Keep the existing authoritative materializer as the legality gate.

## How this maps to industry routing style

- **Global + detailed router split**
  - Industry tools split abstraction: first channel-level net planning, then legalized exact track routing.
  - Matches the proposed route: global guides then constrained detailed routing.

- **Congestion-aware cost functions**
  - Modern routers inflate cost on hot grids, forcing alternate paths before dead-end congestion.
  - Matches the proposed global map and iterative reroute penalties.

- **Rip-up and reroute**
  - Standard router stabilization loop in VLSI; improves quality for highly regular logic networks.
  - Mirrors the reroute-controller section of this design.

- **Hierarchical or region-based decomposition**
  - Used for scalable routing and repeated blocks in commercial workflows.
  - Matches cluster extraction and reuse direction for adder slices and bus/carry structures.

## Feature comparison

### 1) Placement behavior

- Current: mostly fixed placement then route.
- Proposed: placement is part of optimization with objective terms for wire length, overflow, and fanout pressure.
- Industry: placement and routing are tightly coupled through iterative cost feedback.

### 2) Net planning

- Current: implicit from local maze search and reservation costs.
- Proposed: explicit coarse routing guides and demand maps.
- Industry: explicit global routing with capacity and demand modeling.

### 3) Path quality

- Current: good legal correctness, weaker near-term compactness under congestion.
- Proposed: directness-first detailed routing constrained by guide channels.
- Industry: path directness and congestion balancing are first-class.

### 4) Complexity handling

- Current: one dominant pass then final check.
- Proposed: bounded iterative loops with convergence checks.
- Industry: iterative convergence is normal for quality and legalability.

### 5) Reusability and regular structures

- Current: repeated logic may still route as unique instances.
- Proposed: explicit region contracts derived from generic producer-consumer
  topology, fanout, pin compatibility, and boundary demand. Earlier bus/carry
  classifiers are superseded and are not permitted in the implementation.
- Industry: repeated-module awareness is a standard method for dense datapaths.

As of `physical-design-v6-structural-reuse-nand`, repeated NAND islands are a
first-class placement optimization. The implementation uses name-independent
directed-graph isomorphism, reuses only a proven relative placement candidate,
and then redoes physical local routing and validation in context. This is
closer to hierarchical/datapath placement reuse than to a hardcoded adder
macro: every NAND remains individually placed and stamped.

## Expected outcomes

- Reduced FullAdder routing footprint and fewer pathological snaked nets.
- Better scaling when repeating identical structures (more slices should not explode routing chaos).
- Faster debug loop because failures become structured: placement vs global routing vs detailed routing.
- Cleaner parity with established router behavior in logic/FPGA-like flows.

## Tradeoffs

- Higher implementation complexity in the near term.
- Short-term runtime increase because of iterative passes.
- More telemetry and tuning parameters to calibrate.

## Decision recommendation

- Use a **hybrid rollout** first:
  - keep the authoritative final stage unchanged,
  - run new global-and-detailed path planner in parallel with legacy routing,
  - gate by quality thresholds (length and bend targets),
  - promote to primary on FullAdder-class circuits and then scale up.

## Evaluation plan

- Track each run with at least:
  - footprint,
  - routing length,
  - bend count,
  - via count,
  - overflow peak,
  - reroute passes,
  - compile time.
- Compare old vs new on the same set:
  - FullAdder baseline,
  - ripple chain,
  - small ALU block,
  - larger mixed module with bus/control crossing.
- Prefer quality gains over pure speed during first 2 phases, then optimize passes.

## Observed comparison (2026-07-19)

| Metric | Compatibility baseline | Local-first | Result |
| --- | ---: | ---: | --- |
| Wall time | 6.73s | 3.75-3.78s over the first five measured runs | pass |
| 20-run p95 | n/a | 3.884s | pass |
| Length | 563 | 372 | pass, -33.9% |
| Bends | 103 | 95 | fail, target <=66 |
| Vias | 126 | 78 | pass, -38.1% |
| Overflow peak | 2 | 1 | pass |
| Footprint | 1596 | 960 | pass |
| Emitted blocks | 693 | 502 | fail, target <=500 |
| Largest-net share | n/a | 17.473% | pass |
| Final conflicts / unresolved claims | 0 / 0 | 0 / 0 | pass |
| Physical FullAdder truth table | 8/8 | 8/8 | pass |

All 20 FullAdder rewrite artifacts produced the same route metrics. Their
internal runtime summary had median `3.720s`, p95 `3.884s`, minimum `3.690s`,
and maximum `3.989s`. The first five acceptance runs were within 1% of their
median.

The rewrite is not yet Phase C complete. Bend reduction is materially below
the SLO, emitted size misses by two blocks, and RippleCarryAdder4 does not yet
produce a legal local-first assignment. Hybrid fallback remains enabled so
these failures do not silently replace the compatibility path.

Representative diagnostics:
`/tmp/RedstoneAcceptance/RewriteRun1/FullAdderRewriteRun1.PhysicalDesign.json`.

### Post guide-first port comparison

The staged router port supersedes the live rewrite column above while retaining
it as a pre-port checkpoint:

| Metric | Live compatibility | Post-port local-first | Gate |
| --- | ---: | ---: | --- |
| Wall time | 6.437s | 3.000-3.046s, median 3.024s | pass |
| Length | 563 | 362 | pass, -35.7% |
| Bends | 105 current recount (103 original snapshot) | 65 | pass |
| Vias | 127 current recount (126 original snapshot) | 86 | pass |
| Overflow peak | 2 | 2 | fail, target <=1 |
| Footprint | 1596 | 1088 | pass |
| Emitted blocks | 693 | 492 | pass |
| Largest-net share | 13.321% | 17.127% | pass |
| Final conflicts / unresolved claims | 0 / 0 | 0 / 0 | pass |
| Physical FullAdder truth table | 8/8 | 8/8 | pass |

The current 20-run rewrite sample has median `3.046s`, p95 `3.099s`, minimum
`2.979s`, and maximum `3.106s`; all 20 runs produced the same route metrics.

Commands and artifacts:

- compatibility: `python Main.py --example Examples/FullAdder.sv --topmodule FullAdder --output /tmp/OpenSourceRouterCompatibility --outputname FullAdder --routing-strategy compatibility`
- rewrite runs: the same command with outputs
  `/tmp/OpenSourceRouterFinalMeasured1` through `FinalMeasured5` and strategy
  `new-router-first`
- representative diagnostics:
  `/tmp/OpenSourceRouterFinalMeasured1/FullAdder.PhysicalDesign.json`
- hybrid gate proof:
  `/tmp/OpenSourceRouterFinalHybrid/FullAdder.PhysicalDesign.json`; it rejected
  corridor overflow peak `2`, then passed 8/8 using
  compatibility while retaining the rejected rewrite metrics.

### Exact material comparison after NAND-packed rework

`EstimatedBlocks` is no longer used for density acceptance. The writer now
reports exact emitted material and ownership from its canonical block map.

| Exact emitted metric | Compatibility | Accepted rewrite | Dense gate |
| --- | ---: | ---: | --- |
| Non-air blocks | 1262 | 399 | pass, required <=500 |
| Support blocks | 627 | 197 | reported, excluded from functional ratio |
| Component-owned functional | 67 (10.635%) | 67 (33.5%) | fail, required >=60% |
| Routing-owned functional | 563 (89.365%) | 133 (66.5%) | fail, required <=40% |
| Raw dust / functional | 538 (85.397%) | 137 (68.5%) | fail, required <=45% |
| Exact footprint | 1596 | 510-544 | pass, required <=600 |
| Truth table | 8/8 | 8/8 | pass |

The v3 packer produces deterministic individual-NAND placements, localized I/O,
pin-isolation-aware mirroring, and frozen fully local nets. Its selected packed
placement routes successfully; the denser graph-beam alternative remains a
bounded candidate and is rejected when its access/congestion score is worse.

Artifacts: `/tmp/NandDenseCompatibility`, `/tmp/NandGraphBeamFallback`, and
`/tmp/NandDenseFinalHybrid`. Hybrid records the remaining component, routing,
dust, and maximum-net-share failures, then emits compatibility with an 8/8
physical truth table.

Scale runs with the same v3 policy remain unresolved:
`RippleCarryAdder4` fails exact assignment on `A2` after `5.7s`, while
`CarryLookaheadAdder4` fails on `NandNet35` after `32.2s`. No truth-table or
runtime checkbox is claimed for these failed artifacts.

### Organized NAND v4 comparison

This table supersedes the v3 result as the latest retained checkpoint.

| Exact emitted metric | Compatibility | Organized NAND v4 | Phase C result |
| --- | ---: | ---: | --- |
| Runtime | 6.715s | 2.153-2.177s | pass |
| Length | 492 | 105 | pass |
| Bends | 101 | 28 | pass |
| Vias | 113 | 30 | pass |
| Overflow peak | 2 | 0 | pass |
| Footprint | 1368 | 476 | pass |
| Exact non-air blocks | 1120 | 344 | pass |
| Component functional share | 12.0% | 39.0% | fail, requires >=60% |
| Routing functional share | 88.0% | 61.0% | fail, requires <=40% |
| Raw dust functional share | 84.6% | 65.1% | fail, requires <=45% |
| Maximum-net share | 16.667% | 22.857% | fail, requires <=20% |
| Conflicts / unresolved claims | 0 / 0 | 0 / 0 | pass |
| FullAdder simulation | 8/8 | 8/8 | pass |

The five-run median is `2.154s` with every run within 1.1%. The independent
twenty-run sample has median `2.147223s`, p95 `2.162358s`, and one deterministic
geometry/metric tuple across all artifacts.

The remaining global extensions are still too material-heavy for a component
majority. RCA4 additionally fails physical simulation after routing, and CLA4
fails exact assignment on `Carry2Propagate10` after 74.9s. Hybrid therefore
rejects v4 on its material/maximum-net gates and emits compatibility while
retaining the rewrite diagnostics.

Evidence index:
`Output/Acceptance/2026-07-19/AcceptanceSummary.md`.

### Adaptive v5 comparison

| Metric | Organized NAND v4 | Adaptive NAND v5 | Result |
| --- | ---: | ---: | --- |
| FullAdder runtime | 2.153-2.177s | 1.863-1.884s | improved, stable |
| Length / bends / vias | 105 / 28 / 30 | 102 / 26 / 30 | improved/equal |
| Overflow / conflicts / unresolved | 0 / 0 / 0 | 0 / 0 / 0 | pass |
| Footprint / non-air | 476 / 344 | 476 / 339 | equal/improved |
| Component / routing share | 39.0% / 61.0% | 39.6% / 60.4% | improved, Phase C fail |
| Raw dust share | 65.1% | 64.5% | improved, Phase C fail |
| RCA4 | simulation failure | 512/512 in 12.109s | fixed |
| CLA4 | assignment failure | assignment ceiling still exceeded | open |

The v5 JSON records demand, derived budget, effective adaptive controls,
negotiated/exact expansions, escalation history, and normalized quality. The
RCA result used the same generic policy as FullAdder; no circuit-name or
profile-count branch was added.

### Structural-reuse v6 comparison

The retained RCA4 artifact contains four nine-NAND islands with one structural
signature. Cluster 0 is packed normally; clusters 1-3 map all nine NANDs back
to cluster 0 and reuse its relative placement candidate. RCA4 still passes all
512 physical rows with the same `length=458`, `bends=122`, `vias=135`,
`overflow_peak=1`, and zero conflicts. Its observed wall time fell from the
preceding `12.450s` ownership checkpoint to `9.494s` (`23.7%`), though this is
a single-run comparison rather than a stability sample. FullAdder has one
unique island, so it correctly reports zero reuses and remains 8/8 in
`1.885s`.

A controlled in-process placement-only comparison alternated the same RCA4
netlist and policy with structural reuse enabled and disabled. Three enabled
runs had a `0.839472s` median versus `1.843388s` disabled, a `54.5%` reduction
in placement time. Both modes retain the same generic legality path; only the
reuse candidate is toggled.

Artifacts:
`Output/Acceptance/2026-07-19/StructuralReuseV6`.

### Design-wide native batching comparison

On RCA8 with 16 native routing threads, batching every portal job and every
all-net route-tree request reduced the authoritative routing-stage median from
the retained `14.232504s` baseline to `10.005192s` (`29.7%`). Portal generation
fell from `4.108728s` to a three-run median of `0.683723s` (`83.4%`), while
candidate generation fell from `5.447679s` to `4.631135s` (`15.0%`).

End-to-end RCA8 times were `26.158675`, `26.249281`, and `26.349448s`, median
`26.249281s`, versus the retained `30.338185s` baseline (`13.5%` faster). All
runs produced identical `length=914`, `bends=254`, `vias=263`, footprint
`4672`, zero conflicts/unresolved claims, and 131072/131072 physical rows.

This host has 16 physical cores and 32 SMT threads. A 32-thread batched run was
`26.152259s`, effectively equal to 16 threads, so the production default
remains 16 native threads. The optimization is work decomposition, not thread
oversubscription.

Evidence:
`Output/Benchmarks/2026-07-19/MulticoreBatch`.

### Native parallel simulation comparison

On the same routed RCA8 object, the original Python exhaustive simulator took
`6.692038s`; the native parallel evaluator took `0.392390s`, a `17.1x`
speedup. Both produced exactly equal 131072-row reports. A final telemetry run
reports `SimulationBackend=native-parallel` and `SimulationRuntimeSeconds=0.391551`.

Three end-to-end 16-core runs are `20.022497`, `20.027277`, and `20.096896s`
(median `20.027277s`, maximum deviation `0.35%`). This is `23.7%` faster than
the design-wide routing-batch median and `34.0%` faster than the original
`30.338185s` RCA8 checkpoint, with identical geometry, route metrics,
ownership, and truth-table contents. A 32-thread run at `19.962s` again shows
no material SMT advantage over the 16 physical-core default.

Evidence:
`Output/Benchmarks/2026-07-19/ParallelSimulation`.

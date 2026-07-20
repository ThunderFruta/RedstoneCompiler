# Router SLO and Gate-to-Gate Routing Targets

## Purpose

Define quality targets that keep routing from dominating while preserving correctness.

## Speed SLOs

- Compatibility mode (legacy path): keep FullAdder runtime at or below baseline (target `6.7s`, acceptable `≤ 8.0s`).
- New routing mode:
  - FullAdder runtime: `≤ 10.0s`.
  - Small datapath module runtime (up to 4-bit datapath): `≤ 25.0s`.
  - Runtime stability: same input and policy must stay within `±5%` over repeated runs.
- Throughput gating: 95th percentile runtime over 20 runs should be `≤ 2x` baseline for equivalent design class.

## Correctness SLOs

- Functional correctness: 100% truth-table pass for all compiled modules.
- Simulation correctness: 100% litematic simulation match for all outputs.
- Resource legality: authoritative final stage must report zero conflicts and zero unresolved claims.
- Overflow control:
  - preferred: `overflow_peak == 0`
  - hard limit: `overflow_peak <= 1`
- Fallback safety: if new mode fails legality/simulation, automatically fallback to legacy mode (must remain available).

## Footprint and routing-quality SLOs

- FullAdder target deltas vs baseline:
  - wire length: `-25%` to `-40%`.
  - bends: `-35%` to `-50%`.
  - vias: `-30%` to `-50%`.
- Footprint area: target from current `1596` down to `<= 1200`.
- Emitted blocks: target from current `693` down to `<= 500`.
- Routing-locality quality:
  - no net should consume more than 20% of total routed length.
  - median net reroute attempts in reroute loop should be small once stable (`median <= 1` reroute).

## Concrete gate-to-gate defaults for optimization

- Tiny-dust bias:
  - prioritize shortest local geometry first.
  - intra-cluster net span budget: `<= 8` guide steps before long expansion.
  - default penalties: length weight > 2x, bend weight > 1.5x, via weight > 1x, overflow < 0.8x, congestion < 0.5x.
- Local-net target defaults:
  - route span `<= 10`, bends `<= 3`, vias `<= 1` where legal.

## Acceptance matrix

- Phase A pass: correctness SLOs + at least one routing metric improvement (length/bends/vias).
- Phase B pass: all speed SLOs + at least two footprint SLOs.
- Phase C pass: all SLOs before making new mode default for datapath-heavy modules.

## Failure policy

- If route quality degrades or solve failures increase, lower locality aggressiveness before loosening legality constraints.
- If pass is unstable in production usage, hold at last stable phase and keep fallback path enabled.

## Acceptance evidence (2026-07-19)

### Correctness

- [x] FullAdder compatibility and local-first physical truth tables pass 8/8.
- [x] Final authoritative validation reports 0 conflicts and 0 unresolved claims.
- [x] Local-first `overflow_peak=1`.
- [x] Twenty FullAdder runs produced identical routing metrics.
- [x] Hybrid fallback behavior is covered by a forced-failure regression test.

### Quality

- [x] Length `563 -> 372` (`-33.9%`, target at least `-25%`).
- [ ] Bends `103 -> 95` (`-7.8%`, target at least `-35%`).
- [x] Vias `126 -> 78` (`-38.1%`, target at least `-30%`).
- [x] Footprint `1596 -> 960` (target `<=1200`).
- [ ] Blocks `693 -> 502` (target `<=500`).
- [x] Largest net is `17.473%` of routed length (target `<=20%`).

### Speed and phase gates

- [x] Compatibility FullAdder wall time `6.76s` (target `<=8s`).
- [x] Local-first FullAdder wall time `3.75-3.78s` for the first five measured
  runs (target `<=10s`, stability within `+/-5%`).
- [x] Twenty-run local-first p95 `3.884s`, below `2x` compatibility baseline.
- [ ] RippleCarryAdder4 local-first route fails exact assignment after about
  `10.18s`; the `<=25s` small-datapath SLO requires a successful result.
- [x] Phase A gate passes.
- [ ] Phase B gate does not pass because the small-datapath speed/correctness
  run is incomplete.
- [ ] Phase C gate does not pass because bends, blocks, and RCA4 remain open.

Validation: 35 runnable Python tests passed, 2 opt-in scale tests skipped, and
all 4 Rust routing tests passed on 2026-07-19.

### Post guide-first port evidence

Five current rewrite runs (`/tmp/OpenSourceRouterFinalMeasured1` through
`FinalMeasured5`) produced identical `362/65/86` length/bend/via metrics and
`1088/492` footprint/block metrics. Runtime was `3.000-3.046s`, median `3.024s`,
with maximum deviation `0.8%`. Truth table, zero-conflict, unresolved-claim,
maximum-net-share, length, bend, via, footprint, blocks, runtime, and stability
checks pass.

The complete 20-run sample has median `3.046s`, p95 `3.099s`, and range
`2.979-3.106s`, safely below twice the compatibility baseline.

- [ ] Overflow hard limit: observed `2`, required `<=1`.
- [x] Bend hard limit: observed `65`, required `<=66`.
- [x] Via hard limit: observed `86`, required `<=88`.
- [x] Block hard limit: observed `492`, required `<=500`.
- [ ] Phase A remains open because its correctness gate includes
  `overflow_peak <=1`.
- [ ] Phase B and Phase C remain open; the current RippleCarryAdder4 local-first
  run still exhausts exact capacity-one assignment on
  net `B2` after `9.29s`, and FullAdder still fails the overflow gate.

Hybrid validation rejects the rewrite for its overflow budget, records the
rejected metrics, reruns compatibility, and passes 8/8 physical truth-table
rows. Current validation is
36 runnable Python tests passed, 2 opt-in scale tests skipped, and 4 Rust tests
passed.

### NAND-only density gate evidence

- [x] NAND-only validation is enforced at lowered IR, placement, and writer
  boundaries.
- [x] Exact palette count equals `.PhysicalDesign.json` `ExactNonAirBlocks`.
- [x] Compatibility and rewrite FullAdder simulations pass 8/8.
- [x] Hybrid rejects the rewrite and records exact material failures before
  passing via compatibility.
- [ ] Component-owned functional share: `33.5%`, required `>=60%`.
- [ ] Routing-owned functional share: `66.5%`, required `<=40%`.
- [ ] Raw dust functional share: `68.5%`, required `<=45%`.
- [x] Exact non-air blocks: `399`, required `<=500`.
- [x] Exact footprint: `510-544`, required `<=600`.
- [x] Packed placement routes with `length=133`, `bends=39`, `vias=36`,
  `overflow_peak=0`, zero conflicts/unresolved claims, and 8/8 rows.
- [ ] Maximum-net share: `21.805%`, required `<=20%`.

Current focused validation: 41 Python tests pass and 2 opt-in scale tests are
skipped. Phase C remains open.

Current scale evidence remains negative: RippleCarryAdder4 exhausts exact
assignment on `A2` after `5.7s`, and CarryLookaheadAdder4 exhausts on
`NandNet35` after `32.2s`. Neither failed run is counted as an SLO pass.

### Organized NAND v4 evidence

- [x] FullAdder rewrite runtime: `2.153-2.177s`, required `<=10s`.
- [x] Compatibility runtime: `6.715s`, required `<=8s`.
- [x] Five-run stability: maximum deviation from the `2.154s` median is 1.1%.
- [x] Twenty-run p95: `2.162358s`, or `0.322x` compatibility.
- [x] FullAdder exact routing: `length=105`, `bends=28`, `vias=30`,
  `overflow_peak=0`, zero conflicts/unresolved claims, and 8/8 rows.
- [x] FullAdder density: footprint `476` and exact non-air blocks `344`.
- [ ] Component share: `39.0%`, required `>=60%`.
- [ ] Routing share: `61.0%`, required `<=40%`.
- [ ] Raw dust share: `65.1%`, required `<=45%`.
- [ ] Maximum-net share: `22.857%`, required `<=20%`.
- [ ] RCA4: routing finishes in about `7.5s`, but physical simulation fails.
- [ ] CLA4: exact assignment fails on `Carry2Propagate10` after `74.9s`.
- [ ] Phase C and hybrid-default promotion remain blocked.

The new exact-assignment base-owner API, transactional boundary-escape check,
multi-source fanout regression, organization diagnostics, and safe native Rust
rectilinear topology API are implemented. FLUTE3 is vendored for audit but
disabled because the pinned source is not isolated from OpenROAD `stt`/`utl`
and therefore has no valid benchmark result.

Evidence index:
`Output/Acceptance/2026-07-19/AcceptanceSummary.md`.

Final local validation for this checkpoint: 46 Python tests passed with 2
opt-in scale tests skipped, and all 6 Rust routing tests passed.

### Adaptive v5 checkpoint (2026-07-19)

The production policy is now `physical-design-v5-adaptive-nand`. FullAdder's
five measured runs were `1.866631`, `1.876168`, `1.869641`, `1.862896`, and
`1.884218` seconds (median `1.869641s`, maximum deviation `0.78%`). Every run
produced `length=102`, `bends=26`, `vias=30`, `overflow_peak=0`,
`footprint=476`, `non_air=339`, zero final conflicts/unresolved claims, and
8/8 physical rows.

RCA4 passes 512/512 in `12.109s`, below its `25s` benchmark ceiling, with zero
conflicts/unresolved claims and `overflow_peak=0`. CLA4 remains an honest
failure: no accepted assignment was retained within the initial `120s` scale
ceiling. Current validation is 48 Python tests plus 9 subtests passing, 2
opt-in scale tests skipped, and 6 Rust tests passing. The five-run FullAdder
artifacts and logs are under `Output/Acceptance/2026-07-19/AdaptiveV5`.

### Rust ownership checkpoint

The capacity-one solver now keeps pre-owned claims keyed by signal, allowing a
global extension to merge with its own frozen local tree while still rejecting
every foreign claim. Deterministic forward-domain filtering replaces the
temporary Python MRV implementation. FullAdder remains 8/8 at `1.884s`; RCA4
passes 512/512 at `12.450s` with `length=458`, `bends=122`, `vias=135`,
`overflow_peak=1`, and zero final conflicts/unresolved claims. CLA4 still does
not retain a legal assignment within 120 seconds, so its gate remains open.
Validation: 49 Python tests plus 9 subtests pass, 2 scale tests are skipped,
and 7 Rust tests pass.

### Structural-reuse v6 checkpoint

The production policy is now `physical-design-v6-structural-reuse-nand`.
RCA4 automatically reports `UniqueTemplates=1` and `ReusedClusters=3`; each
reuse has an exact nine-NAND mapping and all local routes are recomputed and
validated. The retained run passes 512/512 in `9.494s`, below the `25s` RCA4
ceiling, with `length=458`, `bends=122`, `vias=135`, `overflow_peak=1`, and
zero conflicts/unresolved claims. FullAdder remains 8/8 in `1.885s` with its
v5 geometry and route metrics. The observed RCA speed improvement is not yet a
five-run stability result, and CLA4 remains open, so Phase C is not checked.

Artifacts:
`Output/Acceptance/2026-07-19/StructuralReuseV6`.

### RCA8 scaling experiment (2026-07-19)

`Examples/RippleCarryAdder8.sv` doubles RCA4 to 72 NANDs. The structural
matcher found eight isomorphic nine-NAND islands, packed the first, and reused
its exact mapping for the remaining seven. The first route exposed an adaptive
control defect: exact assignment exhausted 128 expansions, but assignment
growth was incorrectly conditional on coarse-guide overflow. Exact assignment
now doubles its own work limit on exhaustion, independently of guide overflow,
while remaining bounded by the demand-derived maximum and runtime ceiling.

The retained rerun grew the assignment limit from 128 to 256 and succeeded in
210 expansions. It passes all 131,072 physical truth-table rows with zero
conflicts/unresolved claims and `overflow_peak=1`:

| Metric | RCA4 regression | RCA8 experiment | Ratio |
| --- | ---: | ---: | ---: |
| NANDs | 36 | 72 | 2.00x |
| Routed nets | 45 | 89 | 1.98x |
| Length | 458 | 914 | 2.00x |
| Exact non-air blocks | 1423 | 2830 | 1.99x |
| Footprint | 2272 | 4672 | 2.06x |
| End-to-end runtime | 9.686s | 30.338s | 3.13x |

The physical result therefore scales close to linearly, while runtime is
superlinear. RCA8's authoritative routing stages total `14.233s`; exhaustive
simulation also grows from 512 to 131,072 input rows. This is one experiment,
not a stability or p95 claim.

Artifact:
`Output/Experiments/2026-07-19/RippleCarryAdder8AdaptiveAssignment`.

### Native batching checkpoint (2026-07-19)

RCA8 design-wide portal and route-tree batching retains zero conflicts,
zero unresolved claims, `overflow_peak=1`, and all 131072 physical rows. Three
16-thread end-to-end runs are `26.158675`, `26.249281`, and `26.349448s`
(median `26.249281s`, maximum deviation `0.38%`). The authoritative routing
median is `10.005192s`, down `29.7%` from `14.232504s`; total runtime is down
`13.5%` from `30.338185s`. RCA4 remains 512/512 at `7.624s` and FullAdder
remains 8/8 at `1.841s` in retained single regression runs.

The 32-thread RCA8 result (`26.152259s`) is statistically indistinguishable
from 16 threads on this 16-core/32-thread CPU, so SMT oversubscription is not
the default. Current validation is 55 Python tests passing with 2 opt-in scale
tests skipped and all 7 Rust tests passing.

Evidence:
`Output/Benchmarks/2026-07-19/MulticoreBatch`.

### Native parallel simulation checkpoint (2026-07-19)

RCA8 exhaustive physical simulation now runs in `0.391-0.392s` rather than
`6.692s`, with exact equality to the Python report. Three end-to-end runs are
`20.022497`, `20.027277`, and `20.096896s`, median `20.027277s`, maximum
deviation `0.35%`. This is `34.0%` faster than the original RCA8 checkpoint.
All runs retain 131072/131072 rows, zero conflicts/unresolved claims,
`overflow_peak=1`, `length=914`, `bends=254`, and `vias=263`.

FullAdder passes 8/8 at `1.832s`; RCA4 passes 512/512 at `7.854s`. Current
validation is 55 Python tests passing with 2 scale tests skipped and all 8
Rust tests passing.

Evidence:
`Output/Benchmarks/2026-07-19/ParallelSimulation`.

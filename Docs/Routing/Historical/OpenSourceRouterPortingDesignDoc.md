# Open-Source-Informed Router Rewrite Design

> **Historical design notice (2026-07-21):** This document preserves the design
> lineage that informed earlier policies. The
> [router reliability design](../Active/RouterReliabilityDesignDoc.md) controls the v10
> implementation and compatibility behavior.

## Objective

Create a two-stage, iterative routing architecture for the Redstone compiler that pulls proven ideas from open-source physical-design flows (OpenROAD + VTR) while keeping current correctness and output semantics.

The design goal is explicit:

- preserve exact gate logic and final authoritative redstone legality checks
- make routing quality a first-class objective during planning, not a cleanup pass
- reduce snake-like nets by enforcing local structure before long runs
- keep wiring footprints and bends bounded for dense arithmetic datapaths

## Why this is the right move

Current behavior behaves like a single detailed pass trying to resolve everything after placement. Multiple OSS flows treat routing as a **pipeline**:

- placement considers routability
- coarse planning generates guides/corridors
- detailed routing is constrained by those guides
- overflow and conflicts are reduced by reroute loops

That is exactly the failure mode fix needed for `FullAdder`: if carries and local nets are not structurally planned, detailed route becomes a maze with detours.

---

## Open-source references and pullable ideas

## 1) OpenROAD global + detailed split

### Repository + paths to study

- OpenROAD docs: `grt` (global routing) and `drt` (detailed routing)
  - `https://openroad.readthedocs.io/en/latest/main/src/grt/README.html`
  - `https://openroad.readthedocs.io/en/latest/main/src/drt/README.html`
- OpenROAD source architecture (via OpenROAD/DeepWiki references):
  - `src/grt/include/grt/GlobalRouter.h` / `src/grt/src/GlobalRouter.cpp`
  - `src/grt/src/fastroute/src/FastRoute.cpp`
  - `src/drt/src/TritonRoute.cpp`
  - `src/drt/src/dr/FlexDR.cpp` + `src/drt/src/dr/FlexDRWorker.*`

### Pullable ideas

1. **Two clear router stages**
   - Stage A: grid/guide planning (coarse routes, congestion aware)
   - Stage B: local detail route constrained by Stage A outputs

2. **Guide-first flow**
   - OpenROAD’s `global_route -guide_file` creates route guides used by detailed route.
   - In Redstone, a similar guide object (`GlobalGuide`) should be an explicit artifact and a hard input to local routing.

3. **Global congestion feedback in placement**
   - OpenROAD `gpl` and `global_route` loops expose overflow/congestion metrics; placement can be adjusted over iterations.
   - In Redstone, we should add a `PlacementRoutingScore` term and rerun local placement refinements when local corridors saturate.

4. **Iterative reroute**
   - TritonRoute style: identify bad nets, reroute offenders, then continue.
   - In Redstone, replace full “reroute all” behavior with bounded “top-offender reroute” loops.

5. **Developer-facing control knobs**
   - Even if not all knobs are needed, the concept of strategy parameters (iterations, critical-net bias, via penalty, congestion budget) is important for tuning quality without rewriting logic.

---

## 2) OpenROAD-flow-scripts integration points

### Repository + paths to study

- Flow orchestration and stage dependencies:
  - `https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/blob/master/flow/Makefile`
  - `flow/scripts/global_route.tcl`
  - `flow/scripts/detail_route.tcl`
- OpenROAD variable surface:
  - `https://openroad-flow-scripts.readthedocs.io/en/latest/user/FlowVariables.html`

### Pullable ideas

1. **Stage graph with explicit contracts**
   - The flow makes every stage explicit (`global_route` -> `detail_route` -> `fillcell`), and this is directly reusable as Redstone internal stage names.

2. **Fallback and retry structure**
   - OpenLane/OpenROAD style: fallback modes and repair loops are first-class.
   - In Redstone, keep hybrid fallback (`compatibility`/`hybrid`/`new`) but make mode transitions deterministic by metrics.

3. **Variableized experimentation surface**
   - Instead of hardcoded constants in router code, use config-driven phase parameters:
     - coarse-grid congestion target
     - reroute offender count
     - max guide span / channel budget
     - local route strictness

---

## 3) OpenLane route tuning and orchestration

### References

- OpenLane 2 variable reference:
  - `https://openlane2.readthedocs.io/en/dev/reference/step_config_vars.html`
  - `DRT_OPT_ITERS`, `GRT_OVERFLOW_ITERS`, antenna repair controls

### Pullable ideas

1. **Quality budgets over single-pass behavior**
   - Explicit max-iter and stop-condition tuning for deterministic convergence.
2. **Separation of overflow budgets from optimization budgets**
   - Use separate budgets for routing quality pass vs. overflow cleanup pass.
3. **Safe defaults + aggressive profile**
   - Start with conservative defaults for reliability, then a “quality profile” for full-adder/datapath circuits.

---

## 4) VTR (VPR) planning model

### References

- VPR command-line routing and flow controls:
  - `https://docs.verilogtorouting.org/en/latest/vpr/command_line_usage/`
- VPR flow model:
  - `pack -> place -> route` stage decomposition

### Pullable ideas

1. **Separated router stage options**
   - Keep minimum needed options for Redstone:
     - global route pass budget
     - placement-route coupling switches
     - via/bend penalty style controls

2. **Negotiated routing behavior**
   - Use net ranking and conflict pressure to avoid starvation and repeated detours for the same bad net.

3. **Stage-level experimentation**
   - Even in tiny circuits, stage-level control makes tuning clearer than single monolithic knobs.

---

## 5) What to build in Redstone first (direct pull list)

## Phase 0 (no behavior change)

- Add structured telemetry output for:
  - guide count, net-locality score, congestion score, reroute offenders, overflow hotspots
- Keep existing compatibility path unchanged.

## Phase 1 (global planning)

- Implement a coarse routing layer before local tile pathing:
  - `Cluster` / `Net` -> coarse-grid cells
  - assign guides for:
    - intra-cluster nets (strict local envelope)
    - inter-cluster carry/bus/control nets (corridors)
- Output `Guide` objects with:
  - corridor box
  - layer preference
  - max bend budget for this net
  - congestion score

Open-source pull: OpenROAD `grt` concept of guides, capacities and congestion iterations, adapted to redstone grid.

## Phase 2 (placement coupling)

- Add a lightweight feedback term in placement:
  - local adjacency penalty for carry and buses
  - corridor congestion penalty from guides
  - penalty for crossing locked corridors
- Add one or two convergence iterations:
  - if corridor overflow remains high, nudge placements before a detail reroute.

Open-source pull: OpenROAD `gpl` routability-driven iterations idea (`-routability_*` style loop control).

## Phase 3 (constrained detailed route)

- Detail routing should not ignore guides.
- Route order:
  - carry, then buses, then control, then residual glue logic
- Per-net hard/soft budgets:
  - max local span, max bends for local nets, max via pressure

Open-source pull: TritonRoute staged flow (`pin access`, constrained detailed route, DRC/repair), simplified to Redstone graph model.

## Phase 4 (bounded reroute controller)

- After each pass, rank top N worst nets:
  - highest overflow contribution
  - highest detour ratio
  - highest via growth
- Reroute only top offenders with increased local penalties.

Open-source pull: standard open-source router repair loops plus VPR-style reroute pressure.

## Phase 5 (quality gating + fallback)

- Gate routing mode by measured quality:
  - if bends/overflow within budget, keep new path
  - else fallback to compatibility mode and keep diagnostics

Open-source pull: staged confidence profiles from OpenROAD/OpenLane flow scripts.

---

## Concrete metric targets for the rewrite path

- Preserve correctness:
  - truth table pass
  - final simulation pass
  - zero unresolved claims/conflicts in authoritative pass
- Quality:
  - lower FullAdder bend count and via count versus baseline
  - reduce routed length and overflow peak
  - reduce local nets leaving assigned corridor envelopes
- Determinism: repeatability for repeated runs with low variance.
- Runtime stability:
  - cap reroute and guide iterations to avoid uncontrolled expansion

---

## Redstone-specific risks and mitigations

1. **Risk: too strict guides block legality**
   - Mitigation: every net has an explicit escape budget; if guide failure occurs, open bounded repair corridor and reroute in a second pass.

2. **Risk: placement feedback loops overfit local minima**
   - Mitigation: keep max iteration caps and best-score rollback.

3. **Risk: rewrite harms long-path modules**
   - Mitigation: mode-by-module strategy:
     - structural arithmetic: `local-first` first
     - control-heavy or irregular logic: keep compatibility initially.

4. **Risk: performance regressions**
   - Mitigation: add explicit pass budgets and early-stop thresholds; log all pass-level deltas.

---

## Repository artifact update list

- Add this doc and link it from `Docs/Routing/Readme.md`.
- Keep `RouterRewriteDesignDoc.md` as the implementation tracking document.
- Keep `OpenSourceRouterRootAnalysis.md` as rationale/evidence source.
- Keep `RouterRewriteComparisonDoc.md` for benchmark deltas against compatibility.

---

## Deliverable definition (for this doc and downstream implementation)

This design is complete when:

- A new phase-based router path exists with explicit guide generation.
- Guided detailed routing can be disabled/enabled via strategy flags.
- FullAdder improves on current quality metrics while preserving functional pass.
- The flow records which OSS-inspired controls were effective per run.
- Compatibility fallback remains deterministic and non-destructive.

## Implementation checkpoint (2026-07-19)

- [x] Phase 0: `.PhysicalDesign.json` records guide overflow, guide/rip-up
  iterations, placement candidates, failed placement attempts, selected spacing,
  effective penalties, per-net lengths, ownership, and stage timings.
- [x] Phase 1: `BuildCapacityAwareGuidePlan` produces deterministic per-net
  guides, preferred layers/axes/lanes, capacity usage, overflow hotspots, and
  bounded congestion-history rip-up iterations before detailed routing.
- [x] Phase 2: the local-first flow scores bounded placement alternatives using
  guide overflow, pin escape conflicts, HPWL, locality, and gate footprint. It
  retains the nominal placement unless an alternative improves routability and
  rolls back transactionally when exact assignment rejects an alternative.
- [x] Phase 3: detailed-route candidates are generated inside guide envelopes,
  prioritize the planned layer/lane, and price guide deviation, bends, vias,
  and layer changes. Compatibility keeps its original unconstrained policy.
- [x] Phase 4: exact assignment has a bounded offender-only repair controller.
  Clean nets are frozen; crowded-column contributors are reopened with
  congestion-history cost, stagnation detection, and strict quality rollback.
- [x] Phase 5: hybrid mode applies versioned overflow, bend, via, maximum-net,
  ownership, DRC, and physical-simulation gates. A rejected rewrite is retained
  in diagnostics before compatibility is rerun.

Observed command:

```bash
python Main.py --example Examples/FullAdder.sv --topmodule FullAdder \
  --output /tmp/OpenSourceRouterFinalMeasured1 --outputname FullAdder \
  --routing-strategy new-router-first
```

Post-port FullAdder quality result: `length=362`, `bends=65`, `vias=86`,
`overflow_peak=2`, `footprint=1088`, `blocks=492`, maximum-net share `17.127%`,
truth table `8/8`, final conflicts `0`, and unresolved claims `0`. Five measured
runs were `3.000-3.046s` around a `3.024s` median (maximum deviation `0.8%`) and
had identical route metrics. The full 20-run sample was also identical, with
median `3.046s`, p95 `3.099s`, and range `2.979-3.106s`.

The architecture deliverable is implemented, but the router rewrite acceptance
is not complete. The current result passes every hard FullAdder quality gate
except corridor overflow (`2`, required `<=1`). Consequently hybrid rejects it
by metric gate and deterministically
emits the compatibility result; the rejection record is stored under
`Strategy.RejectedRewriteDiagnostics`.

RippleCarryAdder4 was also rerun with the final policy. It reaches exact track
assignment in `9.29s` but fails structurally on net `B2`; therefore the
small-datapath SLO remains unchecked rather than substituting a projection.

## NAND-packed implementation follow-up (2026-07-19)

The policy is now `physical-design-v3-nand-packed`, with exact material
provenance, deterministic pin-aligned NAND packing, frozen short local nets,
and material-gated hybrid fallback. The implementation remains generic and
contains no FullAdder, XOR, carry, or gate-name recognizer.

OpenROAD FLUTE3 revision
`566a2df7ea55bb44c530ff0944b9f4b69b306a23` and its BSD-3 license were audited
and recorded under `RustRouting/ThirdParty/Flute3`. The current upstream source
depends on OpenROAD `stt`, `utl`, generated lookup-table translation units, and
Boost, so it has not been linked. Executable FLUTE3 integration remains
unchecked rather than silently importing the full OpenROAD dependency graph.

- [x] Exact emitted ownership/material telemetry.
- [x] NAND-only pipeline and one-template-per-NAND invariant.
- [x] Packed placement, localized I/O, pin-aware mirroring, frozen local nets,
  and transactional candidate rollback.
- [x] Congestion-history guide allocation remains bounded and deterministic.
- [ ] Executable FLUTE3 topology wrapper.
- [x] Packed FullAdder routes at `399` exact blocks, footprint `510-544`,
  `length=133`, `bends=39`, `vias=36`, and `overflow_peak=0`.
- [ ] 60/40 component/routing split: current exact result is `33.5/66.5`.
- [ ] Scale regressions: RCA4 currently fails exact assignment on `A2` after
  `5.7s`; CLA4 fails on `NandNet35` after `32.2s`.

## Organized NAND v4 porting checkpoint (2026-07-19)

The active implementation supersedes structure-specific bus/carry ordering
with generic producer-consumer topology, shared fanout, pin geometry, boundary
demand, and exact material cost. No FullAdder, XOR, carry, or generated-name
recognizer exists under placement or routing.

Open-source-informed mechanisms now live in the v4 flow:

- typed guide/detail/ownership boundaries,
- dynamic multi-source signal-tree growth,
- exact assignment preloaded with complete local ownership,
- present/history congestion cost for bounded offender-only repair,
- deterministic organization layers and entrance/deviation telemetry, and
- a safe native Rust rectilinear topology API.

The exact FLUTE3 `flute.cpp`, `POWV9.dat`, and `POST9.dat` from OpenROAD revision
`566a2df7ea55bb44c530ff0944b9f4b69b306a23` are now retained under
`RustRouting/ThirdParty/Flute3/Upstream`, with license, notice, revision, and
modification records. FLUTE remains disabled: the pinned C++ imports OpenROAD
`stt` and `utl`, and an unisolated import would violate the dependency boundary.
It has no selected-candidate or scale-recovery benchmark to justify production
enablement.

The current FullAdder result is `length=105`, `bends=28`, `vias=30`,
`overflow_peak=0`, footprint `476`, exact blocks `344`, and 8/8 physical rows.
Its twenty-run p95 is `2.162358s`. Material composition remains outside release
limits (`39.0%` component, `61.0%` routing, `65.1%` dust), RCA4 fails physical
simulation, and CLA4 fails exact assignment after 74.9s. Phase C remains
unchecked. By later explicit user direction, `new-router-first` is the sole CLI
mode and compatibility remains available internally rather than as a CLI
choice.

Evidence index:
`Output/Acceptance/2026-07-19/AcceptanceSummary.md`.

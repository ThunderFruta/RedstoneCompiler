# Router Rewrite Completion Prompt

Use this as a one-shot execution prompt for implementing the rewrite plan and validating all 3 goal areas (speed, correctness, footprint/size).

## Objective

Complete the new routing flow to the point where all active acceptance criteria are met and checked off in:

- [Router Rewrite Design Document](./RouterRewriteDesignDoc.md)
- [Router Rewrite Comparison Document](./RouterRewriteComparisonDoc.md)
- [Router SLO](./RouterSLO.md)

## Execution Prompt (copy/paste)

You are implementing a router/placement rewrite in `/mnt/Projects/RedstoneCompiler`.

1. Implement the phase plan from `Docs/Routing/RouterRewriteDesignDoc.md`.
2. Keep legacy behavior intact behind compatibility and fallback mode.
3. After each phase, validate the new path and fill the checkboxes in this document.
4. Prioritize: correctness > speed stability > footprint improvements.

## Definition of completion

Phase completion is “done” only when all checks below pass:

### 1) Correctness
- [ ] FullAdder truth table passes.
- [ ] Redstone simulation passes.
- [ ] Authoritative final check reports 0 conflicts and 0 unresolved resource claims.
- [ ] `overflow_peak <= 1` and no unexpected deterministic failures.
- [ ] Legacy fallback works and is functionally identical.

### 2) Routing quality / footprint
- [ ] FullAdder routing length reduced by at least 25% from baseline run.
- [ ] FullAdder bend count reduced by at least 35% from baseline run.
- [ ] FullAdder via count reduced by at least 30% from baseline run.
- [ ] FullAdder footprint reduced from 1596 toward `<= 1200`.
- [ ] FullAdder output blocks reduced from 693 toward `<= 500`.
- [ ] No single net dominates >20% of total routed length.

### 3) Speed and stability
- [ ] FullAdder runtime in new mode <= 10.0s.
- [ ] Baseline/compatibility path runtime <= 8.0s.
- [ ] Repeatability test (5+ identical runs) within ±5%.

## Required validation runbook

Run in order, and capture logs with run summary metrics:

```bash
cd /mnt/Projects/RedstoneCompiler
source .venv/bin/activate

# Baseline (legacy mode) reference
python Main.py --example Examples/FullAdder.sv --topmodule FullAdder --output Output/FullAdderBaseline --outputname FullAdderBaseline --routing-strategy compatibility

# New mode (quality mode, explicit routing strategy if available)
python Main.py --example Examples/FullAdder.sv --topmodule FullAdder --output Output/FullAdderRewrite --outputname FullAdderRewrite --routing-strategy new-router-first

# Repeatability check
python Main.py --example Examples/FullAdder.sv --topmodule FullAdder --output Output/FullAdderRewriteRun2 --outputname FullAdderRewriteRun2 --routing-strategy new-router-first
python Main.py --example Examples/FullAdder.sv --topmodule FullAdder --output Output/FullAdderRewriteRun3 --outputname FullAdderRewriteRun3 --routing-strategy new-router-first
```

After each run, record and compare:
- `length`
- `bends`
- `vias`
- `overflow_peak`
- `routing passes` and `conflicts`
- `footprint`
- `runtime`

If command-line flags for routing mode/strategy are not yet implemented, add them first in a compatibility-safe way, then rerun this exact sequence.

## Evidence format for checklist

For each checkbox, store:

- run id / date
- command used
- parsed metrics
- pass/fail + rationale

Example line:

`2026-07-19 | FullAdderRewrite run #1 | length=420 | bends=62 | vias=80 | overflow_peak=1 | footprint=1180 | pass`

## Acceptance sequencing

### Phase A gate
- [ ] All correctness checks pass
- [ ] At least one of length/bends/vias improves vs baseline

### Phase B gate
- [ ] All speed checks pass
- [ ] At least two footprint checks pass

### Phase C gate
- [ ] All checks above pass
- [ ] Design/Comparison docs updated with actual post-change values

## What to do when blocked

- If a metric fails, do not force tuning-only changes.
- First adjust placement locality costs and local corridor budgets.
- Then adjust global guide penalties and rip-up pass budget.
- Keep fallback on and do not regress compatibility.

## Handoff expectation

When implementation is complete, append the final observed metrics to:
- [Router SLO](./RouterSLO.md)
- `PhysicalDesign.json`/run log section in the comparison doc notes
- commit message summary: `feat: implement local-first router rewrite`

## Observed status (2026-07-19)

`2026-07-19 | compatibility | length=563 | bends=103 | vias=126 | overflow_peak=2 | footprint=1596 | blocks=693 | wall=6.73s | truth=8/8 | pass`

`2026-07-19 | local-first runs 1-20 | length=372 | bends=95 | vias=78 | overflow_peak=1 | footprint=960 | blocks=502 | max_net=17.473% | median=3.720s | p95=3.884s | truth=8/8 | partial`

- [x] FullAdder truth table and redstone simulation.
- [x] Zero final conflicts and unresolved claims.
- [x] Length, vias, overflow, footprint, largest-net share, runtime, and
  repeatability goals.
- [ ] Bend target: observed `95`, required `<=66`.
- [ ] Block target: observed `502`, required `<=500`.
- [ ] RippleCarryAdder4 local-first acceptance: exact assignment failed after
  about `10.18s`.
- [x] Phase A.
- [ ] Phase B.
- [ ] Phase C.

# Open Source Router Root-Fix Analysis (Physical Routing, Not Network Routers)

I reviewed open-source physical design router flows to identify what to fix at the **architecture root** instead of only adding constraints.

## What I reviewed

- OpenROAD flow stages and components:
  - Global placement (`gpl`) and the RePlAce routability-driven mode.
  - Global routing (`grt`) based on FastRoute 4.1.
  - Detailed routing (`drt`) with TritonRoute.
- OpenROAD-flow-scripts stage wiring.
- VTR (Verilog-to-Routing) VPR router options.
- OpenLane integration knobs for overflow/rerun behavior.

## Key insight

Most mature open-source flows are router-at-the-root by construction:

- place and route are not one-way switches; they are in feedback.
- routing has explicit coarse planning (global guides/steiner/channel model) before local legalization.
- detailed routing has dedicated repair loops (maze reroute / search and repair / iterations).

This is very different from “single detailed pass over topology only,” which is where snake-like, high-detour patterns usually originate.

## Evidence from open-source references

1. OpenROAD global placement is routability-aware by design.

The OpenROAD global placement docs describe:
- `-routability_driven` mode.
- congestion estimation via RUDY by default, and an optional FastRoute-backed option (`-routability_use_grt`) for more precise congestion feedback.
- per-iteration congestion target checks and cell inflation behavior to reduce congestion in placement.

2. OpenROAD global routing is a staged routing engine with overflow and congestion controls.

Global routing docs show:
- FastRoute-based routing with configurable congestion iterations.
- options for critical-net priority (`-critical_nets_percentage`), fanout-skipping, and allowed congestion.
- explicit layer/region adjustment knobs to shape routing demand.
- guide output and incremental routing support.

3. OpenROAD detailed routing (TritonRoute) is a multi-stage engine.

TritonRoute docs call out:
- pin access analysis.
- track assignment.
- initial route.
- search/repair loops.
- DRC engine.
- configurable reroute order/randomness and optimization iterations.

This is important because it means short direct routes are not just preference flags; they are supported by dedicated phases and iterative cleanup.

4. VPR (VTR) separates global and detailed behavior and exposes routing quality knobs at root level.

VPR docs include:
- `--route_type {global | detailed}` with combined mode possible.
- bend-cost tradeoffs (for route smoothness).
- congestion-driven and timing-budget routing controls.
- dynamic routing bounding box updates in dynamic mode.
- many router/placement/placement-debug switches used in a feedback loop.

5. OpenLane’s integration confirms root-coupled repair loops are expected, not optional.

OpenLane docs expose tuning variables for:
- global routing overflow iteration budget.
- detailed routing optimization iterations (`DRT_OPT_ITERS`).
- antenna repair iterations.
- explicit overflow/repair thresholds and snapshots.

That shows industrial-open flows treat router quality as a staged convergence problem, not a one-shot constraint problem.

## How this maps to your router “snake wire” issue

Your current symptom (“wire snaking”) is usually a root-level pipeline issue:

- Placement is not giving the router enough structure to keep nearby gates physically contiguous.
- Detailed routing is forced to recover from weak upstream structure by growing detours.
- The path search budget is dominated by constraint balancing after-the-fact, not by a structural objective first.

## What a root fix looks like (beyond constraints)

### 1) Make structure explicit first

- Detect repeated local modules (adder slices, carry chains, control islands).
- Build a net/topology contract for each repeated structure with ordered pin roles and legal escape windows.
- Keep carry and buses in fixed orientation/corridors from the start.

### 2) Put routing in a true two-phase flow

- Add a coarse router stage that produces guides/regions:
  - local/short-class nets: strict local budgets and short envelopes.
  - cross-section nets: channel reservations.
  - fanout nets: explicit budgeted branching.
- Feed the detailed router only this constrained guide graph.

### 3) Couple placement to routability

- Add placement objectives that use local HPWL + congestion proxies.
- Use feedback-like placement adjustments when guide occupancy reports high congestion.
- Reuse deterministic fallback if overflow persists, but do not stay in pure post-hoc cleanup mode.

### 4) Use router-native reroute loops

- After each pass, rank worst offender nets by extra length/bends/overflow.
- Reroute only those nets with aggressive penalties in reroute candidates.
- Iterate with bounded passes and converge on quality.

## Practical recommendation for next implementation pass

Start with the same project-level split used by OpenROAD/VPR:

- `Placement policy` produces a placement candidate with routability signal.
- `Guide generator` allocates explicit channels.
- `Detailer` honors guides and uses local cost-first pathing.
- `Repair loop` reroutes worst paths only.

Then only after this root architecture is in place, add low-level tuning knobs.

## Actionable next steps

1. In your root docs, add the above phase model as a required pipeline.
2. Add a “local-first” metric to every pass: local wire share, local net detour ratio, and max-bend-outlier.
3. Add an explicit “guide quality” report alongside existing length/bend/via stats.
4. Keep compatibility fallback, but only as safety, not as the default design pressure.

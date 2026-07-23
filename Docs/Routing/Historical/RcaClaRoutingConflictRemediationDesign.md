# RCA/CLA Routing Conflict Remediation Design

> **Historical proposal notice (2026-07-21):** The still-valid generic demand,
> portal, and escalation concepts were migrated to the
> [router reliability design](../Active/RouterReliabilityDesignDoc.md). Policy status and
> compatibility/hybrid fallback text below are superseded; retained failure
> measurements remain historical evidence.

Status: proposed
Date: 2026-07-20
Scope: generic NAND placement and Redstone routing

## 1. Problem

RCA4 and CLA4 currently fail after NAND synthesis, during physical routing.
The failure is not caused by an invalid NAND netlist. Individual route
candidates exist, but the candidate sets cannot be selected simultaneously
under the authoritative capacity-one ownership rules.

The retained RCA diagnostic reports pairwise-unroutable candidate sets for
`A1/B1`, `B1/NandNet6`, `Carry0/Propagate1`, and `CarryIn/Propagate0`
(`Output/RC4_new.log`, historical and no longer retained). CLA4 reaches the same class of
failure at larger scale, with candidate generation or exact assignment
exhausting its runtime budget on cross-stage propagate/generate nets
(`Output/Benchmarks/continue2/cla1.log`, historical and no longer retained).

The common cause is insufficiently organized boundary capacity: packed NAND
clusters expose competing terminals into the same portals and corridors.
Increasing candidate counts or assignment expansions does not create a legal
disjoint solution; it only delays failure.

## 2. Goals

- Make placement account for boundary-terminal and corridor demand before
  detailed routing.
- Reserve deterministic, disjoint portal capacity for competing signals.
- Preserve and reuse legal local trees and same-signal trunks.
- Reroute only extensions that contribute to a conflict or material failure.
- Keep the implementation generic over NAND netlists; do not recognize RCA,
  CLA, FullAdder, carry, propagate, or generated gate names.
- Preserve compatibility routing as an explicit, immutable fallback.
- Produce deterministic ownership, metrics, and failure diagnostics.

## 3. Non-goals

- Relaxing Redstone electrical, isolation, repeater, or ownership legality.
- Allowing two unrelated signals to share a capacity-one resource.
- Solving failures by unbounded search, larger unexplained constants, or
  circuit-specific placement rules.
- Replacing the existing NAND templates or physical simulator.

## 4. Contracts

### 4.1 Boundary demand

Each packed-cluster candidate must publish a demand record containing:

- boundary terminals grouped by signal;
- fanout and unresolved target count per signal;
- required portal slots and estimated corridor lanes;
- local-claim coverage and predicted global extension material;
- pin-escape, congestion, and conflict estimates.

Placement rejects or penalizes a candidate when demand exceeds the legal
capacity of its surrounding corridors.

### 4.2 Portal reservation

Before global candidate generation, assign deterministic portal slots to each
boundary signal. Slots are ordered by signal identity, terminal geometry, and
stable seed. Same-signal branches may merge; unrelated signals may not share a
slot or downstream capacity-one resource.

The reservation result is part of the placement candidate transaction. If
reservation fails, the complete candidate is rolled back and another retained
placement is tried.

### 4.3 Local route seed

Local NAND-to-NAND claims are routed and validated while the cluster is being
packed. Connected sinks are removed from the global unresolved set. Accepted
local trees become immutable owners unless a final legality check rejects the
placement.

Global routing receives only the remaining boundary targets and their declared
portals.

### 4.4 Conflict diagnostics

For every failed assignment, emit a deterministic conflict graph:

- vertices: signals;
- edges: signals with no pair of mutually compatible candidates;
- resource hotspots: resources used by every candidate of one or more signals;
- candidate counts and affected cluster/portal identifiers.

The diagnostic must distinguish:

1. no candidate for an individual signal;
2. pairwise incompatibility;
3. a larger matching failure despite pairwise compatibility; and
4. work-budget exhaustion before the candidate set was fully examined.

## 5. Routing flow

### Stage A: demand-aware placement

1. Build generic producer/consumer clusters.
2. Enumerate legal NAND template placements and local claims.
3. Calculate boundary demand and corridor capacity from actual geometry.
4. Rank candidates by legality, conflict demand, local capture, predicted
   routing material, congestion, footprint, and stable placement identity.
5. Retain bounded alternatives for feedback.

### Stage B: organized global guides

1. Route and freeze complete local trees.
2. Allocate portal slots from cluster boundaries, not raw pins.
3. Prefer component-plane local routes, X-preferred trunks, Z-preferred
   trunks, and upper-layer crossings.
4. Allocate lanes so competing signals receive disjoint capacity-one paths.
5. Permit bounded escape only when the escape is legal and recorded.

### Stage C: seeded detailed routing

1. Connect each unresolved target to the nearest legal node already owned by
   that signal.
2. Merge same-signal claims and recompute repeaters/signal strength over the
   merged tree.
3. Rank candidates by legality, ownership conflicts, incremental material,
   guide adherence, reuse, length, bends, and vias.
4. Run exact capacity-one assignment over the resulting candidate pool.

### Stage D: structured escalation

On assignment failure, escalate in this order:

1. alternate deterministic portal slots;
2. additional lane diversity within the existing guide plan;
3. one additional legal routing layer;
4. relocation of only affected clusters or retained placement candidates;
5. regenerated candidates for the offender set.

Do not increase all-net search budgets before changing the physical geometry.
Stop on stagnation or the typed runtime budget and return the conflict graph.

### Stage E: offender-only repair

Rank offenders by overflow contribution, conflict degree, incremental material,
and route-shape cost. Rip up only those global extensions. Preserve clean local
trees and unrelated global branches. Revalidate authoritative ownership,
connectivity, signal strength, isolation, DRC, and physical truth-table results
after every accepted repair.

## 6. Compatibility policy

`CompatibilityPhysicalDesignPolicy` must explicitly serialize all baseline
placement and routing controls. It must not inherit evolving defaults such as
local-first spacing, portal, lane, or candidate values. Changes to the new
policy cannot alter compatibility geometry or route metrics.

The user-facing strategies remain:

- `new-router-first`: expose rewrite failure diagnostics;
- `hybrid`: validate rewrite, then rerun compatibility on failure;
- `compatibility`: explicit frozen baseline.

## 7. Validation plan

Unit tests must cover:

- boundary-demand rejection and retained-placement rollback;
- deterministic portal reservation and same-signal merging;
- pairwise conflict graph and larger matching diagnostics;
- local-claim removal from global targets;
- offender-only reroute and escalation ordering;
- compatibility policy serialization and geometry immutability.

Integration tests must verify:

- FullAdder remains 8/8 with zero conflicts and unresolved claims;
- RCA4 reaches 512/512 when a legal route is retained;
- CLA4 either reaches 512/512 or reports a bounded structured failure;
- identical seeds reproduce ownership and route metrics;
- no circuit-name recognizers are used.

Acceptance artifacts must retain the command, policy, derived demand/budget,
raw log, artifact path, route metrics, conflict graph, and pass/fail rationale.

## 8. Success criteria

The rewrite is considered fixed for scale routing only when RCA4 and CLA4 both
produce legal litematics with zero final conflicts/unresolved claims,
`overflow_peak <= 1`, and complete physical truth-table results. Runtime and
material goals remain separate acceptance gates; they must not be substituted
for correctness.

Handoff subject: `feat: remediate organized NAND routing conflicts`.

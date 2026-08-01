# Current routing failures

Status: active working-tree failure record

Evidence date: 2026-07-23

This page describes what fails in the current negotiated-router checkpoint.
It deliberately separates observed artifact facts from likely causes. Dated
acceptance history remains in the
[router reliability guide](RouterReliabilityGuide.md), while failure-type
definitions remain in the [failure catalog](FailureCatalog.md).

Use the [router execution prompt](RouterExecutionPrompt.md)
to turn this evidence into a structured independent diagnosis and solution
proposal.

## Acceptance state

| Circuit | Current state | Consequence |
| --- | --- | --- |
| FullAdder | Fresh physical compile passes in 0.477 seconds with 8/8 rows | Lightweight physical gate is green; the durable 5/5 matrix was not rerun |
| RippleCarryAdder4 | Fails after four deterministic placement attempts | Current blocking gate |
| CarryLookaheadAdder4 | Not attempted after the RCA4 failure | Its current behavior is unknown; historical failures do not establish the present cause |

The acceptance sequence stops at RCA4 by design. CLA4 work begins only after
RCA4 passes 2/2 with 512/512 rows and zero final conflicts.

## RRF-076 implementation checkpoint

Primary current evidence:
`/tmp/rca-request-100ms/RippleCarryAdder4.RoutingFailure.json` (RRF-078).
Earlier current-tree checkpoints remain for continuity:
`/tmp/rrf077-final-rca/RippleCarryAdder4.RoutingFailure.json`,
`/tmp/rrf076-final2-rca4/RippleCarryAdder4.RoutingFailure.json`.

The requested scheduling, dynamic-region, branch-state, native diagnostic,
and cumulative-cut changes are present, and the complete test discovery runs
176 tests with 174 passing and two scale tests skipped. The exact Rust release
gate passes 25/25. A fresh FullAdder artifact at
`/tmp/rrf076-fulladder-final/FullAdder.PhysicalDesign.json` reports 8/8 rows,
zero conflicts, overflow peak one, and `FallbackUsed=false`.

RCA4 is not restored. The final fresh run stops before the 23-second routing
deadline after these four distinct placements:

| Generator | Fingerprint | Elapsed | Typed result |
| --- | --- | ---: | --- |
| `row-beam` | `4fbfb60378c2b189` | 2.630641 s | `TrackAssignmentConflict`, eight cumulative cut owners |
| `row-beam-conflict-relocation` | `492d79c22f7e6500` | 3.211196 s | `RepeaterAccessInfeasible`, `NandNet18` |
| `row-beam-direct-only` | `cc16211b75d258b2` | 6.367586 s | local adaptive slice expires while routing `NandNet18` |
| `configured-packing` | `5c6b03590c0a2aaa` | 1.788408 s | self-claim candidate exhaustion, `NandNet21` |

The final artifact is diagnostic evidence, not acceptance. CLA4 was not run
because the sequential gate stopped at RCA4.

## Pre-RRF-076 RCA4 artifact

Earlier primary evidence:
`/tmp/rca4-current-gate/RCA4.RoutingFailure.json`

The process exits with a typed failure rather than timing out:

- stage: `NegotiatedDetailedRouting`;
- final reason: `GlobalCongestionUnresolved`;
- final affected signal: `NandNet21`;
- detail: no legal portal-aware route tree was found in the bounded negotiated
  sparse region;
- eight placement requests were considered and five reached recorded routing
  attempts;
- router elapsed time: 22.962414 seconds;
- complete process runtime: 22.974599 seconds; and
- deadline state: not expired, with 37 milliseconds remaining.

`NandNet21` is an artifact identifier only. The router must not treat that name,
its numeric suffix, or RCA4 as an algorithm selector.

## Failure sequence

### 1. Primary packed placement reaches an electrical-conflict plateau

Placement `4fbfb60378c2b189` builds a 9,792-node, 47,552-edge detailed graph.
Negotiated overflow progresses:

```text
124 -> 10 -> 10 -> 10 -> 10
```

The first repair pass removes most provisional overuse, but four subsequent
measurements cannot reduce the last ten conflicts. All ten are electrical
claims between these six signals:

- `Carry2`;
- `CarryIn`;
- `NandNet12`;
- `NandNet18`;
- `NandNet21`; and
- `NandNet3`.

The hotspots occur in three physical groups:

- input-side conflicts near `(0, 1, 19)` through `(1, 2, 19)`;
- far-boundary conflicts near `(0, 19, 19)` through `(13, 18, 19)`; and
- an internal group near X 6–12, Z 13–14, on routing levels 3–4.

The typed result is `DetailedCongestionUnresolved`. Its repair actions request
both `ExpandCongestedCut` and `RelocateAffectedClusters`.

### 2. Later placement fails repeater access

Placement `82a5729eb7b1d93d` fails after 1.257215 seconds with
`RepeaterAccessInfeasible` for `NandNet18`. Its sparse graph contains 8,731
nodes. This means the route cannot satisfy the current portal, direction,
signal-strength, repeater, support, air, and electrical constraints inside the
exposed region.

It does not prove that the placement is globally impossible. Repeater-aware
search and region expansion are not yet complete enough to make that claim.

### 3. Remaining routed placements fail sparse-region connectivity

Three later placements fail `GlobalCongestionUnresolved` before producing a
negotiated overflow series:

| Placement fingerprint | Cached nodes | Affected signal |
| --- | ---: | --- |
| `cc16211b75d258b2` | 18,722 | `NandNet18` |
| `775c50aff37c9a91` | 13,221 | `NandNet21` |
| `98a8c2bbb696aaec` | 13,433 | `NandNet21` |

These failures say that the current portal-aware detailed search cannot connect
the signal inside its bounded sparse region. They do not establish that no
legal route exists in the placement-wide resource graph.

## What the evidence proves

- Boundary demand is not the first observed blocker on the primary placement;
  its recorded boundary overflow is zero.
- Negotiation is effective initially: it removes 114 of 124 provisional
  conflicts.
- Present and history penalties alone do not clear the remaining ten electrical
  conflicts within the current repair domain.
- Placement feedback is being attempted, but subsequent placements expose
  repeater-access and sparse-connectivity failures.
- Runtime enforcement works. The router returns a typed failure before its
  deadline rather than being killed externally.
- No current RCA4 placement produces a publishable physical design or 512-row
  truth table.

## Likely causes still requiring proof

### Sparse regions do not expand at the right time

The leading hypothesis is that a legal provisional tree suppresses expansion
even when its retained branches touch the active-region boundary or congestion
stagnates. A real halo tile must be
`4 * Technology.TrackPitch`, and one implicated side should expand at a time
without rebuilding the placement-wide graph.

To prove this, diagnostics must record active tiles, boundary-touching tree and
frontier nodes, expanded sides, and graph deltas for every non-improving pass.

### Repair replaces too much of a multi-terminal tree

Current whole-net replacement can discard a clean trunk or legal target branch
while trying to repair one conflict. The intended design retains clean branches
and prunes only paths touching overused claims.

To prove this cause, record retained and pruned branch identities and compare
occupancy before and after each repair.

### Repeater legality is not fully integrated into path state

The `RepeaterAccessInfeasible` placement indicates that path connectivity and
signal-strength legality can disagree. Detailed search must include position,
incoming direction, and remaining strength and reserve repeater support, air,
wire, and electrical claims while exploring.

To distinguish a genuinely impossible repeater placement from an undersized
region, record rejected repeater states and whether their search frontier
touches the region boundary.

### Placement feedback may be too narrow

The primary detailed cut names six contributing signals, but the final sparse
cut contains only `NandNet21`. Relocating only the last failing signal can leave
the original shared bottleneck intact. Placement feedback should move or
inflate every cluster contributing to the saturated physical cut.

## Comparison with earlier passing RCA4 evidence

Two earlier working-tree artifacts completed in about 15.1 seconds with
512/512 rows, zero final overflow, placement fingerprint `56b5cd84a819a882`,
and a cached graph containing 26,978 nodes and 141,282 edges:

- `/tmp/rc-neg-rca4-release2.yoSJWf/RippleCarryAdder4.PhysicalDesign.json`;
  and
- `/tmp/rc-neg-rca4-release.lJy6Ef/RippleCarryAdder4.PhysicalDesign.json`.

Their overflow progression was `[66, 0]`. This proves that a prior dirty-tree
checkpoint could route RCA4, but it is not an acceptance baseline for the
current source. The placement fingerprints differ, so the graph-size gap is
supporting evidence for the sparse-region hypothesis rather than an isolated
proof of regression cause.

## Required fix evidence

The next RCA4 attempt should not be judged only by whether it passes. Its
artifact must demonstrate:

1. a true one-tile initial halo;
2. boundary-touch or stagnation-triggered incremental expansion;
3. only newly exposed nodes and edges passed to `AddRegion`;
4. retained legal route-tree branches across repair passes;
5. integrated repeater state and claim reservations;
6. congestion feedback containing every contributing cluster;
7. zero final overflow and unresolved claims;
8. 512/512 physical truth-table rows; and
9. identical placement and routing fingerprints across two runs below 25
   seconds.

Until those requirements pass, the current failure should be described as
“RCA4 unresolved in negotiated sparse detailed routing,” not as a CLA4 failure
and not as a special property of a named generated net.

## RRF-077 rooted-search result

The retained current artifact is
`/tmp/rrf077-final-rca/RippleCarryAdder4.RoutingFailure.json`. It completes in
21.064387 seconds and publishes no partial physical design. The primary packed
placement (`4fbfb60378c2b189`) still reports the same eight-signal capacity-one
cut. The first relocated placement (`492d79c22f7e6500`) now fails as
`NoPinAccessPattern` for one signal: its mandatory source/target access cells
contain exact wire/support/headroom self-conflicts before negotiated routing is
allowed to add a branch.

This supersedes the earlier `RepeaterAccessInfeasible` diagnosis for that
placement. The detailed native builder now preserves rooted
`(Position, IncomingDirection, RemainingStrength)` paths, and target access is
included in the searched physical object. The remaining blocker is therefore
inside packed intra-cluster pin geometry, not a missing repeater search state.

The baseline packed gate area is 465 and the enforced maximum is 930. Three
relocation variants and the direct/configured packed alternatives measure
1,456--1,457 and are rejected. The only later legal-area alternative is
unpacked (`c21c1bba830749d4`, area 6,726 because the packed ceiling does not
apply to unpacked placement); its local adaptive slice expires during resource
graph construction. RCA4 remains not accepted and CLA4 remains gated.

## RRF-078 net-owned portal result

The retained fresh artifact is
`/tmp/rca-request-100ms/RippleCarryAdder4.RoutingFailure.json`. Portal stems are
now selected as self-legal net-wide tuples, and every nonempty profile set uses
the negotiated router. The former `NoPinAccessPattern` failure is no longer the
terminal blocker. With the unchanged 23-second internal deadline, negotiated
overflow progressed from 92 to 22 before expiry. No physical design was
published, so RCA4 remains unaccepted and CLA4 was not run.

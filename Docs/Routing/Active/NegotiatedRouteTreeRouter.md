# Negotiated route-tree router

Status: implementation in progress; not accepted

Current policy: `physical-design-v11-negotiated-route-trees`

Production strategy: `new-router-first`

## Purpose

The negotiated router replaces one-shot candidate assignment with a bounded
PathFinder-style loop. Every routable signal owns one reusable route tree.
The first pass may overlap temporarily; later passes reroute only trees that
touch overused physical claims, using present and accumulated history costs.
Final success still requires capacity-one ownership and full Redstone physical
validation.

The implementation must remain circuit agnostic. Circuit names, generated
signal prefixes, NAND counts, and benchmark identities may not select routing
or placement behavior. Benchmark names exist only in the acceptance harness.

## Pipeline

1. Place and locally route packed NAND clusters.
2. Build net profiles and capacity-aware coarse guides.
3. Generate terminal access portals and reserve legal boundary capacity.
4. Materialize the initial sparse resource region.
5. Route one provisional tree per unresolved signal.
6. Measure exact wire, support, air, and electrical overflow.
7. Prune or reroute affected branches with present and history costs.
8. Expand only offender regions that reach a search boundary or stagnate.
9. Return a congestion or escape cut to placement after bounded stagnation.
10. Materialize repeaters, validate all claims, simulate, and publish.

## Current implementation

- `NegotiatedRoutingPolicy` defines tile pitch, iteration and stagnation caps,
  congestion costs, relocation rounds, and the two-times packed-area ceiling.
- `PlanNegotiatedRouteTrees` builds provisional trees, records overflow, and
  performs deterministic repair.
- `RoutingResourceGraph.BuildRegion` caches sparse Python regions and can reuse
  a compatible lower-height or smaller-column region.
- Rust `RoutingContext.AddRegion` deduplicates exposed nodes and edges.
- `RoutingCongestionFeedback` and typed routing failures carry physical cuts
  back to placement.
- `.PhysicalDesign.json` records negotiated iterations, overflow progression,
  rerouted signals, and cached graph size.
- `NegotiatedRegionState` owns active tiles, exact 12-block halo columns,
  boundary touches, expanded sides, and per-signal node/edge ownership.
- One-sided expansion rebuilds only the enlarged signal region and submits
  only previously unseen nodes and edges to `AddRegion`.
- `NegotiatedRouteTreeState` retains clean target paths and prunes only branch
  claims that touch an overused resource.
- The diagnostic native route-tree API returns typed status, target paths,
  boundary-frontier nodes, repeater reservations, and repeater rejections.
- Placement scheduling preserves cumulative cut owners, skips empty-cut
  conflict relocation, and advances after a local adaptive timeout while the
  one absolute deadline remains live.

## Remaining limitations

The following work is required before RCA4 or CLA4 acceptance:

- Native search carries position, incoming direction, and remaining strength,
  but the current RCA4 placements still expose paths whose complete rooted
  tree has no legal repeater sequence. Repeater reservation identity must be
  retained directly from the successful search state rather than reconstructed
  from the merged node set.
- Branch claims are retained at target granularity. Pruning to an interior
  nearest branch point is still less explicit than a native edge-identity tree
  and remains a risk for high-fanout repair.
- Boundary assignment must preserve enough detailed-route reachability; a
  conflict-free portal bundle that collapses the usable layer domain can still
  make the net unroutable.

## Dynamic expansion contract

Each net owns an `ActiveTiles` set. It starts with its coarse guide tiles plus
one complete tile halo. After a repair pass, expansion is requested when any
of these conditions holds:

- a retained or newly routed branch uses a node on the active boundary;
- the search frontier terminates on the active boundary;
- the same physical overflow does not improve for three iterations; or
- the only lower-cost paths leave the active region.

Expansion adds one tile only on sides implicated by the boundary touch or
overflow hotspot. Existing route trees, history costs, resource ownership, and
cached regions survive. Placement-wide graph construction is forbidden during
ordinary escalation.

## Branch repair contract

For every conflicted signal:

1. Mark claims that participate in an overused resource.
2. Walk from affected claims to the nearest retained branch point.
3. Remove only those branches and decrement their global occupancy.
4. Keep the source trunk and every conflict-free target branch.
5. Push retained tree nodes into the detailed-search frontier.
6. Route only disconnected targets through the current or expanded region.
7. Commit the branch and increment occupancy after all Redstone claims are
   legal for that branch.

Branches of the same signal may share their own claims. Different signals may
not share capacity-one wire, support, required-air, or electrical resources.

## Placement feedback

Boundary and congestion failures return all contributing signals, saturated
resources, and hotspots. Placement maps those signals to producer and consumer
clusters, moves or inflates every contributor, and retains the result only when
its packed gate-area footprint is no more than twice the baseline. At most
three relocation rounds are permitted.

Packed retained placements are routed before deferred unpacked or alternative
spacing placements are constructed. Runtime is shared by the entire flow and
never reset by relocation or region expansion.

## Diagnostics

Successful and failed runs must record:

- algorithm and policy version;
- coarse, detailed, and placement-feedback iteration counts;
- overflow progression and conflict resources;
- rerouted signals and retained/pruned branch counts;
- active tiles, boundary touches, expanded sides, and added graph deltas;
- cached node and edge counts after every expansion;
- boundary cuts and congestion cuts;
- baseline, candidate, and maximum packed areas; and
- deadline state and final validation result.

## Acceptance gates

Run sequentially and stop at the first failure:

| Circuit | Runs | Required result | Ceiling |
| --- | ---: | --- | ---: |
| FullAdder | 5 | 8/8 rows, zero final conflicts | 10 s |
| RippleCarryAdder4 | 2 | 512/512 rows, zero final conflicts | 25 s |
| CarryLookaheadAdder4 | 2 | 512/512 rows, zero final conflicts | 120 s |

Repeated successful runs must have identical placement and routing
fingerprints, `FallbackUsed=false`, and authoritative physical validation.

## Current evidence

RRF-076 keeps FullAdder green at 0.477 seconds, 8/8 rows, zero conflicts, and
overflow peak one. Python discovery runs 176 tests with 174 passing and two
scale skips; Rust passes 25/25. RCA4 remains failed: the current
artifact is `/tmp/rrf076-final2-rca4/RippleCarryAdder4.RoutingFailure.json`.
One packed feedback placement fails typed repeater access for `NandNet18`; the
next reaches its local adaptive slice while routing that signal, and the final
configured placement exhausts legal candidates for `NandNet21`.
CLA4 remains gated.

Two earlier negotiated RCA4 runs completed in approximately 15.1 seconds with
placement fingerprint `56b5cd84a819a882`, cached graph size 26,978 nodes and
141,282 edges, overflow `[66, 0]`, and 512/512 truth-table rows.

The current 2026-07-22 working tree regresses RCA4. Its primary placement has
fingerprint `4fbfb60378c2b189`, a 9,792-node/47,552-edge cached graph, and
overflow `[124, 10, 10, 10, 10]`. The ten retained conflicts are electrical
claims. The current failure artifact is
`/tmp/rca4-current-gate/RCA4.RoutingFailure.json`. This evidence motivates
boundary-triggered dynamic expansion; it does not qualify as acceptance.

RRF-077 removes the compatibility-node-set reconstruction from the detailed
native path. The detailed builder now grows one rooted tree directly, carries
incoming direction and remaining strength across retained frontier nodes, and
returns exact rooted target paths and repeater reservations. Python repair
keeps the producer access root first, and complete target portal plus target
access chains are part of native legality rather than appended afterward.

The current retained RCA4 artifact is
`/tmp/rrf077-final-rca/RippleCarryAdder4.RoutingFailure.json` (21.064387
seconds). It reclassifies the relocated placement from repeater failure to
`NoPinAccessPattern`: mandatory access claims conflict within one packed
nine-NAND cluster. Packed alternatives above the 465-to-930 area envelope are
now rejected uniformly. This is better failure truth, but RCA4 remains failed
and CLA4 remains gated.

Net-wide portal ownership now combines source access, target access, and every
portal stem before exact self-claim validation. Inter-net portal pressure is
negotiated with the route tree instead of pre-solved terminal by terminal. The
Rust A* goal also checks whether its arrival direction and remaining strength
can traverse the fixed target continuation; unusable arrival states remain in
search rather than failing only in post-route repeater validation.

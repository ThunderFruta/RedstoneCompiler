# Organized NAND Routing Design

## Status

Implemented architecture for `physical-design-v4-organized-nand`, with release
gates still open. This design does not alter synthesis semantics: every internal gate
remains an ordinary NAND and every placed NAND still stamps `Nand.litematic`.
The measurable acceptance checklist is maintained in
[Organized NAND Routing Goals](OrganizedNandRoutingGoals.md).

## Adaptive v5 amendment (2026-07-19)

`physical-design-v5-adaptive-nand` replaces circuit-size branches with two
serialized contracts: `RoutingDemandEstimate` measures the placed design, and
`DerivedRoutingBudget` records the bounded controls selected from that demand.
Minecraft electrical rules remain in `RedstoneRoutingTechnology`; absolute
FullAdder size and material targets now live only in
`RoutingAcceptanceProfiles`.

The detailed router starts at the technology minimum layer count. It uses
deterministic capacity-one MRV selection, reroutes only offenders, and grows
layers, portals, lanes, candidate diversity, and assignment work by typed
policy factors when assignment exhausts the current space. Exact ownership,
connectivity, signal strength, isolation, DRC, and physical simulation remain
mandatory. No circuit or net names participate in these decisions.

The design addresses the remaining FullAdder density problem measured on
2026-07-19:

| Metric | Current rewrite | Phase C target |
|---|---:|---:|
| Exact non-air blocks | 344 | <=500 |
| Footprint | 476 | <=600 |
| Component-owned functional blocks | 67 (39.0%) | >=60% |
| Routing-owned functional blocks | 105 (61.0%) | <=40% |
| Raw dust share | 65.1% | <=45% |
| Routed length | 105 | <=422 |
| Maximum-net share | 22.857% | <=20% |
| Conflicts / unresolved claims | 0 / 0 | 0 / 0 |

The current result is already compact in area. The remaining failure is
composition: the five global extensions (`A`, `B`, `CarryIn`, `NandNet0`, and
`NandNet3`) dominate the 105 routed cells. With 67 component-owned functional blocks,
the 60% component-majority gate requires routing-owned functional blocks to
fall to approximately 44 or fewer. Penalty tuning alone cannot close that gap.

## Design objective

Make the physical result both organized and materially smaller by treating a
packed NAND island and its local signal trees as one transaction:

1. pack connected NANDs around legal pin access,
2. route and reserve their internal branches during candidate placement,
3. expose only island boundary terminals to global routing,
4. grow fanout nets from the nearest point already owned by that signal, and
5. use predictable layer and corridor conventions for the remaining global
   routes.

"Organized" means that route structure is visible in the data model and the
emitted geometry: local signals stay inside their islands, horizontal and
vertical trunks use preferred layers, boundary pins line up with shared
corridors, and unrelated signals do not wander through an island.

## Non-negotiable invariants

- `ValidateNandOnlyDesign` runs after synthesis, after placement, and before
  writing. Internal logical gates may only be `NAND`.
- A packed island is placement and routing metadata, never a logic macro.
- Every synthesized NAND maps one-to-one to the existing NAND template.
- Routing may emit signal dust, repeaters, and support, but no logic gate.
- The compatibility policy and geometry remain frozen.
- Every accepted design passes authoritative capacity-one ownership, template
  isolation, final DRC, and the physical truth-table simulator.
- Hybrid mode falls back on packing, routing, ownership, DRC, material, or
  simulation failure and retains the rejected diagnostics.
- No circuit, gate, or net-name recognizers are permitted. Decisions use only
  connectivity, pin geometry, fanout, physical claims, and material cost.

## Why the current contracts are insufficient

`PlacedDesign.FrozenNetWires` can reserve a complete short net. Partial local
branches are recorded in `LocalNetBranches`, but they are not authoritative
signal-owned inputs to exact assignment. Consequently a fanout net with one
nearby sink and one remote sink cannot keep the short branch and extend from
it. Treating that branch as a generic obstacle makes the same signal fight its
own geometry; ignoring it makes the global router redraw the net from its
source.

The next revision must make pre-routed signal ownership a first-class resource
graph contract.

## New typed contracts

### `LocalRouteClaim`

One validated, signal-owned tree fragment created while evaluating a packed
placement candidate.

```python
@dataclass(frozen=True)
class LocalRouteClaim:
    Signal: str
    ClusterId: int
    Root: Position3
    ConnectedTargets: tuple[Position3, ...]
    BoundaryNodes: tuple[Position3, ...]
    Nodes: frozenset[Position3]
    Edges: frozenset[RoutingEdge]
    Claims: RoutingResourceClaims
    RepeaterReservations: tuple[RoutingReservation, ...]
    ExactRouteSignalBlocks: int
    ExactRouteRefreshBlocks: int
    ExactRouteSupportBlocks: int
```

Rules:

- Claims are built by the same `RoutingResourceGraph` used by detailed
  routing; placement cannot invent a weaker legality model.
- A local claim may connect all or only some targets of a signal.
- Claims from the same signal may touch and merge. Claims from different
  signals remain capacity-one and electrically isolated.
- `BoundaryNodes` are legal points from which the global tree may continue.
- The root and all connected targets must be reachable within the claimed
  graph, and repeater orientation/signal strength must validate before the
  claim is retained.

### `PackedNandClusterCandidate`

`PackedNandCluster` remains the accepted metadata record. Candidate search uses
a richer transactional form containing:

- member NAND placements, rotations, mirrors, and selected pin escapes,
- local route claims and exact resource ownership,
- unresolved boundary terminals,
- exact incremental component, route, dust, support, and footprint counts,
- rejection reasons and legality diagnostics.

No placement candidate survives the beam merely because its bounding boxes fit.
It must be physically stampable and its committed local routes must be legal.

### `SignalRouteSeed`

The authoritative router receives one seed per remaining signal:

- producer access path,
- zero or more accepted `LocalRouteClaim` trees,
- already-connected targets,
- unresolved targets,
- legal continuation nodes, and
- pre-owned resource IDs.

A complete local net has no unresolved targets and bypasses candidate routing.
A partial local net enters detailed routing as an existing signal-owned tree.

### `RoutingOrganizationPolicy`

Versioned policy fields define geometry conventions rather than circuit names:

- component/local plane,
- preferred X-trunk layer,
- preferred Z-trunk layer,
- bridge layer range,
- cluster keep-in envelope,
- boundary corridor width and pitch,
- maximum local branch distance,
- maximum global entrances per cluster and signal,
- same-signal merge permission,
- cross-signal island traversal prohibition.

## Placement and local routing flow

### 1. Connectivity-driven island construction

Build clusters from the NAND graph using weighted edges:

- producer-consumer adjacency,
- shared fanout source,
- number of pins that can face each other,
- topological distance,
- estimated exact local-route material,
- boundary-cut cost, and
- expected corridor demand.

High-fanout producers should sit near the geometric center of their consumers.
Gates shared across regions sit at an island boundary. Cluster size remains
bounded by `NandPackingPolicy.MaximumClusterCells`.

### 2. Route-while-packing beam search

When adding a NAND to a candidate:

1. enumerate legal rotations, mirrors, and pin-aligned positions,
2. stamp actual template voxels into the candidate resource graph,
3. connect newly local producer-consumer pairs with a bounded multi-source
   search,
4. atomically reserve route and support claims,
5. reject overlap, unintended redstone adjacency, invalid repeater direction,
   or signal-strength failure, and
6. score exact material deltas before HPWL, bends, and vias.

The beam score is lexicographic:

1. physical legality,
2. unresolved/conflicting claims,
3. routing-owned functional blocks,
4. raw dust blocks,
5. boundary terminal count and corridor demand,
6. footprint,
7. support blocks,
8. length, bends, and vias,
9. deterministic placement identity.

This deliberately prefers a slightly less compact island when it removes a
long route or a global boundary crossing.

### 3. Boundary extraction

After a cluster candidate is accepted:

- freeze its NAND template placements and local route claims,
- remove locally connected sinks from the unresolved target set,
- expose the smallest legal set of continuation nodes,
- align boundary nodes to deterministic corridor tracks where possible, and
- block unrelated global nets from crossing the cluster keep-in envelope.

## Multi-source detailed routing

For a signal with a partial local tree, route each remaining sink to the
closest legal point in the already-owned tree rather than back to the producer.
After each connection, the new path becomes part of the source set for the next
sink. This constructs one shared Steiner-like tree and prevents duplicated
trunks.

The search state distinguishes three cases:

- unowned resource: usable at normal cost,
- resource owned by this signal: usable as a zero/new-material merge point,
- resource owned by another signal: unavailable except through an explicitly
  legal bridge-layer transition.

Target order is deterministic and material-aware. Prefer the target with the
lowest legal incremental material cost; break ties by distance, target
coordinate, then logical pin identity. Route candidates report both total tree
metrics and incremental material over the pre-owned seed.

Exact assignment starts with local claims preloaded into the owner map. It
selects only extensions for unresolved targets. Candidate conflicts are tested
against the union of static geometry, all accepted local claims, and already
selected global extensions. Same-signal overlapping claims are merged, not
reported as conflicts.

## Organized global geometry

Use a small, deterministic routing grammar:

- local pin connections and island trees remain on the component plane,
- X-dominant trunks prefer the X layer,
- Z-dominant trunks prefer the Z layer,
- upper layers are used for legal crossings and congestion escape,
- cluster boundary pins snap to shared corridor tracks,
- a global net enters an island only through its declared boundary nodes, and
- support blocks are consolidated only after ownership and electrical legality
  are fixed.

These are preferences with bounded escape, not rigid rules. A legal route may
violate a preferred layer, but the diagnostic must record the deviation and
incremental material it caused.

For two-terminal boundary nets, use direct Manhattan candidates. For nets with
three or more unresolved terminals, use a rectilinear Steiner topology when
the isolated FLUTE3 wrapper becomes available; until then use the same
deterministic multi-source incremental tree. FLUTE supplies topology only—the
Redstone resource graph remains authoritative for physical legality.

## Reroute and placement feedback

The reroute controller operates on extensions, not on frozen clean trees:

1. identify nets contributing to overflow, ownership conflict, excessive
   material, or organization violations,
2. preserve legal local claims and clean global branches,
3. reopen only offending extensions,
4. increase present and historical cost on saturated resources,
5. stop on success, bounded iteration exhaustion, or repeated identical state,
   and
6. return structured corridor and boundary pressure to the retained placement
   candidates.

Placement feedback may choose another packed candidate or widen a boundary
corridor within policy. It must never mutate the frozen compatibility flow.

## Exact accounting and observability

The canonical block map remains the acceptance source. Extend
`.PhysicalDesign.json` with:

- local claim count and claimed resources by signal/cluster,
- targets connected locally versus globally,
- global entrances per cluster,
- reused same-signal nodes and avoided duplicate-trunk blocks,
- local/global route blocks and support blocks,
- preferred-layer deviations and island traversal violations,
- exact incremental material for every route extension,
- placement candidates rejected by local DRC or material score, and
- reroute/placement feedback decisions.

No estimated count may satisfy a material acceptance gate.

## Failure model

Add structured reasons for:

- local claim overlap with another signal,
- local claim connectivity failure,
- no legal boundary escape,
- partial-tree extension failure,
- cluster entrance budget exceeded,
- organization policy violation,
- local material budget exceeded, and
- multi-source routing stagnation.

In `new-router-first`, return the structured failure. In `hybrid`, preserve it
under rejected rewrite diagnostics and rerun compatibility.

## Implementation phases

### Phase 1: authoritative local ownership

- Add `LocalRouteClaim` and replace the three loosely related local-net maps
  with a compatibility reader plus a typed claim collection.
- Build local claims with `RoutingResourceGraph.BuildRouteClaims`.
- Validate local claims independently and preload exact resource ownership.
- Preserve complete frozen-net behavior through the new contract.

Gate: existing FullAdder result remains functionally correct and deterministic;
all local claims report zero cross-signal conflicts.

### Phase 2: partial trees and multi-source extension

- Exclude only locally connected targets from routing profiles.
- Seed candidate generation with local trees and boundary nodes.
- Extend a net from its nearest owned node and merge same-signal resources.
- Recompute repeaters and signal strength over the complete merged tree.

Gate: a focused fanout fixture proves that one near sink can be frozen while a
remote sink extends the same tree without self-conflict or duplicate trunk.

### Phase 3: route-while-packing search

- Introduce transactional packed candidates with a candidate-local resource
  graph.
- Route new internal adjacencies during beam expansion.
- Score candidates by exact route/dust/support deltas and boundary cut.
- Retain multiple legal packed candidates for routing feedback.

Gate: FullAdder routing-owned functional blocks fall below 80, component share
reaches at least 45%, dust share is at most 55%, footprint remains at most 600,
and correctness/ownership gates pass.

### Phase 4: organized corridor grammar

- Add axis-preferred layers, deterministic boundary tracks, cluster keep-ins,
  entrance budgets, and deviation telemetry.
- Make guide allocation consume boundary terminals rather than every raw pin.
- Apply offender-only rerouting to global extensions.

Gate: `overflow_peak <=1`, maximum-net share <=18%, and no unrelated global net
crosses an island envelope without a recorded bounded escape.

### Phase 5: density and scale closure

- Add placement feedback from chronic boundary/corridor pressure.
- Integrate the isolated BSD-3 FLUTE3 topology wrapper if it beats the fallback
  on multi-terminal nets without adding OpenROAD runtime dependencies.
- Run FullAdder, RippleCarryAdder4, and CarryLookaheadAdder4 regressions and the
  required runtime samples.

Final FullAdder gate: component-owned share >=60%, routing-owned share <=40%,
raw dust share <=45%, routing-owned functional blocks approximately <=44,
footprint <=600, non-air blocks <=500, maximum-net share <=20%,
`overflow_peak <=1`, zero conflicts/unresolved claims, and all eight physical
truth-table rows passing.

## Test matrix

Unit tests:

- local claims validate connectivity, ownership, repeaters, and isolation,
- same-signal claims merge while cross-signal claims conflict,
- partial target removal preserves unresolved targets,
- multi-source routing reuses an owned trunk deterministically,
- route-while-packing rolls back all claims on candidate rejection,
- organization layers/corridors have deterministic bounded escape,
- exact provenance totals match the emitted litematic, and
- compatibility snapshots remain unchanged.

Integration tests:

- FullAdder passes 8/8 in compatibility and rewrite modes,
- forced hybrid rejection reproduces compatibility logical outputs,
- identical seeds reproduce placements, claims, routes, ownership, and metrics,
- RippleCarryAdder4 and CarryLookaheadAdder4 pass their physical regressions,
- accepted designs have zero final conflicts and unresolved claims, and
- material gates use the final canonical block map.

Performance tests:

- one compatibility FullAdder baseline,
- one rewrite warm-up and five measured deterministic runs,
- twenty rewrite runs for p95,
- RippleCarryAdder4 <=25s, and
- no unbounded beam, guide, assignment, or reroute loop.

## Explicit non-goals

- No FullAdder, carry, XOR, or generated-name macro recognition.
- No replacement of NAND templates with compound logic cells.
- No restoration of the retired flat negotiated detailed router.
- No weaker placement-only collision model.
- No acceptance based on estimated blocks or projected metrics.
- No full OpenROAD/OpenDB/LEF/DEF runtime dependency.

## Implementation checkpoint (2026-07-19)

Phases 1 and 2 are implemented: typed local claims, boundary-escape
transactions, partial target removal, dynamic multi-source extension, exact
assignment with complete local base ownership, merged-tree repeater validation,
and incremental/full-tree diagnostics are live. The packed flow also emits
deterministic boundary nodes and tries bounded alternative placements.

The retained FullAdder v4 artifact passes 8/8 simulation with zero conflicts,
zero unresolved claims, and `overflow_peak=0`. Its exact result is
`length=105`, `bends=28`, `vias=30`, `footprint=476`, and `344` non-air blocks.
Five measured runtimes are `2.153-2.177s`; the twenty-run p95 is `2.162358s`.

Phase 3's material gate remains open: routing is 105 functional blocks,
component share is 39.0%, routing share is 61.0%, and raw dust share is 65.1%.
Maximum-net share is 22.857%, so the Phase 4 gate also remains open.

RCA4 routes within the runtime budget but fails physical simulation. CLA4
exhausts exact assignment on `Carry2Propagate10` after 74.9s. These retained
failures keep Phase 5 open and keep both CLI and pipeline defaults on
`compatibility`.

FLUTE3 source and lookup data from pinned OpenROAD revision
`566a2df7ea55bb44c530ff0944b9f4b69b306a23` are vendored for audit. The C++
still imports OpenROAD `stt`/`utl`, so it is disabled. A memory-safe,
deterministic native Rust rectilinear topology API is live; FLUTE will not be
enabled without an isolated build and favorable RCA4/CLA4 evidence.

Full commands, logs, artifacts, and rationale are recorded in
`Output/Acceptance/2026-07-19/AcceptanceSummary.md`.

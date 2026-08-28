# Router reliability design

**Status:** Normative v10 recovery design; implementation and verification are
tracked in
[RouterReliabilityImplementationNotes.md](RouterReliabilityImplementationNotes.md).

**Target policy:** `physical-design-v10-routability-feedback`

This document defines the required behavior of the template PCB placement and
routing path. “Must” and “shall” are acceptance requirements. The
[router reliability guide](RouterReliabilityGuide.md) owns operational commands,
current measurements, and the acceptance verdict.

The proposed
[routing-aware placement and access design](RoutingAwarePlacementAccessDesign.md)
defines a candidate replacement for the fixed straight-pin-ray placement
contract and an immutable handoff into the negotiated router. It remains a
proposal until its protected physical gates pass; this reliability design
continues to control current production invariants. The v10 policy label,
older matrix/manifest shape, and eight-file native checkpoint below are retained
as dated implementation history rather than current-source evidence; the live
acceptance harness and timestamped snapshot control those concrete values. The
proposal's measurements and source identities are recorded separately in the
append-only [snapshot log](RoutingAwarePlacementAccessSnapshots.md).

## Negotiated route-tree amendment

The [negotiated route-tree router](NegotiatedRouteTreeRouter.md) is the current
implementation contract for global and detailed routing. It supersedes rigid
candidate enumeration and exact candidate assignment on the normal
authoritative path while preserving this document's reliability invariants:
one shared deadline, deterministic placement order, capacity-one final claims,
typed hard failures, transactional publication, and final physical
validation. Exact candidate assignment remains isolated legacy and unit-test
support only.

The active flow must provision all retained packed placements before producing
deferred placements, match terminals to claim-compatible boundary escapes,
route reusable multi-pin trees under negotiated congestion, grow detailed
resource regions incrementally, and return escape or congestion cuts to every
contributing cluster. No behavior may depend on a circuit name, generated NAND
name, or fixed net count.

## Verified v9 failure chain

The v10 work begins from five observed defects rather than from a general
search-budget increase:

1. Packed placement checks can accept rectangle-disjoint NAND templates that
   the final `PcbGatesConflict` electrical-isolation oracle rejects.
2. `QualityTarget="first-legal"` suppresses placement feedback, retained
   alternatives, and capacity-aware global guides even though those controls
   affect feasibility rather than output quality.
3. Placement constructs alternatives but routing attempts only the first
   retained placement. “First legal” therefore means first generated placement,
   not first placement that completes routing and physical validation.
4. Portal reservation, candidate diversity, lane diversity, and layer retries
   can revisit an equivalent resource graph or silently change mode across a
   recursive retry.
5. The Python deadline is checked after large native calls, allowing candidate
   generation or assignment to overrun the compile budget materially.

The repair must address structure, state, and bounded execution. More threads,
larger default searches, longer timeouts, circuit-name branches, relaxed
electrical rules, or an automatic compatibility fallback are not remedies.

## Implementation checkpoint

The v10 policy flags, exact packed-placement checks, retained placement loop,
boundary records/scoring, core failure artifact, escalation selection,
offender-only cache retention, shared Python deadline, and bounded native
results are implemented with focused tests. Demand-first primary placement,
lazy graph-beam recovery, physically distinct greedy reservation variants, raw
portal-geometry reuse, bounded unreserved request construction, and
first-legal result-only optimization suppression are also implemented.
`RRF-065` carries foreign access exclusions into native search. `RRF-066` adds
mandatory exact self-claim validation during candidate
materialization, cumulative candidate pools for compatible lane/layer
escalation, and honest post-assignment deadline/progress accounting. `RRF-067`
adds typed native conflict signals and exact cross-signal support ownership;
`RRF-068` defines cross-layer portal availability and the stacked-access layer
floor; and `RRF-069` bounds placement generation, routing-control passes, and
candidate starts using measured work while retaining one absolute deadline.
This status means the corresponding contracts compile and their focused tests
pass; it does not substitute for physical acceptance.

`RRF-011` now supplies the complete failure evidence envelope, a parallel
successful-run reliability envelope, and transactional publication guards with
focused tests. `RRF-043` now supplies focused proof for hard no-escape and
capacity-one entrance rejection while preserving soft-overflow ranking. Portal
starts are anchored to graph-valid terminal access, partial placement-owned
claims participate in base assignment ownership, repeated reserved work can
advance once to bounded unreserved portals without changing deadline or
router, and frozen routes are isolated from template-only validation geometry.
`RRF-061` supplies the deterministic evidence harness. The pre-RRF-069 matrix
is preserved at
`Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF069/`.
The RRF-070 matrix is preserved at
`Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF071/`.
The RRF-071 matrix is preserved at
`Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF072/`, and the
RRF-072 pre-envelope matrix is preserved at
`Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF073/`.
`RRF-073` records the current matrix: FullAdder is verified at 5/5, while RCA4
and CLA4 both fail 0/2, so the complete router remains not accepted. All four
scale failures self-exit with typed artifacts and all nine processes remain
inside their immutable wall ceilings. RRF-065 through RRF-072 are
**Implemented**, and the physical deadline-enforcement sub-gate in RRF-073 is
**Verified**. The failed RCA4/CLA4 routes prevent a global router `Verified`
claim. `RRF-074` closes the explicit cluster-zero terminal-coordinate and
all-placement-recipe rollback proof without changing this architecture.

## Required pipeline

```text
NAND IR
  -> deterministic placement candidates
  -> exact electrical and local-claim validation
  -> boundary demand/capacity scoring
  -> stable retained-candidate order
  -> coarse capacity-aware guides
  -> bounded portal and route-tree generation
  -> exact capacity-one assignment
  -> authoritative DRC and truth-table simulation
  -> first fully valid result, or typed hard failure
```

One `RoutingDeadline` begins in `Compiler/Placement/Flow/Runner.py` before
placement-candidate work. Every placement and routing stage consumes that same
absolute deadline. No repair, claim-release, recursive call, or next candidate
may reset it.

## RRF-020: legal-by-construction placement

`PcbGatesConflict` is the single exact legality oracle for committed cell
geometry. Beam expansion, row packing, structural reuse, terminal placement,
vertical stacking, and final commit must all consult it. A cheaper bounding-box
check may reject an obviously illegal candidate, but it cannot accept a
candidate without the exact check.

Candidate construction is transactional:

- placements and local claims are staged outside the accepted design;
- exact overlap, isolation, pin-access, ownership, and frozen-local-route checks
  run before commit;
- a rejection discards all staged geometry and claims; and
- a later candidate cannot observe state from a rejected candidate.

Terminal sorting must distinguish cluster index `0` from a missing cluster.
Tests must assert explicit order and coordinates rather than derive expected
output by calling the production sort expression.

## RRF-030: retained placement contract

Every retained alternative is represented by a deterministic
`PcbPlacementCandidate` with these logical fields:

- candidate ID, generator name, routing spacing, and placement fingerprint;
- the placed design and staged local claims;
- routing-feedback score;
- boundary demand, capacity, overflow, and pin-scarcity metrics; and
- a stable final score and rejection reasons.

Equivalent placement fingerprints are deduplicated. Feasible candidates are
scored before footprint density in this order:

1. exact legality and hard boundary feasibility;
2. boundary overflow and pin scarcity;
3. routing-feedback and global congestion cost;
4. wire-length and footprint objectives; and
5. candidate ID as the deterministic tie-breaker.

The flow retains at most `NandPacking.RetainedPlacementCandidates` and routes
them in stable score order. `QualityTarget="first-legal"` means the first
candidate whose route passes authoritative DRC and simulation. A typed failure
from candidate N advances to candidate N+1 only while the shared deadline has
time remaining.

Placement generation is demand-first and lazy. With production packing
enabled, the initial bounded pair is row-beam at configured spacing followed by
the unpacked oracle. Configured graph-beam, row-beam-direct-only,
graph-beam-direct-only, and alternative-spacing recipes remain deterministic
deferred recovery work. The flow constructs a deferred recipe only after all
currently retained primary candidates fail; a successful primary route must
not pay graph-beam construction cost. Recipe deduplication and every deferred
attempt consume the original `RoutingDeadline`.

The v10 policy adds:

- `PlacementPolicy.EnableRoutingFeedback`; and
- `GlobalRoutingPolicy.EnableCapacityAwareGuides`.

Both are enabled in the production policy. The frozen compatibility policy
keeps both disabled. `QualityTarget` selects among valid results; it must not
enable or disable feasibility analysis, guide construction, portal rules,
retention, or escalation.

## RRF-040: boundary feasibility

`BoundaryDemandRecord` describes the required exits, entrances, and corridor
lanes for one placed cluster boundary. `BoundaryCapacityRecord` describes the
distinct electrically legal portal and lane resources available at that same
boundary.

Placement must reject a candidate when a required terminal has no legal escape
or when hard entrance demand exceeds all realizable capacity. Soft overflow and
pin scarcity remain scoring inputs so that a larger routable placement beats a
smaller placement that predictably deadlocks routing.

The hard test enumerates one-primitive exits from immutable terminal access and
requires a capacity-one matching from every required boundary signal to a
distinct exit coordinate. Hard entrance demand is one per required signal: fanout,
unresolved-target count, preferred-side geometric capacity, and corridor
overflow remain soft because a global tree may enter once and branch. This is a
necessary feasibility test, not a promise that later multi-primitive routes are
mutually compatible.

Portal reservations are transactional and capacity-one. A failed reservation
attempt releases everything it staged. Boundary demand and capacity summaries
are serialized for both successful and failed attempts.

## RRF-041: explicit escalation state machine

`RoutingEscalationState` has, at minimum:

- portal mode and reservation variant;
- candidate- and lane-diversity levels;
- requested and effective layer counts;
- assignment budget;
- candidate-set, conflict-set, and resource-graph fingerprints; and
- the affected signal and cluster sets.

All recursive and native calls receive the complete state. A retry must not
silently omit or change portal mode. A reservation variant that produces the
same effective portal/resource fingerprint is recorded as skipped, not routed
again.

The deterministic transition table is:

| Classified result | Next state |
| --- | --- |
| Assignment budget exhausted | Increase assignment work within the remaining absolute deadline; do not alter geometry |
| No candidates for affected signals | Regenerate portal/route geometry for those signals only |
| Pairwise or higher-order incompatibility | Advance portal reservation, then lane diversity, then one physically realizable layer |
| Effective layer capacity already saturated | Skip layer escalation because it cannot alter the resource graph |
| Candidate, conflict, or resource fingerprint repeats | If reserved portal work has not used its bounded unreserved transition, advance once without changing the deadline; otherwise stop the equivalent retry sequence and relocate the affected cluster or try the next placement |
| Absolute deadline expired | Return `RuntimeBudgetExceeded` immediately |

When reserved portal work repeats, one deterministic transition to bounded
unreserved portal generation is permitted. It reuses the same
`RoutingDeadline`, runs at most once for that repeated work, and still performs
exact capacity-one assignment. This is an internal state of production
`new-router-first`; it is not the frozen compatibility router, an automatic
fallback, or permission to enlarge the search deadline.

Reservation alternatives must alter physical work. For greedy reservations,
`ReservationVariant` rotates the stable portal preference order before scarce
terminals are assigned, so variant N cannot be reported as diversity merely
because its label changed. A retry whose selected portal IDs and resource
fingerprint match prior work is skipped by the repeated-work rule.

Raw portal generation is geometry work and may be reused across a
reservation-only retry. `RawPortalGeometryCache` is immutable and matches only
the same placed-design identity, routing-resource identity and region, layer
count, portal limit, per-signal variant counts, guide expansion, and native
expansion limit. Reservation filtering operates on a fresh dictionary and may
not mutate the raw cache. A layer or other geometry-control change invalidates
the cache.

The one bounded unreserved transition must not construct an unbounded portal,
axis, lane, or target Cartesian product. It applies a deterministic initial
request ceiling, keeps distinct portal variants represented, uses one ordered
lane per axis at a time, and returns to the same capacity-one exact assignment.
Increasing candidate or lane diversity may change the bounded prefix, but may
not reset the deadline or silently broaden compatibility behavior.

Unaffected route candidates and local claims remain cached. Geometry changes
invalidate only offender nets. Pairwise/higher-order conflicts use their
conflict graph to find affected clusters; relocation releases only claims whose
signals intersect that offender set. An empty intersection releases nothing.

Compatible diversity escalation is cumulative. A lane-diversity retry retains
the prior candidate set and metadata, adds candidates from newly exposed lanes,
and deduplicates by deterministic candidate ID before scoring. An
effective-layer retry follows the same rule when the old layer geometry remains
valid. A portal-reservation change does not carry forward candidates whose
reservation assumptions changed. Increasing a diversity label without
increasing the effective lane/request domain is equivalent work and must be
skipped.

### RRF-066: exact candidate self-claim legality

Candidate materialization must construct claims only after combining the
native route tree, source access, target access, and retained local-claim nodes.
It must then run the exact same-net claim oracle before the candidate enters
capacity-one assignment. This includes wire-versus-support, required-air, and
electrical-exclusion conflicts inside one signal; tree connectivity alone is
not legality.

Production may not bypass this check. In particular, if a vertical transition
requires support at a coordinate occupied by another dust node in the same
candidate, the candidate is rejected as `SelfClaimConflict` before assignment.
The FullAdder diagnosis that established this rule found dust and required
support both claiming `(14,1,3)` in the selected `B` route. The previous call
site opted out of a check that already detected that collision; `RRF-066`
removes that opt-out rather than relaxing the resource model.

The dedicated support-under-wire regression makes this slice `Implemented`.
It remains unverified until fresh FullAdder, RCA4, and CLA4 physical gates pass
on the changed source.

### RRF-067: typed native conflicts and cross-signal support ownership

The Python and native capacity-one oracles must apply the same physical claim
rules. A support cell may be shared as inert material, but it may not occupy a
different signal's wire or required-air cell. Wire/electrical and
wire/required-air interference remain exact conflicts. Python conflict
discovery, Python candidate comparison, native `ClaimMask::Conflicts`, and
native conflict-index reporting must agree on those categories.

`RoutingAssignmentResult.ConflictSignals` is a typed, deterministic offender
set for a failed exact assignment. On domain exhaustion it contains the
selected assignment stack, the current signal, and the signal whose domain was
emptied, in stable order. Python exposes that set as `NativeConflictSignals`
and unions it with no-candidate, pairwise, and failure-net evidence before
choosing offender-only regeneration. A successful backtrack must clear
conflicts from discarded branches rather than publish stale offenders.

This interface is `Implemented` after the Python conflict-graph/support tests
and the two native higher-order/backtracking regressions pass. It remains
unverified until the physical matrix passes.

### RRF-069: demand-aware placement work and bounded local routing slices

Placement ordering begins with a generic routability-work estimate:

```text
5 * estimated global-extension nets
+ pre-owned local nodes
+ estimated global-extension nodes
+ 3 * boundary overflow
+ ceil(pin scarcity / 8)
```

This term ranks before the remaining stable physical tie-breakers. It preserves
useful local ownership on a small design while preventing a highly constrained
packed design from looking cheap merely because it has fewer global nets.

The bounded primary pair remains row-beam and unpacked spacing
`configured - 1`. Deferred construction is lazy. When the best retained
candidate still has boundary, pin, guide, or escape pressure, orchestration may
generate a deferred alternative before routing that pressured candidate. The
deferred set includes a wider unpacked placement at `configured + 1`, then the
remaining configured-packing, graph-beam, and spacing alternatives in stable,
deduplicated order.

Each placement attempt gets its own adaptive observation clock and a local
routing-control slice. When another placement remains, that slice is at most
half of the current absolute remainder; the last placement may use the
remainder. The first control escalation is allowed, but a later comparable
control pass starts only when its observed predecessor can fit in the local
slice, capped at a five-second reserve. Before starting another placement, the
flow similarly requires the minimum positive elapsed time observed for a prior
placement attempt, capped at five seconds.

These local clocks and estimates never create a deadline. Every placement,
portal retry, lane/layer escalation, claim release, validation, and deferred
generation receives the same `RoutingDeadline` object and immutable expiry.
Portal-reservation or unreserved-mode changes regenerate offender signals only
when the placement has no local claims; local claims couple base ownership, so
those portal changes rebuild the complete candidate domain instead of assuming
unsafe independence.

This slice is `Implemented` after focused work-estimate, demand-generation,
shared-deadline, observed-pass, and measured-start tests. The current RCA4
diagnostics prove bounded failover behavior but not routability, determinism,
or acceptance.

### RRF-071: enforce local slices inside expensive routing and placement work

Every placement routing attempt has an adaptive expiry no later than the one
shared `RoutingDeadline`. Resource-graph construction, native portal batches,
native route-tree batches, exact assignment with or without base claims, and
Python conflict classification must all observe the tighter remaining value.
An expired local slice returns a typed `TrackAssignmentConflict` that advances
to the next placement; an expired shared deadline remains the authoritative
`RuntimeBudgetExceeded` failure.

Python work that can scale inside one call must expose periodic checks.
Resource-graph node/edge construction reports bounded progress, and conflict
classification checks during candidate pairs rather than only between signal
pairs. Placement construction reports phase and cluster/beam progress through
`WorkCheck`. Each deterministic placement generator receives a fair share of
the absolute remainder based on the number of generator slots still available.
When that local generation share expires before the global deadline, the flow
records `PlacementGeneration:Stagnated` and advances to the next generator.

Native assignment must also check its deadline inside base-claim comparison and
conflict-resource collection loops. An outer recursion check is insufficient
when one candidate/base or candidate/candidate comparison is large.

The acceptance harness may add 2.0 seconds to the subprocess capture timeout
solely to let a compiler self-exit and flush typed evidence. Evaluation still
uses the immutable 10-, 25-, and 120-second circuit ceilings. Capture grace can
never convert an over-ceiling run into an accepted run.

This slice is `Implemented` after focused adaptive-bound, resource-graph,
conflict-classification, placement-work, harness-grace, and native deadline
tests. The current matrix demonstrates bounded typed failure for both RCA4 and
CLA4, but both circuits remain 0/2 and therefore physically unverified.

### RRF-072: interrupt every expensive phase and preserve the original slice

Every placement phase whose work scales with circuit size must expose periodic
`WorkCheck` calls. This includes clustering, slot assignment, beam expansion,
structural mapping, compaction, boundary analysis, routing feedback, isolation,
and placement-resource construction. The physical graph, validation, template,
repeater, ownership-claim, conflict, guide, and rip-up loops have the same
requirement. A helper may choose its check cadence, but no outer caller may be
the only deadline observation around an unbounded inner loop.

Placement generation reserves 20% of the one shared deadline for routing. A
generator receives only its bounded share of the placement portion, and a
candidate becomes retained state only after bounded routing feedback completes.
An exception must keep all placement and escalation histories collected before
the failure. A decision to advance to another placement bypasses local-claim
recovery. When the response table permits local-claim recovery, recovery uses
the original remaining local slice and must not start another clock.

The PyO3 assignment timer starts before manual Python-payload extraction.
Claim-mask construction, mask union, conflict collection, and candidate sorting
are chunked and deadline checked in addition to the existing assignment search.
These checks remain inside the exact eight-file Rust module split.

The harness records wall overrun explicitly and rejects a deadline-enforcement
observation with one second or more of wall overrun. The pre-RRF-073 matrix
under `RouterV10RecoverySnapshotPreRRF073` proved that the new checks produced
typed failures, but CLA4 still ended at 121.725966s and 121.425092s against its
120-second ceiling. That evidence makes RRF-072 `Implemented`, not physically
Verified, and identifies a process-envelope gap outside the routing deadline.

### RRF-073: reserve publication time inside the immutable wall ceiling

The harness owns two distinct bounds for each circuit. The immutable acceptance
wall ceilings remain 10 seconds for FullAdder, 25 seconds for RCA4, and 120
seconds for CLA4. A fixed `PublicationReserve` of 2 seconds is subtracted before
the compiler is launched, producing router deadlines of 8, 23, and 118 seconds.
The reserve covers frontend/process overhead and typed artifact publication
inside the acceptance ceiling; it is not additional search time.

The subprocess watchdog remains wall ceiling plus 2 seconds solely to capture
and classify a compiler that fails to stop. Evaluation always uses the
immutable wall ceiling, and the manifest records both `PublicationReserve` and
the effective router deadline. No compatibility fallback, automatic strategy
change, deadline reset, or ceiling extension is allowed.

The deadline-enforcement physical sub-gate is `Verified` when the sequential
nine-run manifest records no watchdog timeouts, zero wall overrun, and
`DeadlineOverrunWithinLimit=true` for every process. The RRF-073 manifest satisfies
that bounded-completion gate. It does not verify physical routability: RCA4 and
CLA4 still require two routed, DRC-clean, deterministic 512/512 results apiece.

### Portal, base-claim, and frozen-geometry invariants

Every portal candidate starts on a terminal access-path position that exists in
the immutable routing graph. A requested layer may restrict portal targets, but
it must not replace the start with an unrelated graph point or produce a target
chain detached from the terminal.

Exact assignment receives every placement-owned local claim as base ownership,
including a partial local route that has not yet reached all of its targets.
Disabling local base claims is a deliberate test or compatibility control, not
the production default. Omitting partial claims would let global assignment
reuse resources that placement already owns.

Frozen routed redstone is inserted into routing electrical obstacles and may
not be crossed or re-owned. The template-only electrical-isolation validator,
however, receives the immutable template geometry snapshot taken before frozen
routes are added. Routed dust is therefore an obstacle for new routes, not a
fabricated standard-cell template block.

### RRF-065: foreign-access blocked nodes

For each signal, route-tree construction must treat the electrical exclusion
halo of every other signal's immutable source and target access paths as
blocked native graph nodes. The current signal's own protected access remains
available, and an allowed start does not gain permission to traverse a foreign
blocked node. Python supplies this per-request blocked-node set to both bounded
and unbounded native route-tree entry points; native expansion rejects those
nodes throughout branch construction, not only during final materialization.

This slice is **Implemented**: the per-request Python/binding path and native
blocked-node traversal regression pass, including an inaccessible blocked
target and a legal alternate branch. It remains unverified until the physical
matrix passes; the focused result does not qualify a new physical artifact.

### RRF-068: cross-layer portal semantics and stacked-access layer floor

Portal availability is a terminal-wide physical condition, not a requirement
that every terminal reach every routing layer. Reservation keeps each
`(signal, terminal, layer)` domain separate, retains empty per-layer domains,
and fails `NoBoundaryEscape` only when a terminal has no candidate on any
effective layer. Candidate construction may use a layer only when every
terminal required by that signal has a portal on that same layer; reservations
from different layers never consume one another's slots.

Vertically stacked access can require a higher initial routing plane. The
effective layer count must therefore include the smallest technology-valid
floor from `RequiredRoutingLayerCountForAccess`: the highest immutable access
or retained local-claim coordinate must be within the configured guide
expansion of a routing plane. The floor is capped by technology, policy, and
physical-height capacity and must not instantiate every available layer merely
because the design box is tall.

This slice is `Implemented` after focused tests for layer-specific
inaccessibility, terminal-wide no-escape, cross-layer reservation isolation,
and the minimum stacked-access floor. It remains physically unverified.

## RRF-050: bounded native work

`RoutingDeadline` stores one monotonic absolute expiry and exposes remaining
milliseconds without extending the expiry. Portal generation, route-tree
generation, MRV assignment, and exact assignment accept the remaining time or
absolute deadline.

Native loops and parallel batches check expiry during work, stop scheduling new
batches after it, and return:

- `DeadlineExceeded`;
- completed request, expansion, and batch counts; and
- any safe partial diagnostics needed to identify affected signals.

Python converts native deadline expiry into the existing typed
`RuntimeBudgetExceeded` result and starts no later candidate. The allowed
wall-clock overrun is less than one second.

The CLI accepts only a finite positive routing-deadline override. The override
produces an effective immutable policy whose overall and adaptive routing
budgets cannot exceed that value. The authoritative planner reports six
stages, with assignment completing only stage five. Cleanup, compaction,
ownership, repeater validation, authoritative routed validation, and simulation
retain the pending state and the original deadline; only a completed physical
result may publish final progress.

For `QualityTarget="first-legal"`, result-only route-shape optimization is not
part of the feasibility path and must be skipped after a legal assignment.
Required congestion repair, route materialization, repeater planning,
authoritative validation, and simulation still run under the same deadline.
Quality targets that request result comparison may run the bounded shape
optimization pass.

## RRF-010: diagnostics contract

Routing failure remains a nonzero compile result and writes
`<OutputName>.RoutingFailure.json` with schema `routing-failure-v1`. The
implemented core document contains:

- `SchemaVersion`, source revision/dirty state, requested and used strategy;
- the effective policy snapshot;
- typed failure stage, reason, message, affected nets, resources, and locations;
- placement attempts and rejection reasons;
- escalation history with effective controls and skip reasons;
- candidate, conflict, and effective-work fingerprints;
- conflict graph, stage timings, deadline information, and total runtime.

The complete evidence contract includes output identity, technology snapshot,
placement/resource-graph fingerprints when available, physical failure
locations, completed native work, partial-artifact paths, and source,
environment, and command reproduction fields. `RRF-011` implements these
additions and the successful-run fingerprint/native-work parity envelope.

The writer must use exception-safe data already available at the compiler
boundary. Failure-artifact serialization failure must not conceal the original
routing failure.

A successful `.PhysicalDesign.json` records the same placement-attempt,
escalation, fingerprint, and deadline summaries needed to compare accepted
runs. These fields are evidence; the routed design, exact DRC, and simulation
remain authoritative.

At compile start, prior `.litematic`, `.TruthTable.txt`, and
`.PhysicalDesign.json` success artifacts are removed as one success set. A new
success set is staged under the output directory, the schematic is committed
first, and `.PhysicalDesign.json` is committed last. Any ordinary publication
exception removes the whole success set, so a failed rerun cannot retain old
success metadata and metadata cannot precede a successfully written schematic.

## Strategy and compatibility contract

`new-router-first` is the production CLI strategy and the only strategy
eligible for acceptance. The compatibility implementation remains frozen and
may be invoked only by an explicit internal regression test. Production must
not automatically select compatibility or hybrid routing after a new-router
failure. No accepted result may report a non-null fallback reason or
`FallbackUsed=true`.

The template PCB backend, NAND templates, exact electrical rules, capacity-one
ownership, and repeater rules remain unchanged. The recovery introduces no
adder-specific handling.

## Acceptance contract

The implementation is accepted only after the lightweight Python and Rust
gates pass and all physical runs in
[RouterReliabilityGuide.md](RouterReliabilityGuide.md) pass sequentially.
FullAdder requires five runs at 8/8 rows and <=10s; RippleCarryAdder4 requires
two runs at 512/512 rows and <=25s; CarryLookaheadAdder4 requires two runs at
512/512 rows and <=120s. Every run requires zero conflicts, zero unresolved
claims, overflow peak <=1, deterministic fixed-seed physical output, exact DRC,
repeater correctness, and passing simulation.

## RRF-061: deterministic acceptance harness

`Scripts/RunRouterAcceptance.py` owns the executable acceptance matrix. It must
launch five FullAdder, two RippleCarryAdder4, and two CarryLookaheadAdder4 runs
sequentially with `new-router-first` and a fixed seed. Each run receives the
RRF-073 router deadline produced by subtracting the 2-second publication
reserve from the circuit's exact wall-clock ceiling. A dry run may inspect the
nine commands but cannot satisfy an acceptance gate.

The harness writes `router-acceptance-manifest-v1` incrementally and records the
source state, selected environment, exact command, stdout/stderr, process and
reported runtimes, immutable wall ceiling, publication reserve, effective
router deadline, wall overrun, required artifact paths and SHA-256 hashes,
correctness fields, placement fingerprint, ownership counts, route metrics,
and a canonicalized emitted-design digest. Repetitions of one circuit must
match on all deterministic evidence fields. Any missing artifact,
routing-failure artifact, compatibility or fallback use, timeout, runtime
overrun, conflict, unresolved claim, overflow above one, incorrect simulation
result, or determinism mismatch makes the manifest fail.

The harness is `Implemented` after its focused sequencing, rejection,
determinism, publication-reserve, and immutable-ceiling tests pass. RRF-073
separately verifies that the real nine-run process envelope remains bounded;
the router itself is not `Verified` until every durable physical artifact
passes.

## RRF-051: native module boundaries

The Rust extension is intentionally split into exactly eight source files so
deadline checks and ownership logic are reviewable without navigating one
monolithic binding file. The module ownership contract is:

| Module | Sole responsibility |
| --- | --- |
| `Lib.rs` | Module declarations, deterministic thread pool, rectilinear topology, indexed logic simulation, Python module entry point, and top-level regression tests |
| `Models.rs` | Shared native/PyO3 data models, claim masks, routing context, and bounded result types |
| `Deadline.rs` | Monotonic native runtime deadline and periodic check interval |
| `PathRouting.rs` | Resource-graph path search, geometry helpers, and portal materialization |
| `Generation.rs` | Portal-candidate and route-tree generation, including bounded parallel batches |
| `Assignment.rs` | Exact capacity-one candidate ordering, conflict filtering, and MRV recursion |
| `AssignmentPlanning.rs` | Assignment input normalization plus legacy and bounded planning entry points |
| `Bindings.rs` | Python-visible method wrappers and class/function registration |

Public Python names and result meanings remain compatible across the split.
The bounded APIs accept remaining milliseconds for portal batches, route-tree
batches, and both exact-assignment variants. `PortalCandidateBatchResult` and
`RouteTreeBatchResult` expose `DeadlineExceeded`, `CompletedWork`, and
`TotalWork`; `RoutingAssignmentResult` exposes `DeadlineExceeded` and
`CompletedWork` independently of `BudgetExhausted`, plus deterministic
`ConflictSignals` for failed exact assignment.

The split and Cargo feature boundary are `Implemented`: the exact approved
default-feature command,
`cargo test --manifest-path RustRouting/Cargo.toml --release`, passes 25/25
native unit tests. The native gate does not make the router `Verified`; no
qualifying physical acceptance matrix has passed on the split implementation.

The guide remains `NOT ACCEPTED` until the entire matrix passes. A partial pass
updates the evidence and notes but does not weaken or redefine a remaining
gate.

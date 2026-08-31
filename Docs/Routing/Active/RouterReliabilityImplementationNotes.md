# Router reliability implementation notes

**Log policy:** Append-only. Do not delete or rewrite an earlier observation,
failed attempt, measurement, or decision. Add a dated correction that cites the
earlier entry when evidence changes.

This file records implementation activity. The
[design document](RouterReliabilityDesignDoc.md) remains normative, and the
[guide](RouterReliabilityGuide.md) owns the current operator verdict.

## Status vocabulary

- `Planned`: contract is defined but implementation has not begun.
- `In Progress`: the implementation slice or its focused validation is active.
- `Implemented`: focused lightweight tests pass; physical acceptance may remain.
- `Verified`: required physical acceptance evidence passes.
- `Blocked`: a specific recorded condition prevents the next required action.

## Current snapshot

**Current phase:** `RRF-074` has closed the remaining cluster-zero terminal and
all-placement-recipe transactional proofs. RRF-065 through RRF-072 and RRF-074
are Implemented; the RRF-073 deadline-enforcement and FullAdder physical
sub-gates are Verified.
**Current blocker:** The current manifest is `FAILED` with `Accepted=false`.
All nine processes remain inside their immutable wall ceilings, but RCA4 and
CLA4 publish typed failures and routed 0/2. The overall v10 router remains
`NOT ACCEPTED`.
**Next action:** use the bounded current RCA4/CLA4 artifacts to fix physical
routability without changing the verified deadline envelope, then rerun the
failed scale gates before another complete acceptance matrix.

| ID | Work item | Status | Verification gate |
| --- | --- | --- | --- |
| RRF-001 | Canonical documentation and verified v8/v9 baseline | Implemented | Links, commands, and recorded evidence inspected |
| RRF-010 | Core `routing-failure-v1` serializer | Implemented | Core schema and nonfatal writer-error tests pass |
| RRF-011 | Complete failure/success evidence-field parity | Implemented | Five focused artifact-integrity tests and two existing serializer tests pass |
| RRF-020 | Exact legal-by-construction placement | Implemented | Focused electrical and packed-placement legality tests pass |
| RRF-022 | Transactional rollback across every placement generator | Implemented | All nine deterministic placement recipes reject after construction without leaking netlist, placement, or local-claim state |
| RRF-021 | Cluster-zero terminal ordering | Implemented | Explicit RCA4 terminal names, signals, coordinates, rotations, and cluster-zero membership pass |
| RRF-030 | Deterministic retained placement routing | Implemented | Candidate 1 fails/candidate 2 succeeds focused test passes |
| RRF-031 | Decouple routing feedback and guides from `QualityTarget` | Implemented | Policy snapshot and deterministic guide tests pass |
| RRF-040 | Boundary demand/capacity records and ranking | Implemented | Synthetic congestion ranking test passes |
| RRF-043 | Hard no-escape and unsatisfiable-boundary rejection | Implemented | Five focused boundary tests and the existing overflow-order test pass |
| RRF-041 | Fingerprinted escalation state machine | Implemented | Failure-class and effective-control tests pass |
| RRF-042 | Offender-only candidate and local-claim invalidation | Implemented | Cache-retention and empty-intersection tests pass |
| RRF-050 | One Python/native absolute deadline | Verified | Current nine-run physical matrix records no timeout, zero wall overrun, and every process within its immutable ceiling |
| RRF-051 | Rust router eight-file responsibility split | Implemented | Exact release gate passes all 25 native tests in exactly eight source files |
| RRF-052 | Approved default-feature Rust test gate | Implemented | `cargo test --manifest-path RustRouting/Cargo.toml --release` passes 25/25 |
| RRF-053 | Ignore allowed route-tree starts absent from the native graph | Implemented | Paired geometry reproduction, exact Rust regression, and rebuilt-extension smoke pass |
| RRF-060 | Sequential physical acceptance series | In Progress | RRF-073 current matrix: FullAdder 5/5, RCA4 0/2, CLA4 0/2; overall failed |
| RRF-061 | Deterministic physical acceptance harness | Implemented | Focused harness tests pass; historical and current nine-run manifests correctly record failed overall verdicts |
| RRF-062 | FullAdder v10 physical sub-gate | Verified | RRF-073 matrix passes 5/5 with identical fingerprint, ownership, route metrics, and emitted design |
| RRF-063 | Demand-first lazy placement and first-legal post-route work | Implemented | Primary/deferred placement-plan tests and first-legal shape-selection test pass; scale gate remains open |
| RRF-064 | Meaningful reservation diversity and reusable bounded portal work | Implemented | Focused variant/cache/unreserved-transition tests pass; scale gate remains open |
| RRF-065 | Foreign-access blocked nodes in native route-tree search | Implemented | Python request/binding path and native blocked-node alternate/unreachable regression pass |
| RRF-066 | Exact self-claim rejection, cumulative diversity, and honest completion | Implemented | Dedicated support-under-wire regression and current FullAdder 5/5 pass; RCA4/CLA4 remain failed |
| RRF-067 | Typed native offenders and exact cross-signal support ownership | Implemented | Focused Python conflict/support tests and native higher-order/backtracking tests pass; Rust gate 25/25 |
| RRF-068 | Cross-layer portal semantics and stacked-access layer floor | Implemented | Focused layer-isolation, any-layer escape, no-escape, and stacked-access floor tests pass |
| RRF-069 | Demand-aware placement work and bounded local routing slices | Implemented | Focused work estimate, lazy wider placement, shared-deadline, observed-pass, and measured-start tests pass; RCA4 remains unverified |
| RRF-070 | Pre-RRF-071 lightweight and nine-run acceptance evidence | Implemented | Preserved at `RouterV10RecoverySnapshotPreRRF071`; FullAdder 5/5, RCA4 0/2, CLA4 0/2; overall NOT ACCEPTED |
| RRF-071 | Inner-loop local-slice enforcement and post-fix evidence | Implemented | Preserved at `RouterV10RecoverySnapshotPreRRF072`; Python 139/139, Rust 21/21; overall NOT ACCEPTED |
| RRF-072 | End-to-end periodic work checks and pre-envelope evidence | Implemented | Preserved at `RouterV10RecoverySnapshotPreRRF073`; Python 156/156, Rust 25/25; CLA4 exposed greater-than-one-second wall overrun |
| RRF-073 | Publication reserve and immutable process envelope | Verified | Final matrix has zero wall overrun and no timeout in all nine runs; FullAdder 5/5, RCA4 0/2, CLA4 0/2; overall NOT ACCEPTED |
| RRF-074 | Explicit terminal fixture and all-recipe rollback proof | Implemented | Named 2/2 tests and the final scale-excluded Python suite pass 159/159 |

## Entry template

Copy this section to the end of the file before starting an implementation
slice, then append the result to that same entry. Never mark an item `Verified`
without its required physical evidence.

```text
### YYYY-MM-DD HH:MM - RRF-### - short title

Status before:
Status after:
Intent:
Hypothesis:
Intended behavior:
Focused tests and expected evidence:
Code and documentation changed:
Behavior changed:
Commands:
Evidence and artifact paths:
Result and measurements:
Design decision or deviation:
Open risks:
Next action:
```

## Entries

### 2026-07-21 - RRF-001 - Establish canonical documentation and baseline

**Status before:** Planned
**Status after:** Implemented

**Intent:** Establish one operator guide, one normative design, and one
append-only implementation record before changing router behavior.

**Hypothesis:** Separating historical evidence, current acceptance, normative
requirements, and implementation observations will prevent old compatibility
or v8 results from being mistaken for proof that v9/v10 is reliable.

**Intended behavior:** Documentation reports the live new-router failure
honestly, defines the v10 interfaces and gates, preserves prior measurements,
and gives each implementation slice a stable RRF identifier.

**Focused tests and expected evidence:** Inspect the policy and CLI strategy,
the v8 FullAdder physical artifact, fresh v9 failure logs, documentation links,
and Markdown whitespace. Expected result is a `NOT ACCEPTED` guide whose facts
match those sources.

**Code and documentation changed:** Added
`RouterReliabilityGuide.md`, `RouterReliabilityDesignDoc.md`, and this file;
updated the routing-document index and root README. No compiler or router code
was changed by this entry.

**Behavior changed:** None. This entry changes documentation only.

**Commands:**

```bash
git rev-parse HEAD
git status --porcelain=v1
rg -n 'PolicyVersion|QualityTarget|TruthTablePassed|TruthTableRows|OverflowPeak|RuntimeSeconds' Output/BenchFullAdderRun/FullAdderRunNew.PhysicalDesign.json
rg -n 'Operation failed|RuntimeBudgetExceeded|electrical isolation' /tmp/fa_new.log /tmp/rca4_new.log /tmp/cla_new.log
git diff --check
```

**Evidence and artifact paths:**

- Historical v8 oracle:
  `Output/BenchFullAdderRun/FullAdderRunNew.PhysicalDesign.json`.
- Fresh v9 diagnostics: `/tmp/fa_new.log`, `/tmp/rca4_new.log`, and
  `/tmp/cla_new.log`.
- Source revision: `4c91d1b953dd921f665ef6004cd2c79178c49894`;
  working tree dirty at capture.

**Result and measurements:** The v8 FullAdder artifact reports policy
`physical-design-v8-openroad-style`, strategy `new-router-first`, no fallback,
8/8 truth-table rows, zero conflicts and unresolved claims, overflow peak 0,
and run-summary runtime 6.114104s. Fresh v9 FullAdder exhausted a 119.197s
Portal allowance after 119.275s; RCA4 exhausted a 117.674s Track allowance
after 142.850s; CLA4 rejected electrically illegal packed placements and then
exhausted a 107.774s Candidate allowance after 121.118s. The current verdict is
`NOT ACCEPTED`.

**Design decision or deviation:** No deviation. Compatibility evidence is
explicitly classified as regression-only, production failure remains hard, and
older documentation is preserved as history.

**Open risks:** The `/tmp` logs are ephemeral, the baseline tree is dirty, and
no v10 failure artifact or physical acceptance series exists yet. Later entries
must copy durable raw evidence under the dated acceptance directory and record
the exact diff/environment used.

**Next action:** Implement `RRF-010` failure diagnostics and `RRF-020` exact
placement legality with focused tests. Do not start concurrent scale runs.

### 2026-07-21 - RRF-010 - Add core typed failure evidence

**Status before:** Planned
**Status after:** Implemented for the core schema; `RRF-011` remains In Progress

**Intent:** Preserve a typed, reproducible routing failure without converting
the compile to success or hiding the original exception.

**Hypothesis:** A stable JSON envelope at the pipeline boundary can retain
placement/escalation context even when no physical-design artifact is emitted.

**Intended behavior:** Remove a stale failure artifact at compile start, write
`<OutputName>.RoutingFailure.json` for typed routing failure, retain a nonzero
result, and treat diagnostic-write failure as secondary to the original error.

**Focused tests and expected evidence:** Validate the schema version, typed
reason, no-fallback strategy, candidate fingerprint, and nonfatal serializer
failure. Expected result is focused test success without running a physical
circuit.

**Code and documentation changed:** `Compiler/Pipeline.py` owns the failure
artifact boundary and adds reliability summaries to successful physical JSON;
`Tests/test_router_reliability.py` covers the core serializer behavior.

**Behavior changed:** The pipeline now writes the core
`routing-failure-v1` envelope and re-raises the routing failure. The core file
includes policy, strategy, source state, typed failure, placement/escalation
records, effective controls, fingerprints, conflict graph, timings, deadline,
and runtime.

**Commands:**

```bash
python3 -m unittest Tests.test_router_reliability -v
python3 -m compileall -q Compiler SVDecoder SchemEncoder Tests
```

**Evidence and artifact paths:** Focused unit tests only; no new physical
artifact and no acceptance evidence.

**Result and measurements:** 11/11 reliability tests passed in 0.006s;
compileall passed. The intentionally injected serializer error printed its
secondary diagnostic and the test confirmed it did not replace the original
failure.

**Design decision or deviation:** The implemented field is named
`SchemaVersion`. Complete technology/output identity, physical locations,
completed native work, partial-artifact paths, and success/failure field parity
remain `RRF-011`; the core serializer is not presented as the complete evidence
contract.

**Open risks:** No end-to-end failed compile was used as evidence in this
entry, and no physical acceptance gate passed.

**Next action:** Complete and test `RRF-011`, including a real failed-compile
artifact inspection.

### 2026-07-21 - RRF-020/RRF-021 - Make packed placement exact-legal

**Status before:** Planned
**Status after:** `RRF-020` Implemented; `RRF-021` and `RRF-022` In Progress

**Intent:** Stop retaining packed or reused geometry that fails the final
template electrical-isolation oracle.

**Hypothesis:** Applying `PcbGatesConflict` while candidates are staged removes
the v9 gap between rectangle-only packing and final exact validation.

**Intended behavior:** Pin-aligned beam expansion, row-beam packing, structural
reuse, terminal placement, stacked placement, and final packed commit reject
exact template conflicts. Structural-reuse state is committed only after its
candidate passes.

**Focused tests and expected evidence:** FullAdder row-beam placement must have
no exact gate conflict and must pass final isolation; a known Input/NAND
adjacency must be rejected. RCA4 terminal ordering must recognize cluster index
zero.

**Code and documentation changed:** Exact legality and boundary records are in
`Compiler/Placement/Pcb.py`; the focused assertions are in
`Tests/test_local_first_router.py` and `Tests/test_physical_cells.py`.

**Behavior changed:** Exact template occupancy/electrical exclusions now
participate in placement construction, and rejected structural reuse no longer
mutates accepted local placement maps. Terminal sorting uses an explicit
`None` check, so cluster index `0` no longer falls through to the missing-value
sentinel.

**Commands:**

```bash
python3 -m unittest Tests.test_local_first_router.LocalFirstRouterTests.testRowBeamPackedPlacementIsExactLegal Tests.test_local_first_router.LocalFirstRouterTests.testPackedTerminalPlacementGroupsInputsByNandCluster Tests.test_physical_cells.PhysicalCellTests.testPackedCellsRejectActualTemplateElectricalAdjacency -v
```

**Evidence and artifact paths:** Focused unit/example-derived placement tests;
no routed physical artifact.

**Result and measurements:** All three named legality/order tests passed. The
broader five-test placement/policy selector also passed in 4.925s.

**Design decision or deviation:** `RRF-021` remains In Progress because the
test explicitly proves cluster index zero but still derives much of the order
from test-side cluster data and does not lock exact terminal coordinates.
Structural-reuse staging is transactional in the implementation, but a focused
rejected-state test across every generator is still required by `RRF-022`.

**Open risks:** Passing placement-unit checks does not prove that any retained
FullAdder placement routes, simulates, or meets runtime.

**Next action:** Add an explicit expected terminal order/coordinate fixture,
then exercise every placement generator transactionally.

### 2026-07-21 - RRF-030/RRF-031/RRF-040/RRF-041/RRF-042/RRF-050 - Integrate bounded reliability control

**Status before:** Planned
**Status after:** Implemented for focused-tested behavior; physical verification
remains open

**Intent:** Route deterministic alternatives, keep feasibility controls enabled,
choose meaningful retry actions, preserve unaffected work, and share one
deadline.

**Hypothesis:** Stable candidate/state fingerprints plus classified transitions
prevent equivalent Cartesian retries and allow a later legal placement to
succeed without resetting the runtime budget.

**Intended behavior:** The v10 policy enables feedback and capacity-aware
guides; placement candidates are scored/deduplicated and routed in order;
boundary overflow outranks density; assignment, no-candidate, incompatibility,
layer, and placement transitions are explicit; offender-only cache/claim work
is retained; and every placement/native call receives the same Python deadline.

**Focused tests and expected evidence:** Candidate 2 runs after candidate 1
fails, policy flags are frozen correctly, capacity-aware guides are
deterministic, boundary overflow ranks first, escalation classifications select
only meaningful controls, dictionary order does not alter fingerprints,
unaffected candidates remain cached, empty claim intersections release
nothing, and expired deadlines produce typed failures.

**Code and documentation changed:** The shared contracts are in
`Compiler/Routing/Reliability.py`; orchestration is integrated through
`Compiler/Placement/PcbFlow.py` and
`Compiler/Routing/AuthoritativePlanner.py`; policy defaults are in
`Compiler/Routing/Policy.py`.

**Behavior changed:** `physical-design-v10-routability-feedback` is active.
Retained placements run under one deadline and record attempts. Native portal,
route-tree, and assignment deadline results become typed Python runtime
failures. Repeated effective-work fingerprints return `Stagnated`; affected
signals can regenerate while non-offender candidate caches remain intact.

**Commands:**

```bash
python3 -m unittest Tests.test_router_reliability -v
python3 -m unittest Tests.test_local_first_router.LocalFirstRouterTests.testCompatibilityPolicyIsFrozenBesideLocalFirstPolicy Tests.test_local_first_router.LocalFirstRouterTests.testCapacityAwareGuidesAreDeterministicAndBounded -v
```

The first attempt to run the broader placement selector used the nonexistent
class name `LocalFirstRoutingTests` and produced four unittest loader errors.
The selector was corrected to `LocalFirstRouterTests`; the five requested
policy/placement tests then passed. This was an invocation error, not a router
test failure.

**Evidence and artifact paths:** Unit and example-derived placement evidence
only; no qualifying physical acceptance artifact.

**Result and measurements:** 11/11 reliability tests and the corrected five-test
policy/placement selector passed. Compileall also passed.

**Design decision or deviation:** Boundary demand/capacity records and ranking
are implemented, but hard rejection of all no-escape or unsatisfiable-boundary
candidates lacks a focused proof and remains `RRF-043`. The <1s physical
deadline-overrun requirement remains unverified.

**Open risks:** Mocked candidate orchestration does not demonstrate FullAdder
routability or deterministic emitted artifacts. The complete lightweight suite
has not been recorded by this entry.

**Next action:** Close `RRF-043`, run the complete lightweight suite, then run
one diagnostic FullAdder before any repeated acceptance series.

### 2026-07-21 - RRF-051/RRF-052 - Split and bound the native router

**Status before:** Planned
**Status after:** `RRF-051` Implemented; `RRF-052` In Progress

**Intent:** Replace the monolithic Rust routing source with explicit module
ownership while retaining Python API behavior and adding bounded native work.

**Hypothesis:** Separating models, deadlines, path search, generation,
assignment, planning, and bindings makes deadline propagation and exact
ownership independently testable without changing routing semantics.

**Intended behavior:** `Lib.rs` remains the thin extension shell; seven focused
modules own native routing mechanics. Bounded portal/tree/assignment methods
report deadline expiry and completed work separately from assignment-budget
exhaustion.

**Focused tests and expected evidence:** Native tests must cover immediate and
non-expired deadlines, completed-work counts, exact assignment, pre-owned
claims, resource-graph traversal, and deterministic topology after the split.

**Code and documentation changed:** Added `Assignment.rs`,
`AssignmentPlanning.rs`, `Bindings.rs`, `Deadline.rs`, `Generation.rs`,
`Models.rs`, and `PathRouting.rs`; reduced `Lib.rs` to shared extension concerns;
split PyO3's extension feature in `Cargo.toml` so native tests can run without
extension linking.

**Behavior changed:** Bounded Python entry points accept remaining milliseconds
and return result objects with deadline/completed-work telemetry. Legacy entry
points remain registered.

**Commands:**

```bash
cargo test --manifest-path RustRouting/Cargo.toml --release
cargo test --manifest-path RustRouting/Cargo.toml --release --no-default-features
```

**Evidence and artifact paths:** Native unit-test output only; no physical
artifact.

**Result and measurements:** The approved default-feature command failed at
link time with unresolved Python symbols such as `PyExc_ValueError`; it did not
run tests. The no-default-feature command compiled the split and passed 16/16
tests in the release profile.

**Design decision or deviation:** `RRF-051` is Implemented because the split's
focused native suite passes. `RRF-052` stays In Progress because the exact
approved default-feature Cargo gate does not pass in this environment.

**Open risks:** Python-extension build/import compatibility and physical routing
through the rebuilt extension are not established by the no-default-feature
unit suite.

**Next action:** Correct the Cargo test/build feature boundary, rerun the exact
approved command, rebuild/import the extension, and only then use it for a
physical acceptance run.

### 2026-07-21 - RRF-053 - Reject off-graph route-tree starts at the native boundary

**Status before:** In Progress
**Status after:** Implemented; physical verification remains open

**Intent:** Explain and remove the all-zero packed FullAdder route-tree batches
without changing placement, guide geometry, search budgets, or electrical
rules.

**Hypothesis:** Partial local-claim nodes were appended as `RequiredNodes`,
which made them allowed for a request without adding them to the immutable Rust
resource-graph adjacency. Rust accepted those points as multi-source starts and
could choose an off-graph root that can never connect.

**Intended behavior:** `GenerateRouteTreeNative` treats a start as usable only
when it is both in the request's allowed-node set and in the routing context's
adjacency. Off-graph required geometry is ignored as a start; a request with no
remaining graph start still fails normally.

**Focused tests and expected evidence:** Reproduce one packed NandNet3 request,
compare it unchanged against the same request with off-graph starts removed,
and add a native two-node graph regression containing one allowed off-graph
start. The unchanged request must fail before the fix; the adjacency-filtered
request and focused regression must return connected trees.

**Code and documentation changed:** `RustRouting/Src/Generation.rs` filters
route-tree starts by both `AllowedNodes` and `RoutingContext.Adjacency` and owns
the new native regression. Temporary Python instrumentation was removed, and
`PcbFlow.py` was not edited.

**Behavior changed:** Required nodes that are not routable graph vertices can
no longer become `RootStart` or force every other valid start to connect to an
impossible component.

**Commands:**

```bash
cargo fmt --manifest-path RustRouting/Cargo.toml -- --check
cargo check --manifest-path RustRouting/Cargo.toml --release --features python-extension
cargo test --manifest-path RustRouting/Cargo.toml --release --no-default-features Generation::Tests::RouteTreeIgnoresAllowedStartsOutsideResourceGraph -- --exact
cargo test --manifest-path RustRouting/Cargo.toml --release --no-default-features
cargo build --manifest-path RustRouting/Cargo.toml --release --features python-extension
python3 -m unittest Tests.test_authoritative_planner.AuthoritativePlannerTests.testRouteTreeTargetsSelectedPortalOuterEndpoint Tests.test_authoritative_planner.AuthoritativePlannerTests.testBatchedRouteTreesPreserveRequestOrder -v
```

**Evidence and artifact paths:** Live FullAdder diagnostic only; no accepted
physical artifact. The rebuilt extension is
`RedstoneCompiler/RustRouting.cpython-312-x86_64-linux-gnu.so`.

**Result and measurements:** The first failing NandNet3 request had six allowed
starts, but `(9, 1, 19)` and `(11, 1, 17)` were absent from the Rust adjacency;
all target-branch nodes were present. Keeping those starts returned `None`;
filtering only those starts returned a 23-node tree with the same guide and
targets. The exact regression passed, the full native suite passed 17/17, the
two Python route-tree tests passed, and the rebuilt extension exposed all four
bounded APIs and passed an off-graph-start smoke test.

**Design decision or deviation:** The fix is at the native API boundary because
`FindPathWithDeadline` already requires starts to exist in adjacency and every
caller benefits from the same invariant. Python `SeedStarts` remains unchanged;
switching it to `ContinuationNodes` would be a separate optimization requiring
its own contract proof and test.

**Open risks:** FullAdder was not rerun to completion after rebuilding the
extension, so this item is not `Verified`. The fix proves the zero-tree cause,
not overall assignment feasibility, simulation, determinism, or runtime.

**Next action:** Run one sequential debug FullAdder and confirm NandNet3/A no
longer produce all-zero route-tree batches before starting any repeated
acceptance series.

### 2026-07-21 - RRF-054 - Anchor portals to terminal access graph nodes

**Status before:** In Progress
**Status after:** Implemented; physical verification blocked

**Intent:** Remove the post-assignment physical disconnection seen on the
direct row-beam FullAdder without weakening connectivity or electrical rules.

**Hypothesis:** Portal requests discarded every y=1 terminal access point when
the requested routing layer was y=2, then substituted unrelated routing-layer
nodes as starts. Rust could therefore return a legal graph path that was not
connected to the terminal access path.

**Intended behavior:** Every portal search starts at a terminal access-path
position that is present in the immutable routing graph and ends on the
requested routing layer. Target branches remain oriented from the portal's
outer endpoint toward the terminal. No missing-layer fallback may use an
unrelated layer node as a start or a wrong-elevation access cell as a target.

**Focused tests and expected evidence:** A synthetic y=1 access to y=2 routing
transition must filter an off-graph access cell, generate a legal path from the
remaining access anchor, and form a physically connected reversed target chain.
The existing selected-target-portal and Rust/Python portal-claim tests must
remain green. One direct row-beam FullAdder route must no longer reach final
validation with a detached terminal island.

**Code and documentation changed:** `AuthoritativePlanner.py` adds
`SelectGraphAccessStarts`, uses `AccessPath` intersected with `Region.Nodes` for
every portal request, removes routing-layer-start substitution, and restricts
fallback targets to actual nodes at `RoutingY`. The focused portal regression
was added to `test_authoritative_planner.py`.

**Behavior changed:** Portal candidates are now electrically connected to a
real terminal escape or are absent. The former detached candidates can no
longer hide an unroutable fixed-access geometry from exact assignment.

**Commands:**

```bash
python3 -m unittest -v Tests.test_authoritative_planner.AuthoritativePlannerTests.testPortalStartsRemainAnchoredToGraphAccessAndReachRoutingLayer Tests.test_authoritative_planner.AuthoritativePlannerTests.testRouteTreeTargetsSelectedPortalOuterEndpoint Tests.test_authoritative_planner.AuthoritativePlannerTests.testRustPortalClaimsMatchPythonPathClaims
python3 -m compileall -q Compiler Tests
# Direct Python invocation: PlacePcbGraph with GraphBeamEnabled=False,
# RoutePcbDesign with a 15-second RoutingDeadline, then simulation if routed.
```

**Evidence and artifact paths:** Source revision
`4c91d1b953dd921f665ef6004cd2c79178c49894` with a dirty worktree. Live focused
test and FullAdder diagnostic output only; no accepted physical artifact was
produced.

**Result and measurements:** The three focused tests passed and compileall
passed. Before the fix, direct row-beam FullAdder reached exact assignment in
six expansions and then failed after 4.778 seconds with `Physically
disconnected route for net NandNet0: [(12, 1, 4)]`. After the fix, candidate
materialization reported zero disconnected candidates for every routed signal;
the detached-island failure did not recur. The final-code run stopped after
0.932 seconds at typed `TrackAssignment:Stagnated`, so simulation did not run.
Its exact conflict graph identifies pairwise-incompatible `B`/`NandNet0` and
`NandNet0`/`NandNet3` fixed-access geometries.

**Design decision or deviation:** `_BuildTargetPortalBranches` was not changed:
the generated portal plus reversed terminal access path is already a legal
connected chain, as the new regression proves. `RRF-054` is Implemented because
the focused invariant tests pass, but it is not Verified because FullAdder did
not route and simulate.

**Open risks:** The row placement retains fixed access paths whose electrical
claims have no pairwise-compatible candidate combination. The first portal
reservation variant also reproduced the same effective work fingerprint and
stopped as `Stagnated` instead of advancing to a genuinely different routing
control or placement.

**Next action:** Reject or relocate placements with incompatible fixed terminal
access claims before global routing, then rerun this same direct FullAdder
route/simulation before any acceptance series.

### 2026-07-21 - RRF-011 - Complete diagnostic parity and artifact integrity

**Status before:** In Progress
**Status after:** Implemented; physical integration unverified

**Intent:** Make failed and successful router runs directly comparable while
preventing an unsuccessful rerun from leaving artifacts that still claim an
older compile succeeded.

**Hypothesis:** A normalized evidence envelope at the compiler boundary can
capture the router's existing placement, resource-graph, native-work, and
reproduction data without changing routing behavior. Staging the success set
and publishing metadata last can prevent ordinary write failures from
advertising incomplete output.

**Intended behavior:** `routing-failure-v1` includes normalized output identity,
technology, affected nets/resources/locations, available placement and
resource-graph fingerprints, completed native work, partial artifact paths,
and source/environment/command reproduction fields. Successful
`.PhysicalDesign.json` output contains the parallel fingerprint and native-work
envelope. A compile removes the prior success set before work starts and does
not publish new success metadata until schematic writing succeeds.

**Focused tests and expected evidence:** Paired synthetic failure/success
evidence must produce identical fingerprint and native-work envelopes. A stale
success trio must be removed together, a parse failure must not retain it, a
schematic writer failure must publish none of it, and a successful staged write
must commit all three files without a physical router run.

**Code and documentation changed:** `Compiler/Pipeline.py` adds normalized
identity, reproduction, fingerprint, native-work, partial-path, stale-cleanup,
and staged-publication helpers; enriches the failure artifact; and adds
`RouterReliability` to successful physical diagnostics.
`Tests/test_pipeline_artifact_integrity.py` adds five focused tests. The design
checkpoint and this journal now record the implemented contract.

**Behavior changed:** Failure artifacts carry enough stable context to compare
the failed attempt with a later success. The `.litematic`, truth table, and
physical-design JSON are treated as one success set: they are staged under the
output directory, the schematic is committed first, and physical metadata is
committed last. An ordinary publication exception clears the set.

**Commands:**

```bash
python3 -m py_compile Compiler/Pipeline.py Tests/test_pipeline_artifact_integrity.py
python3 -m pytest -q Tests/test_pipeline_artifact_integrity.py Tests/test_router_reliability.py::RouterReliabilityTests::testFailureArtifactWriteErrorIsNonFatal Tests/test_router_reliability.py::RouterReliabilityTests::testRoutingFailureArtifactUsesStableSchema
git diff --check -- Compiler/Pipeline.py Tests/test_pipeline_artifact_integrity.py
```

**Evidence and artifact paths:** Focused temporary-directory artifacts only;
no accepted physical artifact was produced and no live routing run was started.

**Result and measurements:** All seven selected Python tests passed in 0.07
seconds: five new artifact-integrity tests plus two existing serializer
compatibility tests. Python compilation and scoped diff validation passed.

**Design decision or deviation:** The publication helper preserves the final
artifact basenames in a temporary directory so writers derive the same names as
normal output. It commits the schematic before the truth table and commits
physical metadata last. This is exception-safe for normal process errors; it
does not claim a cross-file atomic rename transaction.

**Open risks:** A process or machine crash between final renames can leave a
partial new success set. The complete fields have not yet been inspected from a
real failed and successful physical compile, so this item is not `Verified`.

**Next action:** Run the complete lightweight suite, then inspect one controlled
failed compile and one successful compile during the sequential physical gates.

### 2026-07-21 - RRF-052 - Restore the approved default-feature Cargo gate

**Status before:** In Progress
**Status after:** Implemented

**Intent:** Make the exact approved release test command work without requiring
an undocumented feature override.

**Hypothesis:** Keeping PyO3's extension-module linkage behind a Cargo feature,
then enabling that feature only for Maturin builds, lets Rust tests link against
the normal Python symbols while preserving the importable extension build.

**Intended behavior:** The default Cargo feature set is testable with the exact
approved command. Maturin continues to build the Python extension by requesting
`python-extension` explicitly.

**Focused tests and expected evidence:** The exact release command must link and
pass all native tests; this corrects the earlier RRF-051/RRF-052 observation
that only `--no-default-features` passed.

**Code and documentation changed:** `RustRouting/Cargo.toml` makes the default
feature set empty and maps `python-extension` to `pyo3/extension-module`.
`pyproject.toml` enables that feature for Maturin builds.

**Behavior changed:** Native tests no longer inherit extension-module linker
semantics, while Python extension packaging keeps them.

**Commands:**

```bash
cargo test --manifest-path RustRouting/Cargo.toml --release
```

**Evidence and artifact paths:** Live terminal result only; no physical router
artifact was produced.

**Result and measurements:** The exact command completed in 0.04 seconds and
passed 17/17 native tests with zero failures.

**Design decision or deviation:** Cargo owns the linkage boundary and Maturin
opts into extension linkage. No test-only linker workaround was added.

**Open risks:** This gate proves native test linkage and behavior only; it does
not replace the extension import smoke test or physical routing acceptance.

**Next action:** Keep the exact release command in the complete lightweight
gate, then proceed to the remaining placement-boundary contracts.

### 2026-07-21 - RRF-043 - Reject only provably impossible boundaries

**Status before:** In Progress
**Status after:** Implemented; physical integration unverified

**Intent:** Stop packed placement candidates before routing only when their
terminal boundary is conclusively impossible, while retaining congested but
potentially routable candidates for deterministic ranking.

**Hypothesis:** Exact one-primitive exits from immutable terminal access form a
necessary entrance graph. A required signal with no exit is impossible, and a
failed capacity-one bipartite matching proves that the available exits cannot
serve every required signal. Preferred-side lane estimates and fanout do not
prove impossibility because a routed tree can branch after one entrance.

**Intended behavior:** Placement enumerates legal exit slots from cluster-local
terminal access positions under the routing resource graph and fixed foreign
terminal claims. It rejects `NoBoundaryEscape` when a required signal has no
slot and `HardEntranceCapacityExceeded` when no capacity-one signal/slot
matching exists. Geometric corridor overflow and pin scarcity remain serialized
scoring fields and never trigger this hard validator.

**Focused tests and expected evidence:** Synthetic records must reject an empty
escape set and two signals sharing their only slot. A physically surrounded
access anchor must enumerate zero exits. Two uniquely matchable signals on an
overfull preferred side must remain feasible with nonzero overflow. A forced
late rejection followed by two successful constructions of the same synthetic
NAND graph must show no leaked placement or local-claim state.

**Code and documentation changed:** `Compiler/Placement/Pcb.py` adds the exact
`HardBoundaryFeasibility` result, deterministic capacity-one matching,
resource-graph exit enumeration, hard validation, and a capacity-record builder
that separates physical portal counts from soft geometric lane overflow.
`Tests/test_placement_boundary_feasibility.py` contains five focused tests.
The normative boundary section and current implementation snapshot were updated.

**Behavior changed:** A packed candidate is appended only after its hard
boundary result passes. Legal portal counts now reflect enumerated physical
slots; corridor overflow remains the preexisting preference score. Rejection
does not mutate the input netlist or expose the function-local placed design and
local claims to the placement cache.

**Commands:**

```bash
python3 -m py_compile Compiler/Placement/Pcb.py Tests/test_placement_boundary_feasibility.py
python3 -m pytest -q Tests/test_placement_boundary_feasibility.py
python3 -m pytest -q Tests/test_placement_boundary_feasibility.py Tests/test_router_reliability.py::RouterReliabilityTests::testBoundaryOverflowRanksBeforeDenseFootprint
git diff --check -- Compiler/Placement/Pcb.py
```

**Evidence and artifact paths:** Focused synthetic in-memory placement and
resource-graph evidence only. No physical router, example circuit, schematic,
or accepted artifact was run or produced.

**Result and measurements:** The five new focused tests passed in 0.06 seconds.
Those tests plus the existing boundary-overflow ordering test passed 6/6 in
0.06 seconds. Python compilation and scoped diff validation passed.

**Design decision or deviation:** Hard demand is one entrance per required
boundary signal, not one entrance per unresolved target. This is a necessary
condition: a global net tree may enter once and branch. Matching uses concrete
exit coordinates as capacity-one slots. It deliberately does not reject on
preferred-side overflow or attempt to solve later multi-primitive route
compatibility during placement.

**Open risks:** The hard check is necessary, not sufficient: distinct first
exit coordinates can still conflict in later geometry or assignment. No
FullAdder or other physical circuit was run, so the impact on its generated
placement set and the successful/failing artifact schema remains unmeasured.

**Next action:** Include the focused module in the complete lightweight gate,
then measure candidate-generation effects during the first controlled physical
diagnostic after `RRF-021` and `RRF-022` close.

### 2026-07-21 - RRF-054 correction - Complete the portal, ownership, and geometry chain

**Status before:** RRF-054 Implemented with an incorrect remaining-cause
conclusion
**Status after:** Implemented with the diagnosis corrected; physical acceptance
remains open

**Intent:** Correct the earlier RRF-054 conclusion that the retained row
placement itself had pairwise-incompatible fixed-access geometry.

**Hypothesis:** The post-anchor failure was not proof that row placement was
intrinsically unroutable. Three additional ownership/state defects could make
valid row geometry appear incompatible after the portal-anchor fix.

**Intended behavior:** Portal generation starts from graph-valid terminal access
nodes. Exact assignment receives every placement-owned local claim, including
a partial one, as base ownership. Repeated reserved work advances at most once
to bounded unreserved portals while retaining the same absolute deadline.
Frozen routed geometry remains an obstacle to new routing but is excluded from
the template-only electrical geometry used to validate standard-cell isolation.

**Focused tests and expected evidence:** Prove the access anchor, partial base
owner, same-deadline reserved-to-unreserved transition, and frozen-route
template isolation as independent invariants. Then rerun the direct row
diagnostic without relaxing connectivity, capacity, or electrical validation.

**Code and documentation changed:** The implementation is in
`Compiler/Routing/AuthoritativePlanner.py` and
`Compiler/Routing/Actions/Geometry.py`; the regressions are in
`Tests/test_authoritative_planner.py` and `Tests/test_routing_resources.py`.
This entry corrects the documentation record; it does not add code.

**Behavior changed:** Production `new-router-first` can escape one repeated
reserved portal state through bounded unreserved portal generation, but still
uses exact capacity-one assignment and the original deadline. Despite a
historical internal mode name, this is not the compatibility router and is not
a fallback.

**Commands:**

```bash
python3 -m pytest -q Tests/test_authoritative_planner.py::AuthoritativePlannerTests::testRepeatedReservedWorkTransitionsOnceToUnreservedOnSameDeadline Tests/test_authoritative_planner.py::AuthoritativePlannerTests::testPortalStartsRemainAnchoredToGraphAccessAndReachRoutingLayer Tests/test_authoritative_planner.py::AuthoritativePlannerTests::testPartialLocalBaseOwnerAffectsRustAssignment Tests/test_routing_resources.py::RoutingResourceTests::testFrozenRoutesAreObstaclesButNotTemplateElectricalBlocks
```

The exact direct-row physical invocation was ad hoc and was not retained. It
must be reproduced by a durable diagnostic or the canonical acceptance
harness before it can support an acceptance claim.

**Evidence and artifact paths:** Four focused regressions passed in live
terminal output. The direct row result also exists only in live terminal
output; there is no retained command, raw log, physical design, truth table, or
schematic path.

**Result and measurements:** The four selected tests passed 4/4 in 0.04s. With
the complete chain fixed, the direct row FullAdder diagnostic routed and
simulated 8/8 rows with zero conflicts in 1.713s. This disproves the earlier
RRF-054 statement that the row placement necessarily retained
pairwise-incompatible fixed-access geometry.

**Design decision or deviation:** The older RRF-054 observation remains in the
journal as historical evidence, but its remaining-cause conclusion is
superseded by this entry. No compatibility fallback, relaxed ownership,
relaxed isolation, or deadline reset was used.

**Open risks:** The direct row result bypasses the full retained-placement
orchestration and has no durable evidence. It proves a diagnostic row can
route; it does not satisfy the FullAdder acceptance gate.

**Next action:** Reproduce the complete production flow through the acceptance
harness after its runtime is below the exact FullAdder ceiling.

### 2026-07-21 - RRF-060 - FullAdder correctness checkpoint misses runtime gate

**Status before:** Planned
**Status after:** In Progress; physical gate failed

**Intent:** Check whether the complete `new-router-first` flow can select,
route, validate, and simulate a FullAdder after the failure-chain corrections.

**Hypothesis:** The corrected portal, base-ownership, escalation, and frozen
geometry contracts should remove the former correctness failure, but the full
placement search may still miss the 10-second service-level objective.

**Intended behavior:** A qualifying FullAdder run must use production
`new-router-first`, report no fallback, pass all 8 rows, have zero conflicts and
unresolved claims, keep overflow peak at or below one, complete within exactly
10.000s, and be one of five deterministic durable harness runs.

**Focused tests and expected evidence:** This was a one-run physical diagnostic,
not the acceptance series. Expected diagnostic fields were the selected
placement fingerprint, correctness counts, overflow, strategy/fallback state,
and runtime.

**Code and documentation changed:** No code was changed by this entry. The
guide, current snapshot, and this journal record the observed result and keep
the verdict `NOT ACCEPTED`.

**Behavior changed:** None.

**Commands:** The exact physical invocation was ad hoc and was not retained.
This missing reproduction record is independently disqualifying.

**Evidence and artifact paths:** Live terminal output only. No durable stdout,
stderr, `.PhysicalDesign.json`, truth table, schematic, or acceptance manifest
path exists for this observation.

**Result and measurements:** Production `new-router-first` selected row-beam
placement fingerprint `a8dc7c20513bcfc3`, passed 8/8 truth-table rows, reported
zero conflicts, zero unresolved claims, overflow peak 1, and no fallback. Its
runtime was 17.186s. The result therefore fails the exact FullAdder runtime gate
because 17.186s is greater than 10.000s. It was also only one non-durable run,
not the required five consecutive deterministic runs.

**Design decision or deviation:** Correctness success is recorded without
weakening the runtime or evidence contract. The result is diagnostic, not
accepted, and no projection from the 1.713s direct-row path replaces a measured
full-flow runtime.

**Open risks:** Placement-selection overhead is above the FullAdder budget. The
lack of durable artifacts prevents audit of the exact command, environment,
source state, and emitted-design determinism.

**Next action:** Reduce the measured full-flow runtime below 10.000s, then run
all five FullAdder repetitions through `RRF-061` before starting or claiming the
RCA4 and CLA4 gates.

### 2026-07-21 - RRF-061 - Add deterministic acceptance evidence harness

**Status before:** Planned
**Status after:** Implemented; physical execution not run

**Intent:** Replace ad hoc terminal observations with one canonical,
machine-judged, durable acceptance workflow.

**Hypothesis:** A fixed sequential matrix with hard process timeouts,
incremental manifests, artifact hashes, and deterministic comparisons prevents
an incomplete, fallback, stale, or mismatched run from being mistaken for an
accepted result.

**Intended behavior:** Run five FullAdder, two RippleCarryAdder4, and two
CarryLookaheadAdder4 compiles sequentially with `new-router-first` and a fixed
seed. Enforce each circuit's exact runtime ceiling and correctness contract.
Persist the exact command, source/environment identity, stdout/stderr, artifact
paths and hashes, placement/resource/route evidence, and emitted-design digest.

**Focused tests and expected evidence:** A dry run must plan exactly nine jobs
without launching a process. Passing mocked runs must remain sequential and
deterministic. The evaluator must reject timeout, fallback, compatibility,
missing-artifact, runtime, conflict, unresolved-claim, and overflow violations.
A repeated-run fingerprint mismatch must fail the complete manifest.

**Code and documentation changed:** Added
`Scripts/Routing/RunRouterAcceptance.py` and
`Tests/test_router_acceptance_harness.py`; updated the canonical guide, design,
routing-doc index, root README, current snapshot, and this journal.

**Behavior changed:** The acceptance matrix now has one executable owner. It
writes `Output/Acceptance/<date>/RouterV10Recovery/AcceptanceManifest.json`
incrementally, records per-run logs and artifact SHA-256 values, applies hard
wall timeouts, and compares placement fingerprint, ownership counts, route
metrics, and canonicalized emitted design across repetitions.

**Commands:**

```bash
python3 -m pytest -q Tests/test_router_acceptance_harness.py
cargo test --manifest-path RustRouting/Cargo.toml --release
```

**Evidence and artifact paths:** Focused temporary-directory fixtures and live
terminal test output only. The real physical harness has not been launched, so
no dated `AcceptanceManifest.json` is claimed.

**Result and measurements:** The harness suite passed 4/4 tests and 8/8
rejection subtests in 0.04s. The exact default-feature Rust gate passed 18/18
native tests with zero failures. These are lightweight implementation gates,
not physical acceptance evidence.

**Design decision or deviation:** The harness judges both process wall time and
the runtime reported by the physical artifact. It rejects a routing-failure
artifact even when success-shaped files exist, and it canonicalizes emitted NBT
regions before comparing design hashes so timestamps and output names do not
create false determinism failures.

**Open risks:** The harness has not run the physical matrix. The known 17.186s
FullAdder observation would fail its 10.000s timeout/runtime checks, and no
durable five-run FullAdder baseline exists.

**Next action:** Use `--dry-run` to inspect the immutable command matrix, then
launch the physical harness only after FullAdder is measured below 10.000s and
the complete lightweight suite passes.

### 2026-07-21 - RRF-060/RRF-061/RRF-062 correction - Run the durable physical matrix

**Status before:** `RRF-060` In Progress, `RRF-061` Implemented but not
physically exercised, and the earlier FullAdder observation failed its runtime
and durability requirements
**Status after:** `RRF-060` remains In Progress; `RRF-061` remains Implemented;
`RRF-062` FullAdder sub-gate Verified; overall v10 NOT ACCEPTED

**Intent:** Correct the earlier entries with the first canonical nine-run
manifest rather than another ad hoc terminal observation.

**Hypothesis:** The demand-first placement scoring that selects the row-beam
placement will make FullAdder deterministic and faster than 10 seconds, while
the same harness will expose any remaining scale failure without allowing a
partial pass to become the overall verdict.

**Intended behavior:** Run five FullAdder, two RippleCarryAdder4, and two
CarryLookaheadAdder4 processes sequentially with `new-router-first`, seed zero,
authoritative validation, and the exact 10/25/120-second external ceilings.
Persist the manifest after each process and accept each circuit only when all
of its repetitions pass.

**Focused tests and expected evidence:** The previously focused-tested harness
must produce `router-acceptance-manifest-v1`, retain every command and log, and
mark the complete manifest failed if any scale process times out or omits a
required artifact.

**Code and documentation changed:** No router code was changed by this entry.
The guide, design checkpoint, current snapshot, and status table now point to
the durable matrix. The earlier RRF-060 and RRF-061 observations remain above
unchanged as historical entries.

**Behavior changed:** None. This entry corrects the evidence record: the old
17.186s non-durable FullAdder result is superseded for current acceptance by
five qualifying durable runs.

**Command:**

```bash
python3 Scripts/Routing/RunRouterAcceptance.py --date 2026-07-21
```

The manifest records `/usr/bin/python3.12`, `PYTHONHASHSEED=0`, Linux
6.17.0-35-generic x86_64, revision
`4c91d1b953dd921f665ef6004cd2c79178c49894`, and a dirty working tree.

**Evidence and artifact paths:** Canonical manifest:
`Output/Acceptance/2026-07-21/RouterV10Recovery/AcceptanceManifest.json`.
Each run has its own directory beneath the same recovery root with the exact
command, stdout, stderr, and any emitted design artifacts. The manifest started
at `2026-07-21T16:34:31.398646+00:00` and completed at
`2026-07-21T16:39:52.740493+00:00` with `Status="FAILED"` and
`Accepted=false`.

**Result and measurements:** FullAdder passed 5/5. Wall times were 6.360420s,
6.269970s, 6.165004s, 6.079007s, and 6.135146s; reported runtimes were
6.264466s, 6.180855s, 6.076534s, 5.992425s, and 6.054654s. Every run passed
8/8 rows with zero conflicts, zero unresolved claims, overflow peak 1,
`ValidationMode="authoritative-exact"`, the native-parallel simulation
backend, and no fallback. All five matched placement fingerprint
`a8dc7c20513bcfc3`, ownership counts `{Air: 23, Electrical: 1045, Support:
113, Wire: 113}`, route metrics (length 113, bends 42, vias 23, rerouted nets
2, routing passes 1, conflicts 0, overflow peak 1, access overflow 0), and
canonical emitted-design digest
`955f47a3d4d2c370ec7becb791bb6d046c5a20b74b209cde2e0e0b808bc9a868`.

RippleCarryAdder4 failed 0/2: both processes returned 124 after external
timeouts at 25.044030s and 25.052964s. CarryLookaheadAdder4 failed 0/2 in the
same way at 120.096740s and 120.113080s. Those four processes emitted no
qualifying schematic, truth table, or `.PhysicalDesign.json`.

**Design decision or deviation:** FullAdder is a separately Verified sub-gate,
but `RRF-060` and the overall guide remain NOT ACCEPTED. An external timeout
and a missing success set fail the gate even when no typed failure JSON was
published before the child was killed.

**Open risks:** RCA4 and CLA4 still repeat expensive portal/candidate work and
do not reach a qualifying result within their service-level objectives. Their
external termination also provides less typed internal failure evidence than a
clean deadline return.

**Next action:** Optimize equivalent scale retries under the existing deadline,
then rerun RCA4 alone. Do not rerun CLA4 or the full matrix until the focused
and lightweight gates pass and RCA4 completes within 25 seconds.

### 2026-07-21 - RRF-063 - Make placement recovery lazy and first-legal result-only

**Status before:** Planned
**Status after:** Implemented; scale physical verification remains open

**Intent:** Avoid paying for expensive graph-beam construction or result-only
route shaping before the first physically valid result needs it.

**Hypothesis:** Row-beam and unpacked placement provide a fast, structurally
different primary pair. Generating graph-beam and spacing alternatives only
after retained primaries fail preserves recovery diversity while removing
avoidable work from a successful first-legal path.

**Intended behavior:** The deterministic primary recipes are row-beam with
graph beam disabled and unpacked placement. Configured graph-beam,
row-beam-direct-only, graph-beam-direct-only, spacing 5, and spacing 7 remain
deferred and deduplicated. They are constructed one at a time only after all
currently retained candidates fail. `QualityTarget="first-legal"` skips only
the optional shape-optimization sweep; congestion repair, materialization,
repeater planning, DRC, and simulation remain mandatory.

**Focused tests and expected evidence:** Assert the exact primary/deferred
recipe order and bounded count, prove graph-beam is not constructed after a
primary routes, prove configured graph-beam is next after both primaries fail,
and prove only first-legal disables the result-only shape sweep.

**Code and documentation changed:** The behavior is implemented in
`Compiler/Placement/PcbFlow.py` and
`Compiler/Routing/AuthoritativePlanner.py`; focused regressions are in
`Tests/test_local_first_router.py` and
`Tests/test_authoritative_planner.py`. This entry documents the completed
slice; it does not add router code.

**Behavior changed:** Placement now pays for the row-beam and unpacked primary
pair before any graph-beam or alternative-spacing recipe. Deferred work and
post-route work retain the same absolute deadline. A legal first result is no
longer delayed by an output-shape preference that cannot make it more legal.

**Command:**

```bash
python3 -m unittest \
  Tests.test_local_first_router.LocalFirstRouterTests.testPlacementGenerationPlanBoundsAndDeduplicatesRecipes \
  Tests.test_local_first_router.LocalFirstRouterTests.testGraphBeamIsNotConstructedAfterPrimaryCandidateRoutes \
  Tests.test_local_first_router.LocalFirstRouterTests.testConfiguredGraphBeamRunsAfterEveryPrimaryPlacementFails \
  Tests.test_authoritative_planner.AuthoritativePlannerTests.testFirstLegalSkipsResultOnlyShapeOptimization
```

**Evidence and artifact paths:** Focused in-memory unit-test evidence only; no
new physical artifact or acceptance manifest was produced for this slice.

**Result and measurements:** These four selectors passed as part of an eight
test focused documentation check; the combined command completed 8/8 tests in
0.002s. The existing acceptance manifest predates this scale-recovery slice and
therefore does not verify its RCA4 or CLA4 impact.

**Design decision or deviation:** Graph-beam is deferred, not removed. This is
a construction-order change, not a circuit-name shortcut or a reduction in
exact legality. First-legal still means the first completed route that passes
authoritative validation and simulation.

**Open risks:** If both primary placements fail, deferred graph-beam remains
expensive. No post-slice scale process has yet proved the 25-second RCA4 or
120-second CLA4 gate.

**Next action:** Combine this work with RRF-064 retry reuse and RRF-065 native
foreign-access exclusion, pass the lightweight suite, and measure RCA4.

### 2026-07-21 - RRF-064 - Make portal retries physically distinct and reusable

**Status before:** Planned
**Status after:** Implemented; scale physical verification remains open

**Intent:** Ensure reservation escalation changes the selected portal geometry
without regenerating identical native portal work, and keep the final
unreserved attempt bounded.

**Hypothesis:** Earlier reservation variants could repeat equivalent work and
recursive retries regenerated portal geometry whose inputs had not changed.
Rotating stable greedy preferences, caching immutable raw portals, and
constructing a diverse bounded unreserved prefix should make each retry cheaper
and semantically meaningful.

**Intended behavior:** `ReservationVariant` chooses a different physical slot
from the stable portal list before greedy scarce-first reservation. An
immutable `RawPortalGeometryCache` is reused only when placed/resources/region,
layers, portal limits, variant counts, guide expansion, and native expansion
limits match exactly. Filtering copies the dictionary and cannot mutate cached
portals. The single unreserved transition caps initial requests, represents
portal variants, and uses deterministic axis/lane ordering under the original
deadline and exact assignment rules.

**Focused tests and expected evidence:** Prove a greedy reservation variant
changes its selected portal ID, mismatched geometry controls reject the cache,
reservation filtering leaves cached tuples unchanged, and repeated reserved
work transitions exactly once to unreserved mode on the same deadline.

**Code and documentation changed:** The implementation and focused tests are
in `Compiler/Routing/AuthoritativePlanner.py` and
`Tests/test_authoritative_planner.py`. This journal entry records the slice and
does not add router code.

**Behavior changed:** Reservation-only recursion can reuse raw native portal
geometry while recomputing reservation choices and route-tree requests. An
alternate variant changes the physical preference offset. The unreserved state
is a bounded production-router search mode, never compatibility fallback.

**Command:**

```bash
python3 -m unittest \
  Tests.test_authoritative_planner.AuthoritativePlannerTests.testGreedyBoundaryPortalReservationVariantChangesPhysicalSlot \
  Tests.test_authoritative_planner.AuthoritativePlannerTests.testRawPortalCacheMatchesOnlyIdenticalGeometryControls \
  Tests.test_authoritative_planner.AuthoritativePlannerTests.testReservedFilteringDoesNotMutateRawPortalCache \
  Tests.test_authoritative_planner.AuthoritativePlannerTests.testRepeatedReservedWorkTransitionsOnceToUnreservedOnSameDeadline
```

**Evidence and artifact paths:** Focused in-memory unit-test evidence only; no
new physical artifacts were emitted.

**Result and measurements:** These four selectors passed as part of the same
eight-test focused check, which completed 8/8 in 0.002s. Raw-cache hits and
bounded-request telemetry still require a post-slice scale artifact for
physical verification.

**Design decision or deviation:** The cache stores unfiltered portal geometry,
not reservation results. Geometry-affecting escalation invalidates it. The
bounded unreserved mode does not relax electrical exclusions, capacity-one
ownership, deadline enforcement, or acceptance validation.

**Open risks:** Candidate generation can still dominate large designs, and the
native route tree has not yet been focus-verified with per-signal foreign
access exclusions from RRF-065.

**Next action:** Complete RRF-065, run the full lightweight Python and exact
Cargo gates, then collect an RCA4 diagnostic with cache and escalation
telemetry.

### 2026-07-21 - RRF-065 - Carry foreign-access exclusions into native route trees

**Status before:** Planned
**Status after:** In Progress

**Intent:** Prevent native candidate search from traversing electrically
protected access geometry owned by another signal.

**Hypothesis:** Python computes per-signal foreign electrical exclusion halos,
but a native route-tree request that does not enforce those nodes can spend its
candidate budget on trees that later fail exact validation. Enforcing the halo
during graph expansion should reject those candidates earlier and reduce
futile scale retries.

**Intended behavior:** Every route-tree request carries a deterministic blocked
node list derived from all other signals' immutable source/target access paths.
Native traversal rejects blocked nodes even when they appear in the broader
allowed region. The current signal's protected access and graph-valid starts
remain usable; an allowed start must not permit continued traversal through a
foreign blocked node. Both bounded batch and direct native entry points use the
same semantics and deadline accounting.

**Focused tests and expected evidence:** A native diamond-graph test must route
around an explicitly blocked short branch, a blocked target must remain
unreachable, binding/request tuple tests must prove the blocked list reaches
Rust without positional-field drift, and Python tests must prove each signal
excludes foreign access while preserving its own starts. The exact Cargo and
complete lightweight Python gates must then pass.

**Code and documentation changed:** Work is active in
`Compiler/Routing/AuthoritativePlanner.py`, `RustRouting/Src/Generation.rs`,
`RustRouting/Src/PathRouting.rs`, `RustRouting/Src/Bindings.rs`, and their
focused tests. This documentation entry intentionally does not classify the
working-tree code as complete.

**Behavior changed:** The request and native search paths are being extended
with explicit blocked nodes. Because the complete focused Python/native
validation has not yet closed, no production or acceptance behavior is claimed
from this slice.

**Commands:** None recorded as a completed RRF-065 verification command yet.
The eight-test command above deliberately excluded unfinished RRF-065 tests.

**Evidence and artifact paths:** Working-tree code and test fixtures only. No
new durable router artifact or acceptance manifest is attributed to this
in-progress slice.

**Result and measurements:** In Progress. Do not infer implementation status
from the presence of `RouteTreeBatchHonorsExplicitBlockedNodes` or changed
binding code until the focused suites actually pass.

**Design decision or deviation:** Electrical exclusions belong in native graph
expansion as well as final exact validation. The blocked set is per signal; a
global union would incorrectly block the current signal's own access geometry.

**Open risks:** Request tuple shape changes can desynchronize Python, PyO3
bindings, and legacy/direct native APIs. Overblocking starts or required targets
could replace late invalid candidates with false `NoCandidates` failures.

**Next action:** Finish the binding and Python coverage, run the focused native
and Python tests, and only then change RRF-065 from In Progress to Implemented.

### 2026-07-21 - RRF-066 - Reject self-conflicting candidates and preserve useful retry work

**Status before:** In Progress during FullAdder disconnect diagnosis
**Status after:** Implemented; fresh physical verification remains open

**Intent:** Prevent an abstractly connected native tree from reaching exact
assignment when its completed physical claims conflict with themselves, while
making lane/layer escalation add useful candidates and keeping deadline and
progress reporting truthful through final validation.

**Hypothesis:** The selected FullAdder `B` tree was not evidence that the local
row-beam claim was disconnected. It combined a staircase transition requiring
support at `(14,1,3)` with same-net dust already occupying `(14,1,3)`. The
existing `FindSelfClaimConflicts` oracle detected `Support:14,1,3`, but the
authoritative materialization call explicitly skipped that check. Rejecting
the bad candidate before assignment, and retaining earlier candidates while
new lanes/layers are explored, should replace late physical disconnects and
equivalent retry loss with exact, bounded search.

**Intended behavior:** Production candidate materialization never skips exact
self-claim validation after combining the native tree, access paths, and local
claims. A same-signal wire/support, required-air, or electrical conflict is
counted as `SelfClaimConflict` and rejected before assignment. Lane diversity
expands the effective bounded lane domain (4, 8, 16, then the policy cap of 24
when demand permits), grows the initial request prefix, unions compatible prior
candidates and metadata, and deduplicates by candidate ID. Layer escalation
also preserves still-compatible candidates; a reservation change does not
reuse candidates whose portal assumptions changed. One absolute deadline
continues through cleanup, compaction, validation, and simulation, and progress
cannot report completion immediately after assignment.

**Focused tests and expected evidence:** The dedicated synthetic candidate
must reproduce the FullAdder support-under-wire shape, return no materialized
candidate, and increment `SelfClaimConflict`. Planner/reliability tests must
continue to cover bounded escalation, retained unaffected work, deadline
expiry, and pending progress. The split native router must pass the exact
default-feature Cargo gate, including explicit blocked-node traversal. No
focused result is physical acceptance.

**Code and documentation changed:** `Compiler/Routing/AuthoritativePlanner.py`
restores exact self-claim checking at the production call site, carries
`PriorCandidateCache` and its axis/lane/layer metadata across compatible
lane/layer retries, and makes lane/request growth effective.
`Compiler/Routing/Pcb.py` and `Compiler/Placement/PcbFlow.py` keep the same
deadline and pending progress through post-assignment physical work;
`Compiler/Main.py`, `Main.py`, and `Compiler/Pipeline.py` carry the finite
positive CLI deadline override into an effective immutable policy.
`Tests/test_authoritative_planner.py` contains the root-cause regression, with
deadline/progress coverage in `Tests/test_router_reliability.py`. The native
implementation remains decomposed into `Deadline.rs`, `Models.rs`,
`PathRouting.rs`, `Generation.rs`, `Assignment.rs`,
`AssignmentPlanning.rs`, `Bindings.rs`, and the thin `Lib.rs` shell.

**Behavior changed:** A candidate that previously reached a successful Rust
assignment and then failed authoritative physical validation is now discarded
at materialization. A compatible lane or layer retry no longer overwrites all
valid candidates from its preceding retry. Planner stage 5 represents
assignment, while final completion is withheld until routed cleanup and
physical validation succeed. Native failure results report deadline state and
completed work without misclassifying failed assignment as completion.

**Commands:**

```bash
python3 -m unittest Tests.test_authoritative_planner.AuthoritativePlannerTests.testCandidateWithSupportUnderItsOwnWireIsRejected -v
python3 -m unittest Tests.test_authoritative_planner Tests.test_router_reliability -v
cargo test --manifest-path RustRouting/Cargo.toml --release
```

The dedicated self-conflict selector passed 1/1. An earlier combined
planner/reliability checkpoint passed 44/44 before the dedicated regression was
added; it is retained as evidence for the candidate-pool and deadline/progress
work at that checkpoint. A broader post-slice lightweight run was still in
progress when this entry was appended and is deliberately not claimed here.
The exact Cargo command passed 19/19 after the blocked-node native regression
was added.

**Evidence and artifact paths:** Focused test output and live diagnostic output
only for this slice. No new durable physical manifest supersedes
`Output/Acceptance/2026-07-21/RouterV10Recovery/AcceptanceManifest.json`.
That manifest is retained for its recorded dirty-tree snapshot, but it predates
this change and cannot verify the current source.

An unpacked-spacing audit found spacings 4 through 10 exact-legal and
fingerprint-distinct; spacing 5 had the best observed feedback score in that
live diagnostic. A temporary RCA4 primary-spacing-7 run used the compiler with
`--routing-strategy new-router-first --routing-deadline-seconds 25` under a
26-second external ceiling and timed out without a qualifying artifact. Its
filtered terminal output was truncated, so no finer runtime or typed-artifact
claim is made. The experiment is preserved here and the primary unpacked
spacing is restored to 5; configured spacing and spacing 7 remain bounded,
deferred alternatives rather than new defaults.

**Result and measurements:** The focused regression proves the exact root
condition: the candidate is `None` and its rejection counter contains one
`SelfClaimConflict`. The existing split Rust release gate now reports 19/19.
These are implementation results only. The spacing-7 RCA4 timeout and the old
RCA4/CLA4 acceptance timeouts remain failures, not evidence of recovery.

**Design decision or deviation:** This is a generic resource-ownership fix,
not a FullAdder rule. The exact claim oracle is authoritative even when a
native tree is graph-connected. Candidate reuse is allowed only across
controls that leave prior geometry valid. The spacing-7 experiment did not
justify changing the production primary order, so spacing 5 was restored.
The prior FullAdder 5/5 manifest is not deleted or relabeled; it remains valid
for its source snapshot but does not verify the changed tree.

**Open risks:** The complete post-slice lightweight suite is not yet recorded.
The deadline overrun below one second, current-tree FullAdder determinism, RCA4
completion within 25 seconds, and CLA4 completion within 120 seconds all remain
physically unverified. `RRF-065` remains tracked separately until its complete
cross-language focused result is appended.

**Next action:** Record the in-flight lightweight result, run one RCA4
diagnostic at restored spacing 5, then run the full sequential acceptance
matrix only after every lightweight gate passes. Keep the guide `NOT ACCEPTED`
unless all fresh physical gates pass.

### 2026-07-21 - RRF-065 correction - Complete foreign-access blocked-node propagation

**Status before:** In Progress in the earlier RRF-065 entry
**Status after:** Implemented; physical verification remains open

**Intent:** Close the cross-language request contract that the earlier entry
deliberately left unfinished without rewriting that historical status.

**Hypothesis:** Passing each signal's foreign electrical-access halo into
native expansion prevents route-tree work from traversing geometry that exact
materialization must later reject.

**Intended behavior:** Python supplies a stable blocked-node list for every
route-tree request. PyO3 preserves the request fields, and native traversal
rejects blocked nodes after an allowed start as well as during later branch
expansion. A blocked direct branch may use a legal alternate branch; a blocked
target remains unreachable.

**Focused tests and expected evidence:** The authoritative-planner request path
must remain ordered, and the native diamond graph must route around one blocked
branch while rejecting a blocked target. The exact default-feature Rust gate
must pass.

**Code and documentation changed:** The completed behavior is in
`Compiler/Routing/AuthoritativePlanner.py`, `RustRouting/Src/Bindings.rs`,
`RustRouting/Src/Generation.rs`, and `RustRouting/Src/PathRouting.rs`. This
correction updates the canonical current status; the earlier in-progress entry
is preserved above.

**Behavior changed:** Foreign access exclusions now constrain native search,
not merely final candidate validation. Own-signal access starts remain legal.

**Commands:**

```bash
python3 -m unittest -q Tests.test_authoritative_planner Tests.test_router_reliability Tests.test_resource_graph
cargo test --manifest-path RustRouting/Cargo.toml --release
```

**Evidence and artifact paths:** The Python command passed 61/61 current tests.
The Rust release command passed 21/21, including
`RouteTreeBatchHonorsExplicitBlockedNodes`. These are terminal results; no
physical artifact is attributed to RRF-065.

**Result and measurements:** The focused implementation gate is closed, so
RRF-065 is `Implemented`. The earlier 19-test Rust count was correct for that
checkpoint and is not rewritten; the current suite has two additional
assignment-conflict regressions and totals 21.

**Design decision or deviation:** Blocking is per signal. A global union would
incorrectly remove the current signal's own immutable access. Focused
cross-language success is implementation evidence, not a physical acceptance
result.

**Open risks:** RCA4 and CLA4 have not produced qualifying current-source
physical results, so blocked-node propagation is not `Verified` at scale.

**Next action:** Keep this slice in the broader lightweight gate and judge it
physically only through the fresh sequential acceptance matrix.

### 2026-07-21 - RRF-067 - Type native offenders and restore exact support conflicts

**Status before:** Planned behavior present only as unjournaled working-tree
work
**Status after:** Implemented; physical verification remains open

**Intent:** Give higher-order assignment failures a circuit-independent typed
offender set and keep Python/native ownership rules identical.

**Hypothesis:** A failure net alone is insufficient when every pair has a legal
combination but the complete matching does not. Returning the selected stack
and emptied-domain pair lets offender-only regeneration change the relevant
geometry. At the same time, omitting support-versus-wire/air conflicts can make
the Python conflict graph and Rust assignment accept physically incompatible
candidates.

**Intended behavior:** Failed native assignment returns deterministic
`ConflictSignals`; successful backtracking returns none from discarded
branches. Python serializes the native set as `NativeConflictSignals` and
unions it into `ConflictSignals`. A support cell conflicts with another
signal's wire or required air in `FindClaimConflicts`, `_ClaimsConflict`,
`ClaimMask::Conflicts`, and native conflict indices. Shared inert support alone
is not declared a conflict.

**Focused tests and expected evidence:** Python tests must classify typed
higher-order offenders and support-versus-wire incompatibility. Rust must prove
deterministic three-signal failure reporting, clean successful backtracking,
and cross-category claim conflicts. Rebuilt-extension smoke must expose the new
PyO3 field.

**Code and documentation changed:** `Compiler/Routing/AuthoritativePlanner.py`
and `Compiler/Routing/ResourceGraph.py` apply and expose the exact rules.
`RustRouting/Src/Models.rs`, `Assignment.rs`, `AssignmentPlanning.rs`, and
`Lib.rs` carry the typed result and native regressions. The local Python
extension was rebuilt from the split Rust source.

**Behavior changed:** Escalation can now use the full native higher-order
offender set rather than infer a circuit-specific cluster from one failure
label. Rust and Python again reject foreign support occupying live wire or air.

**Commands:**

```bash
cargo build --manifest-path RustRouting/Cargo.toml --release --features python-extension
cp RustRouting/target/release/libRustRouting.so RedstoneCompiler/RustRouting.cpython-312-x86_64-linux-gnu.so
python3 -m unittest -q Tests.test_authoritative_planner Tests.test_router_reliability Tests.test_resource_graph
cargo test --manifest-path RustRouting/Cargo.toml --release
```

**Evidence and artifact paths:** The current Python command passed 61/61. The
Rust release command passed 21/21, including
`HigherOrderFailureReportsSelectedStackAndEmptyDomainPairDeterministically` and
`SuccessfulBacktrackingDoesNotExposeDiscardedBranchConflicts`. A rebuilt
extension smoke returned a failed result with `ConflictSignals=['A', 'B']`.

**Result and measurements:** RRF-067 is `Implemented`. The field is present in
the native result, used by the current RCA4 failure artifact, and empty on a
successful backtrack. No physical gate is claimed.

**Design decision or deviation:** The typed set is diagnostic and control
state, not a promise of a mathematically minimal unsatisfiable core. It is a
stable, conservative offender set sufficient for bounded regeneration.

**Open risks:** Offender-only regeneration can still expose another
higher-order conflict, and the RCA4 diagnostics show that sequence can consume
the placement's local slice without finding a legal assignment.

**Next action:** Keep the passing
`testDeadlineFailurePreservesEscalationStateAndHistory` regression in the
lightweight gate, then use fresh RCA4 evidence to evaluate geometry changes
rather than increasing assignment work blindly.

### 2026-07-21 - RRF-068 - Make portal reachability layer-aware

**Status before:** In Progress during stacked-access diagnosis
**Status after:** Implemented; physical verification remains open

**Intent:** Stop one inaccessible routing layer from falsely rejecting a
terminal that can escape on another layer, while ensuring stacked terminals
start with enough physical routing elevation.

**Hypothesis:** Portal domains are layer-specific alternatives. Treating an
empty domain on one layer as terminal-wide `NoBoundaryEscape` rejects legal
geometry, but starting all designs at the technology minimum can make a high
stacked access physically unreachable.

**Intended behavior:** Reservation retains empty per-layer entries and rejects
a terminal only when it has zero portal candidates across all effective
layers. Different layers never cross-reserve the same slot. Candidate
construction skips a layer unless every terminal for that signal reaches it.
`RequiredRoutingLayerCountForAccess` raises only the minimum layer floor needed
to put the highest access within guide expansion, capped by policy,
technology, and available design height.

**Focused tests and expected evidence:** Layer-specific inaccessibility must
remain legal when another layer is reachable; all-layer inaccessibility must
produce typed `NoBoundaryEscape`; reservations on different layers remain
independent; and a height-19 access requires eight layers while a low access
stays at the technology minimum.

**Code and documentation changed:** `Compiler/Routing/AuthoritativePlanner.py`
implements terminal-wide availability and the stacked-access floor.
`Tests/test_authoritative_planner.py` contains the four focused cases.

**Behavior changed:** Portal failure is now based on physical reachability
across the complete effective layer set. Tall stacked placements request only
the necessary initial floor rather than every layer allowed by the design box.

**Commands:**

```bash
python3 -m unittest -q Tests.test_authoritative_planner
```

**Evidence and artifact paths:** The authoritative-planner tests are included
in the current 61/61 Python command recorded above. No dedicated physical
stacked-access artifact was retained.

**Result and measurements:** The focused layer semantics pass and RRF-068 is
`Implemented`. This changes feasibility behavior but does not satisfy any
FullAdder, RCA4, or CLA4 gate.

**Design decision or deviation:** Height is both a lower-bound input from
immutable access and an upper-bound capacity constraint. It is not a request to
instantiate all nominal technology layers.

**Open risks:** A layer can be individually reachable for every terminal yet
still fail higher-order capacity-one assignment; RRF-068 deliberately does not
weaken that later oracle.

**Next action:** Preserve the per-layer portal counts in physical diagnostics
and include stacked designs in the full lightweight and acceptance runs.

### 2026-07-21 - RRF-069 - Bound placement failover using measured routability work

**Status before:** In Progress during RCA4 deadline recovery
**Status after:** Implemented; RCA4 and the physical matrix remain unverified

**Intent:** Spend the single deadline on meaningfully different placement and
routing work instead of allowing one placement or one control retry to consume
the complete compile allowance.

**Hypothesis:** Global-net count alone overvalues dense local reuse when fixed
claims, boundary overflow, and pin scarcity dominate assignment. A generic
work estimate, lazy demand-driven placement generation, per-placement adaptive
slices, and observed runtime gates should let a wider routable placement run
without resetting or extending the absolute deadline.

**Intended behavior:** `RoutabilityWorkEstimate` combines five times the global
extension-net count, pre-owned nodes, extension nodes, three times boundary
overflow, and one unit per eight scarce pins. Row-beam and unpacked spacing 5
remain primary. Deferred candidates are generated lazily; pressure can pull a
wider unpacked spacing 7 ahead of another pressured retained placement.

Each placement gets a local adaptive clock and at most half the remaining
runtime while another placement exists. After the first escalation, another
control pass starts only if its observed duration fits the local remainder,
with a five-second reserve cap. A later placement starts only if the minimum
observed positive attempt duration, also capped at five seconds, fits the
absolute remainder. Portal-mode changes retain/regenerate only offender nets
when no local claims exist; local-claim ownership forces a complete portal
candidate rebuild. Every action receives the original `RoutingDeadline` and
expiry.

**Focused tests and expected evidence:** Work ranking must prefer the small
local FullAdder placement but the lower-pressure unpacked RCA-scale placement.
Deferred generation must respond to measured demand, a pressured pending
candidate must allow a clearer deferred candidate to run first, adaptive
escalation must require room for an observed pass, the measured start gate must
be bounded, and all placement attempts must share one deadline object.

**Code and documentation changed:** `Compiler/Routing/LocalFirst.py` publishes
the work estimate. `Compiler/Placement/PcbFlow.py` implements the wider
unpacked request, demand-aware generation, local slices, and measured start
gate. `Compiler/Routing/Reliability.py` implements the observed-pass gate, and
`Compiler/Routing/AuthoritativePlanner.py` applies it and limits localized
portal regeneration. Focused coverage is in
`Tests/test_router_reliability.py` and `Tests/test_local_first_router.py`.

**Behavior changed:** A later placement now gets a bounded opportunity before
the shared deadline is exhausted. An adaptive retry that cannot fit its own
observed cost advances placement with a typed conflict instead of starting
work it cannot finish. Near the end of the deadline, orchestration may decline
to start a placement using measured evidence rather than resetting a timer.

**Commands:**

```bash
python3 -m unittest Tests.test_authoritative_planner Tests.test_router_reliability Tests.test_local_first_router Tests.test_resource_graph -v
cargo test --manifest-path RustRouting/Cargo.toml --release
```

The combined focused Python command exited successfully across 90 discovered
tests. The three planner/reliability/resource modules separately reported
61/61, and the local-first module contributed 29 discovered tests. The exact
Rust command passed 21/21.

**Evidence and artifact paths:**

- `/tmp/rrf-fulladder-sliced/FullAdderSliced.PhysicalDesign.json` and
  `FullAdderSliced.TruthTable.txt`;
- `/tmp/rrf-rca-pass-gate/RCA4PassGate.RoutingFailure.json`; and
- `/tmp/rrf-rca-wide-second/RCA4WideSecond.RoutingFailure.json`.

The FullAdder CLI diagnostic used `new-router-first`, completed in 0.942832s,
passed 8/8 rows with zero conflicts and unresolved claims, reported overflow
peak 1, used authoritative-exact validation and native-parallel simulation,
and did not fall back. It is one `/tmp` run, not the required 5/5 gate.

The first RCA4 diagnostic ended at 24.800356s with `Expired=false` and 0.209s
remaining. Unpacked spacing 5 failed after 10.670079s, configured unpacked
spacing 6 failed after 7.360009s, and the measured placement-start gate did not
launch spacing 7. The latest diagnostic ended at 25.166941s; its deadline
reported expiry at 25.159888s. Unpacked spacing 5 failed after 10.454291s and
wider unpacked spacing 7 ran for 8.980902s before
`LocalClaimRelease:RuntimeBudgetExceeded`.

**Result and measurements:** RRF-069 is `Implemented`, not `Verified`. It
demonstrates one-deadline bounded failover and a current sub-second FullAdder
diagnostic, but RCA4 still has no routed 512-row result and CLA4 has not been
rerun. The latest internal overrun is below one second but any expiry fails the
25-second physical gate.

**Design decision or deviation:** The RCA4 runs came from evolving dirty-tree
checkpoints and make gates from measured wall-clock work. Runtime and source
variation changed whether spacing 6 or spacing 7 was selected and whether the
deadline was crossed. Neither run is deterministic acceptance evidence. The
work estimate and measured gates are circuit-independent; no adder name is
inspected.

**Open risks:** RCA4 assignment remains sensitive to placement order and
higher-order conflicts. `/tmp` is ignored and these diagnostics are not a
durable acceptance manifest. Full current lightweight discovery, five
FullAdder repetitions, two RCA4 repetitions, and two CLA4 repetitions remain.

**Next action:** Preserve the exact dirty diff and complete lightweight output,
remove the remaining RCA4 assignment/placement variability, rerun RCA4 alone,
and launch the full sequential matrix only after RCA4 routes within 25 seconds.
Keep the guide `NOT ACCEPTED` until every physical gate passes.

### 2026-07-21 - RRF-070 - Record the current lightweight and physical acceptance matrix

**Status before:** RRF-065 through RRF-069 Implemented; current physical
matrix not yet recorded
**Status after:** Implemented; RRF-062 FullAdder Verified; overall v10 failed
and remains `NOT ACCEPTED`

**Intent:** Replace diagnostic-only conclusions with one complete, sequential,
durable current-source acceptance run while preserving the earlier matrix as a
named historical snapshot.

**Hypothesis:** The focused recovery work should retain deterministic
FullAdder correctness and bounded runtime. RCA4 and CLA4 remain the scale gates
that determine whether the router can be accepted.

**Intended behavior:** Run compileall, the complete lightweight Python suite,
the exact default-feature Rust suite, and the diff check before launching the
nine physical processes sequentially. The harness must publish every run,
including failures, and mark the manifest failed unless all nine runs satisfy
their circuit-specific correctness, determinism, artifact, and runtime gates.

**Focused tests and expected evidence:** Lightweight Python must pass without
scale tests, Rust must pass its exact 21-test release gate, and the harness must
record five FullAdder, two RCA4, and two CLA4 evaluations. Timeout, missing
artifact, nonzero result, or nondeterminism must fail its run.

**Code and documentation changed:** No new router behavior is attributed to
this slice. The guide, design checkpoint, current status table, and this
journal now distinguish the current manifest from
`RouterV10RecoverySnapshotPreRRF069`.

**Behavior changed:** The operator verdict is now based on the current durable
matrix rather than the preceding `/tmp` diagnostics. FullAdder is restored to
`Verified` for the current source snapshot; the overall verdict remains
failed.

**Commands:**

```bash
python3 -m compileall -q Compiler SVDecoder SchemEncoder Tests
python3 -m unittest \
  Tests.test_authoritative_planner \
  Tests.test_channel_planner \
  Tests.test_legacy_shims_fail_fast \
  Tests.test_local_first_router \
  Tests.test_logic_optimization \
  Tests.test_physical_cells \
  Tests.test_pipeline_artifact_integrity \
  Tests.test_placement_boundary_feasibility \
  Tests.test_redstone_simulation \
  Tests.test_resource_graph \
  Tests.test_router_acceptance_harness \
  Tests.test_router_reliability \
  Tests.test_routing_architecture \
  Tests.test_routing_resources
cargo test --manifest-path RustRouting/Cargo.toml --release
git diff --check
python3 Scripts/Routing/RunRouterAcceptance.py --date 2026-07-21
```

**Evidence and artifact paths:** The current source is Git revision
`4c91d1b953dd921f665ef6004cd2c79178c49894` with `Dirty=true`. The current
manifest is
`Output/Acceptance/2026-07-21/RouterV10Recovery/AcceptanceManifest.json`.
The earlier manifest and its artifacts are preserved under
`Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF069/`.

**Result and measurements:** Compileall and `git diff --check` passed. The full
lightweight Python suite passed 134/134 in 36.534s. The exact Rust release gate
passed 21/21.

The current acceptance manifest has `Status="FAILED"` and `Accepted=false`:

- FullAdder passed 5/5. Wall times were 1.193751s, 1.163705s, 1.048207s,
  1.040231s, and 1.038901s. Reported router times were 1.100603s, 1.073153s,
  0.961148s, 0.961509s, and 0.960259s. Every run passed 8/8 rows with zero
  conflicts and unresolved claims and overflow peak 1.
- All five FullAdder runs match placement fingerprint `a8dc7c20513bcfc3`,
  ownership counts (`Air=20`, `Electrical=1008`, `Support=107`, `Wire=107`),
  route metrics, and emitted-design digest
  `850dcc984e95c26fd598c5c06da84127f110e47eacaa142be39c6947abbef820`.
- RCA4 failed 0/2. Run 1 timed out at 26.038323s with return 124; run 2
  completed without a harness timeout at 25.451761s with return 1. Both runs
  retained a typed `.RoutingFailure.json` and emitted no routed artifacts.
- CLA4 failed 0/2. Both runs timed out with return 124 at 121.131519s and
  121.147175s. Neither emitted a `.RoutingFailure.json` or routed artifacts.

**Design decision or deviation:** A typed RCA4 failure is better diagnostic
evidence than a silent timeout, but it is still a failed physical gate. The
missing CLA4 failure artifacts identify a remaining native/deadline reporting
gap. The previous matrix was moved to a snapshot directory rather than deleted
or overwritten as historical evidence.

**Open risks:** RCA4 still exceeds or exhausts its 25-second allowance without
routing, and CLA4 still overruns its 120-second external ceiling without
publishing typed diagnostics. Only the FullAdder sub-gate is Verified.

**Next action:** Diagnose both current RCA4 failure artifacts, then bound the
CLA4 native call far enough inside the shared deadline to publish a typed
failure. Rerun the failed scale gates after focused fixes; do not weaken the
matrix or mark the guide accepted until RCA4 and CLA4 both pass 2/2.

### 2026-07-21 - RRF-071 - Enforce local slices inside expensive work and record the post-fix matrix

**Status before:** RRF-070 Implemented with FullAdder Verified, RCA4 typed only
in one run, and CLA4 ending at the harness timeout without typed artifacts
**Status after:** Implemented; FullAdder remains Verified; RCA4 and CLA4 both
publish typed failures but remain physically failed 0/2

**Intent:** Make every expensive placement/routing stage observe the tighter
per-placement allowance, stop long Python/native inner loops promptly, and
retain typed evidence before the harness capture timeout.

**Hypothesis:** Passing a local duration only to outer orchestration is not
enough when resource-graph construction, portal/tree generation, assignment
base conflicts, conflict classification, or one placement generator can run
for most of that slice. Periodic inner checks and fair division of placement
generation work should let scale cases advance or fail themselves predictably.

**Intended behavior:** One adaptive expiry is the minimum of the placement
slice and the shared absolute deadline. Resource graph, portal generation,
route-tree generation, assignment, and conflict classification use that tighter
remaining time. A local expiry advances placement with typed
`TrackAssignmentConflict`; shared expiry remains typed
`RuntimeBudgetExceeded`.

Placement construction publishes periodic `WorkCheck` phase/progress data.
Each remaining deterministic generator receives a fair share of the absolute
remainder; an expired generator slice records `PlacementGeneration:Stagnated`
and advances without resetting the global deadline. Rust checks deadlines
inside base-claim scans and conflict-resource collection as well as outer MRV
loops. The harness adds 2.0 seconds only to its subprocess capture timeout so a
self-exiting compiler can flush JSON; evaluation still enforces the exact
10/25/120-second acceptance ceilings.

**Focused tests and expected evidence:** The adaptive helper must choose the
tighter local/global remainder without replacing the shared deadline.
Resource-graph edges, conflict candidate pairs, and placement construction must
be interruptible through `WorkCheck`. The harness must retain its immutable
case ceiling while exposing the capture grace. Native deadline tests and the
complete explicit scale-excluded Python list must pass.

**Code and documentation changed:** `Compiler/Routing/Reliability.py` defines
the local/global runtime enforcement. `Compiler/Routing/AuthoritativePlanner.py`
passes the tighter milliseconds into portal, tree, and both assignment APIs and
checks conflict classification. `Compiler/Routing/ResourceGraph.py`,
`Compiler/Placement/Pcb.py`, and `Compiler/Placement/PcbFlow.py` add periodic
work checks and fair-share placement-generation slices.
`RustRouting/Src/Assignment.rs` checks the native deadline inside base and
conflict loops. `Scripts/Routing/RunRouterAcceptance.py` defines the 2.0-second
capture-only grace.

**Behavior changed:** RCA4 now ends both processes around 22.27 seconds with
typed, unexpired-deadline artifacts. CLA4 now self-exits both processes with
typed artifacts before the 122-second capture timeout. Neither improvement is
mistaken for a physical pass.

**Commands:**

```bash
python3 -m compileall -q Compiler SVDecoder SchemEncoder Tests
python3 -m unittest \
  Tests.test_authoritative_planner \
  Tests.test_channel_planner \
  Tests.test_legacy_shims_fail_fast \
  Tests.test_local_first_router \
  Tests.test_logic_optimization \
  Tests.test_physical_cells \
  Tests.test_pipeline_artifact_integrity \
  Tests.test_placement_boundary_feasibility \
  Tests.test_redstone_simulation \
  Tests.test_resource_graph \
  Tests.test_router_acceptance_harness \
  Tests.test_router_reliability \
  Tests.test_routing_architecture \
  Tests.test_routing_resources
cargo fmt --manifest-path RustRouting/Cargo.toml -- --check
cargo test --manifest-path RustRouting/Cargo.toml --release
git diff --check
python3 Scripts/Routing/RunRouterAcceptance.py --date 2026-07-21
```

**Evidence and artifact paths:** The current manifest is
`Output/Acceptance/2026-07-21/RouterV10Recovery/AcceptanceManifest.json`.
It started at `2026-07-21T19:53:47.955945+00:00`, completed at
`2026-07-21T19:58:39.103948+00:00`, has `Status="FAILED"`, and records revision
`4c91d1b953dd921f665ef6004cd2c79178c49894` with `Dirty=true`. The RRF-070
manifest and artifacts are preserved under
`Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF071/`; the still
earlier pre-RRF-069 snapshot remains unchanged.

**Result and measurements:** Compileall, Rust formatting, and `git diff
--check` passed. The explicit scale-excluded Python list passed 139/139 in
36.261s. The exact Rust release gate passed 21/21.

The current manifest records:

- FullAdder passed 5/5. Wall times were 1.002021s, 1.002928s, 1.000096s,
  0.986873s, and 0.990030s; reported router times were 0.923125s, 0.927339s,
  0.923784s, 0.913400s, and 0.914244s. Every run passed 8/8 rows with zero
  conflicts and unresolved claims and overflow peak 1. Fingerprint
  `a8dc7c20513bcfc3`, ownership, route metrics, and emitted-design digest
  `850dcc984e95c26fd598c5c06da84127f110e47eacaa142be39c6947abbef820`
  matched across all five runs.
- RCA4 failed 0/2 but bounded itself. Both runs returned 1 without a harness
  timeout at 22.269156s and 22.272633s. Artifact runtimes were 22.181874s and
  22.184947s; `Deadline.Expired=false` with 2.824s and 2.820s remaining.
  Spacing 5 ran for 10.614026s and 10.607792s. Spacing 7 received local slices
  of 4.591098s and 4.593325s and returned after 4.821814s and 4.836653s, local
  overruns of 0.230716s and 0.243328s. The spacing-6 third candidate was
  recorded but not started. Both runs emitted typed failures and no routed
  artifacts.
- CLA4 failed 0/2. Both processes returned 1 without a harness timeout at
  120.866246s and 120.732578s and emitted typed failures with three placement
  attempts each. The internal failures report shared expiry after 120.364s and
  120.278s, both less than one second beyond the 120-second internal deadline.
  Neither run emitted routed artifacts.

**Design decision or deviation:** Capture grace is 2.0 seconds because the
process timer includes frontend startup and artifact publication outside the
placement-origin routing deadline. It is evidence-capture allowance only:
`EvaluateRun` still fails any wall or reported runtime above the case's exact
ceiling. The RCA4 local-slice overruns are measured and remain below one second,
but the runs fail because they did not route, not because bounded failure is an
acceptance substitute.

**Open risks:** RCA4 now fails early and reproducibly but still has no legal
capacity-one route. CLA4 publishes diagnostics but still consumes the entire
shared allowance. The remaining problem is physical routability and search
effectiveness, not absence of bounded failure evidence.

**Next action:** Use the current RCA4 conflict and placement fingerprints to
change physical geometry or assignment compatibility, then reduce CLA4 work
while retaining its typed self-exit. Rerun the affected scale gates after
focused fixes; keep the guide `NOT ACCEPTED` until both pass 2/2.

### 2026-07-21 - RRF-072 - Interrupt expensive work end to end and expose the process-envelope gap

**Status before:** RRF-071 Implemented; FullAdder Verified; RCA4 and CLA4
bounded but physically failed 0/2
**Status after:** Implemented; all scale failures retain typed histories, but
the pre-envelope CLA4 wall overrun prevents deadline verification

**Intent:** Carry deadline observation through every expensive placement,
physical-validation, Python-routing, and native-assignment loop, while keeping
candidate publication and recovery transactional under the original slice.

**Hypothesis:** Outer deadline checks cannot bound a large cluster, beam,
validation, graph, claim-mask, or conflict loop. Periodic checks throughout
those loops, combined with a reserved routing share, should stop equivalent
work promptly and preserve enough history to diagnose the next physical fix.

**Intended behavior:** Clustering, slot selection, beam expansion, structural
mapping, compaction, boundary analysis, feedback, isolation, and placement
resource work publish periodic `WorkCheck` progress. Physical graph,
validation, template, repeater, claim, conflict, guide, and rip-up work do the
same. Placement generation reserves 20% of the shared deadline for routing and
publishes a retained candidate only after bounded feedback completes.

Unexpected failures keep placement and escalation histories. A response that
advances placement skips local-claim recovery; an allowed recovery continues
with the original remaining local slice. The PyO3 assignment timer begins
before manual payload extraction, and native claim-mask construction, union,
conflict collection, and sorting are chunked and deadline checked. Native code
remains split across exactly eight Rust source files. The harness records wall
overrun and rejects the deadline-enforcement sub-gate at one second or more.

**Focused tests and expected evidence:** Placement and routing helpers must be
interruptible inside their scaling loops, recovery must neither reset nor
replace the original slice, unexpected failures must serialize prior history,
and native payload/claim work must stop under tiny deadlines. The full
scale-excluded Python and exact Rust release gates must pass before the physical
matrix runs.

**Commands:**

```bash
python3 -m compileall -q Compiler SVDecoder SchemEncoder Tests
python3 -m unittest \
  Tests.test_authoritative_planner \
  Tests.test_channel_planner \
  Tests.test_legacy_shims_fail_fast \
  Tests.test_local_first_router \
  Tests.test_logic_optimization \
  Tests.test_physical_cells \
  Tests.test_pipeline_artifact_integrity \
  Tests.test_placement_boundary_feasibility \
  Tests.test_redstone_simulation \
  Tests.test_resource_graph \
  Tests.test_router_acceptance_harness \
  Tests.test_router_reliability \
  Tests.test_routing_architecture \
  Tests.test_routing_resources
cargo fmt --manifest-path RustRouting/Cargo.toml -- --check
cargo test --manifest-path RustRouting/Cargo.toml --release
git diff --check
python3 Scripts/Routing/RunRouterAcceptance.py --date 2026-07-21 \
  --output-root Output/Acceptance --python /usr/bin/python3
```

**Evidence and artifact paths:** The RRF-072 matrix and its typed failures are
preserved under
`Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF073/`.

**Result and measurements:** Compileall, Rust formatting, and the diff check
passed. The explicit 14-module scale-excluded Python gate passed 156/156 in
37.261s, and the exact eight-file Rust release gate passed 25/25.

- FullAdder passed 5/5 at 1.041832s, 1.033418s, 1.030760s, 1.035767s, and
  1.039969s wall time.
- RCA4 failed 0/2 with typed failures at 20.487121s and 20.475821s.
- CLA4 failed 0/2 with typed failures at 121.725966s and 121.425092s. Both
  crossed the immutable 120-second wall ceiling by more than one second.

**Design decision or deviation:** RRF-072 bounded and preserved internal work,
but a routing deadline equal to the process ceiling left no envelope for
startup and typed-artifact publication. The failed CLA4 timing is retained as
evidence of that process-envelope defect; it is not rounded down or accepted.

**Open risks:** RCA4 and CLA4 remain unrouted. The process also needs an
explicit publication envelope before native deadline enforcement can be called
physically Verified.

**Next action:** Reserve publication time inside, not beyond, each immutable
wall ceiling; record both bounds in the manifest and rerun every physical case.

### 2026-07-21 - RRF-073 - Verify the immutable wall envelope with an internal publication reserve

**Status before:** RRF-072 Implemented; FullAdder Verified; process envelope
unverified after both CLA4 runs exceeded one second of wall overrun
**Status after:** Deadline-enforcement and FullAdder physical sub-gates
Verified; RCA4 and CLA4 remain failed 0/2; overall `NOT ACCEPTED`

**Intent:** Separate the compiler's routing deadline from the acceptance wall
ceiling so frontend and artifact publication complete within the ceiling
without extending search time or weakening evaluation.

**Hypothesis:** A fixed two-second reserve subtracted before launch should let
typed scale failures publish within the unchanged wall ceilings. Keeping the
watchdog at ceiling plus two seconds for capture only should still expose a
compiler that ignores its internal bound.

**Intended behavior:** `PublicationReserve=2.0` yields router deadlines of 8
seconds for FullAdder, 23 seconds for RCA4, and 118 seconds for CLA4 inside the
immutable 10/25/120-second wall ceilings. The watchdog remains ceiling plus two
seconds and never participates in acceptance. The manifest serializes the
reserve, effective router deadline, wall overrun, and within-ceiling result.
There is no fallback, deadline reset, or ceiling extension.

**Focused tests and expected evidence:** The harness must keep wall ceilings
immutable, pass only the reduced router deadline to the compiler, retain the
capture-only watchdog, and report zero overrun for an in-ceiling self-exit. All
lightweight gates must pass, and the nine-run manifest must record no timeout
and `DeadlineOverrunWithinLimit=true` for every process.

**Commands:**

```bash
python3 -m compileall -q Compiler SVDecoder SchemEncoder Tests
python3 -m unittest \
  Tests.test_authoritative_planner \
  Tests.test_channel_planner \
  Tests.test_legacy_shims_fail_fast \
  Tests.test_local_first_router \
  Tests.test_logic_optimization \
  Tests.test_physical_cells \
  Tests.test_pipeline_artifact_integrity \
  Tests.test_placement_boundary_feasibility \
  Tests.test_redstone_simulation \
  Tests.test_resource_graph \
  Tests.test_router_acceptance_harness \
  Tests.test_router_reliability \
  Tests.test_routing_architecture \
  Tests.test_routing_resources
cargo fmt --manifest-path RustRouting/Cargo.toml -- --check
cargo test --manifest-path RustRouting/Cargo.toml --release
git diff --check
python3 Scripts/Routing/RunRouterAcceptance.py --date 2026-07-21 \
  --output-root Output/Acceptance --python /usr/bin/python3
```

**Evidence and artifact paths:** The current manifest is
`Output/Acceptance/2026-07-21/RouterV10Recovery/AcceptanceManifest.json`, with
SHA-256
`35e5b4c3449dcdee257de920fd7e99442ed3a5e385b3815155cdafc82955c395`.
It started at `2026-07-21T20:36:14.510691+00:00`, completed at
`2026-07-21T20:40:55.885542+00:00`, records revision
`4c91d1b953dd921f665ef6004cd2c79178c49894` with `Dirty=true`, and has
`Status="FAILED"` and `Accepted=false`. The RRF-072 matrix remains under
`RouterV10RecoverySnapshotPreRRF073`; earlier pre-RRF-069, pre-RRF-071, and
pre-RRF-072 snapshots remain unchanged.

**Result and measurements:** Compileall, Rust formatting, and the diff check
passed. Focused Python passed 95/95. The explicit 14-module scale-excluded
Python gate passed 158/158 in 37.256s, and the exact eight-file Rust release
gate passed 25/25.

- FullAdder passed 5/5. Wall times were 1.034566s, 1.034114s, 1.018414s,
  1.020388s, and 1.036595s; reported router times were 0.954818s, 0.957215s,
  0.941270s, 0.944164s, and 0.957889s. Every run passed 8/8 rows with zero
  conflicts and unresolved claims and overflow peak 1. Fingerprint
  `a8dc7c20513bcfc3` and emitted-design digest
  `850dcc984e95c26fd598c5c06da84127f110e47eacaa142be39c6947abbef820`
  matched across all five runs with identical ownership and route metrics.
- RCA4 failed 0/2 at 18.828730s and 18.848981s wall time. Both returned 1 with
  no timeout, zero wall overrun, `DeadlineOverrunWithinLimit=true`, typed failures, and
  no routed outputs. Run 1's artifact runtime was 18.666887s; its shared
  deadline remained unexpired with 4.358s available. Unpacked spacing 5 ended
  in capacity-one `TrackAssignmentConflict` after 9.879444s, and the row-beam
  candidate did not start because its minimum-start gate could not fit.
- CLA4 failed 0/2 at 119.268383s and 119.248057s wall time. Both returned 1
  with no timeout, zero wall overrun, `DeadlineOverrunWithinLimit=true`, typed failures,
  and no routed outputs. Run 1's artifact runtime was 118.523271s and its
  shared deadline expired exactly after 118.000s. Its attempts recorded
  spacing 5 for 54.140904s ending at a ResourceGraph local slice, spacing 6 for
  23.114764s ending at a Candidate local slice, and row-beam for 19.155558s
  before Guide observed global expiry.

**Design decision or deviation:** Publication time is reserved inside each
wall ceiling. The capture watchdog is deliberately outside that ceiling only
as a failure detector; `EvaluateRun` cannot use it as runtime allowance. The
final zero-overrun matrix verifies bounded completion, not physical routing.

**Open risks:** RCA4 still has no capacity-one legal route, and CLA4 still
exhausts its 118-second router deadline without a route. Neither circuit has a
qualifying 512/512 physical artifact.

**Next action:** Preserve the verified 8/23/118-second router envelope while
changing RCA4 assignment compatibility and reducing CLA4 resource-graph,
candidate, and guide work. Keep the guide `NOT ACCEPTED` until both circuits
pass 2/2.

### 2026-07-21 - RRF-074 - Close explicit terminal and transactional placement proofs

**Status before:** RRF-021 and RRF-022 In Progress because cluster-zero had no
fixed coordinate fixture and rollback had not been exercised across every
deterministic placement recipe
**Status after:** RRF-021 and RRF-022 Implemented; overall `NOT ACCEPTED`

**Intent:** Replace derived ordering evidence with a fixed physical fixture and
prove that every placement recipe discards all candidate-local state after a
late rejection.

**Hypothesis:** An expected list of terminal names, signals, coordinates, and
rotations will fail if cluster index zero is treated as missing. Forcing a
rejection after boundary-capacity construction in every generated recipe will
expose any placement or local-claim state reused by a subsequent attempt.

**Intended behavior:** The RCA4 packed placement begins with the exact cluster-0
terminal sequence `InputA0`, `InputB0`, and `InputCarryIn` at their fixed
coordinates, followed by the corresponding terminals for clusters 1 through
3. Row-beam, unpacked, direct-only, configured-packing, graph-beam, and both
spacing alternatives may all reject after construction without mutating the
input netlist or changing a later placement/local-claim result.

**Focused tests and expected evidence:** The explicit terminal fixture and the
nine-recipe forced-rejection loop must pass together. Two accepted placements
after each rejection must have identical gate coordinates and local claims.

**Code and documentation changed:** Tests only. The former terminal-order test
now asserts the complete expected RCA4 terminal geometry and cluster-zero
membership. The placement-boundary suite enumerates all primary and deferred
requests from `BuildPlacementGenerationPlan`, forces a late rejection, checks
the netlist snapshot, and compares two clean subsequent results.

**Behavior changed:** No compiler behavior changed after the RRF-073 physical
matrix. This slice closes missing proof obligations for existing transactional
placement behavior.

**Commands:**

```bash
python3 -m unittest \
  Tests.test_local_first_router.LocalFirstRouterTests.testPackedTerminalPlacementGroupsInputsByNandCluster \
  Tests.test_placement_boundary_feasibility.PlacementBoundaryFeasibilityTests.testEveryPlacementRecipeRollsBackRejectedCandidateState \
  -v
python3 -m compileall -q Compiler SVDecoder SchemEncoder Tests
python3 -m unittest \
  Tests.test_authoritative_planner \
  Tests.test_channel_planner \
  Tests.test_legacy_shims_fail_fast \
  Tests.test_local_first_router \
  Tests.test_logic_optimization \
  Tests.test_physical_cells \
  Tests.test_pipeline_artifact_integrity \
  Tests.test_placement_boundary_feasibility \
  Tests.test_redstone_simulation \
  Tests.test_resource_graph \
  Tests.test_router_acceptance_harness \
  Tests.test_router_reliability \
  Tests.test_routing_architecture \
  Tests.test_routing_resources
git diff --check
```

**Evidence and artifact paths:** The named tests are source-controlled evidence.
The current physical evidence remains the RRF-073 manifest at
`Output/Acceptance/2026-07-21/RouterV10Recovery/AcceptanceManifest.json` because
RRF-074 changes tests and documentation only, not compiler or router source.

**Result and measurements:** The two named tests passed 2/2 in 4.544s.
Compileall passed, and the explicit 14-module scale-excluded suite passed
159/159 in 37.312s.

**Design decision or deviation:** No architecture changed, so the normative
design receives only a checkpoint correction rather than a new routing rule.
Historical RRF-020/RRF-021 notes remain unchanged; this append-only entry
closes their recorded proof gaps.

**Open risks:** Transactionality and terminal order now have direct proof, but
RCA4 and CLA4 remain physically unrouted 0/2. Test-only closure cannot satisfy a
scale acceptance gate.

**Next action:** Continue from the RRF-073 RCA4 capacity-one conflict and CLA4
guide/resource evidence without changing the verified process envelope.

### 2026-07-22 - RRF-075 - Negotiate route trees and isolate sparse-region regression

**Status before:** The authoritative path relied on bounded candidate domains
and exact assignment; RCA4 and CLA4 remained unrouted in the RRF-073 matrix.

**Status after:** Negotiated route-tree scaffolding is implemented and focused
tests pass; overall `NOT ACCEPTED` because current RCA4 retains ten electrical
conflicts and CLA4 is gated behind it.

**Intent:** Replace one-shot global compatibility search with deterministic
provisional routing, present/history congestion costs, reusable route trees,
lazy detailed graph regions, and typed congestion feedback to placement.

**Implemented behavior:** `NegotiatedRoutingPolicy` supplies the four-track
tile pitch, 32-iteration bound, three-pass stagnation bound, three placement
feedback rounds, and two-times area ceiling. `PlanNegotiatedRouteTrees`
produces provisional plans and overflow history. `RoutingResourceGraph`
supports cached region construction, and Rust `RoutingContext.AddRegion`
deduplicates incrementally exposed nodes and edges. Routing diagnostics carry
algorithm, overflow, reroute, graph-cache, and failure-cut evidence. No
circuit name, generated NAND name, or fixed net count selects behavior.

**Focused verification:** The latest focused routing set passes 48/48 in
1.075s, the complete FullAdder diagnostic passes, and `git diff --check`
passes. These are regression checks, not physical scale acceptance.

**Physical evidence:** Earlier negotiated RCA4 artifacts at
`/tmp/rc-neg-rca4-release2.yoSJWf/RippleCarryAdder4.PhysicalDesign.json` and
`/tmp/rc-neg-rca4-release.lJy6Ef/RippleCarryAdder4.PhysicalDesign.json`
completed 512/512 rows in about 15.1s with overflow `[66, 0]`, placement
fingerprint `56b5cd84a819a882`, routing fingerprint `91319270745ab338`, and a
26,978-node/141,282-edge cached graph.

The current artifact at
`/tmp/rca4-current-gate/RCA4.RoutingFailure.json` uses placement fingerprint
`4fbfb60378c2b189`, caches only 9,792 nodes and 47,552 edges, progresses through
overflow `[124, 10, 10, 10, 10]`, and ends with
`GlobalCongestionUnresolved` involving `NandNet21` at about 22.96s. The ten
remaining conflicts are electrical claims.

**Design conclusion:** Lazy construction is required, but the active region is
too narrow. A detailed halo tile must equal
`4 * Technology.TrackPitch`, expand on route-tree boundary touch or stagnant
overflow even when a path exists, and add only the newly exposed delta. Repair
must retain clean trunks and target branches instead of replacing the whole
net. Repeater legality must ultimately be part of detailed search state.

**Next action:** Restore RCA4 2/2 with boundary-triggered region expansion and
branch-level repair. Preserve the failure artifact and do not begin CLA4
acceptance until RCA4 passes.

### 2026-07-22 - RRF-076 - Implement dynamic regions and branch-preserving repair

**Status before:** RRF-075 had negotiated scaffolding but a narrow static
region, whole-net repair, post-route repeater rejection, and a transactional
placement feedback regression.

**Status after:** The requested implementation and lightweight gates are
complete; overall `NOT ACCEPTED` because RCA4 still fails and CLA4 remains
gated.

**Implemented behavior:** Placement feedback now skips empty relocation cuts,
preserves every typed congestion-cut owner, bounds three relocation rounds and
two-times packed-area growth, routes packed recipes deterministically, and
advances after a local adaptive timeout without replacing the absolute
deadline. `NegotiatedRegionState` owns exact 12-block halo columns and active
tiles. One-sided expansion rebuilds only the implicated signal region and adds
only unseen graph nodes and edges. `NegotiatedRouteTreeState` retains clean
target paths and prunes conflicted branch claims. Rust exposes a typed detailed
route-tree result and searches `(Position, IncomingDirection,
RemainingStrength)` states. Physical diagnostics include region, branch,
repeater, native-status, and cumulative-cut evidence.

**Verification:**

```text
python -m unittest discover -s Tests -p 'test_*.py'
176 run, 174 passed, 2 scale tests skipped, 19.759 s

cargo test --manifest-path RustRouting/Cargo.toml --release
25 passed

git diff --check
passed
```

Fresh FullAdder evidence at
`/tmp/rrf076-fulladder-final/FullAdder.PhysicalDesign.json` completed in
0.477 seconds with 8/8 rows, zero conflicts, overflow peak one, and no
fallback.

**RCA4 result:**
`/tmp/rrf076-final2-rca4/RippleCarryAdder4.RoutingFailure.json` records four
distinct placement attempts. The primary placement reports an eight-signal
capacity-one cut. Relocated packing fails typed repeater access for
`NandNet18`; direct-only packing spends its local slice on the same signal.
Configured packing exhausts eight routed trees for `NandNet21` because every
materialized tree has a self-claim conflict. No RCA4 physical design or
512-row truth table was published.

**Decision:** The sequential physical matrix stopped at RCA4, so CLA4 was not
launched. Historical `56b5cd84a819a882` RCA4 artifacts remain comparison
evidence only.

**Next action:** Make the primary native multi-target tree builder retain exact
rooted path states instead of first collapsing them into the compatibility
node-set result; the current diagnostic fallback cannot complete `NandNet18`
inside its local slice. Then rerun RCA4 2/2 before any CLA4 acceptance attempt.

### 2026-07-22 - RRF-077 - Preserve rooted native paths and expose fixed-access failure

**Implemented behavior:** Detailed native tree construction no longer builds a
compatibility node set and reconstructs powered paths afterward. It carries
rooted direction and signal strength through retained starts, reserves
repeaters during path search, returns exact target paths, and includes complete
target portal/access chains in the searched tree. Branch repair preserves the
real producer root. Mandatory access self-conflicts now hard-fail as
`NoPinAccessPattern` with exact resources and locations. The two-times packed
area ceiling applies to every packed generator. Acceptance policy validation
now recognizes `physical-design-v11-negotiated-route-trees`, and the harness
stops on the first failed run.

**Verification:**

```text
python -m unittest discover -s Tests -p 'test_*.py'
180 passed, 2 scale tests skipped, 19.892 s

cargo test --manifest-path RustRouting/Cargo.toml --release
25 passed

cargo fmt --manifest-path RustRouting/Cargo.toml -- --check
passed

git diff --check
passed
```

**Physical result:**
`/tmp/rrf077-final-rca/RippleCarryAdder4.RoutingFailure.json` finishes in
21.064387 seconds. The primary placement retains its eight-signal assignment
cut. Relocated fingerprint `492d79c22f7e6500` now reports the more precise
`NoPinAccessPattern` failure for mandatory access inside one packed nine-NAND
cluster. Baseline packed area is 465, the maximum is 930, and packed repair
alternatives at 1,456--1,457 are rejected. No RCA4 physical design or 512-row
truth table was published; CLA4 remains gated.

**Next action:** Move exact mandatory-access legality into the packed
intra-cluster placement search so it can choose legal pin orientations and
offsets inside the 930-area envelope. Whole-cluster unstacking and cluster
splitting were measured above the ceiling and were not retained.

## RRF-078 implementation checkpoint

- Removed the profile-count legacy-routing threshold and the independent
  per-terminal portal reservation path from negotiated routing.
- Added bounded exact self-legal portal products, continuation-aware repeater
  goal states, and local claim-derived rooted-tree blockages.
- Routed retained packed placements before allocating time to deferred unpacked
  generation; the absolute deadline and publication reserve are unchanged.

Verification retained 181 passing scale-excluded Python tests with two skips,
25 passing release Rust tests, and clean format/diff checks. The fresh RCA4
artifact `/tmp/rca-request-100ms/RippleCarryAdder4.RoutingFailure.json` reaches
overflow `92, 22` but expires before zero; RCA4 and CLA4 remain unaccepted.

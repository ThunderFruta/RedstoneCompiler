# Router reliability guide

**Status: NOT ACCEPTED**

The production strategy is `new-router-first`. The frozen compatibility router
is a regression oracle only: production never invokes it automatically, and a
compatibility result cannot satisfy a new-router acceptance gate.

This guide explains how to run and judge the router. The
[reliability design](RouterReliabilityDesignDoc.md) defines how the v10 router
must behave, and the
[implementation notes](RouterReliabilityImplementationNotes.md) preserve the
append-only record of what was changed and observed.

## 2026-07-22 negotiated-router checkpoint

The active implementation now follows the
[negotiated route-tree design](NegotiatedRouteTreeRouter.md). It provisions
coarse routes with temporary overlap, carries present and history congestion
costs, and incrementally exposes detailed resource regions. Routing behavior
must remain circuit agnostic: circuit names, generated NAND names, and fixed
net counts are diagnostics only and must never select an algorithm.

This working tree is **NOT ACCEPTED**. The latest RCA4 artifact at
`/tmp/rca4-current-gate/RCA4.RoutingFailure.json` reports overflow progression
`[124, 10, 10, 10, 10]`, 9,792 cached graph nodes, 47,552 cached graph edges,
and a final `GlobalCongestionUnresolved` failure involving `NandNet21` after
about 22.96 seconds. The remaining ten conflicts are electrical claims.

Earlier checkpoint artifacts
`/tmp/rc-neg-rca4-release2.yoSJWf/RippleCarryAdder4.PhysicalDesign.json` and
`/tmp/rc-neg-rca4-release.lJy6Ef/RippleCarryAdder4.PhysicalDesign.json` reached
zero overflow and 512/512 rows in about 15.1 seconds with 26,978 cached nodes
and 141,282 edges. Their matching placement fingerprint
`56b5cd84a819a882` and route fingerprint `91319270745ab338` are historical
working-tree evidence, not a current acceptance result. The graph-size gap
supports the current diagnosis: detailed regions are too narrow and are not
expanding when surviving branches touch a boundary or stagnate.

The long RRF-073 matrix below remains the last complete, durable 5+2+2 record.
It is retained verbatim so that historical results are not confused with the
current working-tree state. RCA4 must pass 2/2 before CLA4 is attempted.

## Current verdict

The current durable acceptance matrix was captured on 2026-07-21 at Git revision
`4c91d1b953dd921f665ef6004cd2c79178c49894` with a dirty working tree. The
dirty state is material: reproduce a result from the recorded revision and
diff, not from the revision alone. Its current source of truth is
`Output/Acceptance/2026-07-21/RouterV10Recovery/AcceptanceManifest.json`, whose
status is `FAILED` and whose `Accepted` field is false. It started at
`2026-07-21T20:36:14.510691+00:00`, completed at
`2026-07-21T20:40:55.885542+00:00`, and has SHA-256
`35e5b4c3449dcdee257de920fd7e99442ed3a5e385b3815155cdafc82955c395`.
Earlier matrices are preserved at
`Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF069/`,
`Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF071/`,
`Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF072/`, and
`Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF073/`.

| Design | Evidence | Result | Acceptance consequence |
| --- | --- | --- | --- |
| FullAdder, v8 historical oracle | `Output/BenchFullAdderRun/FullAdderRunNew.PhysicalDesign.json` | `physical-design-v8-openroad-style`; 8/8 truth-table rows; zero conflicts and unresolved claims; overflow peak 0; run-summary runtime 6.114104s; no fallback | Useful regression oracle, but not v10 acceptance |
| FullAdder, fresh v9 | `/tmp/fa_new.log` | `Portal:RuntimeBudgetExceeded`; 119.197s allowance exhausted after 119.275s following 49 adaptive attempts | Failed |
| RippleCarryAdder4, fresh v9 | `/tmp/rca4_new.log` | `Track:RuntimeBudgetExceeded`; 117.674s allowance exhausted after 142.850s | Failed and exceeded its deadline materially |
| CarryLookaheadAdder4, fresh v9 | `/tmp/cla_new.log` | Packed candidates rejected for exact electrical isolation, followed by `Candidate:RuntimeBudgetExceeded`; 107.774s allowance exhausted after 121.118s | Failed |
| FullAdder direct row diagnostic, earlier v10 checkpoint | Live terminal output only; no durable path | 8/8 truth-table rows; zero conflicts; 1.713s | Correctness diagnostic only; the invocation and artifacts were not retained |
| FullAdder `new-router-first`, earlier v10 checkpoint | Live terminal output only; no durable path | 8/8 truth-table rows; zero conflicts; zero unresolved claims; overflow peak 1; no fallback; selected row-beam fingerprint `a8dc7c20513bcfc3`; 17.186s | **FAILED:** 17.186s exceeds the exact 10.000s ceiling, and this was one non-durable ad hoc run rather than five consecutive harness runs |
| FullAdder, historical v10 acceptance snapshot | `Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF069/AcceptanceManifest.json` and `FullAdderRun1` through `FullAdderRun5` beneath that directory | **5/5 PASSED**; wall times 6.360420s, 6.269970s, 6.165004s, 6.079007s, and 6.135146s; every run passed 8/8 rows with zero conflicts and unresolved claims, overflow peak 1, authoritative-exact validation, native-parallel simulation, and no fallback | **PASSED only for the recorded source snapshot:** deterministic fingerprint `a8dc7c20513bcfc3`, ownership, route metrics, and emitted-design digest matched across all five runs |
| RippleCarryAdder4, historical v10 acceptance snapshot | Same historical snapshot; `RippleCarryAdder4Run1` and `RippleCarryAdder4Run2` | Both processes reached the external ceiling at 25.044030s and 25.052964s, returned 124, and emitted no qualifying schematic, truth table, or physical-design artifact | **NOT ACCEPTED** |
| CarryLookaheadAdder4, historical v10 acceptance snapshot | Same historical snapshot; `CarryLookaheadAdder4Run1` and `CarryLookaheadAdder4Run2` | Both processes reached the external ceiling at 120.096740s and 120.113080s, returned 124, and emitted no qualifying schematic, truth table, or physical-design artifact | **NOT ACCEPTED** |
| FullAdder, current sliced diagnostic | `/tmp/rrf-fulladder-sliced/FullAdderSliced.PhysicalDesign.json` and `.TruthTable.txt` | `new-router-first`; 0.942832s; 8/8 rows; zero conflicts and unresolved claims; overflow peak 1; no fallback | **Diagnostic pass only:** one `/tmp` run cannot satisfy the required fresh 5/5 gate |
| RippleCarryAdder4, measured-start diagnostic | `/tmp/rrf-rca-pass-gate/RCA4PassGate.RoutingFailure.json` | Failed cleanly at 24.800356s without deadline expiry and with 0.209s remaining after routing unpacked spacings 5 and 6; the measured start gate declined to launch spacing 7 | **NOT ACCEPTED:** bounded failure is useful evidence, not a routed result |
| RippleCarryAdder4, latest wider-unpacked diagnostic | `/tmp/rrf-rca-wide-second/RCA4WideSecond.RoutingFailure.json` | Failed at 25.166941s; the shared deadline expired at 25.159888s after spacing 5 failed in 10.454291s and spacing 7 then consumed 8.980902s before `RuntimeBudgetExceeded` | **NOT ACCEPTED:** current RCA4 still has no qualifying 512-row routed result |
| FullAdder, historical `RRF-070` acceptance series | `Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF071/AcceptanceManifest.json`; `FullAdderRun1` through `FullAdderRun5` | **5/5 PASSED**; wall times 1.193751s, 1.163705s, 1.048207s, 1.040231s, and 1.038901s; reported router times 1.100603s, 1.073153s, 0.961148s, 0.961509s, and 0.960259s | **PASSED for the pre-RRF-071 snapshot** |
| RippleCarryAdder4, historical `RRF-070` acceptance series | Same pre-RRF-071 snapshot; `RippleCarryAdder4Run1` and `RippleCarryAdder4Run2` | **0/2**; run 1 timed out with return 124 at 26.038323s; run 2 returned 1 without a harness timeout at 25.451761s; both retained typed routing failures and neither emitted routed artifacts | **NOT ACCEPTED** |
| CarryLookaheadAdder4, historical `RRF-070` acceptance series | Same pre-RRF-071 snapshot; `CarryLookaheadAdder4Run1` and `CarryLookaheadAdder4Run2` | **0/2**; both timed out with return 124 at 121.131519s and 121.147175s; neither emitted a routing-failure artifact or routed artifacts | **NOT ACCEPTED** |
| FullAdder, historical `RRF-071` acceptance series | `Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF072/AcceptanceManifest.json`; `FullAdderRun1` through `FullAdderRun5` | **5/5 PASSED**; wall times 1.002021s, 1.002928s, 1.000096s, 0.986873s, and 0.990030s; reported router times 0.923125s, 0.927339s, 0.923784s, 0.913400s, and 0.914244s | **PASSED for the pre-RRF-072 snapshot** |
| RippleCarryAdder4, historical `RRF-071` acceptance series | Same pre-RRF-072 snapshot | **0/2**; both self-exited with return 1 and typed routing failures at 22.269156s and 22.272633s wall time; neither emitted routed artifacts | **NOT ACCEPTED** |
| CarryLookaheadAdder4, historical `RRF-071` acceptance series | Same pre-RRF-072 snapshot | **0/2**; both self-exited with typed routing failures at 120.866246s and 120.732578s wall time; neither emitted routed artifacts | **NOT ACCEPTED** |
| FullAdder, historical `RRF-072` pre-envelope series | `Output/Acceptance/2026-07-21/RouterV10RecoverySnapshotPreRRF073/AcceptanceManifest.json`; `FullAdderRun1` through `FullAdderRun5` | **5/5 PASSED**; wall times 1.041832s, 1.033418s, 1.030760s, 1.035767s, and 1.039969s | **PASSED for the pre-RRF-073 snapshot** |
| RippleCarryAdder4, historical `RRF-072` pre-envelope series | Same pre-RRF-073 snapshot | **0/2**; both returned typed failures at 20.487121s and 20.475821s; neither emitted routed artifacts | **NOT ACCEPTED** |
| CarryLookaheadAdder4, historical `RRF-072` pre-envelope series | Same pre-RRF-073 snapshot | **0/2**; both returned typed failures at 121.725966s and 121.425092s, more than one second beyond the immutable 120-second wall ceiling | **NOT ACCEPTED:** this matrix exposed the process-envelope gap closed by RRF-073 |
| FullAdder, current `RRF-073` acceptance series | Current manifest; `FullAdderRun1` through `FullAdderRun5` | **5/5 PASSED**; wall times 1.034566s, 1.034114s, 1.018414s, 1.020388s, and 1.036595s; reported router times 0.954818s, 0.957215s, 0.941270s, 0.944164s, and 0.957889s; all runs passed 8/8 rows with zero conflicts and unresolved claims and overflow peak 1 | **VERIFIED FullAdder sub-gate:** fingerprint `a8dc7c20513bcfc3`, ownership, route metrics, and digest `850dcc984e95c26fd598c5c06da84127f110e47eacaa142be39c6947abbef820` match across all five runs |
| RippleCarryAdder4, current `RRF-073` acceptance series | Current manifest; `RippleCarryAdder4Run1` and `RippleCarryAdder4Run2` | **0/2**; both self-exited with return 1, no timeout, zero wall overrun, and typed routing failures at 18.828730s and 18.848981s; neither emitted routed outputs | **NOT ACCEPTED:** deadline enforcement passed, but bounded failure is not a routed result |
| CarryLookaheadAdder4, current `RRF-073` acceptance series | Current manifest; `CarryLookaheadAdder4Run1` and `CarryLookaheadAdder4Run2` | **0/2**; both self-exited with return 1, no timeout, zero wall overrun, and typed routing failures at 119.268383s and 119.248057s; neither emitted routed outputs | **NOT ACCEPTED:** deadline enforcement passed, but both physical routes failed |
| Current working-tree recovery, `RRF-065` through `RRF-074` | Current manifest plus focused Python/Rust evidence | Compileall, Rust formatting, and diff checks pass; focused routing Python passes 95/95; the explicit terminal/transactional proof passes 2/2; the 14-module scale-excluded Python suite passes 159/159 in 37.312s; the exact eight-file Rust release gate passes 25/25 | **Deadline enforcement and FullAdder Verified; overall NOT ACCEPTED:** RCA4 and CLA4 remain physically unrouted |

The `/tmp` logs and old live observations remain historical investigation
evidence. The current manifest supersedes the ad hoc FullAdder conclusions
without deleting them and verifies the current FullAdder 5/5 sub-gate. All four
preserved snapshot directories remain evidence only for their recorded
dirty-tree states. The complete v10 verdict remains
**NOT ACCEPTED** because FullAdder cannot substitute for the failed RCA4 and
CLA4 gates.

The two pre-matrix RCA4 diagnostics came from evolving dirty-tree checkpoints
and made decisions from measured wall-clock work. Small runtime and source-diff
changes altered whether the next spacing started and whether expiry was
observed. RRF-072 made the expensive placement, validation, graph, guide,
rip-up, Python/native assignment, and conflict loops interruptible, but its
pre-envelope CLA4 runs still crossed the wall ceiling by more than one second.
RRF-073 closes that process-envelope gap without changing physical behavior:
all four current scale failures stop themselves with typed artifacts and zero
wall overrun, but RCA4 and CLA4 still route 0/2.

The harness separates the router deadline from the immutable process wall
ceiling with a 2.0-second `PublicationReserve`: router deadlines are 8 seconds
for FullAdder, 23 seconds for RCA4, and 118 seconds for CLA4 inside unchanged
10-, 25-, and 120-second acceptance ceilings. The watchdog remains ceiling plus
2 seconds only to capture a process that fails to stop. The manifest records
the reserve and effective router deadline. This is neither fallback nor a
runtime extension.

## V10 implementation checkpoint

The following recovery structure is present and has focused lightweight test
coverage. This is an implementation checkpoint, not physical acceptance:

- production policy `physical-design-v10-routability-feedback` enables
  `Placement.EnableRoutingFeedback` and
  `GlobalRouting.EnableCapacityAwareGuides`; the compatibility policy keeps
  both disabled;
- exact placement legality is applied during pin-aligned packing, row-beam
  packing, structural reuse, and retained-candidate validation;
- deterministic `PcbPlacementCandidate` records are fingerprinted, scored,
  deduplicated, and routed in order until one passes its routed-validation
  callback or the shared deadline expires;
- row-beam and unpacked placement recipes are the bounded primary pair;
  configured graph-beam, direct-only, and spacing alternatives are constructed
  lazily only after retained primaries fail, under the original deadline;
- `BoundaryDemandRecord` and `BoundaryCapacityRecord` values are serialized and
  boundary overflow ranks before footprint density;
- `RoutingEscalationState`, stable work fingerprints, offender-only candidate
  retention, empty-intersection-safe claim release, and `RoutingDeadline` are
  integrated through placement and authoritative routing;
- portal searches begin at graph-valid terminal access nodes, all
  placement-owned local claims (including partial claims) enter exact
  assignment as base ownership, greedy reservation variants select physically
  different portal slots, and repeated reserved work may advance once to
  bounded unreserved portals on the same absolute deadline;
- immutable raw portal geometry is cached across reservation-only retries when
  every geometry-affecting control is identical; reservation filtering never
  mutates that cache;
- bounded unreserved portals remain a mode of the production new router. They
  are not the compatibility router, do not relax capacity-one ownership, and
  cannot reset the deadline; their route-tree request set is explicitly capped
  and ordered for portal, axis, and lane diversity;
- route-candidate materialization applies exact same-net claim validation after
  source access, target access, local claims, and the native tree are combined;
  a support required beneath one dust node may not be occupied by another dust
  node of that same candidate;
- native assignment returns deterministic typed conflict signals for failed
  higher-order matching, successful backtracking clears discarded-branch
  conflicts, and support material conflicts exactly with another signal's wire
  or required air in both Python and Rust;
- portal reservation treats each layer as a separate physical domain, permits
  an individual terminal layer to be empty when another layer is reachable,
  and derives a minimum routing-layer count from the highest stacked access;
- lane- and layer-diversity retries retain and deduplicate candidates from
  compatible earlier geometry while adding genuinely new lanes or layers;
  reservation-changing retries do not reuse candidates whose portal ownership
  assumptions changed;
- placement ranking begins with a bounded routability-work estimate; pressured
  retained candidates can trigger lazy wider-unpacked generation, each
  placement receives a bounded local adaptive slice, and measured pass/start
  gates avoid launching work that cannot fit while preserving the one absolute
  deadline;
- `QualityTarget="first-legal"` skips result-only shape optimization after a
  legal route is found while retaining required congestion repair, DRC,
  repeater validation, and simulation;
- one finite-positive CLI deadline is clamped into the effective immutable
  policy, and routing progress is not reported complete after assignment:
  cleanup, compaction, ownership, repeater checks, routed validation, and
  simulation must finish first under the same absolute deadline;
- placement construction, compaction, feedback, isolation, resource-graph,
  portal/tree, assignment, conflict, validation, repeater, claim, guide, and
  rip-up loops publish periodic work checks; placement generation reserves 20%
  of the shared routing deadline and publishes a candidate only after bounded
  feedback completes;
- native assignment starts its timer before PyO3 payload extraction and checks
  chunked claim-mask construction, union, conflict, and sorting work while
  retaining the exact eight-file Rust ownership split;
- advance-placement failures bypass local-claim recovery, permitted recovery
  keeps the original remaining slice, and unexpected failures retain their
  placement and escalation histories;
- the acceptance process gives routing an explicit 2.0-second publication
  reserve inside, rather than beyond, each immutable wall ceiling and records
  both values in the manifest;
- frozen routed geometry remains an immutable routing obstacle but is excluded
  from the template-only electrical-isolation geometry used to validate cell
  templates;
- typed routing failures write `routing-failure-v1` diagnostics without
  changing the nonzero result; and
- native routing is split by responsibility and its bounded batch/assignment
  results report `DeadlineExceeded` and completed work.

The hard-boundary `RRF-043` selector passes 6/6 focused tests (five boundary
tests plus the existing overflow-order test). The last completed exact
default-feature Rust gate,
`cargo test --manifest-path RustRouting/Cargo.toml --release`, passed 25/25.
The final focused routing Python gate passed 95/95, and the explicit
terminal/transactional proof passed 2/2. The 14-module scale-excluded Python
suite passed 159/159 in 37.312s; compileall, Rust
formatting, the 25/25 Rust gate, and the final diff check also passed. The
deterministic harness completed the current nine-run matrix within every
immutable process ceiling. FullAdder passed 5/5 and is `Verified`; RCA4 and
CLA4 both failed 0/2. `RRF-072` is Implemented, and the `RRF-073` physical
deadline-enforcement sub-gate is Verified. Neither status weakens the failed
routability gates. `RRF-074` closes the explicit cluster-zero terminal fixture
and all-recipe transactional rollback proof without changing the compiler used
by the RRF-073 physical matrix.

## Acceptance matrix

| Gate | Required runs | Correctness | Runtime ceiling | Current state |
| --- | ---: | --- | ---: | --- |
| FullAdder | 5 consecutive | 8/8 rows; zero conflicts and unresolved claims; overflow peak <= 1 | 10s each | **VERIFIED:** current matrix passed 5/5 at 1.018414s-1.036595s wall time with deterministic fingerprint, ownership, route metrics, and emitted design |
| RippleCarryAdder4 | 2 consecutive | 512/512 rows; zero conflicts and unresolved claims; overflow peak <= 1 | 25s each | **NOT ACCEPTED:** current matrix self-exited 0/2 at 18.828730s and 18.848981s with typed failures, no timeout or wall overrun, and no routed outputs |
| CarryLookaheadAdder4 | 2 consecutive | 512/512 rows; zero conflicts and unresolved claims; overflow peak <= 1 | 120s each | **NOT ACCEPTED:** current matrix self-exited 0/2 at 119.268383s and 119.248057s with typed failures, no timeout or wall overrun, and no routed outputs |

For a fixed seed, qualifying repeated runs must have identical placement
fingerprints, resource ownership, route metrics, and emitted physical design.
Runtime is measured independently and need not be identical. A timeout,
compatibility result, fallback artifact, projected measurement, or relaxed
validator fails the gate.

Material use and density remain recorded engineering metrics. They never
replace electrical legality, exact ownership, repeater validity, DRC, or
truth-table simulation.

## Running a recovery build

Use the acceptance harness so the exact nine-run matrix is sequential, each
router deadline leaves the explicit publication reserve inside its immutable
wall ceiling, and the evidence manifest is updated after every run. Inspecting
the plan does not launch the compiler and does not qualify as evidence:

```bash
python3 Scripts/Routing/RunRouterAcceptance.py --date 2026-07-21 \
  --output-root Output/Acceptance --python /usr/bin/python3 --dry-run
```

After the lightweight gates pass, run the physical matrix once, without other
scale routing jobs:

```bash
python3 Scripts/Routing/RunRouterAcceptance.py --date 2026-07-21 \
  --output-root Output/Acceptance --python /usr/bin/python3
```

The harness writes
`Output/Acceptance/<date>/RouterV10Recovery/AcceptanceManifest.json`, per-run
stdout and stderr logs, hashes for every expected artifact, source and
environment identity, the exact command, and deterministic comparisons of the
placement fingerprint, resource ownership, route metrics, and canonicalized
emitted design. It rejects a timeout, compatibility or fallback result,
runtime overrun, conflict, unresolved claim, overflow above one, missing or
failure artifact, or repeated-run mismatch. It records the 2.0-second
publication reserve and resulting 8/23/118-second router deadlines; its
ceiling-plus-2 watchdog is capture-only and cannot make an over-ceiling run
pass.

The command must exit successfully and emit a `.PhysicalDesign.json`, truth
table, and final schematic artifact. A typed routing failure exits nonzero and
writes `<OutputName>.RoutingFailure.json` with
`SchemaVersion="routing-failure-v1"`; the diagnostic artifact does not make the
compile successful. Failure-artifact write errors are reported separately and
must never conceal the original routing exception.

## Verification order

Do not start scale routing until the lightweight gates pass:

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
RC_RUN_SCALE_TESTS=1 python3 -m unittest Tests.test_scale_routing -v
```

The explicit list above is the scale-excluded lightweight gate. Run the scale
command by itself and retain its complete output with the physical artifacts.
The approved default-feature Cargo command is the native acceptance gate and
currently passes 25/25. A
`--no-default-features` run may remain useful during native development, but it
does not replace the exact command above. Only after these lightweight checks
pass should the physical harness be launched:

```bash
python3 Scripts/Routing/RunRouterAcceptance.py --date 2026-07-21 \
  --output-root Output/Acceptance --python /usr/bin/python3
```

## Reading a failure

| Failure evidence | Interpretation | Required response |
| --- | --- | --- |
| Electrical-isolation rejection during placement | The candidate was never physically legal | Reject it using the exact placement oracle; never relax final validation |
| `NoCandidates` | At least one affected signal has no viable route geometry | Regenerate geometry for the affected signals, then relocate their affected cluster if the fingerprint repeats |
| Pairwise or higher-order incompatibility | Candidates exist but cannot coexist under capacity-one ownership | Change portal reservation, then lane diversity, then a physically available layer; skip states that do not alter resources |
| `SelfClaimConflict` during materialization | One candidate's own dust, support, air, or electrical claims conflict after all access and tree nodes are combined | Reject that candidate before assignment; never bypass the exact check in production and continue with other retained geometry under the same deadline |
| `NoPinAccessPattern` with `mandatory-access-self-conflict` | Fixed producer/consumer access geometry conflicts before a negotiated branch can be added | Feed the exact signal and locations back to packed intra-cluster placement; do not widen routing budgets or label the failure as repeater infeasibility |
| Reserved portal work repeats | Reservation produced equivalent effective work | Advance once to bounded unreserved portals on the same absolute deadline; this remains production `new-router-first`, not compatibility routing |
| `RuntimeBudgetExceeded` with the shared deadline expired | The one absolute compile deadline expired | Stop further candidates, preserve partial diagnostics, and report the stage; do not restart or silently enlarge the deadline |
| Local adaptive slice expired while the shared deadline remains live | Only the current placement slice ended | Publish no partial candidate and advance to the next deterministic placement under the same deadline object |
| Repeated candidate/conflict fingerprint | The retry is equivalent to prior work | Leave the Cartesian retry loop and move to affected-cluster relocation or the next retained placement |
| Compatibility success after new-router failure | Only the historical implementation succeeded | Keep the new-router result failed; never label the compile accepted |

When available, inspect the failure artifact in this order:

1. `Failure.Stage`, `Failure.Reason`, and the deadline fields.
2. Effective policy controls and effective layer capacity.
3. Placement-attempt and escalation histories.
4. Candidate, conflict, and resource-graph fingerprints.
5. Affected nets, saturated resources, and physical locations.

## Evidence record

Every dated implementation-note entry that makes a performance or correctness
claim must retain:

- Git revision and whether the working tree was dirty;
- exact command, environment variables, routing thread count, policy version,
  technology version, seed, and deadline;
- exit result and paths to logs, failure diagnostics, physical design, truth
  table, and schematic;
- placement and resource fingerprints, attempted escalation states, stage
  timings, runtime overrun, conflicts, unresolved claims, and overflow peak;
- simulation row count and result; and
- whether the result is diagnostic, historical, or acceptance evidence.

RRF-078 tightens `NoPinAccessPattern`: it now means no self-legal net-wide
access/portal product exists. A nonzero overflow progression such as `92, 22`
is diagnostic progress only and does not satisfy the zero-conflict gate.

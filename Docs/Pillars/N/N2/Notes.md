# N2 notes

Working notes for [N2](N2.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- None recorded.

## Open questions

- None recorded.

## Working notes

### 2026-09-06 bounded physical-rule-contract record

- **Identity and scope.** Verification was performed on `Physical-Rules` HEAD
  `ffd8c76430eccb7f4ae1e06b4a777a4ed6ac3b9f`, above Physical checkpoint
  `2f69160a85f1ac800243698aebec4f172164b9f8`.  Joint references
  `eb3ef79e2b53efd47bbd90d138fdeba2eb405e98` and
  `ea77a28afde1168d4243028f0a00a4ebb869549e` share that merge base.  This is
  a source-local evidence record only: no commit, dependency integration,
  history update, or live-runtime action occurred.
- **Compatibility transport.** The narrowly reviewed change makes the shared
  profile producer reject omitted or length-mismatched explicit selected
  access, retain complete explicit paths, and preserve default ordinary-call
  behavior.  It also adds paired default-empty domain/witness fingerprints to
  `PlacementAccessFabric` and serializes both exact fields.  Joint confirmed
  its consumer compatibility for precisely those two changes without requesting
  a companion edit; it did not consume or test this uncommitted worktree.
- **Conformance and non-duplication.** Independent catalog tests check the
  technology/model/access identity boundary, transform occupancy-adjacent
  wire/support/electrical claims with local arithmetic, preserve the supported
  empty required-air scope, and reject omitted/truncated explicit access.  They
  complement rather than repeat existing occupancy, support, power,
  same-signal/foreign-conflict, proof/domain-completeness, and rendering tests.
- **Historical pre-freshness disposition.** The previously recorded 13 compatibility
  blockers (10 profile keyword callers and 3 fabric-field constructors) all
  passed in the one settled full Python suite: **1433 passed, 4 skipped, 257
  subtests passed in 52.96s**.  The documented structural/schema command
  `.venv/bin/python -m pytest -q Tests/Structural/test_source_structure.py
  Tests/PhysicalDesign/Routing/test_routing_contract_schema.py` passed 7 tests
  in 2.84s, and collection reported 1437 tests.  The selected interpreter was
  the worktree `.venv/bin/python`; system `python3` remains `/usr/bin/python3`
  and is recorded as environment drift.  Loaded native SHA-256 was
  `c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`.
  Evidence is retained in
  `Output/PhysicalRulesFinalVerification/20260906TFinal/`, with prior oracle,
  review, baseline, and compatibility records under
  `Output/PhysicalAccessConformance/20260906TIndependentOracle`,
  `Output/PhysicalAccessConformance/20260906T004716ZIndependentReview`,
  `Output/AccessProfileOwnership/20260906T003729Z`, and
  `Output/AccessProfileOwnership/20260906T004710Z`.
- **Limits retained at that review point.** The current straight-only valid domain cannot
  independently distinguish whole-path retention from equal-prefix slicing;
  the E/W oracle challenge does not prove production E/W access; and nonempty
  required-air access transformation has no current fixture.  Stale
  consumer/catalog identity rejection and real five-stage acceptance remained
  separate.  Native rebuild/parity, MCHPRS, Fabric, scale routing, and full
  R10/N2 acceptance were not-run.  The later current-state review below
  supersedes this record's earlier commit-ready conclusion.

### 2026-09-06 explicit selected-access freshness record

The reviewed freshness correction was first committed and pushed as historical
`dc95e349ca96470046d39c8513184be32cf3fbe9`, then recorded on the current
`Physical-Rules` history as same-parent checkpoint
`1d9e8995d0fed2ed1afa6f2eb6b20524aa8a89ab` (`Enforce current selected access
and add conformance coverage`). The seven production/test paths are
blob-identical between the two commits, which share parent `ffd8c764`; the
original nine-file binary diff is retained as SHA-256
`411ba33bf1edeea565d4aafeea99be2efd01e5ea403acf090ff7a34f8cde856e`.
It remains a Physical-owned contract checkpoint for Joint dependency planning;
it neither performs nor proves Joint integration or full N2 acceptance.

- **Independent regression result.** Real catalog enumeration, witness freezing,
  and feasible solving produced unchanged-current controls plus four adversarial
  current-state cases.  Before the correction, moved-terminal, same-terminal
  changed-face, new foreign ownership, and changed-technology cases were all
  accepted; the retained red result is **4 failed, 2 passed** at
  `Output/PhysicalRulesStaleAccess/20260906TRed/`.  The foreign-ownership oracle
  uses literal coordinate `(1, 1, 4)` beside the source first leg and verifies
  that fresh complete enumeration has zero source options and one rejection.
- **Shared-contract behavior.** Explicit profile consumption now validates the
  selected bindings against the Physical-owned canonical current terminal query.
  Explicit fabric construction validates the current resource model, including
  placed geometry and `FrozenNetWires`, plus exact selected/requested/resource-
  graph technology identity before materialization.  A mismatch raises directly;
  no consumer substitutes local legality, regenerates access, re-solves the
  selection, silently truncates it, or emits a false complete/unsatisfiable result.
- **Verification and status.** The corrected six-node set passed; all affected
  files passed **61 tests**; the historical 13 compatibility nodes passed; five
  incomplete-domain/solver checks preserved their classifications; and the
  structural/schema gate passed **7 tests**.  Green evidence is retained under
  `Output/PhysicalRulesStaleAccess/20260906TGreen/`.  Settled current-snapshot
  verification then passed the affected **61 tests in 0.49s**, the structural/
  schema **7 tests in 2.10s**, collection of **1442 tests**, and the full
  deterministic suite at **1438 passed, 4 skipped, 257 subtests passed in
  51.11s**.  Its JUnit report confirms the historical 13 nodes and the six
  current/stale controls all passed.  Final evidence is retained at
  `Output/PhysicalRulesCompletionReview/20260906T014429Z/Verification/`; the
  independent resolving review is retained at
  `Output/PhysicalRulesCompletionReview/20260906TFinalFixReview/`.  Joint task
  `01a07418-c4c5-7290-ad60-4d9773df4c76`, review
  `01a0741b-5b17-7fe3-9ec0-c7d5f4cec0a1`, and final turn
  `01a07458-2765-73d1-8b7a-287997cea8ce` accepted the exact API and consumer
  behavior without a companion change or uncommitted-input consumption. This
  bounded correction is committed; no Joint integration occurred. The
  historical **1433 passed, 4 skipped, 257 subtests passed** result above remains
  historical.  Full five-stage handoff, native rebuild/parity, MCHPRS, Fabric,
  scale routing, and full R10/N2 acceptance remain separate or not-run.

### 2026-09-06 stale supplied resource-graph identity record

- **Independent regression.** A selected source access claim provides the
  adversarial frozen position from its actual electrical influence scope. The
  position is deliberately absent from the supplied earlier resource graph and
  present in current `FrozenNetWires`. Before correction, direct model
  fingerprinting did not raise and explicit fabric construction falsely
  completed; this is distinct from the existing current-graph foreign-owner
  regression, which rebuilds resources and expects a changed identity.
- **Shared-contract behavior.** Pre-owned node iterables are normalized once at
  each Physical catalog boundary, and every node must belong to the supplied
  resource graph's current `ElectricalBlocks`. Domain enumeration, direct model
  fingerprinting, and selected-witness fabric validation therefore fail closed
  on the same stale graph. Coherent resource construction remains accepted and
  retains the same identities. No new public type, field, export, consumer
  policy, routing outcome, or local substitute for the shared rule was added.
- **Frozen-candidate verification.** The three affected access/profile files
  passed **62 tests in 0.86s**; placement/routing compile-all passed; the
  structural/schema gate passed **7 tests in 2.09s**; collection reported
  **1443 tests**; and one full deterministic run with
  `RC_RUN_SCALE_TESTS=0` passed **1439 tests, 4 skipped, and 257 subtests in
  52.42s**. Loaded native SHA-256 was
  `c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`.
  Evidence is retained at
  `Output/PhysicalRulesStaleGraphCorrection/20260906T131150Z/Verification/`.
- **Status and limits.** This bounded verified Physical producer correction
  passed independent precommit review. No Joint worktree was modified and no
  dependency merge occurred. Native rebuild/parity, MCHPRS, Fabric, live and
  scale validation, the full five-stage handoff, and complete N2/R10 acceptance
  remain not-run or separate gates.

### 2026-09-06 current frontier

`f23293a18487cde6638c8b05be18ebd71afc0d30` is the current Physical-Rules
producer checkpoint atop `2f69160`'s catalog/realization/exact-claim/proof/
domain contract and `1d9e899`'s current selected-binding checks. It rejects a
selected witness whose pre-owned electrical position is absent from the
supplied current resource graph before any domain or placed-resource-model
identity can be accepted. The retained verification is 62 affected tests, 7
structural/schema tests, and a 1,439-pass deterministic suite; see [the frozen
verification record](/mnt/Projects/RedstoneCompiler-Worktrees/Physical-Rules/RedstoneCompiler/Output/PhysicalRulesStaleGraphCorrection/20260906T131150Z/Verification/Summary.txt).

Joint consumes this exact Physical checkpoint at `2902d1d` and current Joint
`64efbe1`. That confirms the public producer-to-consumer boundary on its
controlled scope; it does not establish all N2 consumers, stale salvage, or
MCHPRS/Fabric acceptance.

### 2026-09-06 supported stair-rule shared-consumer record

- **Frozen decision and oracle.** The supported clear-headroom fixture uses
  lower dust `(0, 1, 0)`, upper dust `(1, 2, 0)`, supports `(0, 0, 0)` and
  `(1, 1, 0)`, and required headroom `(0, 2, 0)`. These coordinates and the
  Java dust states `east=up` below and `west=side` above are literal expected
  values, independent of production helpers and fingerprints. Replacing the
  headroom with a solid block requires no stair primitive, rejection of the
  old clear-headroom claim, no physical graph edge, and `east=none` below.
- **Consumer agreement and non-duplication.** One conformance test observes
  the actual resource primitive, exact wire/support/required-air route claims,
  `ValidateLocalRouteClaims`, `BuildPhysicalGraphs`, and the canonical rendered
  wire states. Earlier tests own each nearby local boundary separately; none
  compares these consumers on one positive/adverse fixture. The required-air
  claim is nonempty because this is a supported route stair. It does not add or
  claim a selected-access family with nonempty required-air. No production or
  public API changed, so a red/green production correction is not applicable.
- **Evidence.** The clean baseline is `Physical-Rules`
  `b65847043c7ba354ea3835486df60840f9510cc2`. Independent pre-edit evidence at
  `Output/PhysicalRulesSharedContract/20260906T230415Z/Baseline/` passed 1,439
  tests with 4 skipped and 257 subtests, passed 7 structural/schema tests, and
  collected 1,443 tests. Settled verification at
  `Output/PhysicalRulesSharedContract/20260906T231840Z/Verification/` passed
  compile-all, 17 owning tests, 7 structural/schema tests, collection of 1,444
  tests, and the full suite at 1,440 passed, 4 skipped, and 257 subtests in
  50.99 seconds. The placement/rendering and access-catalog supplement passed
  69 tests and 14 subtests. Python 3.12.3 loaded native SHA-256
  `c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`;
  source and native identities remained stable.
- **Review and boundary.** Independent review at
  `Output/PhysicalRulesSharedContract/20260906T231148Z/Review/` resolved its
  missing reverse blocked-adjacency assertion, then passed the corrected node,
  owning module, independent probe, and diff check with no unresolved in-scope
  technical finding. Test SHA-256 is
  `e64b4bdfa3a0db19cbfacd9a615d9aa844ff609c94dc5ecf52fa50cd8d93087a`.
  This three-file conformance-only candidate is commit-ready pending user
  approval; no production/API change or consumer dependency integration
  occurred. Wall-torch headroom remains an exact disagreement: the resource
  primitive is absent while the final physical edge exists and rendering emits
  `east=up`/`west=side`. That case remains outside this clear/solid scope and is
  not relabeled as a different runtime outcome. Native rebuild/parity, MCHPRS,
  Fabric, live or scale routing, the full consumer matrix, and N2 acceptance
  remain not run or unproved.


### Batch1 declarative fixture candidate

The public fixture checker and separate data loaders, strict expectations, raw
MCHPRS observer, comparison, and reporting now support source-bound conditional
route-transfer conformance. Native observations independently verify complete
input baselines, applied vectors, root power, and every tick through the declared
settlement/stability horizon. See [Physical-Rules Batch1](../../R/R10/PhysicalRulesBatch1.md) for the contract.

General device truth and model timing remain unavailable; no Fabric, scale, or
full-router acceptance is claimed. This is an uncommitted review candidate, not
an integrated checkpoint.

### 2026-09-07 state-aware dust-stair query candidate

- **One physical owner.** `dust-stair-query-v1` publishes independent
  `GeometryStatus`, headroom classification, and `RouteClaimStatus` axes plus a
  deterministic normalized-input identity. ResourceGraph, final physical graph
  construction, renderer wire arms, and fixture reporting consume this query;
  geometric connectivity is not promoted into a route claim. Complete relevant
  raw states and validity are identity-bound, and ResourceGraph freezes its
  caller-supplied state snapshot before any region or route-claim cache entry.
- **W1 boundary.** Exact east-backed, lit W1 geometry is connected and visible
  to final-graph/rendering consumers, but its electrical headroom has
  `Unknown` claim legality with reason
  `electrical-headroom-ownership-unavailable` and no claim positions. P0/N0,
  flat routes, and existing supported behavior remain protected; malformed,
  unsupported, missing-support, and insufficient-context inputs fail closed.
- **Verification and status.** The complete deterministic suite passed 1,489
  tests with 4 skipped and 257 subtests; the structural/schema gate passed 7
  tests; and all seven declarative fixture cases passed. This is an uncommitted
  candidate awaiting fresh independent review, not an integrated N2 checkpoint.

The accepted A/B observation proves only the exact geometry and its
fixture-scoped electrical intervention. Ownership, same-net compatibility,
general device behavior, timing, native parity, live Fabric, scale routing, and
full N2 acceptance remain unproved or not run.

### 2026-09-07 current selected-access receipt boundary candidate

The Physical producer now owns a narrow typed current-validation receipt for
selected placement access. Its input identity binds canonical terminal
selection, supplied witness/domain/solve identity when feasible, current
technology, placed-resource model, and a complete immutable current observation;
its result identity binds status, reason, input identity, and an exact zero
access-regeneration count. No producer revision, policy/catalog expectation,
candidate, envelope, trust, commitment, cache/reuse, routing, or readiness axis
is represented. Receipt decoding checks internal structure and fingerprints only
and is explicitly not a proof of currentness or authority.

The retained contract coverage verifies typed mismatch/unresolved separation,
malformed frozen-wire propagation, replay non-authority, deterministic equivalent
snapshots, no regeneration through public failure sentinels, and an adversarial
mid-validation current-input change. It is a source-local candidate pending
independent review and explicit commit approval; Joint integration and all
native, MCHPRS, Fabric, scale, and full N2 acceptance gates remain not-run.

### 2026-09-07 current selected-access receipt correction candidate

The non-authoritative receipt boundary now uses one shared finite canonical
block-state representation for catalog resource-model identity and validation
observation. The resource-model schema is explicitly `v2`; valid current block
state changes invalidate old witness evidence and a fresh witness/solve under the
same state remains eligible for a fresh current validation. Frozen-wire input
rejects duplicate positions instead of erasing multiplicity, and one-shot
iterables fail closed because they cannot be truthfully re-attested. Drift
receipts retain both initial and mandatory final observations.

### 2026-09-07 frozen-wire Mapping-entry correction candidate

The complete frozen-wire identity now includes every logical Mapping entry or
fails before receipt creation: duplicate signal entries cannot be collapsed by a
temporary dictionary. This correction preserves the Physical-owned canonical
normalization boundary used by direct catalog callers and fresh validation.

### 2026-09-07 versioned placed-template state candidate

The v3 Physical graph owns explicit transformed template states as a frozen,
re-attestable producer observation. Frozen wires remain electrical membership
only; route-created state, renderer adjustments, and live-world observations do
not enter this snapshot. State-only graph changes are already identity-bound by
the v2 pin-access resource model, so old selected evidence mismatches while
fresh evidence can validate. This is not an R10/N2 closure or a ReadyForRouting
consumer claim; downstream Joint integration remains a separate gate.

`PhysicalDesign.Resources.ResourceGraph.FreezeRoutingResourceState` is the
single lower-layer finite JSON-like state freeze/canonicalization gate. Geometry
and the Physical access catalog delegate to it, preserving canonical JSON-shaped
receipt values while rejecting non-finite floats, non-string keys, cyclic,
one-shot, unsupported, or lossy state before a snapshot, graph, or identity can
be published. Nested mappings and sequences are recursively immutable in the
graph and snapshot facade.

The producer's state schema is intentionally narrower than generic JSON-like
freezing: only the top-level fields the public redstone state transform retains
are admitted. Positive template extents and half-open local-coordinate bounds
are validated before state freezing or transformation.

### 2026-09-07 supported clear dust-stair MCHPRS conformance candidate

`PhysicalRulesBatch1/SupportedStair` adds a fixture-scoped observation of one
P0 `+X/+Y` supported, clear-headroom dust stair. A floor lever at `(0,1,0)`
drives lower route-root dust `(1,1,0)` through required air `(1,2,0)` to upper
dust `(2,2,0)`. The physical checker independently validates the literal
bidirectional dust edges, supports, required air, and empty missing/blocked/
conflict sets. The real namespaced MCHPRS observer runs independent fresh
off-to-on and on-to-off cases with complete input receipt, pinned compiler
options, all `0..7` samples, logical output, and lower-root power readback;
expectation-only mutation cannot alter prediction or raw observation.

Evidence is retained under `Output/PhysicalRulesBatch1Evidence/20260907T125818Z/`
and `Output/PhysicalRulesBatch1/20260907T130646Z/`: focused source/contracts
`40 passed`, real observer `5 passed`, runner `9/9` cases passed,
structural/schema `7 passed`, collection `1,570`, and deterministic tests
`1,566 passed, 4 skipped, 257 subtests`. Upper-dust analog power is **not
observed** by the existing receipt and is not inferred; `0/15` is only the
lower declared route-root condition. No blocked N0 backend case, strength
limit, fanout, ownership, general device/timing or stateful behavior, Fabric,
scale, router acceptance, or full N2 acceptance is claimed.

### 2026-09-07 blocked all-stone dust-stair conformance candidate

`PhysicalRulesBatch1/BlockedHeadroomStair` supplies a literal native N0
fixture instead of promoting the existing smooth-stone or headroom-only
diagnostics. Its four-stone, one-lever, two-dust arrangement makes the public
stair decision blocked/solid/illegal with no route claim, yet leaves the
aggregate checker `Legal` after the edge is removed. Both fresh transitions
observe false `Y` across ticks `0..7` and declared lower-root power only
(`15`/`0`); the current receipt exposes no upper analog field. This is narrow
fixture conformance, not broad N2, Fabric, scale, router, or acceptance proof.

### 2026-09-07 dust signal-strength boundary conformance candidate

The paired F15/F16 all-stone fixtures independently bound straight dust
transfer at the current declared route root: fourteen edges retain Boolean
endpoint transfer from root `15`, while fifteen edges do not; both applied-off
vectors remain false from root `0`. They retain exact directed dust edges and
supports, and empty headroom, blocked-headroom, missing-support, conflict, and
repeater fields. Fresh native receipts cover only the literal Boolean endpoint
and lower/root readback through the finite fixture horizon; expected values do
not enter either executor.

This is a narrow R10/N2 fixture-conformance candidate, not a claim about
remote analog power, general timing, fanout, ownership, arbitrary materials,
devices, Fabric, scale, router acceptance, or complete N2 acceptance.

### 2026-09-09 placement-access domain completion candidate

- **Capability packet and source.** `Physical-Placement-Access-Domain-Completion-v1`
  starts from `Physical-Rules` commit
  `ea2e22d8f8743417691fdb25a642bb6427e8955f`, tree
  `de2e8e777b3f209f8d5e7554ce9fc12faab538df`. The shared Physical contract now
  distinguishes a complete finite access domain from an empty or interrupted
  enumeration: every enabled certified template/layer must have one canonical,
  identity-bound attempt before the domain can support feasibility or scoped
  unsatisfiability.
- **Contract behavior.** Each attempt records its semantic identifier, domain,
  template/family/layer, catalog, technology and resource-model identities,
  typed status and closed reason, plus exact option linkage when legal or
  deduplicated. Domain construction validates attempt ordering, uniqueness,
  family coverage, counts, options, exact Boolean/integer types, and input-drift
  evidence. Work-cap, cooperative deadline, missing-pattern, unknown-decision,
  and drift outcomes remain incomplete and cannot publish a conflict core.
  Raised outer deadline failures retain their existing typed propagation.
- **Independent oracle and failure shields.** A literal three-pattern INPUT
  oracle proves one legal domain and one domain where all patterns are blocked;
  separate controls stop before the final pattern by work cap or deadline,
  mutate the terminal before publication, reverse enumeration order, and remove
  certified terminal patterns. These controls fail generated-count completion,
  index-based identity, stale publication, and the former zero-options shortcut.
- **Scope and limits.** The candidate changes the Physical producer contract and
  only the integration fixtures needed to express truthful all-rejected domains.
  It does not add a physical rule, enable a pattern family, change Joint
  orchestration, recover candidates, relax legality, or alter any work bound.
  It is uncommitted and does not establish Joint consumption, full N2 acceptance,
  Router integration, native parity, MCHPRS, Fabric, scale, or promotion
  readiness.
- **Verification evidence.** The required focused suite passed **154 tests**;
  the selected-access envelope consumer module passed **63 tests**; the
  structural/schema gate passed **7 tests**; compile-all passed; collection
  reported **2,346 tests**; and the final deterministic suite passed **2,341
  tests, 5 skipped, and 378 subtests** in 401.92 seconds. Fresh retained logs
  are under `Output/PhysicalAccessDomainCompletion/20260909T215543Z/`.

### 2026-09-09 placement-access completion review correction

Independent review rejected the preserved first candidate diff
`625794c5d66098e71e201ca593bc04d9135e5db33ed8fd9eb32a6bffaea8c050`:
its supplied family set did not prove the full per-terminal catalog universe,
its aggregate counts admitted ambiguous legal-option linkage and forged work
caps, and a stable final observation was indistinguishable from no observation.

- **Corrected manifest and linkage.** Each domain carries the exact ordered
  certified template/layer manifest and a one-for-one attempt set. Legal and
  deduplicated links must agree with the retained option's template, family,
  fingerprint, layer, catalog, technology, and resource-model identities;
  legal links are bijective. The complete domain-set boundary validates shared
  controls and total evaluated work under the original global cap.
- **Corrected observation and currentness.** Every returned domain carries both
  nonempty initial and final input fingerprints. Equality is required without
  drift; inequality is required for typed input drift. Current Physical
  validation recomputes the required manifest and evaluation identity before
  classifying feasible or unsatisfiable evidence, preventing truncated or
  invented portable domains from becoming current authority.
- **Cross-owner dependency.** Physical cannot infer the current policy's family,
  catalog, generation-cap, and assignment-cap values from its existing
  validation arguments. The Joint-owned envelope and final-publication callers
  already possess them but require a separately routed controls contract and
  consumer change. This repair remains dependency-blocked and is not
  capability-proven or commit-ready until that exact boundary is integrated and
  independently reviewed.
- **Repair evidence.** The repair root is
  `Output/PhysicalAccessDomainCompletionRepair/20260909T230914Z/`; the rejected
  first-candidate evidence remains immutable.
- **Provisional verification.** The repaired Physical suite passed **160
  tests**, the selected-envelope consumer module passed **65 tests**, the
  structural/schema gate passed **7 tests**, compile-all passed, collection
  reported **2,354 tests**, and the deterministic suite passed **2,349 tests,
  5 skipped, and 378 subtests** in 326.09 seconds. The live-policy controls
  dependency remains open, so these results are not a full N2 capability or
  commit-readiness claim.

### 2026-09-10 evaluation-controls producer migration

The Physical contract now owns frozen `PlacementAccessEvaluationControls` with
normalized enabled families, catalog version, exact positive generation cap,
exact positive assignment cap, and a deterministic fingerprint retained by the
v2 current-validation input identity. Lists, unsorted or repeated families,
unknown families, empty/non-string catalog versions, Boolean integers,
non-integers, and nonpositive caps fail closed.

`ValidateCurrentSelectedPlacementAccess` remains the one public producer
entrypoint. Its optional Python signature preserves import/call compatibility,
not authority: omitted controls always produce
`Unresolved/EvaluationControlsMissing`; exact supplied controls are checked
against all domains, solve work controls, and the current recomputed catalog
before feasible or unsatisfiable evidence can be current. A mismatch is typed
and cannot authorize routing.

Both Joint-owned live callers still omit this record on the Physical branch.
Their migration is separately authorized and owned by Joint; until the exact
consumer change is frozen and later verified with this producer, the current
Physical branch deliberately blocks those consumers. No combined-source run,
commit, integration, or N2 acceptance is claimed here.

The expanded Physical producer suite passed **181 tests with 1 known
Joint-caller test deselected**; structural/schema passed **7 tests**;
compile-all passed; and collection reported **2,376 tests**. Direct probes of
the unchanged current-envelope and final-publication callers both failed closed
with missing controls. The full suite is dependency-blocked and was not run;
the prior 2,349-pass result belongs only to the pre-controls Physical slice.

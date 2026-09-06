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

The reviewed freshness correction is committed and pushed as
`dc95e349ca96470046d39c8513184be32cf3fbe9` (`Enforce current selected access
and add conformance coverage`). It remains a Physical-owned contract checkpoint
for Joint dependency planning; it neither performs nor proves Joint integration
or full N2 acceptance.

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

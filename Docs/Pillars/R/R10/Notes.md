# R10 notes

Working notes for [R10](R10.md). This file is non-normative; the requirement
file controls when the two disagree.

## Decisions

- None recorded.

## Open questions

- None recorded.

## Working notes

### 2026-09-06 first-wave access conformance and compatibility record

- **Source and dependency identities.** The bounded change started from
  `Physical-Rules` at `ffd8c76430eccb7f4ae1e06b4a777a4ed6ac3b9f`.  The
  Physical access checkpoint is `2f69160a85f1ac800243698aebec4f172164b9f8`;
  reviewed Joint references are `eb3ef79e2b53efd47bbd90d138fdeba2eb405e98`
  and `ea77a28afde1168d4243028f0a00a4ebb869549e`, each with that Physical
  checkpoint as merge base.  No commit, dependency integration, merge, or
  live-runtime action occurred.
- **Bounded compatibility slice.** `BuildNetRoutingProfiles` now has its
  default-false `RequireExplicitAccessWitness` mode: explicit callers must
  supply a witness, requested access length must equal witness access length,
  and explicit source/target paths stay whole; ordinary callers retain legacy
  prefix behavior.  `PlacementAccessFabric` now transports and serializes the
  paired default-empty `PinAccessDomainFingerprint` and
  `PinAccessWitnessFingerprint`.  Joint reviewed exactly these two producer
  changes for consumer compatibility and requested no companion change; that
  review used committed-reference behavior and reported Physical results, not
  Joint consumption of this uncommitted worktree.
- **Independent conformance coverage.** The catalog conformance cases use a
  local rigid-transform arithmetic oracle, literal mirror-only E/W direction
  challenges, controlled technology/catalog identity mutations, and public
  omitted/truncated-witness corruptions.  They establish transformed nonempty
  wire/support/electrical claims, preserved empty supported required-air scope,
  correct scoped identity effects, and rejection of the three witness
  corruptions.  Existing coverage already owns work-cap, complete/incomplete,
  preownership/foreign conflict, support obstruction, power propagation,
  rendering, and general-domain claims; no duplicate test was added.
- **Historical pre-freshness verification.** With the worktree `.venv/bin/python`, loaded native
  `RedstoneCompiler/RustRouting.cpython-312-x86_64-linux-gnu.so` SHA-256
  `c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`, and a
  fresh `PYTHONPYCACHEPREFIX`, the structural/schema gate passed 7 tests in
  2.84s; collection reported 1437 tests; and
  `.venv/bin/python -m pytest -q Tests` settled at **1433 passed, 4 skipped,
  257 subtests passed in 52.96s**.  The historical 13 failures are all
  resolved: 10 missing-profile-keyword failures by the explicit-profile
  correction and 3 missing fabric-fingerprint constructor failures by the
  paired fields.  There were no new or unexplained failures.  Full evidence:
  `Output/PhysicalRulesFinalVerification/20260906TFinal/`; independent oracle:
  `Output/PhysicalAccessConformance/20260906TIndependentOracle`; accepted
  focused re-review:
  `Output/PhysicalAccessConformance/20260906T004716ZIndependentReview`; API
  baseline and final compatibility records:
  `Output/AccessProfileOwnership/20260906T003729Z` and
  `Output/AccessProfileOwnership/20260906T004710Z`.
- **Limits retained at that review point.** No current supported access option has nonempty
  required-air, so nonempty required-air access-transform behavior remains
  unproved.  The E/W helper challenge is not a claim of production E/W access.
  For valid straight access, whole tuple and equal-prefix behavior cannot be
  distinguished because selection path and first leg have equal length.  Stale
  catalog/consumer identity handling and the full real five-stage handoff remained
  separate.  Native rebuild/parity, MCHPRS, Fabric, scale routing, and full
  R10/N2 acceptance were not run.  The later current-state review below
  supersedes this record's earlier commit-ready conclusion.

### 2026-09-06 selected-access current-state correction

This reviewed correction was committed and pushed as
`dc95e349ca96470046d39c8513184be32cf3fbe9` (`Enforce current selected access
and add conformance coverage`) on `Physical-Rules`. It is an exact
Physical-owned dependency checkpoint for Joint planning, not evidence that
Joint has integrated it or that full routing acceptance has occurred.

- **Observed stale acceptance.** New real-producer regressions retained at
  `Output/PhysicalRulesStaleAccess/20260906TRed/` first proved that an explicit
  profile accepted a valid old witness after a same-named target moved or its
  face changed at the same terminal, and that explicit fabric construction
  accepted a valid old witness/solve after current foreign ownership or routing
  technology changed.  The red run settled at **4 failed, 2 passed**; both
  unchanged-current controls passed.
- **Current identity enforcement.** The Physical access catalog now exposes one
  pure validator over its canonical placed-terminal binding traversal.  Explicit
  profiles require exact current signal, gate name/kind, role, pin, terminal,
  and face coverage, including no missing, extra, or duplicate selection.
  Explicit fabric construction requires agreement among the supplied technology,
  resource-graph technology, selected technology fingerprint, and the current
  placed-resource-model fingerprint including `FrozenNetWires`.  These checks
  compare identities only; they do not enumerate, regenerate, reselect, solve,
  salvage, or manufacture a routing outcome.
- **Focused verification.** With the same worktree interpreter and loaded native
  SHA-256 `c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`,
  the six current/stale regressions passed, all three affected files passed
  **61 tests**, all historical 13 compatibility nodes passed, the five focused
  incomplete-domain/solver checks passed, and the structural/schema gate passed
  **7 tests**.  Evidence is retained at
  `Output/PhysicalRulesStaleAccess/20260906TGreen/`.
- **Current disposition and limits.** The bounded current-state correction is
  commit-ready after settled current-snapshot verification and independent
  review.  The three affected test files passed **61 tests in 0.49s**; the
  structural/schema gate passed **7 tests in 2.10s**; collection reported
  **1442 tests**; and the full deterministic suite passed **1438 tests, 4
  skipped, and 257 subtests in 51.11s**.  JUnit confirms all historical 13
  compatibility nodes and all six current/stale controls passed.  Verification
  is retained at
  `Output/PhysicalRulesCompletionReview/20260906T014429Z/Verification/`, and
  the resolving independent review is retained at
  `Output/PhysicalRulesCompletionReview/20260906TFinalFixReview/`.  Joint's
  task `01a07418-c4c5-7290-ad60-4d9773df4c76`, review
  `01a0741b-5b17-7fe3-9ec0-c7d5f4cec0a1`, and final turn
  `01a07458-2765-73d1-8b7a-287997cea8ce` accepted the exact public APIs and
  behavior without a companion change or uncommitted-input consumption. The
  historical 1433-pass suite above predates this correction and is not relabeled
  as current. No Joint integration occurred. Nonempty required-air
  transformation, the full real five-stage handoff, native rebuild and parity,
  MCHPRS, Fabric, scale routing, and full R10/N2 acceptance remain separate or
  not-run.

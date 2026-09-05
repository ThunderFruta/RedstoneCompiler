# R2 commit history

This file records commits intentionally attributed to [R2](R2.md). A commit is
listed only when its scope and verification are known.

| Commit | Date | Relationship to R2 |
|---|---|---|
| `f946ffc` — Enforce selected access across joint routing | 2026-09-05 | Joint-owned placement, orchestration, assignment, and routing consumer slice. Physical realization, domain solving, and legality are provided by Physical-Rules. |
| `ec6c7d1` — Harden joint access handoff | 2026-09-05 | Joint-owned result, orchestration, assignment, commitment, and integration-test hardening over Physical-Rules proof contracts. Retained evidence was captured at patch-equivalent pre-split `2024d7d`; Stage 1 remains unaccepted. |

## Reclassified prerequisites from the legacy R1 branch

- `b8160bb`'s opt-in policy and caller/provenance behavior is already present
  through `7a88051`, followed by the intentional Joint v17-default change `16be70c`.
  Do not replay the old commit and restore v16 as the development default.
  Its archive-aware integration belongs to `Telemetry-And-Acceptance` and is
  on `Telemetry-And-Acceptance`.
- `7c68af4` is patch-equivalent to the catalog owned by Physical-Rules
  (pre-split identity `96d9604`). Physical-Rules owns
  the catalog's shared-rule/proof binding; Joint-Physical-Design consumes the
  placement/access contract types. No duplicate production patch is needed.
- `14646a9` mixed archive infrastructure with core-router scope guidance and a
  broad documentation relocation. The scope guidance is restored at the
  current [design path](../../../Routing/Active/RoutingAwarePlacementAccessDesign.md#branch-scope-core-router-rewrite-not-cla4-repair)
  in this bucket, preserving the newer readiness workflow and v17-default
  decision. The obsolete directory relocation is not replayed.

These are supporting architecture/policy prerequisites, not implementation of
R1 lazy expansion or proof of complete R2 acceptance. Original commits remain
available in the preserved legacy R1 history.

## Verification of pre-split `1fb7db6`

The committed source was verified on `R2-Joint-Placement-And-Routing` against
the `Router-Refactor(R10-N5)` base:

- Python compilation and staged-diff checks passed.
- The structural and routing-contract schema gate passed: 11 tests.
- The focused placement and routing gate passed: 349 tests and 35 subtests.
- The complete Python suite passed: 1,496 tests, 3 skipped, and 257 subtests.
- Test collection found 1,499 tests.
- No Rust source changed, so the Rust rebuild and test gates were not run.
- A fresh live acceptance archive was not run after the rebase; no R2
  completion or production-acceptance claim is attached to this commit.

## Verification and evidence of pre-split `2024d7d`

Full implementation revision: `2024d7ddfde26195221414d1d7ee6567567a179d`.
Its parent remains `b0cced5cebcde3f7579c5559e78d72f710dcfa6d`; no history was
amended. Evidence is recorded afterward in a separate documentation commit so
that the final implementation hash can be cited without rewriting it.

- Placement/routing gate: 432 passed, 52 subtests, 22.88 s.
- Structural/schema/snapshot/harness gate: 92 passed, 71 subtests, 18.92 s.
- Full Python suite: 1,558 passed, 3 skipped, 257 subtests, 66.02 s.
- Rust/Gradle build gates: **not-run**, their source is unchanged.
- Clean opt-in Stage-1 live matrix: **0 passed, 4 failed, 0 skipped**. FA/RCA4
  fail at interface selection; RCA8/CLA4 retain scoped access-unsatisfiable
  evidence. The prior R2 revision reproduces the same failure identities.
  Candidate physical, MCHPRS, and Fabric phases are **not-run**.
- Clean v16 control at this implementation revision: **3 passed**, with exact
  MCHPRS vectors and required Fabric canaries; no Stage-1 contract is emitted.
- All 25 solve occurrences in the live failure JSON round-trip exactly.
- Clean, checksum-valid S0 and S1 diagnostic snapshots are retained separately.
  S0's CLA4 result is an actual harness timeout without a typed compiler failure;
  S1 is a failed candidate capture, not an accepted milestone.

The [Stage 1 conformance ledger and evidence record](Notes.md#stage-1-conformance-ledger)
contains the exact commands, per-case counts/times, artifact locations, source
and native identities, SHA-256 values, remaining gates, and capture limitations.
Cross-cutting attribution is explicitly scoped in
[N1](../../N/N1/CommitHistory.md), [N2](../../N/N2/CommitHistory.md),
[N3](../../N/N3/CommitHistory.md), [N4](../../N/N4/CommitHistory.md),
[N5](../../N/N5/CommitHistory.md), and [N6](../../N/N6/CommitHistory.md).

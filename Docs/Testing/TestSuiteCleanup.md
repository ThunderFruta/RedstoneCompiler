# Test-suite cleanup evidence

This document records the one-time collection migration performed while
integrating the validation feature at
`d2e14073014ffe5d422a333a761a956e7459487e` onto `main`. The counts are
historical evidence for reviewing this change, not permanent test-count gates.

## Collection accounting

- Validation-feature inventory before cleanup: 1,526 collected cases.
- Removed as unreachable: 21 automatic-relaunch tests for an unmaintained
  experimental branch.
- Replaced by advisory reporting: four subjective source-size gates.
- Retired as implementation-shape checks: 73 pipeline and five physical-
  assembly tests that inspected source strings, local names, or statement
  positions instead of executing behavior.
- Added: one deterministic advisory-source-review contract plus 11 parser,
  synthesis, native differential, package, litematic, MCHPRS-policy, and
  compatibility cases.
- After cleanup and additions: 1,435 collected cases.

The resulting difference is exactly 91 cases. No active executable test had an
identical body before cleanup.

## Retained behavioral ownership

The 659 executable tests formerly concentrated in three large routing modules
were moved without changing their bodies:

- authoritative routing: assignments, caches, deadlines, portals, exterior
  distance, guide-stage boundaries, and global routes;
- component pipeline: orchestration, proof scheduling, repair queues, and
  cache lifetime; and
- physical assembly: port domains, exact proofs, fabric, and global handoff.

Correctness-sensitive ordering remains covered through typed outcomes,
deadline/incomplete classification, exact no-good scope, scheduling and repair
state, cache identity, final conflict checks, and handoff validation. Local
helper placement and source spelling are reviewed with
`Tools/Routing/ReviewSourceStructure.py` and do not gate pytest.

The FullAdder and RCA8 MCHPRS cases read hash-bound tracked fixtures under
`Tests/Fixtures/Mchprs/`, so a clean checkout exercises all 131,072 RCA8
vectors without relying on ignored `Output/` state.

## September 2026 outcome-first follow-up

A second audit removed 43 implementation-coupled or redundant cases from the
1,450-case collection, leaving 1,407 collected cases. This is not a target
count. The main removals were the 81-class introspection hash, private
placement/orchestration call choreography, source-text bans, exact menu/default
argument snapshots, duplicated worktree/package smoke checks, and tests of
snapshot-analysis helpers rather than published evidence.

See [OutcomeFirstTestAudit.md](OutcomeFirstTestAudit.md) for the disposition,
retained contract ownership, and replacement rationale. The earlier counts in
this document remain historical migration accounting only.

## October 2026 bounded follow-up

This follow-up removes only demonstrated superseded coverage and incidental
assertions. Historical collection counts above remain historical evidence.

| Test | Disposition | Retained outcome coverage |
|---|---|---|
| `Tests/PhysicalDesign/Routing/test_routing_resources.py::RoutingResourceTests::testRoutingUsesOneAuthoritativeStrictAttempt` | Removed: the single configured attempt plus exact margin, penalty, iteration and ordering defaults repeats the policy-snapshot claim already retired in OutcomeFirstTestAudit. | `testCapacityAwareGuidesAreDeterministicAndBounded`, authoritative access-bound propagation, stoppable routing-resource construction, assignment-cap/deadline tests, and final physical legality. |
| `Kernels/Routing/Src/Generation/BatchOutcomes.rs::IndexedParallelCollectionPreservesSlotsAcrossPermutedCompletion` | Removed: executes standalone Rayon collection rather than the compiler and assumes a completion order from a sleep. | Public native ordinal/aggregate-work tests, adapter ordinal projection, mixed-batch receipt preservation, and the adjacent production worker-boundary panic/sibling-receipt regression. |
| `Tests/Structural/test_source_review.py::test_source_review_is_advisory_and_deterministic` | Retained; removed only the exact 1,000-line advisory threshold assertion. | Deterministic advisory output, ownership/definition evidence, absence of pass/fail verdict fields, successful public command, and JSON output consistency remain enforced. |

These removals change no production Python or Rust behavior. Exact proof scope,
complete/incomplete outcomes, capacity-one ownership, deterministic receipts,
physical oracles, canonical versioned serialization, current identity,
deadlines, work bounds and acceptance gates remain strict. Uncertain tuning,
recipe-domain and traversal-order findings require separate scoped decisions.

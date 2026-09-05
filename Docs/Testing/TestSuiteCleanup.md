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

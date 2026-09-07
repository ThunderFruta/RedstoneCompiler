# N1 commit history

This file records commits intentionally attributed to [N1](N1.md). A commit is
listed only when its scope and verification are known.

| Commit | Date | Relationship to N1 |
|---|---|---|
| `f5ffd47` — Normalize child worker exits and prove admission bounds | 2026-09-06 | Current Runtime frontier, parent `9f432cd`. Child `SystemExit` is typed `WorkerFailure` / `Unresolved` / `Failed` with no value or proof, while parent interrupt/exit controls propagate. Independent result-serialization and occupied-capacity oracles cover the bounded spawned path. Retained complete-suite evidence is 1,455 passed, 4 skipped, and 257 subtests in 64.76 s; no Router integration or full N1 acceptance is claimed. |
| `9f432cd` — Bound spawned symbolic unary admission | 2026-09-06 | Builds on `f083554` with one real per-call spawned unary admission path, bounded in-flight/queued work and typed unresolved exits. It is not a persistent R6 pool, native cancellation, global admission policy, exact parent-deadline propagation, or full N1/N4 acceptance. |
| `f083554` — Add typed bounded symbolic runtime outcomes | 2026-09-06 | Live bounded N1 checkpoint replacing historical identity `ef473ec`. Both commits have parent `9a97f40`; their `PhysicalDesign/` and `Tests/` blobs are identical, while `f083554` adds only the finalized N1 documentation/evidence record. The checkpoint provides immutable typed work/result records, a synchronous bounded adapter, and one real symbolic-caller migration. Adapter cancellation is covered, but this caller has no live `CancellationCheck`; coordinator-to-worker cancellation wiring remains future work. This is not persistent-worker R6 work, repository-wide N1/N4 acceptance, or a dependency integration. |
| Joint checkpoint `ea77a28`, consuming Physical checkpoint `2f69160` | 2026-09-05 | External Stage-1 behavior distinguishes `Feasible`, `Unsatisfiable`, and `Incomplete`, retains work limits and scoped evidence, and rejects invalid proof transport. This does not implement N1's repository-wide lifecycle or commit-eligibility axes. |

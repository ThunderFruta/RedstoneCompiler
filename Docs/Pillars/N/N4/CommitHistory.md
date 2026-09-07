# N4 commit history

This file records commits intentionally attributed to [N4](N4.md). A commit is
listed only when its scope and verification are known.

| Commit | Date | Relationship to N4 |
|---|---|---|
| `f5ffd47` — Normalize child worker exits and prove admission bounds | 2026-09-06 | Current repair of `9f432cd`: child `SystemExit` becomes typed `WorkerFailure` / `Unresolved` / `Failed` without value/proof, and independent oracles prove the result byte boundary and occupied capacity after failed cancellation. Retained full-suite evidence is 1,455 passed, 4 skipped, and 257 subtests in 64.76 s; no full N4 acceptance or Router merge is claimed. |
| `9f432cd` — Bound spawned symbolic unary admission | 2026-09-06 | One real per-call spawned fan-out receives bounded in-flight/queued admission and typed failure exits. This does not implement persistent workers, priority/preemption, native cancellation, global Python/native bounds, bounded teardown, or exact parent-deadline propagation. |
| Joint checkpoint `ea77a28` | 2026-09-05 | External Stage-1 behavior adds generation/assignment caps, bounded domains, deadline checkpoints, and serialized incomplete reasons. This is attribution of consumed behavior, not Runtime-And-Kernels implementation or bounded-runtime acceptance. |

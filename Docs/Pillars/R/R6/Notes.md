# R6 notes

Working notes for [R6](R6.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- Task promotion and demotion require explicit coordinator decisions.
- Demotion changes scheduling priority while preserving a live or resumable
  task; termination permanently ends that task instance.
- Task termination normally leaves the persistent worker process alive.
- Pressure-aware cleanup is coordinator policy. It grants immutable absolute
  work and cleanup cutoffs before admission and may revoke cleanup only to an
  earlier cutoff when critical reclaim requires it; Runtime does not extend a
  live cutoff because capacity appears relaxed.

## Open questions

- None recorded.

## Working notes

### 2026-09-06 current frontier

No R6 implementation checkpoint is recorded. The committed Runtime chain
`f083554` → `9f432cd` → `f5ffd47` supplies immutable typed outcomes and
bounded per-call spawned unary admission for one caller; it deliberately does
not create a persistent pool. Priority classes, coordinator-authorized
promotion/demotion, preemption bounds, worker-local cache retention, native
cancellation, global admission, and exact parent-deadline propagation remain
target behavior. The final bounded-path evidence is recorded in the
[N1 final repair](../../N/N1/Notes.md#final-child-exit-and-admission-authority-repair).

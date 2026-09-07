# N4 notes

Working notes for [N4](N4.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- Cancellation is a request; termination is the completed lifecycle state.
- Runtime receives caller-granted absolute work and cleanup cutoffs. It may
  enforce a cutoff but does not decide whether relaxed capacity justifies a
  longer one.
- Upstream pressure may select a cleanup budget before admission or explicitly
  revoke it to an earlier cutoff. It never extends an admitted operation's work
  or cleanup cutoff.

## Open questions

- Define measured pressure classes, numeric cleanup allowances, cooperative
  grace intervals, and the forced-termination escalation sequence through a
  versioned coordinator policy. The bounded spawned-unary checkpoint must not
  invent Runtime defaults for them.

## Working notes

### Current authority-contract candidate

The current candidate adds standalone immutable `RuntimeWorkAuthority` v1 with
explicit exact built-in finite-float work deadline and cleanup cutoff values plus
exact built-in boolean force-termination authority. Coercible values and float
subclasses are rejected without conversion, and the closed codec retains the
caller-granted values. Legacy `RuntimeWorkRequest` v1 remains unchanged. This slice
adds no Runtime or caller integration, revocation, live cancellation, or cleanup
implementation. Early-cancel, grace, escalation, and revocation policies remain
open.

### Bounded symbolic unary admission checkpoint

The Runtime checkpoint based on `f083554` bounds one real spawned symbolic
unary fan-out without introducing a persistent scheduler. Its declared default
admits six in-flight tasks plus two queued tasks and applies 64 MiB per-task
request/payload and result-envelope limits. The parent holds pending tasks in
its own bounded queue and submits only when a process slot becomes available.

Admission, payload, result, deadline-before-start, and worker-failure exits are
typed `Unresolved` N1 results. The symbolic consumer publishes no completed
clause entry and merges no child cache unless the whole requested signal domain
completes. Serial, one-worker, and multiple-worker executions are required to
produce the same observable clauses, semantic diagnostics, and cache content.

This is a bounded one-shot checkpoint, not R6 persistent-worker acceptance or
full N4 acceptance. Live cancellation, bounded forced teardown, unified native
thread admission, persistent cache memory accounting, and coordinator-owned
absolute-deadline propagation remain outside this slice.

The scheduler records an expired running task until it settles; it does not
discard a later-deadline task merely because another future reached its bound.
Only a future whose cancellation request succeeds is removed immediately.
Coordinator-wait telemetry is emitted by the symbolic caller through a narrow
callback, preserving Runtime's no-telemetry-import boundary.

### Planned pressure-aware cleanup boundary

The next bounded lifecycle slice will keep pressure assessment above Runtime.
Before admission, the caller will provide an immutable work deadline, cleanup
cutoff, and forced-termination authorization for the whole unary invocation.
On deadline or live cancellation, Runtime will stop further admission and
publication, request child cancellation, and retain every occupied permit until
the child exits and release is acknowledged. A late child result cannot merge a
child cache or publish the completed clause cache.

The caller may select a shorter cleanup budget for critical reclaim work and a
longer one for relaxed capacity. After admission, only an explicit upstream
revocation for new critical work may shorten the cleanup cutoff; Runtime never
extends it from observed idleness. The values and escalation timings remain
unimplemented policy decisions, not evidence that R6 priority/preemption or
full N4 acceptance exists.

### Final child-exit and capacity-authority repair

Current Runtime HEAD `f5ffd47` (parent `9f432cd`) classifies a child
`SystemExit` as typed `WorkerFailure` / `Unresolved` / `Failed` without a value
or proof; parent `KeyboardInterrupt` and `SystemExit` continue to propagate.
The independent capacity test proves that cancellation returning false keeps a
task occupied and prevents a third submission until actual release. The
independent byte boundary accepts an N-byte serialized `RuntimeWorkExecution`
result with limit N and rejects it with limit N-1; it does not bound the whole
IPC envelope.

Final reviewed/committed status is in the
[repair closure](/mnt/Projects/RedstoneCompiler-Worktrees/Router-Integration/RedstoneCompiler/Output/RepairClosure/20260906T180628Z/Report.md),
with replay evidence under
`Output/RuntimeAndKernels/N1-Bounded-Admission/20260906T184020Z-PostCommitReplay/`.
The retained full deterministic suite is 1,455 passed, four skipped, and 257
subtests in 64.76 seconds, not a fresh documentation-update run. Persistent
workers, priorities/promotion/demotion/preemption, native cancellation, global
bounds, exact parent deadlines, and a Router consumer merge remain outside scope.

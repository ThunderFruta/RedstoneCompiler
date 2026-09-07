# N1 notes

Working notes for [N1](N1.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- Search outcome, lifecycle, freshness, claim strength, and commit eligibility
  remain independent state axes.

## Open questions

- None recorded.

## Working notes

### 2026-09-06 bounded symbolic runtime checkpoint

The `Runtime-And-Kernels` worktree's live first bounded N1 checkpoint is
`f083554ee5967e69540e6647ee543c8e92747141` (`Add typed bounded symbolic
runtime outcomes`), from
`9a97f40b93b608fc40750afa7df0ca265357be1e`. Historical identity
`ef473ec886aa5ce2b0b5f8f252d6bda76b39ab0f` has the same parent and identical
`PhysicalDesign/` and `Tests/` blobs. `f083554` is its documentation-only
replacement and preserves the evidence mapping below. The checkpoint
introduces versioned,
immutable work/result documents for the five independent N1 axes, exact domain
and dependency scope, and scoped infeasibility-proof identity. The runtime
adapter always returns deadline, work-cap, cancellation, incomplete, and worker
failure exits as `Unresolved`; a populated prepared value remains explicitly
`Ineligible`.

The real producer/consumer slice is the prepared symbolic net-state worker used
for a mandatory related signal in
`CompilePhysicalComponentSymbolicPortPairDomain`. The current cross-owner API
supplies a remaining duration, so this boundary constructs one absolute bound
from that duration and preserves that same bound through dispatch and the
post-return check. It preserves a zero `MaximumWork` as already exhausted and
reports a returned compilation's observed expansion count before classifying
an overrun. This post-hoc accounting is not a hard per-expansion admission
guarantee. The path emits the portable result document through the existing
work diagnostics callback.

The migrated production caller currently supplies no live cancellation source
and does not pass `CancellationCheck`. Cancellation tests establish only the
adapter boundary's request/acknowledgement semantics; wiring a live production
cancellation source through this caller remains a future dependency.

This checkpoint does not establish a persistent scheduler, pool lifecycle,
memory admission, native cancellation, repository-wide caller migration, N1 or
N4 acceptance, physical acceptance, reuse certification, or accepted-state
commit authority. Calls that explicitly omit a deadline retain the legacy
unbounded path and are outside the checkpoint claim. Infeasibility is only
representable when a producer supplies a complete proof with the exact result
scope; this slice does not create a new proof producer.

Exact propagation of the parent coordinator's absolute `RoutingDeadline`
remains an open cross-owner dependency. The Joint-owned production callers
currently pass `RemainingSeconds()`, so integration must later extend their
boundary to pass the existing deadline object or exact expiration without
reconstructing it here. This local checkpoint does not edit or claim that
Joint-owned propagation.

Fresh evidence is retained under
`Output/RuntimeAndKernels/N1-Bounded-Symbolic/20260906T020359Z/`: 49 focused
tests passed, 7 structural tests passed, 1,439 tests collected, and the complete
deterministic suite passed with 1,435 passed, 4 classified skips, and 257
subtests. The ignored directory contains `Summary.txt`, `RawDump.txt`, and the
repository-generated complete-suite reports.

The correction-cycle tests were designed prospectively under the repository's
spec-first workflow from N1/N4 and the independent review findings before the
seven-file patch was reinspected. They use explicit public fixture identities,
an independent canonical fingerprint calculation, observed solver expansion
arithmetic, and an injected monotonic clock. The real-boundary success case
uses the exact Gamma context and relaxed problem created by the caller through
the bounded wrapper and actual symbolic compiler; only the unrelated
Alpha/Beta factor-batch preparation remains controlled by the fixture. This
statement applies to the correction cycle; it does not retroactively establish
provenance for source known before that review.

The exact live checkpoint is available for later consumers as `f083554`, with
`ef473ec` retained only as the historical source/test-equivalent evidence
identity. This entry does not itself register a dependency update or
integration. A later consumer checkpoint must name the live commit and rerun
the runtime contract tests plus its real producer/consumer boundary tests.

### Bounded spawned unary admission checkpoint

This Runtime-owned checkpoint builds on `f083554` and applies bounded
one-shot admission to the real
`CompilePhysicalComponentSymbolicUnaryApertureDomain` spawned-process fan-out.
The per-call policy admits at most six in-flight signal tasks and two queued
tasks. It measures the serialized immutable request-plus-payload for a 64 MiB
per-task payload bound and measures the child execution envelope before it can
cross the 64 MiB per-task result-publication bound. These are conservative
correctness bounds for the existing at-most-eight-way fan-out, not production
performance targets.

The parent submits new work only when an in-flight slot is released. Capacity
plus one is explicitly `AdmissionRejected`; an oversized payload is
`PayloadLimitExceeded`; an oversized result is `ResultLimitExceeded`; and a
deadline already exhausted before submission or child start remains
`DeadlineExhausted`. Spawn or worker failures remain `WorkerFailure`. Every
such exit is `Unresolved`, `Ineligible`, carries no proof, and returns no
process-local value. The unary consumer merges child net-state caches and
publishes its completed clause cache only after every admitted signal returns
`Prepared`.

This checkpoint deliberately creates a bounded per-call process pool rather
than persistent R6 workers. It does not add priority promotion/demotion,
opportunistic scheduling, native cancellation, salvage, coordinator deadline
API changes, or a Runtime consumer merge. Calls that explicitly omit a
deadline and warm parent-cache calls use the exact parent-process compiler and
remain outside this bounded spawned-work claim.

### Review correction: mixed deadlines and wait observation

Independent review found that the first candidate waited until the earliest
in-flight deadline, then incorrectly classified every outstanding future as
expired. The corrected scheduler now evaluates expiration per request. It
removes a queued expired future only when cancellation succeeds; a running
expired future remains accounted for until it actually settles, while a later
deadline continues to wait and can still return `Prepared`. This is not forced
teardown or native cancellation.

The caller now supplies a narrow Runtime callback for coordinator-wait
observation. `PhysicalDesign/Runtime/` remains independent of `App/Telemetry`;
the symbolic unary caller emits paired `wait begin` / `wait end` events around
each actual coordinator wait. The corrected tests prove an earlier 0.50-second
deadline does not collapse a concurrently running 3.0-second task, exact
serialized request/payload and returned-result limits admit equality and reject
one byte less, and deliberately delayed serial and parallel runs have different
completion orders but identical semantic results.

The corrected frozen scope passed independent review. Its verification records
49 focused Runtime/contract tests, 80 component-router tests, seven structural
tests, and the complete deterministic suite with 1,451 passed, four skipped,
and 257 subtests. Retained evidence is under
`Output/RuntimeAndKernels/N1-Bounded-Admission/20260906T133104Z-ReviewCorrection/`.

### Final child-exit and admission-authority repair

`f5ffd47dd35071f6b1dab2e665e407c4d24d1ac2`, with parent `9f432cd`, is the
current Runtime frontier. A child `SystemExit` is typed `WorkerFailure`,
`Unresolved`, and `Failed`, with no value or proof; parent `KeyboardInterrupt`
and `SystemExit` still propagate. An independent oracle accepts a serialized
public `RuntimeWorkExecution` result is accepted with limit N and rejected with
limit N-1; this is not a claim about the complete IPC envelope. Another oracle
proves that a third submission waits for real capacity release when cancellation
returns false.

The final reviewed/committed status and retained evidence are recorded by the
[repair closure](/mnt/Projects/RedstoneCompiler-Worktrees/Router-Integration/RedstoneCompiler/Output/RepairClosure/20260906T180628Z/Report.md)
and `Output/RuntimeAndKernels/N1-Bounded-Admission/20260906T184020Z-PostCommitReplay/`.
Its retained full deterministic result is 1,455 passed, four skipped, and 257
subtests in 64.76 seconds; it was not rerun for this documentation update.
Runtime is not consumed by Router. Persistent workers, priorities/promotion/
demotion/preemption, native cancellation, global bounds, exact parent deadlines,
and omitted-deadline or warm-cache calls remain outside this spawned claim.

### Authoritative native route-batch outcomes

This bounded Runtime Batch 1 checkpoint adds versioned, immutable
per-request receipts for both the legacy-shaped coarse column request and the
detailed-node request. Each receipt retains its caller ID and producer-assigned
original ordinal. Duplicate IDs and identical request payloads remain separate.
The receipt exposes distinct canonical identities for the immutable raw input,
normalized route domain, exact receipt slot, caller echo, and context graph. Native
Rust SHA-256 calculation covers those scopes without trusting mutable Python digest
providers. Each identity has an explicit `Verified`, deadline-interrupted,
unsupported-input, producer-failure, or dependency-unavailable state; canonical
bytes and hashes are absent unless that identity was verified. Once verified, an
identity remains available across later deadline or worker failures.

The native producer validates found route membership, endpoints, required ordered
branches, and graph edges. A found result maps to N1 `Prepared` / `Candidate` /
`Ineligible`; it is not accepted, optimal, or enumeration-complete. The only
infeasible mapping is a complete multi-source traversal proving a required branch
attachment disconnected in an explicitly bound relaxed request graph. It maps to
`Infeasible` / `InfeasibilityProof` / `Ineligible` and cannot become a placement
no-good, assignment cut, global infeasibility result, or proof for another access,
placement, model, or policy. Every other result maps to `Unresolved` /
`Continuation`, carries no proof, and remains `Ineligible`.

One per-request admission counter charges actual detailed route-state expansions
and relaxed-proof vertex expansions separately under the same cap. Zero admits no
work, and staged searches cannot widen a zero remainder. Immutable request input,
caller echo, and a transactionally replaced context snapshot are sealed during their
separate constructor/mutator calls before the timed batch API; that preparation cost
is not claimed as part of the narrow batch deadline. The batch then acquires only
shared immutable ownership and conservatively maps the original caller-absolute
Python cutoff onto an earlier Rust `Instant`. It uses that same cutoff through
normalization, queueing, route search, proof, streaming canonicalization/SHA, and
final acceptance without renewing relative time. The legacy relative-millisecond
APIs are unchanged.

A decoded slot is uniquely identified by batch identity and original ordinal;
duplicate caller IDs remain distinct. If the cutoff is already expired at entry, or
crosses during only shared-snapshot acquisition, `DeadlineExhaustedAtEntry` performs
no semantic normalization or search and preserves the captured cancellation without
acknowledging an unperformed cancellation decision. For a live entry, completed
invalid input is `UnsupportedRequest`; completed valid cancellation is acknowledged
before later deadline and zero-cap admission. If the cutoff instead interrupts
bounded semantic validation, `DeadlineExhaustedDuringValidation` preserves the
snapshot without claiming a stopped search. Receipt-scope expiry clears any pending
success/proof as `DeadlineExhaustedDuringFinalization` while retaining earlier
verified scopes and work/failure facts.

This checkpoint does not infer lifecycle or freshness from a search outcome.
Cancellation is only an immutable request observed before admission, with no work
dispatched. Live cancellation, running-search termination, process cleanup/reaping,
persistent-worker capacity release, accepted-state authority, Joint consumer
migration, and repository-wide N1 acceptance remain unimplemented.

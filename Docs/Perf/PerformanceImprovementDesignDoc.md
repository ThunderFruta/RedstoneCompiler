# Routing Performance Improvement & Benchmark Plan (RedstoneCompiler)

## Scope and constraints

- Focus only on routing/runtime throughput, not placement policy rewrites.
- Keep acceptance semantics unchanged in this phase (`new-router-first`, `physical-design-v10-routability-feedback`, seed `0`).
- Add telemetry as an additive evidence contract; do not alter routing behavior.

## Baseline lock

- Baseline manifest root: `Output/Acceptance/<date>/RouterV10Recovery/AcceptanceManifest.json`.
- Dry-run matrix remains:
  - `FullAdder` × 5
  - `RippleCarryAdder4` × 2
  - `CarryLookaheadAdder4` × 2
- Baseline artifacts from each run:
  - `RunDirectory/<run>.PhysicalDesign.json`
  - stdout/stderr logs
  - `.PhysicalDesign.json` route/perf evidence

## Evidence contract implemented in this branch

Acceptance now persists `AcceptanceManifest.Runs[].Evaluation.Perf` with additive fields:

- `SchemaVersion: "router-performance-v1"`
- `StageTimingsSeconds` (from `RouterReliability.StageTimingsSeconds`)
- `NativeWork`
  - `Batching`
  - `RequestCounts`
  - `CompletedWork`
  - `Assignment`
  - `CandidateDiagnosticsSummary`
- `Deadline`

This contract is non-breaking: if sections are missing, an empty structured block is still emitted.

## Performance work (lowest-risk order)

1) Telemetry first (implemented)

- Parse `RouterReliability` evidence and persist `Evaluation.Perf` every run.
- Add focused acceptance tests that validate schema and parser stability.
- Keep gating checks unchanged.

2) Batch-first calls

- Increase request granularity before FFI boundaries.
- Reuse immutable envelopes for per-signal metadata where safe.
- Move remaining per-signal read-only loops to batched native calls once validated.

3) Request-shape cache

- Add deterministic cache keyed by immutable geometry/policy/budget context.
- Read-only use only for diagnostics/scoring.

4) Thread profile policy

- Benchmark with fixed thread profiles now, then convert to phase-aware policy:
  - `min(2, cpus)`
  - `min(cpus//2, 16)`
  - `min(cpus, 16)`

5) Failure-time optimization path while RCA4/CLA4 fail

- Track:
  - `time-to-failed-state`
  - attempts completed before failure
  - last overflow/conflict signature
  - escalation and deterministic retry counts
- Use these metrics to guard regressions before expanding optimization scope.

## Benchmarks and gate order

1. Gate slice (lightweight)
   - compileall + focused unit list (unchanged)
   - full rust gate (unchanged)
   - acceptance quick slice: `FullAdder ×2`, `RippleCarryAdder4 ×1`
2. Full matrix only when branch is ready for longer validation.

### Matrix

- Thread profiles tested:
  - default profile from environment
  - half-core cap
  - capped full-core profile
- For each profile collect:
  - `FullAdder`: median wall/runtime, p95, 5-run determinism
  - `RippleCarryAdder4`: runtime-to-fail or success + conflict signatures + stage telemetry
  - `CarryLookaheadAdder4`: same failure-time metrics

### Guardrails

- no new failure classes
- policy/version/strategy unchanged
- no deterministic mismatch
- no fallback artifacts
- no regression in `FullAdder` 5-run determinism

### Promotion thresholds

- keep `FullAdder` under current wall budget and deterministic parity
- for RCA4/CLA4: do not increase time-to-failure or conflict severity; improve time-to-typed-failure by `5%` on at least one profile, or hold it while reducing routing-stage work by `10%`
- require 1-3 successful iterations meeting both speed + correctness gates

## Planned next code work (post-this-pass)

- Add richer counters from `RouterReliability.NativeWork` where available.
- Add a dedicated benchmark command wrapper under `Scripts/` for thread-profile sweeps.
- Add optional `Perf` mirror block in `.PhysicalDesign.json` for post-hoc tooling.

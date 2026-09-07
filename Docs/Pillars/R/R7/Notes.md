# R7 notes

Working notes for [R7](R7.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- The current bounded Runtime slice keeps scheduling, admission, diagnostics,
  and publication decisions in Python; it adds no Rust policy or native-call
  expansion.

## Open questions

- None recorded.

## Working notes

### 2026-09-06 Runtime boundary record

The committed `f083554` → `9f432cd` → `f5ffd47` chain uses immutable typed
runtime documents, a synchronous Python adapter, and a bounded Python
spawned-process path. No `Kernels/Routing` source changed. This is a narrow
boundary-preservation record, not an R7 implementation or acceptance claim;
native cancellation, bounded native deadline checks, reference parity, and
measured justification for future native expansion remain open. The final
bounded-path evidence is recorded in the
[N1 final repair](../../N/N1/Notes.md#final-child-exit-and-admission-authority-repair).

### Authoritative route-batch outcomes

This bounded checkpoint keeps one narrow computational boundary in Rust:
coarse request normalization, detailed route-state expansion, relaxed connectivity
proof, immutable per-request receipt construction, and SHA-256 calculation with the
Rust `sha2` implementation. The native extension owns both the canonical scope bytes
and their digests; the exact bytes remain exposed for independent verification.
Request and context constructors seal immutable shared state before the separately
timed batch call. After entry, shared ownership avoids proportional cloning and the
one caller cutoff checks normalization, streaming canonicalization/hash, search,
proof, and finalization. Constructor preparation is recorded behavior, not claimed
as batch-attributable performance work.

Python and Joint continue to own caller identity truth, policy, alternative-access
selection, retry/continuation decisions, no-good interpretation, commitment,
diagnostics, and publication. Native labels caller model/technology/resource/
placement/access/policy/dependency values as echoed rather than producer-validated.
No Joint caller is migrated by this checkpoint, and no lifecycle, MCHPRS, Fabric,
cache, promotion, or publication responsibility moves into Rust.

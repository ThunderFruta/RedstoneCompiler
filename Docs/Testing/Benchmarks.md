# Routing benchmarks

| Benchmark | Purpose | Runs | Truth rows | Wall ceiling |
| --- | --- | ---: | ---: | ---: |
| FullAdder | Small correctness and deterministic overhead gate | 5 | 8 | 10 s |
| RippleCarryAdder4 | Repeated-stage congestion and regression gate | 3 | 512 | 25 s |
| RippleCarryAdder8 | 8-bit carry ripple scalability gate | 3 | 131072 | 30 s |
| CarryLookaheadAdder4 (compatibility) | Optional exact-proof compatibility check | 2 | 512 | 120 s |

All successful runs require zero final conflicts, zero unresolved claims,
authoritative physical simulation, identical repeated fingerprints, and no
fallback.

## Current checkpoint

The strict acceptance sequence is FA/RCA4/RCA8 with reproducible determinism and
strict no-fallback evidence.
Use acceptance compatibility mode to include the optional compatibility circuit check for
CarryLookaheadAdder4 and fixture-backed exact proof evidence.

Attempt-by-attempt behavior, evidence boundaries, and current hypotheses are
maintained in
[Current routing failures](../Routing/Active/CurrentRoutingFailures.md).

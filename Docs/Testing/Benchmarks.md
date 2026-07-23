# Routing benchmarks

| Benchmark | Purpose | Runs | Truth rows | Wall ceiling |
| --- | --- | ---: | ---: | ---: |
| FullAdder | Small correctness and deterministic overhead gate | 5 | 8 | 10 s |
| RippleCarryAdder4 | Repeated-stage congestion and regression gate | 2 | 512 | 25 s |
| CarryLookaheadAdder4 | Reconvergent high-fanout scale gate | 2 | 512 | 120 s |

All successful runs require zero final conflicts, zero unresolved claims,
authoritative physical simulation, identical repeated fingerprints, and no
fallback.

## Current checkpoint

FullAdder's focused complete-diagnostics test passes in the current working
tree. RCA4 currently fails after primary negotiated overflow progresses
`[124, 10, 10, 10, 10]`; therefore CLA4 is intentionally not run. See
[Negotiated route-tree router](../Routing/Active/NegotiatedRouteTreeRouter.md) for the
measured graph-size comparison and required fix.

The attempt-by-attempt RCA4 breakdown, evidence boundaries, and leading
hypotheses are maintained in
[Current routing failures](../Routing/Active/CurrentRoutingFailures.md).

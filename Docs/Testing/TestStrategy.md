# Test strategy

## Layers

1. Objective structural tests cover dependency direction, imports, API owners,
   and serialized contracts.
2. Contract tests cover policies, failures, claims, and deterministic ordering.
3. Placement tests cover boundary capacity, relocation cuts, and area limits.
4. Router tests cover provisional overlap, history costs, branch retention,
   repeater legality, and incremental graph reuse.
5. Deterministic integration tests compile representative designs without
   requiring the opt-in scale or live server tiers.
6. Scale and acceptance runs exercise complete physical compiles under explicit
   runtime and correctness gates.

Source size, local helper order, variable names, and internal call placement are
review signals rather than pass/fail contracts. Correctness-sensitive ordering
must be tested through observable results, typed failures, collaborator calls,
or event sequences.

## Required negotiated-router regressions

- capacity-one boundary matching and saturated cuts;
- temporary overlap followed by zero-overflow convergence;
- three-net conflict recovery without name-based priority;
- high-fanout shared-trunk retention;
- pruning only branches touching overuse;
- boundary-triggered one-tile graph expansion;
- repeated `AddRegion` deduplication;
- no approximately 271K-node full-graph escalation;
- directional signal-strength and repeater claims during search; and
- deterministic placement and route fingerprints.

Physical truth tables and final claim validation are authoritative. Unit tests
alone cannot qualify a router.

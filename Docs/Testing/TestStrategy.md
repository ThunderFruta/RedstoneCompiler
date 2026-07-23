# Test strategy

## Layers

1. Contract tests cover policies, failures, claims, and deterministic ordering.
2. Placement tests cover boundary capacity, relocation cuts, and area limits.
3. Router tests cover provisional overlap, history costs, branch retention,
   repeater legality, and incremental graph reuse.
4. Integration tests compile and physically validate representative designs.
5. Acceptance runs repeat complete physical compiles under immutable ceilings.

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


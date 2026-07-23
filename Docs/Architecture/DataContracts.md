# Data contracts

## Logical contracts

- Parsed modules contain scalar ports, gates, and signal connectivity.
- Synthesis produces a logically equivalent NAND-only module.
- Standard-cell definitions own pin cardinality and physical access geometry.

## Placement contracts

- `PcbPlacement` contains a complete placed design, packed-cluster metadata,
  local route claims, and placement diagnostics.
- Retained candidates have stable fingerprints and are routed before deferred
  placement alternatives are generated.
- Placement feedback is a typed boundary or congestion cut, not an arbitrary
  list of signal names.

## Routing contracts

- `NetRoutingProfile` defines root, targets, fanout, access paths, and optional
  local-tree seed.
- `RoutingGraphRegion` contains legal nodes and normalized edges.
- `RoutingResourceClaims` contains wire, support, required-air, and electrical
  cells.
- `NegotiatedRoutePlan` contains selected trees, iteration metrics, overflow
  progression, rerouted signals, and cached graph counts.
- `RoutedDesign` is publishable only after final authoritative validation.

## Evidence contracts

- Success: `.PhysicalDesign.json`, truth table, litematic, and fingerprints.
- Failure: `.RoutingFailure.json` with typed reason, stage, affected physical
  objects, partial work, and deadline state.
- Acceptance: sequential manifest with commands, hashes, ceilings, and repeated
  fingerprint comparison.


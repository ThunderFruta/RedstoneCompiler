# Incremental Physical Factor Reuse for Core-Guided CLA4 Repair

## Decision

Refactor physical component eligibility into two exact layers:

1. **Signal-local access factors**: the closed-component access witnesses,
   local claims, powered-tree support, and local terminal compatibility for a
   single signal.
2. **Placement-dependent exterior factors**: guide egress, exterior seams,
   aperture options, global claims, and cross-signal capacity assembly.

Only the first layer is reusable. A local factor is reused only when an exact
signal-local identity proves that the repair did not change any geometry,
claims, terminal contract, or technology input it reads. Exterior factors and
the final capacity proof are always recomputed for every repaired placement.

This is an internal router change. It introduces no command-line control and
does not change successful artifact identities.

## Why This Is Needed

CLA4 now produces complete, useful repair evidence:

- the broad ownership conflict is repaired through NandNet26;
- the remaining complete symbolic capacity core is NandNet28/NandNet29;
- repeated assembly replans have been bounded, so that core is no longer
  hidden by retry churn.

The core is discovered too late to prepare its targeted repair. A measured
run spent approximately 17s then 23s in physical eligibility preparation and
11s then 14s in unary support compilation before the NandNet28/NandNet29 core
became available. The final repair therefore reaches the 12s global-routing
reserve without entering global routing.

The current `PreparedPhysicalComponentPortFactorDomain` treats every factor
as placement-wide. That is correct but unnecessarily coarse: moving the two
clusters implicated by a capacity core invalidates the domain identity for all
signals, even when most signal-local access witnesses are unchanged. Reusing
the whole domain would be unsound because exterior seams and shared capacity
claims changed. Reusing only independently certified local factors is sound.

## Data Model

Add these internal immutable records in `Compiler/Routing/Models.py`.

```text
PreparedPhysicalSignalLocalFactorDomain
  Signal
  LocalIdentityFingerprint
  ComponentTopologyFingerprint
  TerminalContractFingerprint
  LocalGeometryFingerprint
  LocalClaimsFingerprint
  TechnologyFingerprint
  Complete
  Feasible
  LocalAccessFactors
  LocalSupportFacts

PhysicalSignalLocalFactorReuseEntry
  LocalIdentityFingerprint
  Domain
  SourcePlacementFingerprint
```

Add a non-identity `RoutingResources.PhysicalSignalLocalFactorDomainCache`.
It maps only `LocalIdentityFingerprint` to a complete immutable local domain.
It never stores incomplete work or a placement-wide aperture/seam result.

The local identity must contain, in sorted canonical form:

- signal name and owned terminal identities;
- the signal's reachable component-fabric subgraph, including node and edge
  coordinates;
- local access candidates and their owned-candidate fingerprints;
- local wire/support/air/electrical claims;
- fixed component obstacles intersecting the reachable subgraph;
- local power/repeater contract inputs;
- resource-graph and technology identities.

It must not contain guide cells, exterior fabric, global path claims,
placement-wide envelope bounds, sibling aperture selections, or the whole
placement fingerprint. Those belong to the exterior layer and force a fresh
calculation.

## Pipeline

```text
placement repair
  -> identify moved clusters and changed local geometry
  -> build signal-local identity for every interface signal
  -> exact cache lookup for unchanged complete local domains
  -> compile/cache changed local domains
  -> build fresh exterior seams and aperture options for every signal
  -> assemble fresh cross-signal capacity CSP
  -> global negotiated router and detailed legalizer
```

The component planner must report `LocalFactorCacheHitSignals`,
`LocalFactorRebuiltSignals`, and elapsed time for both local and exterior
layers. The routing-failure artifact must retain these fields beside existing
`PhysicalConnectorDiagnostics`.

## Implementation Strategy

### 1. Extract the local compiler without behavior changes

Move the local portion of `DecomposePhysicalPortLaneFactors` and unary support
preparation behind a pure function:

```text
PreparePhysicalSignalLocalFactorDomain(
  Problem, AccessCertificate, Signal, ResourceGraph
) -> PreparedPhysicalSignalLocalFactorDomain
```

Initially call it for every signal and compare its derived local factors and
unary clauses with the current monolithic result. Do not enable reuse yet.

### 2. Build exact invalidation evidence

For each core-guided placement repair, derive the moved cluster set and the
signals whose reachable local fabric intersects it. Rebuild identities for all
signals; cache reuse is permitted only on an exact identity match. A signal
outside the moved set may still rebuild if its local geometry or claims differ.

### 3. Enable cache reuse at the local boundary

Store only complete local domains. On a cache hit, use the stored immutable
local factors but reconstruct all exterior seam/aperture factors from the new
placement. Merge signal results in sorted signal order.

### 4. Reuse unary proof results only through local identity

Replace the current placement-wide unary cache key with:

```text
(local-domain identity, unary compiler version)
```

This permits unchanged signals to skip worker-process setup and DP expansion
on the NandNet28/NandNet29 repair. A changed signal always runs the complete
unary proof. Do not reuse pair or higher-order certificates unless their own
complete input identities match.

### 5. Keep exterior and global legality fresh

Always rerun:

- `PreparePhysicalComponentPortFactorDomain` exterior connector/seam work;
- exterior fixed-claim certification;
- physical port CSP and symbolic capacity proof;
- global portal routing and detailed negotiated legalization.

This prevents a stale local witness from certifying a changed exterior route.

## Failure Rules

- Incomplete local compilation is never cached and never converted to UNSAT.
- A cache hit with a mismatched resource graph, technology, local claims, or
  reachable fabric is a hard identity error, not a best-effort miss.
- Cache entries are immutable. The parent process alone publishes them after
  complete proof; worker processes return values only.
- A NandNet28/NandNet29 core may request one distinct local placement repair.
  Equivalent implicated-cluster geometry is rejected before physical work.
- The detailed router remains the final legalizer.

## Tests and Acceptance

1. Unit-test that identical local geometry yields a cache hit with byte-equal
   local factors and unary clauses.
2. Change one reachable node, terminal, claim, power contract, resource graph,
   or technology value; require a cache miss.
3. Move unrelated clusters without changing a signal's reachable local
   subgraph; require local cache hit but fresh exterior factor construction.
4. Move NandNet28/NandNet29 clusters; require their local domains to rebuild
   and unaffected signals to reuse only when identities match.
5. Compare cached and cold component preparation: same factor-domain
   fingerprint, selected ports, claims, truth table, and failure core.
6. Verify incomplete local work does not enter the cache or placement feedback.
7. Run focused component-pipeline, placement-feedback, determinism, FA, RCA4,
   and RCA8 tests.
8. Run CLA4 twice. Acceptance remains: under 120 seconds, emitted schematic,
   physical design, litematic, authoritative 512-row simulation, zero
   unresolved claims/conflicts, no fallback, and equal repeated fingerprints.

## Expected Benefit

The immediate objective is not to reuse global routing. It is to avoid
recompiling unchanged signal-local domains and their unary proofs after a
narrow capacity repair. That should move NandNet28/NandNet29 repair discovery
and preparation early enough to leave the existing global-routing reserve
intact. If the cached run still fails, its new terminal stage—not a longer
deadline—determines the next repair.

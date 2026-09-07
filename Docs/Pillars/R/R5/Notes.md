# R5 notes

Working notes for [R5](R5.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- Completed component-template cache storage binds each exact produced
  component net and foreign-transit reservation to a required immutable
  `GenericClaims` receipt created by its solver.  The receipt is transported in
  category and original-ordinal order and covers wire, support, required-air,
  and electrical claims.  Cache publication copies those produced receipts;
  it never rereads the mutable resource graph after the solve.  A translated
  hit moves the receipt with its net and requires exact current generic equality
  across all four claim fields.  Missing, malformed, legacy, or incoherent
  provenance falls through to an ordinary solve.  Repeater-bearing public
  claims remain separate from the generic electrical receipt.  This
  is not current-world freshness authority, a selected-access receipt/envelope
  authority, topology-wide R5 completion, or N6 salvage.
- Batch 1 uses explicit imports from Regions `TopologyIdentity` and
  `PhysicalDependencies`; extraction, package exports, shared contracts and
  production caches/consumers are unchanged.
- Canonicalization schema `ordered-nand-region-v1` visits ordered output roots
  and Input0/Input1 edges, retaining shared nodes once. The rooted ordered DAG
  domain needs no factorial canonical search. Region traversal is bounded by
  18 gates; source indexing still scales with the originating module.
- `selected-access-dependencies-v1` requires all 18 categories: technology,
  graph/model version, placed model, cell/templates, transforms, policy, domain,
  access domain, access witness, ordered boundary bindings, bounds, selected
  paths, ownership, support, required air, electrical influence, foreign frozen
  wires and external reservations. Subject and snapshot identities are also
  bound. Missing categories, even on both sides, remain unresolved.
- Opaque producer identities carry producer, exact revision, recognized SHA-256
  encoding and value. Legacy 16-hex prefixes retain their original collision
  limits. Producers remain responsible for issuance and complete category
  content; this comparison cannot authenticate their claims or validate legality.
  Syntactically valid but unverified or invented revisions/values can match as
  declarations. Every comparison reports `ProducerValidation=NotPerformed`:
  source existence, issuance, content correctness and current compatibility with
  the pinned Physical producer have not been checked. Known revisions are not
  an authenticity allowlist. Unknown coverage/encoding/scope remains distinct
  from a well-formed unverified declaration.
  Placed-model identity must cover resource inclusion freshness, owned and
  unowned exclusions and current foreign wires. Boundary-binding identity must
  cover ordered signal/gate/kind/role/pin/terminal/face bindings. Full paths and
  claims cannot be replaced by endpoint or bounding-box summaries.
- Boundary counts follow extraction's distinct-consumer convention; repeated
  gate pin edges remain distinct in topology. Capacity and external counts are
  separate dependency data. Missing net declarations use scalar IR defaults;
  explicit non-scalar or constant metadata is rejected, and gate attributes
  are never guessed to be irrelevant.

## Open questions

- Producer validation and a real coordinator consumer remain future work.
  No normalized match establishes reusable physical work or completion of R5.

## Working notes

- Batch 1 candidate starts at `4de7305380f91995a842e955f2b9bdedced0f61a`,
  common Router base `c0aaf5f00bbba7c0aefe6733c7d1150a3bb76a1d`.
  [Pinned interface context](../../CapabilityDependencies.md#reuse-batch-1-interface-context)
  is not an integrated producer revision or a production-path migration.
- Owning tests use real extracted modules, independent explicit adjacency and
  exhaustive small node-bijection checks. A separate 18-node chain oracle checks
  the supported maximum; a real 19-node extraction is rejected. Counterexamples
  cover role swaps, same-degree/coarse-hash graphs, reconvergence, shared outputs,
  repeated terminals, stale boundary counts and every changed/missing dependency.
- Candidate evidence is retained at `Output/ReuseBatch1/20260907TBatch1/`:
  `Summary.txt`, `RawDump.txt`, source manifests before/after and final recheck,
  candidate patch, native identity, pytest logs/XML, oracle comparisons and
  deliberately broken-implementation challenges. The final report records exact
  gate outcomes and hashes; candidate source has not been committed or integrated.
- Dependency fixture hashes are synthetic declarations for contract testing.
  Physical producer validation, MCHPRS/Fabric acceptance, native changes,
  performance acceptance, production caches, lazy expansion and stale-result
  salvage are not claimed by this prerequisite.

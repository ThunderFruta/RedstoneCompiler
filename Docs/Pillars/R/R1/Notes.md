# R1 notes

Working notes for [R1](R1.md). This file is non-normative; the requirement file
controls when the two disagree.

## Current context

- History/checkpoint branch: `R1-Routability-By-Construction`.
- Ongoing implementation bucket: `Reuse-And-Salvage`, shared with R5/N6 under
  the [bucket map](../../WorktreeBuckets.md). Reconcile unique old branch work
  before reuse; this assignment does not merge or discard that history.
- Uncommitted work is not included in [CommitHistory.md](CommitHistory.md).

## Decisions

- Batch 3's cache lifetime is exactly one uncached
  `BuildPinAlignedPackedClusterPortfolio` evaluation. The global portfolio
  cache is checked first; a hit performs no oriented-cache work and cannot
  replay the receipt from the producing evaluation.
- Before that global lookup, the same authoritative complete producer-context
  value plus strong macro/template mapping and loaded-object generation
  identities enter the portfolio key. This check applies equally with
  `UseOrientedGeometryCache=False`. Unsupported or recursive identity skips
  global lookup and publication; changed identity rebuilds under a distinct
  key. No oriented request or producer-call metric is charged for identity
  validation alone.
- Loaded-template identity follows explicit option B: extra template entries
  are permitted, but every entry contributes its exact key, concrete class,
  `Size`, and canonically frozen complete `Blocks`/state content. Macro entries
  still define the supported oriented-key domain and its finite bound. An
  extra template is never ignored merely because it has no matching macro.
- Both cache-enabled and eager portfolio misses use an explicit resolver bound
  to the pre-lookup identity. The eager resolver calls the public geometry
  producer without retaining masks; both resolvers construct current access
  rays without the legacy transform-only LRUs. Global publication additionally
  requires resolver attestation. The ordinary no-context
  `PcbGatesConflict` path remains unchanged for unrelated consumers.
- `BuildPlacedCellGeometryWithKeepOut` remains the sole miss and fallback
  producer. Each valid miss stores `ActualBlocks`, `ElectricalBlocks`,
  `SolidBlocks`, and `ExplicitKeepOut` as origin-relative frozensets.
  Translated exclusions are exactly
  `Technology.BuildElectricalExclusions(ElectricalBlocks) | ExplicitKeepOut`.
- The only key is canonical kind, normalized 0/90/180/270 rotation, and
  macro-legal mirror. Origin is excluded only because every returned mask is
  translated from an immutable origin-relative entry. INPUT and OUTPUT ignore
  a requested mirror; NAND retains both mirror identities. The macro-derived
  finite bound is currently 16 and no in-context eviction is permitted.
- The fixed producer context is enforced at creation, before every resolution,
  and after every miss call. Its deterministic value identity covers the
  versioned schema, complete public `CellMacro` values, exact template size and
  canonically sorted complete block/state content, and the full frozen
  technology value and concrete class. Strong references separately bind the
  exact macro/template mapping and loaded object generation. Any unsupported
  or changed identity uses the eager public producer and a stable fallback
  reason; unknown kinds and malformed transforms never hit.
- `PcbGatesConflict` accepts an explicit context only for the packed portfolio
  and resolves each gate once when exact masks are required. Rectangle overlap,
  broad phase, access rays, signal ownership, pin-access conflicts and every
  non-mask conflict rule retain their existing control flow. The no-context
  behavior remains compatible. Final eager geometry and electrical-isolation
  validation remains authoritative.
- The frozen receipt is non-semantic portfolio evidence. It enforces
  `RequestCount = CacheHitCount + CacheMissCount + EagerFallbackCount`,
  `ProducerCallCount = CacheMissCount + EagerFallbackCount`,
  `AvoidedGeometryExpansionCount = CacheHitCount`, and
  `TranslationMaterializationCount = CacheHitCount + CacheMissCount`.
  Every count has exact nonnegative-integer type, `PortfolioCacheHit` has exact
  boolean type, fallback reasons are nonempty sorted unique `(reason, count)`
  tuples with positive integer counts, and retained entries cannot exceed the
  finite maximum.
  Avoided expansion means only public-producer calls avoided against the
  one-request/one-producer eager reference.
- The Physical-Rules owner decisions are preserved: there is no Physical rule,
  builder, technology, template, selected-access or native source change;
  rectangle overlap, broad phase, access, signal ownership, support, headroom,
  route claims and eager validators remain authoritative. The Joint owner
  decisions are preserved: ordered states, candidate indices, objectives,
  fingerprints, dominance, selected maps and failure classification remain
  eager-equivalent; there is no orchestration migration or dependency on
  `FrozenPhysicalPlacementContract`.
- Batch 2's exact-current contract shares the immutable
  `RoutingStaticGeometry` sets and the same current-lineage
  `RoutingResourceGraph` within one unchanged eager lineage. The graph is a
  mutable dataclass; its private pure region/claim memoization is intentionally
  graph-owned and shared, not fork-local state. Every sibling starts with
  dataclass-default top-level fork-local state. The test seeds every
  discovered top-level mutable container plus representative tuple/Any portal,
  proof/result, candidate, assignment, and prepared-component fields before
  forking; only those two static fields are allowed to share. The graph's own
  pure region/claim memoization is deliberately shared with its graph.
- The nonempty oracle uses the public `Nand.litematic` at a legal 90-degree
  orientation and writes the clockwise transform in the test. A foreign frozen
  wire must affect `ElectricalBlocks` without entering
  `TemplateElectricalBlocks`; a separate sloped public route claim observes
  support and required-air cells.
- Changed placement and frozen-wire controls are new eager lineages, not fork
  inputs. The candidate makes no stale-result, topology, selected-access,
  route/proof, score, cache-authority, or coordinator-commitment claim.

## Open questions

- None recorded.

## Working notes

- Further-corrected Batch 3 starts from `75a62f87d2df127c46c08ee27b892e59432fb735`
  (tree `d6417f00d1a0eeef3f4515f7947a456a96d33bad`) on
  `Reuse-And-Salvage`. Fresh candidate evidence is retained under
  `Output/ReuseBatch3/20260907T055754Z/`. The candidate is uncommitted and
  awaits fresh independent review; it is not a complete R1 implementation,
  R5/N6 evidence, integration acceptance, or promotion evidence.
- Superseded corrected patch
  `0aaed23b7828a1d25fefd02f41521e22085366b1e3b2a5a4f286cebcd1c12954`
  and `Output/ReuseBatch3/20260907T053511Z/` are retained unchanged as rejected
  history. It ignored complete content for extra loaded templates and allowed
  eager portfolio evaluation to inherit transform-only legacy geometry/access
  LRUs. Its prior review result is invalidated and no PASS transfers.
- Rejected patch
  `23537ac8dfdb4357d3e0cda0e31aa02504f4296a31c60bdb6a5f226438039f3f`
  and its original `Output/ReuseBatch3/20260907T050819Z/` evidence are retained
  unchanged as negative history. That patch admitted stale process-global
  portfolio replay after producer identity changed, and its property probes
  overclaimed mutation testing; none of its PASS conclusions transfer.
- The [legacy R1 audit](../../R1HistoryReconciliation.md) found archive,
  policy/catalog prerequisites, and shared documentation rather than a distinct
  lazy-expansion implementation. Their correct bucket dispositions are recorded
  in the history. R1 implementation/acceptance remains a target.
- The completed reconciliation commit is
  `0cbb17916fe3239adee04762c0fddd7cea3d2a4c`. The outcome-first cleanup
  `5cd445621d492e1b62fa72d63c3d021945edbc8a` is historical, not this
  worktree's current tip. This candidate's live base is
  `e4609e7f96a65a437ff33c04300f1111204e4081`. At correction inspection the
  locally advertised `origin/Reuse-And-Salvage` ref was
  `4de7305380f91995a842e955f2b9bdedced0f61a`; the local branch had no
  configured upstream, so this records no upstream attachment or publication.
- `Output/ReuseBatch2/20260907T034144Z/` is superseded first-candidate evidence
  and is preserved unchanged. Correction evidence is retained under
  `Output/ReuseBatch2/20260907T035701Z/`, including source/native provenance,
  the independent public-template geometry oracle, observed non-gating
  `WorkCheck` stream, populated-state mutation observations, transcripts,
  commands, and candidate hash. It is superseded, but preserved unchanged, by
  `Output/ReuseBatch2/20260907T042355Z/`. The corrected candidate is
  uncommitted and not independently accepted; it is not capability-proven or
  promotion-ready evidence.

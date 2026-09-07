# R1 commit history

This file audits history previously associated with the legacy R1 branch.
Those commits are prerequisites and cross-cutting work, not implementation of
[R1 lazy physical expansion](R1.md). See the
[bucket reconciliation](../../R1HistoryReconciliation.md) for exact dispositions,
equivalence checks, extraction commits, and verification.

| Original commit | Date | Correct scope and bucket |
|---|---|---|
| `14646a9` — Archive router benchmark baselines | 2026-09-04 | Archive implementation remains on Telemetry-And-Acceptance; joint-router scope remains in Joint checkpoint `ea77a28`; old documentation relocation is not replayed |
| `b8160bb` — Add opt-in routing-aware placement policy | 2026-09-04 | Joint-Physical-Design prerequisite already represented by `789fbd3` and the later v17-default decision; remaining archive integration extracted into Telemetry-And-Acceptance |
| `7c68af4` — Define exact pin-access catalog contracts | 2026-09-04 | Physical-Rules rule/proof binding and joint placement/access types are consolidated in Physical checkpoint `2f69160` |
| `22d112f` — Document physical design pillars and contracts | 2026-09-04 | Shared requirement/governance documentation already represented at current paths; not an R1 implementation |

No R1-specific implementation commit is established by this audit. Future
entries must identify actual lazy-expansion behavior and its scoped verification.

| Candidate state | Date | Correct scope and bucket |
|---|---|---|
| Further-corrected uncommitted Batch 3 candidate based on `75a62f8` | 2026-09-07 | Adds one finite per-uncached-portfolio cache for exact translation-normalized immutable oriented cell masks, enforced full producer-context identity before global lookup and every local resolution, eager fallback for recursive/unsupported identity, strict immutable receipt domains, and a non-semantic current-invocation receipt. Option B freezes exact key/class/Size/full Blocks/state for every loaded template, including entries without macros. Both cached and eager portfolio misses use identity-attesting current resolvers and bypass the legacy transform-only geometry/exclusion/access LRUs. The source-bound reconvergent Joint regressions cover in-place extra-template content and all-air NAND changes without clearing, current eager parity, restored zero-work hits, exact states/objectives, and current AccessLength errors. Evidence is retained at `Output/ReuseBatch3/20260907T055754Z/`. Entirely fresh independent review and commit approval remain required. |
| Superseded corrected Batch 3 patch `0aaed23b` based on `75a62f8` | 2026-09-07 | Fixed macro/template-generation/schema/technology stale replay, recursion, receipt domains and evidence wording, but froze full template content only for macro keys and left eager portfolio evaluation on legacy transform-only geometry/exclusion/access LRUs. Extra-template in-place changes could retain false hit provenance; all-air NAND changes could poison a new global entry with stale helper geometry. `Output/ReuseBatch3/20260907T053511Z/` remains unchanged as rejected history; its review PASS is invalidated. |
| Rejected Batch 3 patch `23537ac8` based on `75a62f8` | 2026-09-07 | The process-global portfolio key omitted complete macro/template/schema/generation identity, so a changed NAND macro could replay stale states with a false zero-work hit. Its `Output/ReuseBatch3/20260907T050819Z/` evidence is retained unchanged as rejected history; property/differential probes were also mislabeled as executed mutation tests. No PASS or readiness conclusion transfers to the corrected candidate. |
| Uncommitted corrected Batch 2 candidate based on `e4609e7` | 2026-09-07 | Adds an independent nonempty exact-current static-geometry-sharing contract test and R1 documentation in Reuse-And-Salvage. It seeds every fork-local mutable container and representative non-container state before fork, then requires fresh dataclass-default state in each child while sharing immutable static geometry sets plus the same current-lineage mutable resource graph and its private pure memoization. It does not establish lazy expansion, a measured copying reduction, R5 topology reuse, or N6 salvage. Independent review and commit approval remain required. |

The reconciliation record is committed as
`0cbb17916fe3239adee04762c0fddd7cea3d2a4c` — `Record reuse and salvage history`.
Historical cleanup `5cd445621d492e1b62fa72d63c3d021945edbc8a` — `Refocus
active tests on observable outcomes` — is not the live candidate base. The
candidate base is `e4609e7f96a65a437ff33c04300f1111204e4081`; the separately
locally advertised `origin/Reuse-And-Salvage` ref was `4de7305` at correction
inspection, with no configured upstream attachment asserted. Neither historical
cleanup nor this uncommitted candidate establishes R1 implementation or a new
R1 acceptance result.

# Legacy R1 history: bucket reconciliation

The old `R1-Routability-By-Construction` branch ends at
`22d112f6aea02ab7b995b562230f971ab08119e2`. Its name does not describe a completed
R1 lazy-expansion implementation. This audit separates its mixed prerequisites,
evidence infrastructure, and documentation into the [worktree buckets](WorktreeBuckets.md).
It preserves the old commits rather than rewriting or deleting their history.

## Commit-by-commit disposition

| Original commit | Actual contents | Correct owner | Disposition |
|---|---|---|---|
| `14646a9` — Archive router benchmark baselines | Archive publisher, complete reports/checksums, harness integration/tests; also broad documentation moves and architectural scope prose | Telemetry-And-Acceptance for archives; Joint-Physical-Design for placement/router scope; shared documentation governance for layout | Archive feature and scope guidance are preserved on their simplified owner branches; obsolete documentation-directory relocation is not replayed |
| `b8160bb` — Add opt-in routing-aware placement policy | Strategy/policy selection, caller/provenance tests, and archive-aware harness integration | Joint-Physical-Design; archive-specific integration belongs to Telemetry-And-Acceptance | Core policy is represented by Joint checkpoint `ce96cde`; archive integration remains independently owned by Telemetry-And-Acceptance |
| `7c68af4` — Define exact pin-access catalog contracts | Physical access templates, technology/resource claims and proof identities, placement/access records and fixtures | Physical-Rules for rule/proof binding; Joint-Physical-Design for placement/access consumers | Patch-equivalent implementation and attribution are consolidated in Physical checkpoint `2f69160` |
| `22d112f` — Document physical design pillars and contracts | Requirement catalog and architectural guidance, using the older documentation layout | Shared governance; each actual R/N requirement remains with its primary bucket | The entire original pillar tree is byte-identical to `b82b8ee:Docs/Pillars`; architectural review prose matches after Markdown target normalization. Preserve current paths and newer ledgers |

The legacy merge bridge `b066502` has exactly the same tree as the common
ancestor `d578b9b`. Its different merge ancestry, including the older layout
merge commits, is not a separate feature patch to cherry-pick into a bucket.

## Resulting bucket checkpoints

| Bucket | New checkpoint | Scope |
|---|---|---|
| Telemetry-And-Acceptance | Simplified branch checkpoint | Functional archive extraction, current-layout archive documentation, R8/N5 attribution |
| Joint-Physical-Design | `ea77a28` | Circuit-agnostic core-router scope, R2 implementation/evidence, and policy/catalog dependency records |
| Physical-Rules | `2f69160` | R10/N2 catalog, realization, proof, and domain-query checkpoint |
| Reuse-And-Salvage | This reconciliation record | Correct the R1 history classification; no new lazy-expansion, normalized reuse, or salvage implementation is claimed |
| Runtime-And-Kernels | No new change from this audit | No distinct runtime/native implementation was found to extract |

These bucket commits have not been merged into `Router-Refactor(R10-N5)`;
its common starting point is the capability-neutral Router Refactor history. `main`, CLA4 work, and the
legacy R1 ref are unchanged. New code is confined to the telemetry bucket.

## Equivalence and preservation evidence

- Stable patch ID of both legacy `7c68af4` and pre-rewrite `96d9604`:
  `86b9e35fa6dd9e44c9492db039d5c05f564f7222`.
- Tree ID of both `22d112f:Docs/Pillars` and `b82b8ee:Docs/Pillars`:
  `3259d9b54cabbd2fb9412dc7ee084fd7f4b5b6ad`.
- Tree ID of both `b066502` and `d578b9b`:
  `3a06475930d1e353f99cb7be057631f50c546791`.
- The old architecture review matches the current review text after replacing
  Markdown link destinations only. This establishes documentation equivalence,
  not runtime equivalence.
- Extracted `App/BenchmarkArchive.py` exactly matches original blob
  `cc1d5dea0236aeb388bf095678d973a0397dc2e0`; its core test file exactly matches
  `94ca29036691b5e461d371997ab576e932ddbfa6`.
- The archive-aware harness matches the legacy R1 tip except for preserving
  current v17-default help. Its provenance source scope correctly includes
  `Compilation/` and `Formats/`, rather than the obsolete `Compiler/` path.
- Two imported synthetic baseline-mirror tests initially failed because the new
  worktree has no local venv. They now simulate only the required interpreter
  file and pass its exact path explicitly; no compiler is launched and no
  production interpreter check is weakened.

The original documentation relocation into `Docs/Main/`, `Docs/Branches/`,
and `Docs/Features/` remains available in the legacy R1 history. It is not a
missing R1 algorithm, and replaying it would move the current shared docs and
invalidate newer links. Its useful archive guidance and router scope have been
ported to their current owners without reintroducing that alternative layout.
No old commit, generated evidence, runtime payload, or worktree was deleted.

## Verification

In `/mnt/Projects/RedstoneCompiler-Worktrees/Telemetry-And-Acceptance`:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/bananawewe/.codex/worktrees/634f/RedstoneCompiler/.venv/bin/python -B -m pytest -q \
  Tests/App/test_benchmark_archive.py \
  Tests/Tools/test_router_acceptance_archiving.py \
  Tests/Tools/test_router_acceptance_harness.py \
  Tests/Structural/test_source_structure.py \
  Tests/Structural/test_routing_design_snapshot.py

PYTHONDONTWRITEBYTECODE=1 /home/bananawewe/.codex/worktrees/634f/RedstoneCompiler/.venv/bin/python -B -m pytest -q
```

- Focused archive/harness/structural/snapshot gate: **109 passed**, 71 subtests,
  14.37 s, after correcting the test-only venv assumption.
- Full Python suite: **1,577 passed**, 3 skipped, 257 subtests, 61.25 s.
- Joint-design documentation structural gate: **9 passed**, 2.46 s.
- Changed documentation links and diff checks pass. App/core test blob identity,
  source-equivalence checks, and bucket scopes were verified explicitly.
- The telemetry worktree uses the existing interpreter without an editable
  install into a shared environment. Its ignored native module matches SHA-256
  `c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`.
- Rust/Gradle gates and live physical acceptance: **not-run**; no routing,
  kernel, or Java source changed. Synthetic archive outcomes are infrastructure
  tests, not successful physical benchmark runs or full R8/N5 acceptance.

R1 remains a target for lazy physical expansion. Do not count the old branch's
catalog definitions, policy selector, or archive publisher as implementation of
that requirement.

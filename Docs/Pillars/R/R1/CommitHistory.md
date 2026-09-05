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

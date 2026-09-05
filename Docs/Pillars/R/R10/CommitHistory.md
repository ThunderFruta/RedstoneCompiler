# R10 commit history

This file records commits intentionally attributed to [R10](R10.md). A commit
is listed only when its scope and verification are known.

| Commit | Date | Relationship to R10 |
|---|---|---|
| `f411c92` — Define exact pin-access catalog contracts; patch-equivalent legacy R1 commit `7c68af4` | 2026-09-04 | Reclassified shared-rule prerequisite: technology-bound physical access templates, exact claims, and proof identities. This contributes a pin-access slice, not the repository-wide shared model or full R10 acceptance. |
| `0ab510a` — Define selected-access physical realization | 2026-09-05 | Centralize physical realization and legality of selected access geometry in Physical-Rules. This is a bounded access slice, not complete R10 migration. |
| `9aa31fc` — Harden physical-access proof contracts | 2026-09-05 | Add strict physical-access proof transport and corruption rejection around shared model identities. |
| `3df59e9` — Own physical access-domain solver | 2026-09-05 | Consolidate exact access-domain construction and physical conflict queries under Physical-Rules authority. |

The placement/access consumer contract types also support
Joint-Physical-Design. See [N2 attribution](../../N/N2/CommitHistory.md) and
the [R2 history](../R2/CommitHistory.md). The complete stable patch ID shared
by `7c68af4` and the pre-rewrite `96d9604` is
`86b9e35fa6dd9e44c9492db039d5c05f564f7222`.

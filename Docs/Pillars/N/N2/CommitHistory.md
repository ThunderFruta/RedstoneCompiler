# N2 commit history

This file records commits intentionally attributed to [N2](N2.md). A commit is
listed only when its scope and verification are known.

| Commit | Date | Relationship to N2 |
|---|---|---|
| `dc95e349` — Enforce current selected access and add conformance coverage | 2026-09-06 | Current selected-access freshness checkpoint: rejects stale terminal bindings, resource-model identities, and technology identities without regenerating or re-solving access. The Physical checkpoint is available to Joint planning but does not prove consumer integration or complete N2 acceptance. |
| `2f69160` — Implement physical access rule contracts | 2026-09-05 | Squashed Physical-Rules checkpoint: exact catalog, physical realization and legality, proof codecs, domain construction, conflict queries, and Physical-owned tests. Joint candidate selection and integration tests remain outside this branch. |

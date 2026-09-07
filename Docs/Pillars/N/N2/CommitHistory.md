# N2 commit history

This file records commits intentionally attributed to [N2](N2.md). A commit is
listed only when its scope and verification are known.

| Commit | Date | Relationship to N2 |
|---|---|---|
| `f23293a` — Reject stale resource graphs missing frozen wires | 2026-09-06 | Current Physical-Rules checkpoint. It rejects stale supplied resource graphs when selected claims require a frozen electrical position they omit, before a model identity can be accepted. It is bounded N2 producer behavior, not complete N2 acceptance. |
| `1d9e899` — Enforce current selected access and add conformance coverage | 2026-09-06 | Earlier Physical-Rules checkpoint replacing historical same-parent source/test commit `dc95e349`; rejects stale terminal bindings, resource-model identities, and technology identities without regenerating or re-solving access. It does not prove consumer integration or complete N2 acceptance. |
| `2f69160` — Implement physical access rule contracts | 2026-09-05 | Squashed Physical-Rules checkpoint: exact catalog, physical realization and legality, proof codecs, domain construction, conflict queries, and Physical-owned tests. Joint candidate selection and integration tests remain outside this branch. |

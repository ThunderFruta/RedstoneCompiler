# N3 commit history

This file records commits intentionally attributed to [N3](N3.md). A commit is
listed only when its scope and verification are known.

| Commit | Date | Relationship to N3 |
|---|---|---|
| `f946ffc` — Enforce selected access across joint routing | 2026-09-05 | Physical-design orchestration selects and attaches an immutable, deterministic access witness. Routing consumes that selection instead of committing a replacement access choice. |
| `ec6c7d1` — Harden joint access handoff | 2026-09-05 | Add immutable five-stage observations, result transport, commitment validation, and integration-style context-adapter tests. Physical proof codecs remain owned by Physical-Rules. No live case reaches successful five-stage finalization. |

See the [R2 Stage 1 conformance ledger](../../R/R2/Notes.md#stage-1-conformance-ledger).
Full joint-candidate dependency manifests, reusable subclaims, cross-worker
commitment, and global N3 acceptance remain outside this slice.

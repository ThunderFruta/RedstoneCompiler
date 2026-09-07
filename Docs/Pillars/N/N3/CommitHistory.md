# N3 commit history

This file records commits intentionally attributed to [N3](N3.md). A commit is
listed only when its scope and verification are known.

| Commit | Date | Relationship to N3 |
|---|---|---|
| `ea77a28` — Implement joint physical-design Stage 1 | 2026-09-05 | Squashed Joint checkpoint: orchestration selects an immutable access witness; routing consumes it; result transport and commitment validation retain five-stage identity. Physical proof codecs remain owned by Physical-Rules. The historical `2024d7d` live cases did not reach successful five-stage finalization. |

Current Joint `64efbe1` is a dependency merge, not another N3 production
implementation commit. Its controlled two-NAND fanout supplies the current
five-stage immutable-witness/coordinator-publication evidence; broader
cross-worker commitment and reuse remain outside this slice.

See the [R2 Stage 1 conformance ledger](../../R/R2/Notes.md#stage-1-conformance-ledger).
Full joint-candidate dependency manifests, reusable subclaims, cross-worker
commitment, and global N3 acceptance remain outside this slice.

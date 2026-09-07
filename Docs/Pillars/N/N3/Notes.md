# N3 notes

Working notes for [N3](N3.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- Only the physical-design coordinator may commit accepted design state.

## Open questions

- None recorded.

## Working notes

### 2026-09-06 current frontier

Current Joint `64efbe1` proves the bounded public two-NAND fanout keeps its
selected access witness and current Physical identities immutable through
placement, access fabric, raw assignment, detailed routing, compaction, and
coordinator publication. Stale selected access is rejected before later-stage
work; no worker or cache receives independent accepted-state authority.

This is a controlled N3 path only. Reusable subclaim manifests, cross-worker
deterministic commitment, selective salvage, and global N3 acceptance remain
unproved or not run. See the [bounded R2 evidence](../../R/R2/Notes.md#2026-09-06-current-bounded-frontier).

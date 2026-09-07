# R3 notes

Working notes for [R3](R3.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- Cell coordinates remaining unchanged is insufficient for stale placement
  salvage; pin access, physical influence, capacity, and neighborhood
  dependencies must also remain valid.

## Open questions

- None recorded.

## Working notes

### 2026-09-06 current frontier

Current Joint `64efbe1` proves a straight-only public two-NAND fanout handoff
through the real downstream stages. It supplies no folded, mirrored, rotated,
serpentine, or alternative pin-facing result. R3 remains target behavior; the
historical FullAdder/RCA diagnostics do not establish a present R3 failure or
success. See the [bounded R2 evidence](../R2/Notes.md#2026-09-06-current-bounded-frontier).

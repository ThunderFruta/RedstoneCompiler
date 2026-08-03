# Routing design and active status

The active routing design notes are now maintained in:

- [`Docs/Reference/RoutingDesignDoc.md`](Docs/Reference/RoutingDesignDoc.md)
- [`Docs/Routing/Active/`](Docs/Routing/Active/)

The current active strategy is the authoritative default router exposed by
`Compiler/Main.py` (`--routing-strategy default`, which is the only supported
strategy in the CLI).

Current live status (2026-08-02): RippleCarryAdder4 completes successfully with an
authoritative routed artifact from a non-interactive run; CarryLookaheadAdder4 fails
in the physical emission phase with `prepared physical factor batch identity mismatch`.

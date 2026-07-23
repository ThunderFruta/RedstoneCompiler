# Routing resource graph

## Authority

`Compiler/Routing/ResourceGraph.py` owns the physical routing-resource model.
Nodes represent legal wire positions. Edges represent legal Redstone movement
primitives. Route claims include wire, support, required air, and electrical
exclusion cells.

## Lazy regions

`RoutingResourceGraph.BuildRegion` accepts physical bounds, allowed X/Z
columns, and allowed access nodes. Exact requests are cached. Compatible cached
regions may seed a larger height or column request so previously constructed
nodes and edges are reused.

`CachedNodeCount` and `CachedEdgeCount` count the union of cached regions, not
the size of the most recent request.

Rust `RoutingContext.AddRegion(Nodes, Edges)` adds only newly exposed graph
content, validates that every edge references present nodes, sorts adjacency,
and deduplicates nodes and edges.

## Required expansion behavior

- Initial region: coarse route tiles plus one true tile halo.
- Expansion unit: one negotiated tile on implicated sides.
- Delta: new nodes and edges only.
- Reuse: existing adjacency, route trees, occupancy, and history remain live.
- Forbidden escalation: rebuilding the entire placement-wide graph.

The regression guard must prove repeated offender expansion reuses the same
context and does not recreate the former approximately 271K-node graph.

## Claims

Different signals conflict when their claims violate capacity-one ownership or
Redstone electrical isolation. Same-signal branches may share their own
resources. Final validation is authoritative even when planning predicts zero
overflow.

## Diagnostics

Record requested and added columns, cache hit/miss, base-region identity,
added and total node/edge counts, active tiles, halo size, and expansion cause.


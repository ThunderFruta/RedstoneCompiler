# Hierarchical routing regions

## Region levels

1. Placement bounds define the absolute legal physical envelope.
2. Coarse tiles model crossing capacity and congestion.
3. Per-net active tiles bound ordinary detailed search.
4. A one-tile extended halo supplies detour and claim context.
5. A validation context covers all electrical and repeater effects.

## Tile geometry

Tile pitch is `4 * Technology.TrackPitch`. Capacity is derived from legal
crossing slots in the actual technology, never from a circuit name or fixed
benchmark constant.

## Expansion

Boundary detection marks the sides touched by a route, search frontier, or
overflow hotspot. Only those sides expand by one tile. Multiple offenders may
share the newly cached region, but their active-tile ownership remains
independent.

Three non-improving detailed iterations produce a congestion cut. Region
expansion does not reset the stagnation history or deadline.

## Placement coupling

When expansion cannot clear a saturated boundary or would exceed the physical
envelope, return the contributing nets and clusters to placement. Placement
may perform three relocation rounds within the two-times packed-area cap.


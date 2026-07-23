# Router research and implementation inspiration

Status: active design reference

Last reviewed: 2026-07-22

## Selection criteria

References are included only when they provide either directly reusable source
structure or a close algorithmic match for negotiated Redstone routing.
External code is an implementation reference, not a dependency. Preserve its
license and provenance before copying any code.

## Corolla: the exact region-expansion idea

Paper: [Corolla: GPU-Accelerated FPGA Routing Based on Subgraph Dynamic
Expansion](https://ceca.pku.edu.cn/media/lw/137e5df7dec627f988e07d54ff222857.pdf)

Corolla restricts each net to a routing subgraph, detects when its route tree
touches that subgraph's boundary, and expands the subgraph on a later
negotiation iteration. This is the closest match to the RCA4 failure: a legal
tree exists inside the current region, but congestion cannot be eliminated
without detouring outside it.

Adopt:

- per-net active subgraphs;
- explicit boundary-touch detection;
- incremental expansion rather than full-graph rebuilding; and
- preservation of PathFinder congestion history across expansion.

Do not adopt the GPU Bellman-Ford implementation. The Redstone graph and Rust
search boundary are already different and substantially smaller.

## VPR: persistent route trees and partial pruning

Documentation: [VPR RouteTree](https://docs.verilogtorouting.org/en/latest/api/vpr/route_tree/)

Source: [VPR route directory](https://github.com/verilog-to-routing/vtr-verilog-to-routing/tree/master/vpr/src/route)

VPR keeps one route tree per net, pushes the existing tree into the routing
heap, appends new sink paths, updates global occupancy, and prunes congested
paths between iterations while retaining legal portions.

Adopt:

- one persistent tree per signal;
- target-branch identity;
- occupancy decrement before pruning and increment after commit;
- retained tree nodes as multi-source search starts; and
- a spatial lookup for attaching a new path to the existing tree.

## OpenROAD TritonRoute: bounded repair regions

Documentation: [OpenROAD detailed routing](https://openroad.readthedocs.io/en/latest/main/src/drt/README.html)

Source: [OpenROAD DRT](https://github.com/The-OpenROAD-Project/OpenROAD/tree/master/src/drt/src)

TritonRoute separates the box a worker may modify from the extended context it
loads and the box it checks for design-rule violations.

Redstone mapping:

| TritonRoute concept | RedstoneCompiler concept |
| --- | --- |
| route box | offender coarse tiles |
| extended box | one-tile search and claim halo |
| DRC box | electrical, support, air, and repeater validation context |
| search-and-repair worker | branch repair for one offender set |

The important lesson is that search bounds and validation context are related
but not identical.

## OrthoRoute: PCB PathFinder reference

Repository: [bbenchoff/OrthoRoute](https://github.com/bbenchoff/OrthoRoute)

OrthoRoute applies PathFinder to a multilayer Manhattan PCB lattice. Its first
pass permits overuse and later passes apply congestion costs while sequentially
rerouting nets against shared occupancy.

Adopt as a readable reference for:

- provisional first-pass routing;
- node and edge congestion accounting;
- present/history cost updates; and
- deterministic sequential net repair.

Do not adopt its full fixed lattice or GPU requirement. Redstone resource
claims and incremental graph exposure are materially different.

## Freerouting: mature PCB repair mechanics

Repository: [freerouting/freerouting](https://github.com/freerouting/freerouting)

Freerouting provides mature maze routing, pass control, localized rip-up, and
deterministic search behavior. It is useful for comparing repair-loop and
failure-handling structure. Its Java implementation and GPL license make it a
secondary reference rather than a direct port target.

## Redstone projects

- [MinecraftHDL](https://github.com/itsfrank/MinecraftHDL) demonstrates a full
  Verilog-to-Redstone flow but reports severe physical growth for moderate
  circuits.
- [RHDL](https://github.com/BradenEverson/redstone_description_language) uses
  3D pathfinding, padding, and bridges and is useful for Redstone geometry
  comparison.
- [HDL-to-Minecraft](https://devpost.com/software/hdl-to-minecraft) uses basic
  3D Dijkstra routing and identifies placement/routing optimization as future
  work.

These projects can inform bridge, padding, and export behavior. None supplies
the negotiated, reusable, dynamically expanded route-tree router required for
RCA4 and CLA4.

## Chosen synthesis

The implementation direction is:

1. OrthoRoute/PathFinder provisional congestion.
2. VPR persistent trees and partial pruning.
3. Corolla per-net boundary-triggered subgraph expansion.
4. TritonRoute route/context/validation region separation.
5. RedstoneCompiler's existing exact claims, repeater rules, materializer, and
   physical simulator as the authoritative legality layer.

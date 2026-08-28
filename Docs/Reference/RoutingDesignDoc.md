# Hierarchical Region Router Design

This document is the active reference for the routed-logic architecture and
acceptance behavior. It supersedes older compatibility-era planning notes and the
historical fallback strategy discussions.

Canonical implementation focus remains:

- staged hierarchical reasoning (regions, boundaries, and contracts),
- shared canonical contracts for repeated arithmetic structure,
- resource-ownership-aware inter-region routing,
- deterministic materialization plus authoritative validation and simulation.

Current implementation note (2026-08-28):

- `Main.py` / `Compiler/Main.py` drives the authoritative default router.
- The active default strategy is `default`.
- RCA4 currently compiles to a routed, truth-verified artifact in live runs.
- Compatibility checks are now profile-driven by normalized circuit metrics and are
  no longer keyed to circuit names.
- Acceptance now includes the full FA/RCA4/RCA8 matrix and compatibility checks
  once regression gates pass.
- Routing contracts and physical interfaces are neutral packages; component
  solving and authoritative global routing have separate concrete owners.
- Placement orchestration uses `PlacementFlowState`/`PlacementFlowServices` and
  authoritative routing uses
  `AuthoritativeRoutingState`/`AuthoritativeRoutingServices`.
- Native kernels live in nested Rust domain folders; `RustRouting/Src/Lib.rs`
  contains only module registration and the PyO3 entrypoint.

## Current code map

| Responsibility | Owner |
|---|---|
| immutable routing schemas | `Compiler/Routing/Contracts/` |
| portal/claim/boundary relations | `Compiler/Routing/Interfaces/` |
| component problem, portfolios, solvers, certificates, cache | `Compiler/Routing/Components/` |
| candidates, leases, negotiated trees, ports, assignment, materialization | `Compiler/Routing/Authoritative/` |
| placement access, core search/repair/commit, flow | `Compiler/Placement/{Access,Core,Flow}/` |
| native runtime, geometry, path, assignment, escape, generation, planning, simulation, binding | `RustRouting/Src/*/`; escape candidates/catalogs and generation detailed-tree phases use nested subdomains |

The six supported Python entrypoints and clean-break retirement list are in
[`ProjectTreeDesignDoc.md`](../../ProjectTreeDesignDoc.md). Historical code
paths in dated implementation notes describe their original evidence and are
not current import instructions.

## Purpose

Build compact, orderly redstone designs by recognizing repeated logical
structure, solving one canonical physical region at a time, caching the result,
and routing only the connections between regions at top level.

The router must stop treating an arithmetic design as one flat set of unrelated
NAND nets. A four-bit ripple-carry adder is four repeated bit slices plus
ordered buses and a carry chain. A future ALU is a composition of arithmetic,
register, control, and bus regions.

## Core principle

```text
flat NAND netlist
        ↓
discover repeated, consistent regions
        ↓
solve each canonical region once
        ↓
cache its abstract physical contract and local proof
        ↓
compose region instances with organized buses and channels
        ↓
materialize redstone only after composition is complete
        ↓
final authoritative DRC and simulation
```

This is a hybrid router:

- repeated structures use discovered reusable physical regions;
- buses, carry chains, clocks, and control use structured global channels;
- unusual glue logic uses the generic resource-graph router.

No circuit name is special. Reuse is driven by normalized topology and pin
roles, not by names such as `FullAdder` or `RCA4`.

## Region discovery

A region is a connected normalized NAND subgraph with a stable external
interface. The discovery pass should find:

- repeated bit slices;
- carry chains;
- indexed operand and result buses;
- mux/register/control clusters;
- generic unmatched glue regions.

The initial target is the canonical full-adder bit slice:

```text
A[i], B[i], CarryIn[i]  →  Sum[i], CarryOut[i]
```

RCA4 becomes four instances of this region. The carry output of one instance
must physically align with the carry input of the next instance.

## Canonical cache

Every reusable region has a deterministic cache key:

```text
CanonicalRegionKey =
  normalized NAND topology
  + ordered interface roles
  + boundary fanout contract
  + technology version
  + physical-design policy version
  + allowed orientation/layer contract
```

Canonicalization removes instance names and bit indexes, but preserves pin
direction, ordered pin role, and boundary connectivity. A cache miss solves a
region once. A cache hit reuses the proven local layout. A changed technology
or policy version invalidates the proof.

## Abstract physical contract

Do not build dust, supports, headroom, and repeaters while exploring every
placement possibility. Each solved region instead exposes a cheap abstract
contract:

- footprint and legal orientations;
- placement anchor and region boundary;
- external pin locations, directions, and access windows;
- layer/track reservations at each boundary pin;
- internal occupancy and electrical-clearance masks;
- required air/headroom and support masks;
- repeater-capable sites and estimated delay;
- material, area, length, bend, and via estimates;
- local resource-graph proof and version metadata.

The abstract contract is still physically meaningful. It reserves capacity and
clearance early enough to prevent impossible composition. It merely delays
concrete block emission.

## Region placement

Place repeated region instances as a regular array or ordered strip. The
placer optimizes:

- adjacent carry output/input pins;
- ordered, parallel operand and result buses;
- compact region footprint with no empty field between dependent cells;
- consistent orientation and pin escape direction;
- dedicated control and clock channels separate from datapath buses.

For a ripple-carry adder:

```text
      A bus  =====================================
      B bus  =====================================

             [slice 0] [slice 1] [slice 2] [slice 3]
carry chain  ========> ========> ========>

    Sum bus  =====================================
```

Internal NAND connections remain inside each compact slice. The top-level
router sees only bus boundaries and short carry links.

## Structured inter-region routing

Top-level routing is track-first, not maze-first.

1. Reserve coarse corridors for each region boundary, bus, carry chain, and
   control connection.
2. Assign exact ordered tracks with capacity one and pitch/clearance rules.
3. Keep bus bit order stable across the entire corridor.
4. Use preferred-direction layers: long trunks on their preferred layer,
   short branches and planned transitions on the other layer.
5. Allow detailed A* only inside a narrow region-boundary escape box or an
   explicit repair box.

The detailed router may realize a reservation, but it may not replace a clean
bus/carry topology with an unrelated detour.

## Late materialization

After region placement and inter-region track assignment:

1. instantiate cached local region geometry;
2. materialize reserved inter-region dust, supports, stairs, and repeaters;
3. rebuild all claims through the authoritative resource graph;
4. require exactly one owner for every claimed physical resource;
5. run final DRC and exhaustive physical simulation.

Late materialization is not late legality. The abstract masks and reservations
are the legality model during planning; final materialization proves that the
emitted blocks exactly match that model.

## Generic fallback

Unmatched logic is partitioned into small glue regions. It may use the generic
resource-graph router, but only within its assigned local region and with a
fixed interface contract. Generic routing must not cause a solved arithmetic
region or bus to become tangled.

## Arithmetic direction

Addition and subtraction should share one adder path:

```text
A + (B XOR subtract) + subtract
```

Multiplication and division should be iterative regions with registers,
counters, muxes, and control, rather than giant combinational NAND networks.
That keeps the datapath narrow and makes region reuse practical for a compact
ALU.

## Required diagnostics

`PhysicalDesign.json` must record:

- discovered region types and instance counts;
- canonical cache hits and misses;
- region footprint and interface contracts;
- inter-region nets, buses, channels, and track ownership;
- generic-fallback region count and cost;
- abstract-versus-materialized resource counts;
- final conflicts, route material, vias, bends, delay estimate, and timings.

## Acceptance criteria

- Equivalent bit-slice instances reuse one canonical contract.
- Repeated arithmetic regions are placed in a compact, ordered layout.
- Carry chains are short, straight, and visually continuous.
- Bus tracks are parallel, ordered, and do not weave through unrelated gates.
- Adding another regular bit slice adds approximately constant placement and
  routing work, aside from the linear bus extension.
- Region composition cannot overlap another region's footprint, keepout, or
  reserved physical resource.
- Materialized output passes authoritative DRC and physical simulation.
- Generic glue logic remains local and cannot destroy structured region layout.

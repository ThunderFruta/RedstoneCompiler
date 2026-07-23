# Structured and Route-Minimal Redstone Router Design

> Superseded constraint (2026-07-19): the organized NAND v4 design permits
> only generic connectivity, fanout, pin-compatibility, topological-distance,
> boundary-demand, and exact-material features. The FullAdder/carry/XOR/bus
> classifiers described below are historical intuition and must not be
> implemented. Layer/corridor organization remains applicable when derived
> generically. See `./OrganizedNandRoutingDesignDoc.md`.

## Problem statement

Current behavior is still "wire-first": most effort is spent searching long dust paths after placement, which causes visible snakes, high bend counts, and oversized channels.
This redesign makes structure the primary artifact and routing a constrained realization step.

## Design goals

- Preserve gate topology first: nearby gates that communicate should be physically adjacent.
- Reduce total routed length and bends before optimizing secondary objectives.
- Produce PCB-like regularity:
  - stable bus lanes,
  - monotonic carry paths,
  - short local escape paths,
  - minimal detours.
- Keep reliability: no functional regression; retain authoritative legality/simulation checks.

## Architecture overview

The flow becomes four explicit layers:

1. **Structure extraction**
   - Build a logic structure graph from NAND IR.
   - Classify repeated or ordered regions (full-adder slice, carry chain segment, bus group, control island).
   - Create region interfaces with ordered pins and side-exit metadata.

2. **Structured placement**
   - Place structure regions first, then internal gates.
   - Impose a local adjacency objective:
     - carry neighbors should be next to each other,
     - buses stay in ordered lanes,
     - repeated regions align consistently (same orientation unless blocked).

3. **Channel-aware routing plan**
   - Solve routing at guide level before detailed dust tracing.
   - Reserve corridor budgets per net class:
     - local nets: short local windows,
     - carry nets: monotonic channel,
     - buses: dedicated straight tracks,
     - control/long fanout: explicit trunk corridors only.

4. **Constrained detailed routing**
   - Run detailed pathing only inside reserved corridors and escape windows.
   - Apply strict cost bounds for long detours and turn-heavy routes.

5. **Validation + reroute**
   - Authoritative resource-graph materialization remains final check.
   - Iterate only on top offenders (overflows/high bends/high length).

## "Less routing" rules (hard constraints)

- **Local-first**: if legal within envelope, routing must stay local.
- **Monotonic backbone**:
  - carry chains and ordered buses should remain monotonic and ordered.
  - no reverse zig-zag unless blocked.
- **Bend cap**:
  - non-bus, non-branch local nets: default bend cap 3,
  - carry/cascade: bends only at transitions/obstacle crossings,
  - branch nets: minimize Steiner-like shared trunks before stubs.
- **Via cap**:
  - prefer single-layer completion for local nets,
  - via only when congestion/clearance requires crossing.
- **Route expansion budget**:
  - intra-cluster: bounded to short envelope first,
  - inter-cluster: only expand after local budget exhaustion.

## Data contracts

- `RegionContract`
  - ordered pins, side-escape options, local keepout envelope, legal rotations.
- `PlacementEnvelope`
  - region positions, channel seed points, adjacency targets.
- `NetClassSpec`
  - class (`local`, `carry`, `bus`, `control`), preferred directions, max span.
- `ChannelPlan`
  - corridor geometry, width/capacity, occupancy quotas.
- `RoutePathPlan`
  - guide path, allowed detour budget, bend budget, via budget, confidence score.

## Routing cost model

Primary sort order for route scoring:
1. path length within local budget,
2. bend count,
3. via count,
4. corridor overflow,
5. secondary penalties (cleaning/repeater density).

If a direct low-bend path exists inside envelope, no global detour is considered.

## Stage-by-stage plan

### Stage 0: Structure extraction + contracts
- Build/refresh region detection for repeated structures.
- Serialize `RegionContract` and `NetClassSpec` per output module.

### Stage 1: Structured placement
- Introduce deterministic placement scoring:
  - HPWL for local nets,
  - adjacency bonus for immediate fanout/carry neighbors,
  - soft collision penalty with future corridors.
- Freeze placement if local adjacency and carry continuity improve.

### Stage 2: Channel planner (global)
- Create corridor map and assign net classes to default lanes.
- Prevent early detailed-routing from entering crowded channels.

### Stage 3: Constrained detailed routing
- Route with envelope + corridor constraints.
- Reject path candidates violating bend/via budgets unless fallback to compatibility is explicitly allowed.

### Stage 4: Iterative correction
- If overflow/length outliers remain:
  - identify top offenders,
  - reroute only those nets with updated penalties.

## Success metrics (existing docs aligned)

- FullAdder:
  - length reduced substantially,
  - bend and via reductions,
  - fewer blocks/footprint,
  - overflow_peak ≤ 1,
  - 8/8 truth-table + simulation pass,
  - runtime within 10s in local-first mode.
- Structural quality:
  - high carry monotonicity,
  - bus lane continuity,
  - low net dominance (largest net share ≤ 20%).

## Compatibility and rollout

- Keep existing compatibility path as fallback.
- New path runs behind strategy switch:
  - `compatibility`,
  - `hybrid`,
  - `structured-local-first` (new default once acceptance gates pass).

## Immediate implementation check

- If this document is followed, routing behavior should transition from snake-like free routing to:
  - short local dust bursts,
  - straight structured trunking,
  - repeated regular block placement,
  - lower routing-area inflation.

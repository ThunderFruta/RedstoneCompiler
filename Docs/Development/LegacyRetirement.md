# Legacy Routing and Shim Retirement

The [repository layout migration](../Reference/RepositoryLayoutMigration.md)
records the current source paths. The dated retirement plans below retain their
original scope, including old package and cache names; they are not instructions
to move the retained runtime/cache locations again.

## 2026-08-28 structural clean break

The Python/Rust monolith split retired `Placement/{AccessFabric,Pcb,PcbFlow}.py`,
`Routing/{Models,AuthoritativePlanner,ComponentAccess,ComponentPlanning,
ComponentRouter,ComponentPipeline}.py`, and the former flat Rust source files.
No forwarding modules remain. Repository imports, test patch targets, and
workers now name their concrete owners under `Placement/{Access,Core,Flow}` and
`Routing/{Contracts,Interfaces,Components,Authoritative}`; Rust uses nested
domain directories under `Native/Routing/Src/`.

`Routing/Actions/ConflictRepair.py` was consolidated into `Actions/Validation.py`
without changing the `Actions` exports. The unused `Cells/Nand.py` duplicate was
deleted rather than copied. This was structural cleanup only: legacy and
dynamic component solvers both remain because reachability/dead-code parity was
not established, and routing-aware-placement v17 was not introduced.

The detailed retired-path list and hard structural gates are authoritative in
[`ProjectTreeDesignDoc.md`](../Reference/ProjectTreeDesignDoc.md) and
`Tests/Structural/test_source_structure.py`. Historical sections below retain their dated
meaning and are not evidence that a retired path still exists.

> **Historical scope notice (2026-07-22):** In this document, “flat
> negotiated-routing stack” means the retired candidate/guide/pair-repair
> implementation described below. It does not mean the active
> [negotiated route-tree router](../Routing/Active/NegotiatedRouteTreeRouter.md), which
> uses persistent per-signal trees, exact Redstone claims, and incremental
> regions. The deletion list must be re-audited against live callers before any
> removal; this document is not deletion authorization.

## Goal

Retire the obsolete flat negotiated-routing stack and the temporary repository
compatibility shims after their authoritative replacements have full test and
artifact coverage.

This document is a deletion plan, not authorization to delete everything at
once. Each group is removed only after its live callers and useful tests have
been replaced.

## Legacy routing stack to retire

Remove these together because they implement one old idea: independently route
many candidate guides, negotiate conflicts, repair pairs, and recursively
assign a flat whole-design solution.

- `PhysicalDesign/Routing/Planning/ChannelPlanner.py::BuildChannelPlan` and helpers used only
  by it;
- legacy guide negotiation, pair repair, and Python exact assignment in that
  path;
- `PhysicalDesign/Routing/Assignment/TrackAssignment.py::AssignGlobalTracks` and its old
  assignment/repeater helpers;
- `PhysicalDesign/Redstone/Actions/ConflictRepair.py`;
- `PhysicalDesign/Routing/Workers/PinAccess.py` after Rust portal parity coverage
  replaces its tests;
- Rust `FindPathOnResourceGraph` and `FindPathsOnResourceGraph` after the old
  pin-access worker is gone;
- tests that assert retry counts, negotiated passes, pair repair, or old
  global-plan selection behavior.

Keep these contracts and live components:

- net profiles, guides, and routing metrics still consumed by the
  authoritative planner;
- `AssignedTrack` and `TrackAssignment` records used to materialize selected
  routes;
- the authoritative resource graph, Rust portals, route candidates, exact
  assignment, final DRC, and repeater reservations.

## Other stale implementation paths

### Late repeater search

`BuildRepeaters` is the previous strategy of discovering repeaters after the
route exists. Retire it once tests prove `MaterializeReservedRepeaters` is the
sole production emitter. Do not preserve it as a fallback.

### No-op routing configuration

The authoritative detailed-routing worker currently accepts legacy values such
as signal order, access length, electrical clearance, iteration count, detour
ratio, detour allowance, and guide penalty, then discards them. Remove these
arguments and simplify `RoutingAttemptPolicy` after callers have been updated.

### Stale policy fields

Retire policy knobs that no live path consumes:

- pair-repair penalties and expansion budgets;
- stagnation and reassignment controls;
- legacy repeater-site flags;
- retry-oriented guide/attempt controls;
- unused clustering, quality-target, and runtime-budget settings.

Never serialize a user-visible policy setting that does nothing.

### Dormant placement heuristics

The spacing-zero-only compact placement and its old guide/congestion estimator
should either be redesigned as part of hierarchical region placement or be
removed. It must not remain as a second inactive placement philosophy.

## Compatibility shims to retire

The following exist only during the project-tree migration:

```text
RedstoneCompiler.* imports  → Compiler.*, Compiler.Frontend.*, PhysicalDesign.Rendering.*
Build/                      → Output/
.RedstoneWork/             → Cache/Frontend/
.pytest_cache/             → Cache/Tests/
RustRouting/target/        → Cache/Rust/ where tooling supports it
legacy writer aliases       → SchemEncoder public API
```

Before deleting any shim:

1. update all repository imports, scripts, and documentation;
2. run both old/new import tests for one stable compatibility cycle;
3. publish one migration note with the replacement command or import;
4. delete the shim and its compatibility test in the same retirement change.

## Required replacement coverage

Deletion cannot begin until the following tests exist:

- Rust portal tests for diversity, missing access, and claim parity;
- Rust assignment tests for capacity, MRV, forward checking, deterministic
  result, and expansion-budget failure;
- Python tests for route materialization, reserved repeaters, cleanup DRC, and
  physical-design diagnostics;
- region-router tests for cache hit/miss, boundary contracts, carry adjacency,
  bus order, and generic-glue fallback;
- target-tree import and launcher tests;
- FullAdder physical acceptance plus opt-in arithmetic-region acceptance.

## Deletion sequence

### 1. Stop documenting dead behavior

Replace README, CLI help, and artifact descriptions that still mention
negotiated passes, pair repair, retry ladders, or automatic fallback.

### 2. Replace legacy-only tests

Move useful conflict, legality, and pin-access cases into resource-graph and
Rust parity tests. Delete tests that merely preserve the old algorithm.

### 3. Delete the flat router as one unit

Delete the old global planner, old track assignment algorithm, conflict repair,
old pin-access worker, and unused Rust search exports. Remove their façade and
package exports at the same time.

### 4. Remove dead policy/configuration fields

Remove no-op route arguments and stale policy fields only after their owning
implementation group has been deleted.

### 5. Retire tree shims

Make `Compiler`, `SVDecoder`, `SchemEncoder`, `Output/`, and `Cache/` the only
documented public paths. Delete old package/output/cache paths after the
announced compatibility cycle.

## Safety rules

- No automatic legacy fallback survives retirement.
- No dead knob remains in the CLI or `PhysicalDesign.json`.
- Do not mix routing-algorithm redesign with deletion-only cleanup.
- Every deletion phase passes byte compilation, Rust tests, lightweight Python
  tests, artifact-schema verification, and physical truth-table checks.

## Completion criteria

- One authoritative route planner, portal generator, and repeater emitter
  remain.
- Every advertised setting changes live behavior.
- No production import references `RedstoneCompiler.*` or legacy output/cache
  roots.
- No documentation presents the retired flat candidate/pair-repair model as
  the active negotiated route-tree router.

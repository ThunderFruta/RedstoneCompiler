# Project tree design reference

The canonical clean-break monolith-split design, ownership map, structural
limits, verification commands, and 2026-08-28 evidence record are maintained in
[`ProjectTreeDesignDoc.md`](../../ProjectTreeDesignDoc.md).

## Active compiler tree

```text
Compiler/
├── Placement/
│   ├── Access/       placement-access geometry and capacity
│   ├── Core/         constraints, search, repair, final placement
│   └── Flow/         attempts, feedback, assembly, runner/publication
└── Routing/
    ├── Contracts/    neutral immutable schemas
    ├── Interfaces/   neutral physical boundary relations and claims
    ├── Components/   local component planning and solving
    └── Authoritative/ global candidates, leases, ports, assignment, output

RustRouting/Src/
├── Core/
├── Geometry/
├── Path/
├── Assignment/
├── Escape/
│   ├── Candidates/
│   └── Catalog/
├── Generation/
│   └── DetailedTrees/
│       └── Phases/
├── Planning/
├── Simulation/
├── Python/
└── Lib.rs
```

Dependencies point from contracts toward the compiler pipeline and never back
up the stack. `Lib.rs` owns only native module registration and the PyO3
entrypoint. Internal helpers belong to concrete modules; the package APIs expose
only the six supported placement/routing entrypoints listed in the canonical
design.

The former placement, routing-model, authoritative-planner, component-stack,
flat Rust, duplicate NAND, and conflict-repair paths are retired without
forwarders. `Tests/Structural/test_source_structure.py` is the executable source of truth
for the deleted-path list, one-way imports, cycle freedom, narrow entrypoint
owners, and physical line/function limits.

Timestamped evidence is additive. Never edit an existing bundle under
`Docs/Routing/Snapshots/` or
`Output/DesignSnapshots/RoutingAwarePlacementAccess/`; publish a fresh UTC
directory through `Scripts/CaptureRoutingDesignSnapshot.py`.

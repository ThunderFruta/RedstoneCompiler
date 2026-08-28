# Monolith split and ownership design

## Status and scope

This is the canonical ownership record for the behavior-preserving Python/Rust
routing refactor completed in the working tree on 2026-08-28. The change is a
clean break: repository callers use the concrete new owners and the former
monolith paths are deleted, with no forwarding modules.

The split does not implement routing-aware-placement v17, change the CLI,
change artifact schemas, change Python-visible native names, or move policy and
orchestration into Rust. Python still owns orchestration, diagnostics, policy,
deadlines, and publication. Rust still owns bounded compute kernels.

## Dependency direction

```text
Routing contracts and resource primitives
        ↓
Routing physical interfaces
        ↓
Component routing
        ↓
Authoritative global routing
        ↓
Placement flow
        ↓
Compiler pipeline, simulation, writer
```

`Contracts` and `Interfaces` are neutral. `Components` must not import
`Authoritative` or placement. `Authoritative` must not import placement search
or flow; the existing pure `Placement.Geometry` and `Placement.Rotation`
physical primitives are documented exceptions. The static import graph is
required to be acyclic.

## Python ownership

```text
Compiler/Placement/
├── Access/       geometry, escape paths, attachment, capacity oracle
├── Core/         constraints, channels, clusters, search, mandatory access,
│                 repair, compactness, final commit
└── Flow/         run state/services, demand, feedback, portfolios, attempts,
                  component assembly, routing, publication, runner

Compiler/Routing/
├── Contracts/    core, placement, component, physical-interface, result schemas
├── Interfaces/   portal constraints, exact claims, boundary relations
├── Components/   fabric/problem construction, portfolios, legacy and dynamic
│                 solvers, no-goods, certification, cache, assembly pipeline
└── Authoritative/
    ├── FlowPhases/          ordered global-routing phases
    ├── NegotiatedRouting/   initialization, preparation, search, state
    ├── PortSolving/         validation, search, finalization
    └── *.py                 candidates, leases, guides, ports, assignment,
                             materialization, run state/services, public flow
```

The narrow supported entrypoints are:

| Entrypoint | Concrete owner |
|---|---|
| `Compiler.Placement.Flow.PlaceAndRoutePcb` | `Placement/Flow/Runner.py` |
| `Compiler.Placement.Core.PlacePcbGraph` | `Placement/Core/Commit.py` |
| `Compiler.Placement.Access.BuildPlacementAccessFabric` | `Placement/Access/Fabric.py` |
| `Compiler.Routing.Authoritative.RouteAuthoritativeResources` | `Routing/Authoritative/Flow.py` |
| `Compiler.Routing.Components.SolveComponentRoutingProblem` | `Routing/Components/Solver.py` |
| `Compiler.Routing.Components.CompileClosedComponent` | `Routing/Components/Pipeline.py` |

Internal helpers are imported from their concrete owner. Package APIs do not
re-export the old broad implementation surfaces.

### Explicit run state and services

`PlacementFlowState` and `PlacementFlowServices` make placement candidates,
repair history, deadlines, callbacks, and validators run-local and injectable.
`AuthoritativeRoutingState` and `AuthoritativeRoutingServices` do the same for
the clock/deadline, Rust context, caches, retained plans, telemetry,
materialization, and validation. Services are constructed at call time so tests
can replace a dependency without restoring module-global monkeypatch coupling.

Process-global caches remain in one concrete owner and retain their object
identity and reset behavior. Process-pool workers remain top-level importable
functions in their owning worker/boundary module.

## Native Rust ownership

The native source is intentionally a nested domain tree, not a flat folder:

```text
RustRouting/Src/
├── Core/                 Runtime.rs, Deadline.rs, Models.rs
├── Geometry/             RouteClaims.rs, ExteriorConnectors.rs
├── Path/                 PathRouting.rs
├── Assignment/           Witness.rs, Domains.rs, Search.rs, Api.rs
├── Escape/
│   ├── State.rs, Traversal.rs, Api.rs
│   ├── Candidates/       Access.rs, AccessRamps.rs, GuideDomain.rs,
│   │                     GuideEnumeration.rs, GuideGeometry.rs,
│   │                     PoweredWitness.rs
│   └── Catalog/          BundleDomain.rs, Search.rs,
│                         SolverPreparation.rs, Solver.rs
├── Generation/
│   ├── SelectedWorldClaims.rs, Batches.rs, Factorized.rs, Api.rs
│   └── DetailedTrees/
│       ├── ClaimAware.rs, GuidePreparation.rs, PathGeneration.rs,
│       │   Preparation.rs, Search.rs
│       └── Phases/       Initialization.rs, SearchClosures.rs,
│                        SourceIntegration.rs, TargetRouting.rs,
│                        FrozenBranches.rs, Finalization.rs
├── Planning/             AssignmentPlanning.rs, LeasePlanning.rs
├── Simulation/           LogicSimulation.rs
├── Python/               Bindings.rs
└── Lib.rs                module registration and the PyO3 entrypoint only
```

Every domain and nested subdomain has a small `mod.rs`. In particular, escape
candidate generation, escape catalog solving, detailed-tree generation, and
detailed-tree phases are directories rather than replacement monolith files.
`Lib.rs` owns only registration and `RustRouting`; Python-visible names,
signatures, status strings, completion masks, result shapes, and deadline units
remain unchanged.

## Clean-break retirement

The following implementation paths must stay absent:

```text
Compiler/Placement/AccessFabric.py
Compiler/Placement/Pcb.py
Compiler/Placement/PcbFlow.py
Compiler/Routing/Models.py
Compiler/Routing/AuthoritativePlanner.py
Compiler/Routing/ComponentAccess.py
Compiler/Routing/ComponentPlanning.py
Compiler/Routing/ComponentRouter.py
Compiler/Routing/ComponentPipeline.py
Compiler/Routing/Actions/ConflictRepair.py
Compiler/Cells/Nand.py
RustRouting/Src/{Assignment,AssignmentPlanning,Bindings,Deadline,
                 EscapePlanning,Generation,LeasePlanning,Models,PathRouting}.rs
```

`ConflictRepair.py` was consolidated into `Routing/Actions/Validation.py` while
the established `Actions` exports were preserved. The unused duplicate NAND
dataclass was removed; `Compiler/Cells/Library.py` remains authoritative.

The retired names may still appear in immutable snapshots and dated
implementation notes as pre-refactor provenance. They are not import
instructions. Use this ownership map when following that history into the
current tree:

| Pre-refactor path | Current owner |
|---|---|
| `Placement/AccessFabric.py` | `Placement/Access/{Fabric,Capacity,EscapePaths,Geometry}.py` |
| `Placement/Pcb.py` | `Placement/Core/` |
| `Placement/PcbFlow.py` | `Placement/Flow/` |
| `Routing/Models.py` | `Routing/Contracts/{Core,Placement,Component,PhysicalInterface,Results}.py` |
| `Routing/AuthoritativePlanner.py` | `Routing/Authoritative/` |
| `Routing/ComponentAccess.py` | `Routing/Components/Access.py` |
| `Routing/ComponentPlanning.py` | `Routing/Components/{InterfacePlanning,NetPlanning,PhysicalPlanning}.py` |
| `Routing/ComponentRouter.py` | `Routing/Components/{Solver,LegacySolver,DynamicSolver,Domains,NoGoods}.py` |
| `Routing/ComponentPipeline.py` | `Routing/Components/{Pipeline,Cache,Certification}.py` |
| `Routing/Actions/ConflictRepair.py` | `Routing/Actions/Validation.py` |
| `Cells/Nand.py` | `Cells/Library.py` |
| former flat `RustRouting/Src/*.rs` kernels | the matching nested Rust domain under `RustRouting/Src/` |

## Structural acceptance contract

- implementation modules are at most 3,000 physical lines;
- named orchestrators are below 500 physical lines;
- Python and Rust functions are below 1,000 physical lines;
- a new split implementation file is at least 150 lines unless it is an
  explicitly documented package API, binding, worker, state/schema, cache
  identity owner, or phase-contract boundary;
- retired paths and dotted import/patch targets are forbidden;
- routing dependency layers stay one-way and the Compiler import graph stays
  acyclic;
- the six narrow public entrypoints resolve to their concrete owners.

`Tests/test_source_structure.py` enforces these rules. Contract field order,
defaults, signatures, aliases, and serialization are pinned separately by
`Tests/test_routing_contract_schema.py`.

## Verification commands

```bash
python3 -m compileall -q Compiler/Placement Compiler/Routing
python3 -m pytest -q Tests/test_source_structure.py Tests/test_routing_contract_schema.py
python3 -m pytest --collect-only -q
python3 -m pytest -q
cargo fmt --manifest-path RustRouting/Cargo.toml -- --check
cargo test --manifest-path RustRouting/Cargo.toml --release
cargo build --manifest-path RustRouting/Cargo.toml --release --features python-extension
```

After a Rust change, copy the release library into the Python package and verify
the path and hash of the module actually imported before parity tests. Run the
fixed 5/3/3/2 physical matrix in a fresh output root:

```bash
python3 Scripts/RunRouterAcceptance.py --date 2026-08-28 \
  --output-root /tmp/RedstoneCompilerMonolithPostRefactor \
  --python .venv/bin/python --include-cla4
```

The final gate compares semantic routing payloads, exact truth tables,
conflicts, unresolved claims, fallback state, fingerprints, wall medians, and
internal stages. Source paths and source hashes are expected to change. Every
wall median and every stage with a baseline median of at least 100 ms must stay
within `1.05 ×` baseline; a failed first comparison is rerun as a complete case
and judged by the combined median.

## Timestamped evidence — 2026-08-28

The pre-refactor capture is revision
`1681514368979f2cca1635b90b7f27062a966e33`, with 1,301 collected pytest cases,
56 passing Rust release tests, and native-extension SHA-256
`519bf9ebab4700539a93ee0718fc63069698b1dcbcc51cef399fc25d02447113`.
Its acceptance medians were 5.231127 s (FullAdder), 6.978186 s (RCA4), and
10.671757 s (RCA8). CLA4 failed in 16.646 s at
`Placement / PlacementOverlap` with detail
`no exact-legal placement candidate was generated`; it was not timed out.

The final post-refactor bundle is
[`20260828T151609Z`](Docs/Routing/Snapshots/RoutingAwarePlacementAccess/20260828T151609Z/Snapshot.md),
snapshot ID `20260828T151609Z-2c2132084dda3215`. Its exact-evidence SHA-256 is
`06d4b8149212ab611814af1dad6af25181b75f782f4d3a3b6b86f7b87136a539`
and its portable-semantic SHA-256 is
`2c2132084dda3215ab17b96b22e639e804ba0f2dd3d1ca3e89496866811f34df`.
It includes the primary fixed-matrix manifest and the required complete-case
RCA8 performance rerun manifest.

The rebuilt native module was loaded from
`RedstoneCompiler/RustRouting.cpython-312-x86_64-linux-gnu.so`, SHA-256
`9750ecb2752be302ecf789e1bbc739f19886a0a9529d3895144d2e39435c956e`.
FullAdder/RCA4/RCA8 retain exact semantic fingerprints and pass 5/3/3 runs;
CLA4 retains the non-timeout `Placement / PlacementOverlap` failure. Wall
median changes are `+0.756%`, `+1.496%`, and `+0.194%` (combined RCA8 rerun).
Every qualifying internal median is below the 5% ceiling; the largest is the
combined RCA8 total at `+3.256%`.

Existing directories under `Docs/Routing/Snapshots/` are immutable historical
evidence and must never be rewritten. Exploratory captures continue to belong
under `Output/DesignSnapshots/RoutingAwarePlacementAccess/`.

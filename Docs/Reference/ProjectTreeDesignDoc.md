# Monolith split and ownership design

> **Canonical reference.** This is the authoritative ownership and
> clean-break record for the current source tree. Current source and active
> documentation control imports and module ownership.

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
Compiler pipeline, Fabric validation, writer
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

`Compiler/FabricServer/` owns fixture creation, authenticated live validation,
source-linked mismatch traces, imported-schematic testing, and settled-world snapshots. `ValidationServerHarness/`
contains the tracked Fabric mod source; its `Server/` subdirectory is the one
canonical local runtime and is deliberately not versioned.

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
Compiler/Simulation/{Redstone.py,__init__.py}
RustRouting/Src/{Assignment,AssignmentPlanning,Bindings,Deadline,
                 EscapePlanning,Generation,LeasePlanning,Models,PathRouting}.rs
RustRouting/Src/Simulation/{LogicSimulation.rs,mod.rs}
```

`ConflictRepair.py` was consolidated into `Routing/Actions/Validation.py` while
the established `Actions` exports were preserved. The unused duplicate NAND
dataclass was removed; `Compiler/Cells/Library.py` remains authoritative.

The retired names below are an active denylist, not import instructions. They
may be mentioned only in this ownership policy and in
[`LegacyRetirement.md`](../Development/LegacyRetirement.md) when enforcing the
clean break. Use this ownership map to locate the current owner:

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

`Tests/Structural/test_source_structure.py` enforces these rules. Contract field order,
defaults, signatures, aliases, and serialization are pinned separately by
`Tests/Routing/test_routing_contract_schema.py`.

## Verification commands

```bash
python3 -m compileall -q Compiler/Placement Compiler/Routing
python3 -m pytest -q Tests/Structural/test_source_structure.py Tests/Routing/test_routing_contract_schema.py
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
python3 Scripts/Routing/RunRouterAcceptance.py --date 2026-08-28 \
  --output-root /tmp/RedstoneCompilerMonolithPostRefactor \
  --python .venv/bin/python --include-cla4
```

The final gate compares semantic routing payloads, exact truth tables,
conflicts, unresolved claims, fallback state, fingerprints, wall medians, and
internal stages. Source paths and source hashes are expected to change. Every
wall median and every stage with a baseline median of at least 100 ms must stay
within `1.05 ×` baseline; a failed first comparison is rerun as a complete case
and judged by the combined median.

## Evidence boundary

This document records source ownership and structural gates, not a current
acceptance result. Fresh acceptance manifests, typed `.RoutingFailure.json`
artifacts, and exploratory design captures belong under `Output/`; routing-aware
placement captures use `Output/DesignSnapshots/RoutingAwarePlacementAccess/`.
Use the current [testing documentation](../Testing/RunningTests.md) to produce
and assess evidence for the checkout under test.

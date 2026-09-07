# Source layout and ownership contract

> **Canonical reference.** This document defines the current source locations,
> supported package entrypoints, and the structural checks preserved by the
> repository layout migration. The [migration record](RepositoryLayoutMigration.md)
> contains the complete old-to-new file map.

## Status and scope

The September 2026 layout migration moves existing modules intact. Imports,
resource paths, package initialization, launch wiring, source inventories, and
build configuration follow those moves. Compiler algorithms, policy defaults,
schemas, native names, deadline behavior, validation criteria, and runtime data
locations retain their existing behavior.

This builds on the earlier Python/Rust module split. The broader interface,
state, compactness, and redstone-model changes in the
[architecture review](../Architecture/PhysicalDesignArchitectureReview.md) remain
proposals. This layout does not establish new dependency restrictions.

## Dependency restrictions

The existing lower-layer restrictions now refer to `PhysicalDesign.Contracts`,
`PhysicalDesign.Constraints`, `PhysicalDesign.Routing.Regions`, and
`PhysicalDesign.Routing.Global`. Their prohibited placement dependencies also
cover the relocated `PhysicalDesign.Orchestration` and `PhysicalDesign.Geometry` modules.
The existing candidate-cache/placement-geometry and dependency-service/rotation
exceptions retain their exact corresponding owners.

The structural import inventory includes `App`, `Compiler`, `PhysicalDesign`,
and `Validation`, so moving a module cannot remove it from those checks.

## Python ownership

```text
App/                         guided CLI, argument CLI, reports, telemetry
Assets/Templates/            template catalog and three litematic templates
Compilation/
  Frontend/                  SystemVerilog parser
  Ir/                        logical intermediate representation
  Synthesis/                 logic and NAND transformations
  Pipeline.py                existing end-to-end coordinator
PhysicalDesign/
  Cells/                     standard-cell definitions
  Contracts/                 shared physical data contracts and failures
  Execution/                 existing reliability helpers
  Flow/                      existing placement/routing orchestration
  Geometry/                  placement geometry and rotation primitives
  Interfaces/                claims, boundary relations, portal constraints
  Placement/                 access, search, repair, and commit modules
  Redstone/                  technology rules and existing action modules
  Rendering/                 existing schematic writer
  Resources/                 routing resource graph
  Routing/
    Assignment/              track and template assignment
    Global/                  existing authoritative global router
    Planning/                channel planning and local-first routing
    Regions/                 existing component routing modules
    Workers/                 eligibility, pin-access, detailed routing
  Policy.py                  existing physical design policy
Validation/
  Core/                      physical fixture, vector, and validation types
  Fabric/                    Fabric client, fixtures, testing, traces
    Harness/                 tracked Java/Gradle mod sources
    Runtime/                 tracked Python runtime-manager sources
  Mchprs/                    existing MCHPRS validation coordinator
Kernels/Routing/              existing Rust crate, lockfile, and nested Src tree
RedstoneCompiler/            Python facade, native import, native stub
Tools/{Fabric,Mchprs,Routing}/ developer and runtime tools
Tests/                       tests grouped by the owning domain
Docs/                        current references and design documents
Assets/Examples/                    existing SystemVerilog inputs
Main.py                      compatibility launcher
```

The supported physical entrypoints retain their functions and signatures:

| Entrypoint | Concrete owner |
|---|---|
| `PhysicalDesign.Orchestration.PlaceAndRoutePcb` | `PhysicalDesign/Orchestration/Runner.py` |
| `PhysicalDesign.Placement.Engine.PlacePcbGraph` | `PhysicalDesign/Placement/Engine/Commit/Commit.py` |
| `PhysicalDesign.Placement.Access.BuildPlacementAccessFabric` | `PhysicalDesign/Placement/Access/Fabric.py` |
| `PhysicalDesign.Routing.Global.RouteAuthoritativeResources` | `PhysicalDesign/Routing/Global/Orchestration/Flow.py` |
| `PhysicalDesign.Routing.Regions.SolveComponentRoutingProblem` | `PhysicalDesign/Routing/Regions/Solving/Solver.py` |
| `PhysicalDesign.Routing.Regions.CompileClosedComponent` | `PhysicalDesign/Routing/Regions/Pipeline.py` |

Existing run-state objects, services, process-global caches, and worker functions
remain intact in their moved modules. Package initializers retain the established
exports; newly added grouping packages expose no broad forwarding API.

`Validation/Fabric/ServerHarness/` contains the mod source. The Python manager runs
from `Validation/Fabric/ServerManager/`, while installed server data stays at the
resolved `Runtime/FabricServer/` runtime. The source checkout and the
runtime checkout are deliberately resolved separately.

## Native Rust ownership

The native source is intentionally a nested domain tree, not a flat folder:

```text
Kernels/Routing/Src/
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
PhysicalDesign/Placement/AccessFabric.py
PhysicalDesign/Placement/Pcb.py
PhysicalDesign/Placement/PcbFlow.py
PhysicalDesign/Routing/Models.py
PhysicalDesign/Routing/AuthoritativePlanner.py
PhysicalDesign/Routing/ComponentAccess.py
PhysicalDesign/Routing/ComponentPlanning.py
PhysicalDesign/Routing/ComponentRouter.py
PhysicalDesign/Routing/ComponentPipeline.py
PhysicalDesign/Redstone/Rules/ConflictRepair.py
PhysicalDesign/Cells/Nand.py
Compilation/Simulation/{Redstone.py,__init__.py}
Kernels/Routing/Src/{Assignment,AssignmentPlanning,Bindings,Deadline,
                 EscapePlanning,Generation,LeasePlanning,Models,PathRouting}.rs
Kernels/Routing/Src/Simulation/{LogicSimulation.rs,mod.rs}
```

`ConflictRepair.py` was consolidated into `PhysicalDesign/Redstone/Rules/Validation.py` while
the established `Actions` exports were preserved. The unused duplicate NAND
dataclass was removed; `PhysicalDesign/Cells/Library.py` remains authoritative.

The retired names below are an active denylist, not import instructions. They
may be mentioned only in this ownership policy and in
[`LegacyRetirement.md`](../Development/LegacyRetirement.md) when enforcing the
clean break. Use this ownership map to locate the current owner:

| Pre-refactor path | Current owner |
|---|---|
| `Placement/AccessFabric.py` | `Placement/Access/{Fabric,Capacity,EscapePaths,Geometry}.py` |
| `Placement/Pcb.py` | `Placement/Core/` |
| `Placement/PcbFlow.py` | `PhysicalDesign/Orchestration/` |
| `Routing/Models.py` | `PhysicalDesign/Contracts/{Core,Placement,Component,PhysicalInterface,Results}.py` |
| `Routing/AuthoritativePlanner.py` | `PhysicalDesign/Routing/Global/` |
| `Routing/ComponentAccess.py` | `PhysicalDesign/Routing/Regions/Boundaries/Access.py` |
| `Routing/ComponentPlanning.py` | `PhysicalDesign/Routing/Regions/Planning/{InterfacePlanning,NetPlanning,PhysicalPlanning}.py` |
| `Routing/ComponentRouter.py` | `PhysicalDesign/Routing/Regions/{Solving,Proofs}/` and `Domains.py` |
| `Routing/ComponentPipeline.py` | `PhysicalDesign/Routing/Regions/{Pipeline,Cache}.py` and `Proofs/Certification.py` |
| `Routing/Actions/ConflictRepair.py` | `PhysicalDesign/Redstone/Rules/Validation.py` |
| `Cells/Nand.py` | `PhysicalDesign/Cells/Library.py` |
| former flat `Kernels/Routing/Src/*.rs` kernels | the matching nested Rust domain under `Kernels/Routing/Src/` |

## Structural acceptance contract

The executable gates preserve retired-path/import exclusions, the established
dependency restrictions, concrete public owners, and physical contract schemas.
Both historical retired paths and their corresponding new locations stay banned.
`Tests/Structural/test_source_structure.py` owns the structural checks;
`Tests/PhysicalDesign/Routing/test_routing_contract_schema.py` owns the neutral
contract package's one-way dependency boundary. Versioned serialization is
owned by behavior tests that construct a public contract, emit its document,
and exercise the consuming boundary. Exact class counts, introspected
signatures, defaults, and aggregate implementation hashes are not compatibility
contracts.

Source size and definition spans remain advisory review signals, reported by
`Tools/Routing/ReviewSourceStructure.py`. They are not new migration gates.

## Verification commands

```bash
python3 -m compileall -q PhysicalDesign/Placement PhysicalDesign/Routing
python3 -m pytest -q Tests/Structural/test_source_structure.py Tests/PhysicalDesign/Routing/test_routing_contract_schema.py
python3 -m pytest --collect-only -q
python3 -m pytest -q
cargo fmt --manifest-path Kernels/Routing/Cargo.toml -- --check
cargo test --manifest-path Kernels/Routing/Cargo.toml --release
cargo build --manifest-path Kernels/Routing/Cargo.toml --release --features python-extension
```

After a Rust change, copy the release library into the Python package and verify
the path and hash of the module actually imported before parity tests. Run all seven expanded acceptance examples in a fresh output root:

```bash
python3 Tools/Routing/RunRouterAcceptance.py --date 2026-08-28 \
  --output-root /tmp/RedstoneCompilerMonolithPostRefactor \
  --python .venv/bin/python --matrix expanded
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

# Project Tree

The repository groups complete modules by responsibility. The layout migration
changes source locations and their references; it preserves compiler behavior,
public launch commands, native exports, and existing runtime data locations.

## Source layout

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

`App/Main.py` owns the guided launcher and `App/CompilerCli.py` the argument CLI.
`Compilation/Pipeline.py` remains the coordinator. Folder ownership does not imply
that the broader boundary refactors proposed in the architecture review are done.

The installed commands remain `redstone-compiler` and `redstone-benchmark`.
`python Main.py` and `python -m RedstoneCompiler` retain their existing entrypoint
behavior, and the native module remains `RedstoneCompiler.RustRouting`.

## Runtime and generated files

- `Runtime/FabricServer/` retains the server installation and worlds.
  The existing override/local/sibling-worktree lookup chooses its runtime root.
- `Cache/Gradle/ServerHarness/Build/` and `Cache/Gradle/ServerHarness/Project/` retain
  Gradle output and project caches.
- `RustRouting/target/` retains Cargo build output through `.cargo/config.toml`.
- `Output/`, `Cache/`, `.venv/`, and external Minecraft/template locations keep
  their existing roles.

These retained runtime/cache containers are ignored and contain no tracked
compiler or harness source. See the [migration record](RepositoryLayoutMigration.md)
for the complete file crosswalk and verification boundary.

## Documentation ownership

- `Docs/Architecture/` explains stage boundaries and cross-stage contracts.
- `Docs/Routing/Active/RoutingAwarePlacementAccessDesign.md` owns the proposed
  fixed-access replacement and its typed placement/access handoff contracts.
- `Docs/Routing/Active/NegotiatedRouteTreeRouter.md` owns the current router design.
- `Docs/Routing/Active/RouterResearchAndInspiration.md` records external algorithmic
  sources and the parts adopted here.
- `Docs/Routing/Active/FailureCatalog.md` owns the typed routing failure taxonomy.
- `Docs/Architecture/PhysicalDesignArchitectureReview.md` owns the boundary
  findings and proposed module seams.
- `Docs/Testing/` owns test commands, layers, and physical acceptance gates.

[`ProjectTreeDesignDoc.md`](ProjectTreeDesignDoc.md) defines the completed
clean-break ownership contract and structural gates.

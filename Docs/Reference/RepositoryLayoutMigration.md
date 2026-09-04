# Repository layout migration

This migration groups existing modules under `App`, `Compiler`, `PhysicalDesign`,
`Validation`, `Native`, `Assets`, and `Tools`. It starts from `5eaf49c` and contains
336 tracked file moves. Two test packages containing only placeholder initializers
were removed. Existing function/class implementations stay in their whole modules.

## Resulting layout

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

## Compatibility and runtime boundaries

The root launcher, `redstone-compiler`, `redstone-benchmark`, and
`python -m RedstoneCompiler` keep their entrypoint behavior. The native package
and stub remain `RedstoneCompiler.RustRouting`. Internal callers use the new
owners, including `Core/Commit/Commit.py` and `Global/Flow/Flow.py`.

The Fabric runtime manager executes from the current source checkout and uses
the existing override/local/sibling-worktree lookup to find server data. Its
world, configuration, logs, fixtures, and associated built JAR derive from the
selected runtime. Existing server installations are not moved or restarted.

Cargo output remains `RustRouting/target/`, configured in `.cargo/config.toml`.
Gradle output remains `Cache/Gradle/ServerHarness/Build/`; the project cache remains
`Cache/Gradle/ServerHarness/Project/` through the supported
[`org.gradle.projectcachedir` property](https://docs.gradle.org/current/userguide/build_environment.html).
`Output/`, `Cache/`, `.venv/`, and external Minecraft/template locations retain
their behavior. Template selection still prefers the complete external pack and
otherwise uses the local assets.

Build the Python package from the repository root (`pip install -e .`, or
`maturin develop` in an activated environment) so Maturin reads the root
`pyproject.toml` and its native module name/features. Cargo commands continue to
use `--manifest-path Kernels/Routing/Cargo.toml`.

## Scope and verification

Routing algorithms, policy/scoring defaults, schemas, native exports, retry and
deadline behavior, settlement criteria, and concurrency policy are unchanged.
The compactness/shortcut, redstone-model, service, and state refactors in the
architecture review remain future work.

The source audit checks 276 existing Python modules and 4,815 definitions.
All existing signatures match. After import/path normalization, 267 modules
match completely; the remaining nine contain reviewed path lookup, source
inventory, package bootstrap, or test-loading changes. Two focused regression
tests cover manager dispatch and the separation of source from runtime data.
All 57 Rust source files, Cargo.lock, eight Java sources, three litematic assets,
and pre-existing fixture contents match their pre-migration inputs.

Final test and acceptance evidence is recorded under
`Output/RepositoryLayout/20260903023556Z/`. The baseline deterministic suite has
1,439 passing tests and three opt-in scale skips. The baseline expanded matrix
attempted every case: FullAdder/RCA4/RCA8 reached an unavailable authenticated
Fabric endpoint, HalfAdder/DecimalToBinary4/TFlipFlopLatch reported existing
interface-selection failures, and CLA4 timed out. These results are inherited
baseline limitations, not successful physical acceptance.

Final verification: **1,441 pytest tests passed, three existing scale tests
skipped, and 255 subtests passed**. All 1,442 original test cases remain collected;
two focused runtime-path tests were added. The 49 Rust release tests, eight Java
harness tests, native editable build/import, eleven CLI checks, and documentation
link checks passed. Gradle's retained cache/build paths were verified explicitly.

All seven before/after acceptance outcomes matched, as did NAND diagrams,
observed failure fingerprints, and policy data. MCHPRS again passed all 8, 512,
and 131,072 vectors for FullAdder, RCA4, and RCA8 respectively, with the same
settlement diagnostics. Their Fabric checks remained infrastructure failures;
the other three circuits retained their interface-selection failure and CLA4
retained its timeout. Final schematic geometry comparison was **not-run** because
neither matrix produced an accepted final schematic. This migration does not
claim successful live Fabric acceptance.


The file crosswalk below is the source-location record. Dated historical
evidence retains the source identities it originally recorded.

## Complete file crosswalk

<details>
<summary>336 whole-file moves</summary>

| Previous path | Current path |
|---|---|
| `Compilation/Cells/Library.py` | `PhysicalDesign/Cells/Library.py` |
| `Compilation/Cells/__init__.py` | `PhysicalDesign/Cells/__init__.py` |
| `Compilation/FabricServer/FailureTrace.py` | `Validation/Fabric/FailureTrace.py` |
| `Compilation/FabricServer/Fixture.py` | `Validation/Fabric/Fixture.py` |
| `Compilation/FabricServer/Models.py` | `Validation/Fabric/Models.py` |
| `Compilation/FabricServer/SchemImport.py` | `Validation/Fabric/SchemImport.py` |
| `Compilation/FabricServer/ServerSnapshot.py` | `Validation/Fabric/ServerSnapshot.py` |
| `Compilation/FabricServer/Testing.py` | `Validation/Fabric/Testing.py` |
| `Compilation/FabricServer/Validation.py` | `Validation/Fabric/Validation.py` |
| `Compilation/FabricServer/__init__.py` | `Validation/Fabric/__init__.py` |
| `Compilation/Main.py` | `App/CompilerCli.py` |
| `Compilation/PhysicalValidation/Fixture.py` | `Validation/Physical/Fixture.py` |
| `Compilation/PhysicalValidation/Models.py` | `Validation/Physical/Models.py` |
| `Compilation/PhysicalValidation/Testing.py` | `Validation/Physical/Testing.py` |
| `Compilation/PhysicalValidation/Vectors.py` | `Validation/Physical/Vectors.py` |
| `Compilation/PhysicalValidation/__init__.py` | `Validation/Physical/__init__.py` |
| `Compilation/Placement/Access/Capacity.py` | `PhysicalDesign/Placement/Access/Capacity.py` |
| `Compilation/Placement/Access/EscapePaths.py` | `PhysicalDesign/Placement/Access/EscapePaths.py` |
| `Compilation/Placement/Access/Fabric.py` | `PhysicalDesign/Placement/Access/Fabric.py` |
| `Compilation/Placement/Access/Geometry.py` | `PhysicalDesign/Placement/Access/Geometry.py` |
| `Compilation/Placement/Access/__init__.py` | `PhysicalDesign/Placement/Access/__init__.py` |
| `Compilation/Placement/Core/Cache.py` | `PhysicalDesign/Placement/Engine/Cache.py` |
| `Compilation/Placement/Core/Channels.py` | `PhysicalDesign/Placement/Engine/Channels.py` |
| `Compilation/Placement/Core/Clustering.py` | `PhysicalDesign/Placement/Engine/Clustering.py` |
| `Compilation/Placement/Core/Clusters.py` | `PhysicalDesign/Placement/Engine/Clusters.py` |
| `Compilation/Placement/Core/Commit.py` | `PhysicalDesign/Placement/Engine/Commit/Commit.py` |
| `Compilation/Placement/Core/CommitHelpers.py` | `PhysicalDesign/Placement/Engine/Commit/CommitHelpers.py` |
| `Compilation/Placement/Core/CommitPreparation.py` | `PhysicalDesign/Placement/Engine/Commit/CommitPreparation.py` |
| `Compilation/Placement/Core/CommitRouting.py` | `PhysicalDesign/Placement/Engine/Commit/CommitRouting.py` |
| `Compilation/Placement/Core/CommitState.py` | `PhysicalDesign/Placement/Engine/Commit/CommitState.py` |
| `Compilation/Placement/Core/Compactness.py` | `PhysicalDesign/Placement/Engine/Compactness.py` |
| `Compilation/Placement/Core/Constraints.py` | `PhysicalDesign/Placement/Engine/Constraints.py` |
| `Compilation/Placement/Core/Costs.py` | `PhysicalDesign/Placement/Engine/Costs.py` |
| `Compilation/Placement/Core/MandatoryAccess.py` | `PhysicalDesign/Placement/Engine/MandatoryAccess.py` |
| `Compilation/Placement/Core/Repair.py` | `PhysicalDesign/Placement/Engine/Repair.py` |
| `Compilation/Placement/Core/Search.py` | `PhysicalDesign/Placement/Engine/Search.py` |
| `Compilation/Placement/Core/__init__.py` | `PhysicalDesign/Placement/Engine/__init__.py` |
| `Compilation/Placement/Flow/AttemptHistory.py` | `PhysicalDesign/Orchestration/AttemptHistory.py` |
| `Compilation/Placement/Flow/CandidateRouting.py` | `PhysicalDesign/Orchestration/CandidateRouting.py` |
| `Compilation/Placement/Flow/Candidates.py` | `PhysicalDesign/Orchestration/Candidates.py` |
| `Compilation/Placement/Flow/Demand.py` | `PhysicalDesign/Orchestration/Demand.py` |
| `Compilation/Placement/Flow/Feedback.py` | `PhysicalDesign/Orchestration/Feedback.py` |
| `Compilation/Placement/Flow/PhysicalAssembly.py` | `PhysicalDesign/Orchestration/PhysicalAssembly.py` |
| `Compilation/Placement/Flow/PhysicalFlow.py` | `PhysicalDesign/Orchestration/PhysicalFlow.py` |
| `Compilation/Placement/Flow/PlacementAttempts.py` | `PhysicalDesign/Orchestration/PlacementAttempts.py` |
| `Compilation/Placement/Flow/Portfolios.py` | `PhysicalDesign/Orchestration/Portfolios.py` |
| `Compilation/Placement/Flow/Preparation.py` | `PhysicalDesign/Orchestration/Preparation.py` |
| `Compilation/Placement/Flow/Results.py` | `PhysicalDesign/Orchestration/Results.py` |
| `Compilation/Placement/Flow/RoutingAttempts.py` | `PhysicalDesign/Orchestration/RoutingAttempts.py` |
| `Compilation/Placement/Flow/Runner.py` | `PhysicalDesign/Orchestration/Runner.py` |
| `Compilation/Placement/Flow/Setup.py` | `PhysicalDesign/Orchestration/Setup.py` |
| `Compilation/Placement/Flow/State.py` | `PhysicalDesign/Orchestration/State.py` |
| `Compilation/Placement/Flow/__init__.py` | `PhysicalDesign/Orchestration/__init__.py` |
| `Compilation/Placement/Geometry.py` | `PhysicalDesign/Geometry/Placement.py` |
| `Compilation/Placement/PreRouteInterface.py` | `PhysicalDesign/Placement/PreRouteInterface.py` |
| `Compilation/Placement/Rotation.py` | `PhysicalDesign/Geometry/Rotation.py` |
| `Compilation/Placement/__init__.py` | `PhysicalDesign/Placement/__init__.py` |
| `Compilation/Routing/Actions/Geometry.py` | `PhysicalDesign/Redstone/Rules/Geometry.py` |
| `Compilation/Routing/Actions/Repeaters.py` | `PhysicalDesign/Redstone/Rules/Repeaters.py` |
| `Compilation/Routing/Actions/Validation.py` | `PhysicalDesign/Redstone/Rules/Validation.py` |
| `Compilation/Routing/Actions/__init__.py` | `PhysicalDesign/Redstone/Rules/__init__.py` |
| `Compilation/Routing/Authoritative/AssignmentState.py` | `PhysicalDesign/Routing/Global/Assignment/AssignmentState.py` |
| `Compilation/Routing/Authoritative/BoundaryLeaseDomains.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeaseDomains.py` |
| `Compilation/Routing/Authoritative/BoundaryLeaseHelpers.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeaseHelpers.py` |
| `Compilation/Routing/Authoritative/BoundaryLeasePatterns.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeasePatterns.py` |
| `Compilation/Routing/Authoritative/BoundaryLeasePlanning.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeasePlanning.py` |
| `Compilation/Routing/Authoritative/BoundaryLeaseState.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeaseState.py` |
| `Compilation/Routing/Authoritative/BoundaryLeases.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeases.py` |
| `Compilation/Routing/Authoritative/CandidateCache.py` | `PhysicalDesign/Routing/Global/Candidates/CandidateCache.py` |
| `Compilation/Routing/Authoritative/CandidateDomains.py` | `PhysicalDesign/Routing/Global/Candidates/CandidateDomains.py` |
| `Compilation/Routing/Authoritative/CandidateGuides.py` | `PhysicalDesign/Routing/Global/Candidates/CandidateGuides.py` |
| `Compilation/Routing/Authoritative/Dependencies.py` | `PhysicalDesign/Routing/Global/Orchestration/Dependencies.py` |
| `Compilation/Routing/Authoritative/ExteriorConnectors.py` | `PhysicalDesign/Routing/Global/Ports/ExteriorConnectors.py` |
| `Compilation/Routing/Authoritative/Flow.py` | `PhysicalDesign/Routing/Global/Orchestration/Flow.py` |
| `Compilation/Routing/Authoritative/FlowPhases/AssignmentPreparation.py` | `PhysicalDesign/Routing/Global/Orchestration/Phases/AssignmentPreparation.py` |
| `Compilation/Routing/Authoritative/FlowPhases/AssignmentSolve.py` | `PhysicalDesign/Routing/Global/Orchestration/Phases/AssignmentSolve.py` |
| `Compilation/Routing/Authoritative/FlowPhases/Bootstrap.py` | `PhysicalDesign/Routing/Global/Orchestration/Phases/Bootstrap.py` |
| `Compilation/Routing/Authoritative/FlowPhases/CandidateMaterialization.py` | `PhysicalDesign/Routing/Global/Orchestration/Phases/CandidateMaterialization.py` |
| `Compilation/Routing/Authoritative/FlowPhases/CandidatePreparation.py` | `PhysicalDesign/Routing/Global/Orchestration/Phases/CandidatePreparation.py` |
| `Compilation/Routing/Authoritative/FlowPhases/GuidePlanning.py` | `PhysicalDesign/Routing/Global/Orchestration/Phases/GuidePlanning.py` |
| `Compilation/Routing/Authoritative/FlowPhases/Materialization.py` | `PhysicalDesign/Routing/Global/Orchestration/Phases/Materialization.py` |
| `Compilation/Routing/Authoritative/FlowPhases/PortalPreparation.py` | `PhysicalDesign/Routing/Global/Orchestration/Phases/PortalPreparation.py` |
| `Compilation/Routing/Authoritative/FlowPhases/__init__.py` | `PhysicalDesign/Routing/Global/Orchestration/Phases/__init__.py` |
| `Compilation/Routing/Authoritative/Materialization.py` | `PhysicalDesign/Routing/Global/Materialization.py` |
| `Compilation/Routing/Authoritative/NegotiatedRouting/Preparation.py` | `PhysicalDesign/Routing/Global/Negotiation/Engine/Preparation.py` |
| `Compilation/Routing/Authoritative/NegotiatedRouting/Search.py` | `PhysicalDesign/Routing/Global/Negotiation/Engine/Search.py` |
| `Compilation/Routing/Authoritative/NegotiatedRouting/State.py` | `PhysicalDesign/Routing/Global/Negotiation/Engine/State.py` |
| `Compilation/Routing/Authoritative/NegotiatedRouting/__init__.py` | `PhysicalDesign/Routing/Global/Negotiation/Engine/__init__.py` |
| `Compilation/Routing/Authoritative/NegotiatedTrees.py` | `PhysicalDesign/Routing/Global/Negotiation/NegotiatedTrees.py` |
| `Compilation/Routing/Authoritative/PhysicalGuides.py` | `PhysicalDesign/Routing/Global/Guides/PhysicalGuides.py` |
| `Compilation/Routing/Authoritative/PortPreparation.py` | `PhysicalDesign/Routing/Global/Ports/PortPreparation.py` |
| `Compilation/Routing/Authoritative/PortPreparationFactors.py` | `PhysicalDesign/Routing/Global/Ports/PortPreparationFactors.py` |
| `Compilation/Routing/Authoritative/PortPreparationHelpers.py` | `PhysicalDesign/Routing/Global/Ports/PortPreparationHelpers.py` |
| `Compilation/Routing/Authoritative/PortPreparationInputs.py` | `PhysicalDesign/Routing/Global/Ports/PortPreparationInputs.py` |
| `Compilation/Routing/Authoritative/PortPreparationState.py` | `PhysicalDesign/Routing/Global/Ports/PortPreparationState.py` |
| `Compilation/Routing/Authoritative/PortSolving/Finalization.py` | `PhysicalDesign/Routing/Global/Ports/Solving/Finalization.py` |
| `Compilation/Routing/Authoritative/PortSolving/Search.py` | `PhysicalDesign/Routing/Global/Ports/Solving/Search.py` |
| `Compilation/Routing/Authoritative/PortSolving/Validation.py` | `PhysicalDesign/Routing/Global/Ports/Solving/Validation.py` |
| `Compilation/Routing/Authoritative/PortSolving/__init__.py` | `PhysicalDesign/Routing/Global/Ports/Solving/__init__.py` |
| `Compilation/Routing/Authoritative/Portals.py` | `PhysicalDesign/Routing/Global/Ports/Portals.py` |
| `Compilation/Routing/Authoritative/RunModels.py` | `PhysicalDesign/Routing/Global/Orchestration/RunModels.py` |
| `Compilation/Routing/Authoritative/RunState.py` | `PhysicalDesign/Routing/Global/Orchestration/RunState.py` |
| `Compilation/Routing/Authoritative/TrackPortfolio.py` | `PhysicalDesign/Routing/Global/Assignment/TrackPortfolio.py` |
| `Compilation/Routing/Authoritative/__init__.py` | `PhysicalDesign/Routing/Global/__init__.py` |
| `Compilation/Routing/ChannelPlanner.py` | `PhysicalDesign/Routing/Planning/ChannelPlanner.py` |
| `Compilation/Routing/Components/Access.py` | `PhysicalDesign/Routing/Regions/Boundaries/Access.py` |
| `Compilation/Routing/Components/Cache.py` | `PhysicalDesign/Routing/Regions/Cache.py` |
| `Compilation/Routing/Components/Certification.py` | `PhysicalDesign/Routing/Regions/Proofs/Certification.py` |
| `Compilation/Routing/Components/Core.py` | `PhysicalDesign/Routing/Regions/Core.py` |
| `Compilation/Routing/Components/Domains.py` | `PhysicalDesign/Routing/Regions/Domains.py` |
| `Compilation/Routing/Components/DynamicSolver.py` | `PhysicalDesign/Routing/Regions/Solving/DynamicSolver.py` |
| `Compilation/Routing/Components/Fabric.py` | `PhysicalDesign/Routing/Regions/Boundaries/Fabric.py` |
| `Compilation/Routing/Components/Feedthroughs.py` | `PhysicalDesign/Routing/Regions/Boundaries/Feedthroughs.py` |
| `Compilation/Routing/Components/GlobalNoGoods.py` | `PhysicalDesign/Routing/Regions/Proofs/GlobalNoGoods.py` |
| `Compilation/Routing/Components/InterfacePlanning.py` | `PhysicalDesign/Routing/Regions/Planning/InterfacePlanning.py` |
| `Compilation/Routing/Components/LegacySolver.py` | `PhysicalDesign/Routing/Regions/Solving/LegacySolver.py` |
| `Compilation/Routing/Components/NetPlanning.py` | `PhysicalDesign/Routing/Regions/Planning/NetPlanning.py` |
| `Compilation/Routing/Components/NoGoods.py` | `PhysicalDesign/Routing/Regions/Proofs/NoGoods.py` |
| `Compilation/Routing/Components/PhysicalPlanning.py` | `PhysicalDesign/Routing/Regions/Planning/PhysicalPlanning.py` |
| `Compilation/Routing/Components/Pipeline.py` | `PhysicalDesign/Routing/Regions/Pipeline.py` |
| `Compilation/Routing/Components/Portfolios.py` | `PhysicalDesign/Routing/Regions/Planning/Portfolios.py` |
| `Compilation/Routing/Components/Problem.py` | `PhysicalDesign/Routing/Regions/Boundaries/Problem.py` |
| `Compilation/Routing/Components/Reservations.py` | `PhysicalDesign/Routing/Regions/Boundaries/Reservations.py` |
| `Compilation/Routing/Components/Solver.py` | `PhysicalDesign/Routing/Regions/Solving/Solver.py` |
| `Compilation/Routing/Components/SymbolicDomains.py` | `PhysicalDesign/Routing/Regions/Symbolic/SymbolicDomains.py` |
| `Compilation/Routing/Components/SymbolicState.py` | `PhysicalDesign/Routing/Regions/Symbolic/SymbolicState.py` |
| `Compilation/Routing/Components/SymbolicWorkers.py` | `PhysicalDesign/Routing/Regions/Symbolic/SymbolicWorkers.py` |
| `Compilation/Routing/Components/Validation.py` | `PhysicalDesign/Routing/Regions/Proofs/Validation.py` |
| `Compilation/Routing/Components/__init__.py` | `PhysicalDesign/Routing/Regions/__init__.py` |
| `Compilation/Routing/Contracts/Component.py` | `PhysicalDesign/Contracts/Component.py` |
| `Compilation/Routing/Contracts/Core.py` | `PhysicalDesign/Contracts/Core.py` |
| `Compilation/Routing/Contracts/PhysicalInterface.py` | `PhysicalDesign/Contracts/PhysicalInterface.py` |
| `Compilation/Routing/Contracts/Placement.py` | `PhysicalDesign/Contracts/Placement.py` |
| `Compilation/Routing/Contracts/Results.py` | `PhysicalDesign/Contracts/Results.py` |
| `Compilation/Routing/Contracts/__init__.py` | `PhysicalDesign/Contracts/__init__.py` |
| `Compilation/Routing/EligibilityPreparation.py` | `PhysicalDesign/Routing/Execution/EligibilityPreparation.py` |
| `Compilation/Routing/Failures.py` | `PhysicalDesign/Contracts/Failures.py` |
| `Compilation/Routing/Interfaces/BoundaryRelations.py` | `PhysicalDesign/Constraints/BoundaryRelations.py` |
| `Compilation/Routing/Interfaces/PhysicalClaims.py` | `PhysicalDesign/Constraints/PhysicalClaims.py` |
| `Compilation/Routing/Interfaces/PortalConstraints.py` | `PhysicalDesign/Constraints/PortalConstraints.py` |
| `Compilation/Routing/Interfaces/__init__.py` | `PhysicalDesign/Constraints/__init__.py` |
| `Compilation/Routing/LocalFirst.py` | `PhysicalDesign/Routing/Planning/LocalFirst.py` |
| `Compilation/Routing/Pcb.py` | `PhysicalDesign/Routing/Pcb.py` |
| `Compilation/Routing/Policy.py` | `PhysicalDesign/Policy.py` |
| `Compilation/Routing/Reliability.py` | `PhysicalDesign/Runtime/Reliability.py` |
| `Compilation/Routing/ResourceGraph.py` | `PhysicalDesign/Resources/ResourceGraph.py` |
| `Compilation/Routing/Technology.py` | `PhysicalDesign/Redstone/Technology.py` |
| `Compilation/Routing/TemplateAssignment.py` | `PhysicalDesign/Routing/Assignment/TemplateAssignment.py` |
| `Compilation/Routing/TrackAssignment.py` | `PhysicalDesign/Routing/Assignment/TrackAssignment.py` |
| `Compilation/Routing/Workers/DetailedRouting.py` | `PhysicalDesign/Routing/Execution/DetailedRouting.py` |
| `Compilation/Routing/Workers/PinAccess.py` | `PhysicalDesign/Routing/Execution/PinAccess.py` |
| `Compilation/Routing/Workers/__init__.py` | `PhysicalDesign/Routing/Execution/__init__.py` |
| `Compilation/Routing/__init__.py` | `PhysicalDesign/Routing/__init__.py` |
| `Compilation/RunReporting.py` | `App/RunReporting.py` |
| `Compilation/Telemetry.py` | `App/Telemetry.py` |
| `Compilation/TelemetryObserver.py` | `App/TelemetryObserver.py` |
| `Main.py` | `App/Main.py` |
| `RustRouting/Cargo.lock` | `Kernels/Routing/Cargo.lock` |
| `RustRouting/Cargo.toml` | `Kernels/Routing/Cargo.toml` |
| `RustRouting/Src/Assignment/Api.rs` | `Kernels/Routing/Src/Assignment/Api.rs` |
| `RustRouting/Src/Assignment/Domains.rs` | `Kernels/Routing/Src/Assignment/Domains.rs` |
| `RustRouting/Src/Assignment/Search.rs` | `Kernels/Routing/Src/Assignment/Search.rs` |
| `RustRouting/Src/Assignment/Witness.rs` | `Kernels/Routing/Src/Assignment/Witness.rs` |
| `RustRouting/Src/Assignment/mod.rs` | `Kernels/Routing/Src/Assignment/mod.rs` |
| `RustRouting/Src/Core/Deadline.rs` | `Kernels/Routing/Src/Core/Deadline.rs` |
| `RustRouting/Src/Core/Models.rs` | `Kernels/Routing/Src/Core/Models.rs` |
| `RustRouting/Src/Core/Runtime.rs` | `Kernels/Routing/Src/Core/Runtime.rs` |
| `RustRouting/Src/Core/mod.rs` | `Kernels/Routing/Src/Core/mod.rs` |
| `RustRouting/Src/Escape/Api.rs` | `Kernels/Routing/Src/Escape/Api.rs` |
| `RustRouting/Src/Escape/Candidates/Access.rs` | `Kernels/Routing/Src/Escape/Candidates/Access.rs` |
| `RustRouting/Src/Escape/Candidates/AccessRamps.rs` | `Kernels/Routing/Src/Escape/Candidates/AccessRamps.rs` |
| `RustRouting/Src/Escape/Candidates/GuideDomain.rs` | `Kernels/Routing/Src/Escape/Candidates/GuideDomain.rs` |
| `RustRouting/Src/Escape/Candidates/GuideEnumeration.rs` | `Kernels/Routing/Src/Escape/Candidates/GuideEnumeration.rs` |
| `RustRouting/Src/Escape/Candidates/GuideGeometry.rs` | `Kernels/Routing/Src/Escape/Candidates/GuideGeometry.rs` |
| `RustRouting/Src/Escape/Candidates/PhysicalGuideEnumeration.rs` | `Kernels/Routing/Src/Escape/Candidates/PhysicalGuideEnumeration.rs` |
| `RustRouting/Src/Escape/Candidates/PoweredWitness.rs` | `Kernels/Routing/Src/Escape/Candidates/PoweredWitness.rs` |
| `RustRouting/Src/Escape/Candidates/mod.rs` | `Kernels/Routing/Src/Escape/Candidates/mod.rs` |
| `RustRouting/Src/Escape/Catalog/BundleDomain.rs` | `Kernels/Routing/Src/Escape/Catalog/BundleDomain.rs` |
| `RustRouting/Src/Escape/Catalog/Search.rs` | `Kernels/Routing/Src/Escape/Catalog/Search.rs` |
| `RustRouting/Src/Escape/Catalog/Solver.rs` | `Kernels/Routing/Src/Escape/Catalog/Solver.rs` |
| `RustRouting/Src/Escape/Catalog/SolverPreparation.rs` | `Kernels/Routing/Src/Escape/Catalog/SolverPreparation.rs` |
| `RustRouting/Src/Escape/Catalog/mod.rs` | `Kernels/Routing/Src/Escape/Catalog/mod.rs` |
| `RustRouting/Src/Escape/State.rs` | `Kernels/Routing/Src/Escape/State.rs` |
| `RustRouting/Src/Escape/Traversal.rs` | `Kernels/Routing/Src/Escape/Traversal.rs` |
| `RustRouting/Src/Escape/mod.rs` | `Kernels/Routing/Src/Escape/mod.rs` |
| `RustRouting/Src/Generation/Api.rs` | `Kernels/Routing/Src/Generation/Api.rs` |
| `RustRouting/Src/Generation/Batches.rs` | `Kernels/Routing/Src/Generation/Batches.rs` |
| `RustRouting/Src/Generation/DetailedTrees/ClaimAware.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/ClaimAware.rs` |
| `RustRouting/Src/Generation/DetailedTrees/GuidePreparation.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/GuidePreparation.rs` |
| `RustRouting/Src/Generation/DetailedTrees/PathGeneration.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/PathGeneration.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/Finalization.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/Phases/Finalization.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/FrozenBranches.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/Phases/FrozenBranches.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/Initialization.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/Phases/Initialization.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/SearchClosures.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/Phases/SearchClosures.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/SourceIntegration.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/Phases/SourceIntegration.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/TargetRouting.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/Phases/TargetRouting.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/mod.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/Phases/mod.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Preparation.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/Preparation.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Search.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/Search.rs` |
| `RustRouting/Src/Generation/DetailedTrees/mod.rs` | `Kernels/Routing/Src/Generation/DetailedTrees/mod.rs` |
| `RustRouting/Src/Generation/Factorized.rs` | `Kernels/Routing/Src/Generation/Factorized.rs` |
| `RustRouting/Src/Generation/SelectedWorldClaims.rs` | `Kernels/Routing/Src/Generation/SelectedWorldClaims.rs` |
| `RustRouting/Src/Generation/mod.rs` | `Kernels/Routing/Src/Generation/mod.rs` |
| `RustRouting/Src/Geometry/ExteriorConnectors.rs` | `Kernels/Routing/Src/Geometry/ExteriorConnectors.rs` |
| `RustRouting/Src/Geometry/RouteClaims.rs` | `Kernels/Routing/Src/Geometry/RouteClaims.rs` |
| `RustRouting/Src/Geometry/mod.rs` | `Kernels/Routing/Src/Geometry/mod.rs` |
| `RustRouting/Src/Lib.rs` | `Kernels/Routing/Src/Lib.rs` |
| `RustRouting/Src/Path/PathRouting.rs` | `Kernels/Routing/Src/Path/PathRouting.rs` |
| `RustRouting/Src/Path/mod.rs` | `Kernels/Routing/Src/Path/mod.rs` |
| `RustRouting/Src/PhysicalValidation/Mchprs.rs` | `Kernels/Routing/Src/PhysicalValidation/Mchprs.rs` |
| `RustRouting/Src/PhysicalValidation/mod.rs` | `Kernels/Routing/Src/PhysicalValidation/mod.rs` |
| `RustRouting/Src/Planning/AssignmentPlanning.rs` | `Kernels/Routing/Src/Planning/AssignmentPlanning.rs` |
| `RustRouting/Src/Planning/LeasePlanning.rs` | `Kernels/Routing/Src/Planning/LeasePlanning.rs` |
| `RustRouting/Src/Planning/mod.rs` | `Kernels/Routing/Src/Planning/mod.rs` |
| `RustRouting/Src/Python/Bindings.rs` | `Kernels/Routing/Src/Python/Bindings.rs` |
| `RustRouting/Src/Python/mod.rs` | `Kernels/Routing/Src/Python/mod.rs` |
| `RustRouting/ThirdParty/Flute3/LICENSE` | `Kernels/Routing/ThirdParty/Flute3/LICENSE` |
| `RustRouting/ThirdParty/Flute3/MODIFICATIONS.md` | `Kernels/Routing/ThirdParty/Flute3/MODIFICATIONS.md` |
| `RustRouting/ThirdParty/Flute3/NOTICE` | `Kernels/Routing/ThirdParty/Flute3/NOTICE` |
| `RustRouting/ThirdParty/Flute3/UPSTREAM.md` | `Kernels/Routing/ThirdParty/Flute3/UPSTREAM.md` |
| `RustRouting/ThirdParty/Flute3/Upstream/README.md` | `Kernels/Routing/ThirdParty/Flute3/Upstream/README.md` |
| `RustRouting/ThirdParty/Flute3/Upstream/etc/POST9.dat` | `Kernels/Routing/ThirdParty/Flute3/Upstream/etc/POST9.dat` |
| `RustRouting/ThirdParty/Flute3/Upstream/etc/POWV9.dat` | `Kernels/Routing/ThirdParty/Flute3/Upstream/etc/POWV9.dat` |
| `RustRouting/ThirdParty/Flute3/Upstream/flute.cpp` | `Kernels/Routing/ThirdParty/Flute3/Upstream/flute.cpp` |
| `SVDecoder/Sv.py` | `Formats/SystemVerilog/Sv.py` |
| `SVDecoder/__init__.py` | `Formats/SystemVerilog/__init__.py` |
| `SchemEncoder/SchemWriter.py` | `PhysicalDesign/Rendering/SchemWriter.py` |
| `SchemEncoder/__init__.py` | `PhysicalDesign/Rendering/__init__.py` |
| `Scripts/BuildRepeaterOrientationSmoke.py` | `Tools/Fabric/BuildRepeaterOrientationSmoke.py` |
| `Scripts/Fabric/ConsoleFabricServer.py` | `Tools/Fabric/ConsoleFabricServer.py` |
| `Scripts/Fabric/ControlFabricServer.py` | `Tools/Fabric/ControlFabricServer.py` |
| `Scripts/Fabric/ImportSchemToFabricServer.py` | `Tools/Fabric/ImportSchemToFabricServer.py` |
| `Scripts/Fabric/TestSchemInFabricServer.py` | `Tools/Fabric/TestSchemInFabricServer.py` |
| `Scripts/Mchprs/TestPhysicalFixture.py` | `Tools/Mchprs/TestPhysicalFixture.py` |
| `Scripts/Mchprs/__init__.py` | `Tools/Mchprs/__init__.py` |
| `Scripts/README.md` | `Tools/README.md` |
| `Scripts/Routing/CaptureRoutingDesignSnapshot.py` | `Tools/Routing/CaptureRoutingDesignSnapshot.py` |
| `Scripts/Routing/ReviewSourceStructure.py` | `Tools/Routing/ReviewSourceStructure.py` |
| `Scripts/Routing/RunRouterAcceptance.py` | `Tools/Routing/RunRouterAcceptance.py` |
| `Scripts/RunCla4AccessReplay.py` | `Tools/Routing/RunCla4AccessReplay.py` |
| `Templates/Input.litematic` | `Assets/Templates/Input.litematic` |
| `Templates/Nand.litematic` | `Assets/Templates/Nand.litematic` |
| `Templates/Output.litematic` | `Assets/Templates/Output.litematic` |
| `Templates/__init__.py` | `Assets/Templates/__init__.py` |
| `Tests/Frontend/__init__.py` | `Tests/App/__init__.py` |
| `Tests/Frontend/test_main_paths.py` | `Tests/App/test_main_paths.py` |
| `Tests/Frontend/test_routing_telemetry.py` | `Tests/App/test_routing_telemetry.py` |
| `Tests/Frontend/test_run_reporting.py` | `Tests/App/test_run_reporting.py` |
| `Tests/Frontend/test_sv_parser_failures.py` | `Tests/Formats/SystemVerilog/test_sv_parser_failures.py` |
| `Tests/Integration/test_native_package_contract.py` | `Tests/Native/test_native_package_contract.py` |
| `Tests/Integration/test_repeater_orientation_smoke.py` | `Tests/Tools/test_repeater_orientation_smoke.py` |
| `Tests/Integration/test_router_acceptance_harness.py` | `Tests/Tools/test_router_acceptance_harness.py` |
| `Tests/Placement/__init__.py` | `Tests/PhysicalDesign/Placement/__init__.py` |
| `Tests/Placement/test_access_contract_bounds.py` | `Tests/PhysicalDesign/Placement/test_access_contract_bounds.py` |
| `Tests/Placement/test_cla4_access_replay.py` | `Tests/PhysicalDesign/Placement/test_cla4_access_replay.py` |
| `Tests/Placement/test_derived_perimeter_access_fabric.py` | `Tests/PhysicalDesign/Placement/test_derived_perimeter_access_fabric.py` |
| `Tests/Placement/test_derived_perimeter_slots.py` | `Tests/PhysicalDesign/Placement/test_derived_perimeter_slots.py` |
| `Tests/Placement/test_fixed_pin_access_solver.py` | `Tests/PhysicalDesign/Placement/test_fixed_pin_access_solver.py` |
| `Tests/Placement/test_joint_cluster_orientation.py` | `Tests/PhysicalDesign/Placement/test_joint_cluster_orientation.py` |
| `Tests/Placement/test_physical_cells.py` | `Tests/PhysicalDesign/Placement/test_physical_cells.py` |
| `Tests/Placement/test_pin_aligned_packed_cluster_portfolio.py` | `Tests/PhysicalDesign/Placement/test_pin_aligned_packed_cluster_portfolio.py` |
| `Tests/Placement/test_placement_access_fabric.py` | `Tests/PhysicalDesign/Placement/test_placement_access_fabric.py` |
| `Tests/Placement/test_placement_boundary_feasibility.py` | `Tests/PhysicalDesign/Placement/test_placement_boundary_feasibility.py` |
| `Tests/Routing/__init__.py` | `Tests/PhysicalDesign/Routing/__init__.py` |
| `Tests/Routing/_authoritative_planner_contracts.py` | `Tests/PhysicalDesign/Routing/_authoritative_planner_contracts.py` |
| `Tests/Routing/_component_pipeline_contracts.py` | `Tests/PhysicalDesign/Routing/_component_pipeline_contracts.py` |
| `Tests/Routing/_physical_assembly_contracts.py` | `Tests/PhysicalDesign/Routing/_physical_assembly_contracts.py` |
| `Tests/Routing/test_authoritative_assignments.py` | `Tests/PhysicalDesign/Routing/test_authoritative_assignments.py` |
| `Tests/Routing/test_authoritative_caches.py` | `Tests/PhysicalDesign/Routing/test_authoritative_caches.py` |
| `Tests/Routing/test_authoritative_deadlines.py` | `Tests/PhysicalDesign/Routing/test_authoritative_deadlines.py` |
| `Tests/Routing/test_authoritative_exterior_distance.py` | `Tests/PhysicalDesign/Routing/test_authoritative_exterior_distance.py` |
| `Tests/Routing/test_authoritative_global_routes.py` | `Tests/PhysicalDesign/Routing/test_authoritative_global_routes.py` |
| `Tests/Routing/test_authoritative_guide_stage.py` | `Tests/PhysicalDesign/Routing/test_authoritative_guide_stage.py` |
| `Tests/Routing/test_authoritative_portals.py` | `Tests/PhysicalDesign/Routing/test_authoritative_portals.py` |
| `Tests/Routing/test_channel_planner.py` | `Tests/PhysicalDesign/Routing/test_channel_planner.py` |
| `Tests/Routing/test_component_pipeline_cache_lifetime.py` | `Tests/PhysicalDesign/Routing/test_component_pipeline_cache_lifetime.py` |
| `Tests/Routing/test_component_pipeline_orchestration.py` | `Tests/PhysicalDesign/Routing/test_component_pipeline_orchestration.py` |
| `Tests/Routing/test_component_pipeline_proof_scheduling.py` | `Tests/PhysicalDesign/Routing/test_component_pipeline_proof_scheduling.py` |
| `Tests/Routing/test_component_pipeline_repair_queues.py` | `Tests/PhysicalDesign/Routing/test_component_pipeline_repair_queues.py` |
| `Tests/Routing/test_component_planning.py` | `Tests/PhysicalDesign/Routing/test_component_planning.py` |
| `Tests/Routing/test_component_profile_projection.py` | `Tests/PhysicalDesign/Routing/test_component_profile_projection.py` |
| `Tests/Routing/test_component_router.py` | `Tests/PhysicalDesign/Routing/test_component_router.py` |
| `Tests/Routing/test_component_symbolic_factor_state_contract.py` | `Tests/PhysicalDesign/Routing/test_component_symbolic_factor_state_contract.py` |
| `Tests/Routing/test_component_symbolic_higher_order_domain.py` | `Tests/PhysicalDesign/Routing/test_component_symbolic_higher_order_domain.py` |
| `Tests/Routing/test_eligibility_preparation.py` | `Tests/PhysicalDesign/Routing/test_eligibility_preparation.py` |
| `Tests/Routing/test_local_factor_unsat_projection.py` | `Tests/PhysicalDesign/Routing/test_local_factor_unsat_projection.py` |
| `Tests/Routing/test_physical_assembly_exact_proofs.py` | `Tests/PhysicalDesign/Routing/test_physical_assembly_exact_proofs.py` |
| `Tests/Routing/test_physical_assembly_fabric.py` | `Tests/PhysicalDesign/Routing/test_physical_assembly_fabric.py` |
| `Tests/Routing/test_physical_assembly_global_handoff.py` | `Tests/PhysicalDesign/Routing/test_physical_assembly_global_handoff.py` |
| `Tests/Routing/test_physical_assembly_port_domains.py` | `Tests/PhysicalDesign/Routing/test_physical_assembly_port_domains.py` |
| `Tests/Routing/test_physical_component_models.py` | `Tests/PhysicalDesign/Routing/test_physical_component_models.py` |
| `Tests/Routing/test_pre_route_interface.py` | `Tests/PhysicalDesign/Routing/test_pre_route_interface.py` |
| `Tests/Routing/test_repeater_orientation_contract.py` | `Tests/PhysicalDesign/Routing/test_repeater_orientation_contract.py` |
| `Tests/Routing/test_resource_graph.py` | `Tests/PhysicalDesign/Routing/test_resource_graph.py` |
| `Tests/Routing/test_routing_contract_schema.py` | `Tests/PhysicalDesign/Routing/test_routing_contract_schema.py` |
| `Tests/Routing/test_routing_failures.py` | `Tests/PhysicalDesign/Routing/test_routing_failures.py` |
| `Tests/Routing/test_routing_policy_generic_profile.py` | `Tests/PhysicalDesign/Routing/test_routing_policy_generic_profile.py` |
| `Tests/Routing/test_routing_resources.py` | `Tests/PhysicalDesign/Routing/test_routing_resources.py` |
| `Tests/Routing/test_template_track_assignment.py` | `Tests/PhysicalDesign/Routing/test_template_track_assignment.py` |
| `Tests/Routing/test_topology_demand_profile.py` | `Tests/PhysicalDesign/Routing/test_topology_demand_profile.py` |
| `Tests/Synthesis/__init__.py` | `Tests/Compilation/Synthesis/__init__.py` |
| `Tests/Synthesis/test_adder_arithmetic_oracles.py` | `Tests/Compilation/Synthesis/test_adder_arithmetic_oracles.py` |
| `Tests/Synthesis/test_component_graph.py` | `Tests/Compilation/Synthesis/test_component_graph.py` |
| `Tests/Synthesis/test_logic_optimization.py` | `Tests/Compilation/Synthesis/test_logic_optimization.py` |
| `Tests/Synthesis/test_nand_differential.py` | `Tests/Compilation/Synthesis/test_nand_differential.py` |
| `Tests/test_fabric_server_boundary.py` | `Tests/Validation/Fabric/test_fabric_server_boundary.py` |
| `Tests/test_fabric_server_console.py` | `Tests/Validation/Fabric/test_fabric_server_console.py` |
| `Tests/test_fabric_server_runtime_manager.py` | `Tests/Validation/Fabric/test_fabric_server_runtime_manager.py` |
| `Tests/test_fabric_server_snapshot.py` | `Tests/Validation/Fabric/test_fabric_server_snapshot.py` |
| `Tests/test_mchprs_validation.py` | `Tests/Validation/Mchprs/test_mchprs_validation.py` |
| `Tests/test_schem_import.py` | `Tests/Validation/Fabric/test_schem_import.py` |
| `Tests/test_schem_roundtrip.py` | `Tests/PhysicalDesign/Rendering/test_schem_roundtrip.py` |
| `Tests/test_script_cli_guidance.py` | `Tests/Tools/test_script_cli_guidance.py` |
| `ValidationServerHarness/Mchprs/Validation.py` | `Validation/Mchprs/Validation.py` |
| `ValidationServerHarness/Mchprs/__init__.py` | `Validation/Mchprs/__init__.py` |
| `ValidationServerHarness/README.md` | `Validation/Fabric/ServerHarness/README.md` |
| `Runtime/FabricServer/PyScripts/Anvil.py` | `Validation/Fabric/ServerManager/Anvil.py` |
| `Runtime/FabricServer/PyScripts/Main.py` | `Validation/Fabric/ServerManager/Main.py` |
| `Runtime/FabricServer/PyScripts/Paths.py` | `Validation/Fabric/ServerManager/Paths.py` |
| `Runtime/FabricServer/PyScripts/Process.py` | `Validation/Fabric/ServerManager/Process.py` |
| `Runtime/FabricServer/PyScripts/Protocol.py` | `Validation/Fabric/ServerManager/Protocol.py` |
| `Runtime/FabricServer/PyScripts/__init__.py` | `Validation/Fabric/ServerManager/__init__.py` |
| `ValidationServerHarness/__init__.py` | `Validation/__init__.py` |
| `Cache/Gradle/ServerHarness/Build.gradle` | `Validation/Fabric/ServerHarness/build.gradle` |
| `ValidationServerHarness/gradle.properties` | `Validation/Fabric/ServerHarness/gradle.properties` |
| `ValidationServerHarness/settings.gradle` | `Validation/Fabric/ServerHarness/settings.gradle` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/HarnessConfiguration.java` | `Validation/Fabric/ServerHarness/src/main/java/dev/redstonecompiler/harness/HarnessConfiguration.java` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/HarnessValidation.java` | `Validation/Fabric/ServerHarness/src/main/java/dev/redstonecompiler/harness/HarnessValidation.java` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/RedstoneCompilerHarness.java` | `Validation/Fabric/ServerHarness/src/main/java/dev/redstonecompiler/harness/RedstoneCompilerHarness.java` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/TraceQuiescenceTracker.java` | `Validation/Fabric/ServerHarness/src/main/java/dev/redstonecompiler/harness/TraceQuiescenceTracker.java` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/mixin/MinecraftServerMixin.java` | `Validation/Fabric/ServerHarness/src/main/java/dev/redstonecompiler/harness/mixin/MinecraftServerMixin.java` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/mixin/ServerGamePacketListenerImplMixin.java` | `Validation/Fabric/ServerHarness/src/main/java/dev/redstonecompiler/harness/mixin/ServerGamePacketListenerImplMixin.java` |
| `ValidationServerHarness/src/main/resources/fabric.mod.json` | `Validation/Fabric/ServerHarness/src/main/resources/fabric.mod.json` |
| `ValidationServerHarness/src/main/resources/redstonecompiler-harness.mixins.json` | `Validation/Fabric/ServerHarness/src/main/resources/redstonecompiler-harness.mixins.json` |
| `ValidationServerHarness/src/test/java/dev/redstonecompiler/harness/HarnessValidationProgressTest.java` | `Validation/Fabric/ServerHarness/src/test/java/dev/redstonecompiler/harness/HarnessValidationProgressTest.java` |
| `ValidationServerHarness/src/test/java/dev/redstonecompiler/harness/TraceQuiescenceTrackerTest.java` | `Validation/Fabric/ServerHarness/src/test/java/dev/redstonecompiler/harness/TraceQuiescenceTrackerTest.java` |

</details>

The root `Main.py` implementation moved to `App/Main.py`; a small compatibility
launcher remains at the original path. New grouping-package initializers contain
only their package description unless preserving an existing export surface.

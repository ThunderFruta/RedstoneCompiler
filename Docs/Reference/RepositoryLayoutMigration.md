# Repository layout migration

This migration groups existing modules under `App`, `Compiler`, `PhysicalDesign`,
`Validation`, `Native`, `Assets`, and `Tools`. It starts from `5eaf49c` and contains
336 tracked file moves. Two test packages containing only placeholder initializers
were removed. Existing function/class implementations stay in their whole modules.

## Resulting layout

```text
App/                         guided CLI, argument CLI, reports, telemetry
Assets/Templates/            template catalog and three litematic templates
Compiler/
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
Native/Routing/              existing Rust crate, lockfile, and nested Src tree
RedstoneCompiler/            Python facade, native import, native stub
Tools/{Fabric,Mchprs,Routing}/ developer and runtime tools
Tests/                       tests grouped by the owning domain
Docs/                        current references and design documents
Examples/                    existing SystemVerilog inputs
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
Gradle output remains `ValidationServerHarness/build/`; the project cache remains
`ValidationServerHarness/.gradle/` through the supported
[`org.gradle.projectcachedir` property](https://docs.gradle.org/current/userguide/build_environment.html).
`Output/`, `Cache/`, `.venv/`, and external Minecraft/template locations retain
their behavior. Template selection still prefers the complete external pack and
otherwise uses the local assets.

Build the Python package from the repository root (`pip install -e .`, or
`maturin develop` in an activated environment) so Maturin reads the root
`pyproject.toml` and its native module name/features. Cargo commands continue to
use `--manifest-path Native/Routing/Cargo.toml`.

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
| `Compiler/Cells/Library.py` | `PhysicalDesign/Cells/Library.py` |
| `Compiler/Cells/__init__.py` | `PhysicalDesign/Cells/__init__.py` |
| `Compiler/FabricServer/FailureTrace.py` | `Validation/Fabric/FailureTrace.py` |
| `Compiler/FabricServer/Fixture.py` | `Validation/Fabric/Fixture.py` |
| `Compiler/FabricServer/Models.py` | `Validation/Fabric/Models.py` |
| `Compiler/FabricServer/SchemImport.py` | `Validation/Fabric/SchemImport.py` |
| `Compiler/FabricServer/ServerSnapshot.py` | `Validation/Fabric/ServerSnapshot.py` |
| `Compiler/FabricServer/Testing.py` | `Validation/Fabric/Testing.py` |
| `Compiler/FabricServer/Validation.py` | `Validation/Fabric/Validation.py` |
| `Compiler/FabricServer/__init__.py` | `Validation/Fabric/__init__.py` |
| `Compiler/Main.py` | `App/CompilerCli.py` |
| `Compiler/PhysicalValidation/Fixture.py` | `Validation/Core/Fixture.py` |
| `Compiler/PhysicalValidation/Models.py` | `Validation/Core/Models.py` |
| `Compiler/PhysicalValidation/Testing.py` | `Validation/Core/Testing.py` |
| `Compiler/PhysicalValidation/Vectors.py` | `Validation/Core/Vectors.py` |
| `Compiler/PhysicalValidation/__init__.py` | `Validation/Core/__init__.py` |
| `Compiler/Placement/Access/Capacity.py` | `PhysicalDesign/Placement/Access/Capacity.py` |
| `Compiler/Placement/Access/EscapePaths.py` | `PhysicalDesign/Placement/Access/EscapePaths.py` |
| `Compiler/Placement/Access/Fabric.py` | `PhysicalDesign/Placement/Access/Fabric.py` |
| `Compiler/Placement/Access/Geometry.py` | `PhysicalDesign/Placement/Access/Geometry.py` |
| `Compiler/Placement/Access/__init__.py` | `PhysicalDesign/Placement/Access/__init__.py` |
| `Compiler/Placement/Core/Cache.py` | `PhysicalDesign/Placement/Core/Cache.py` |
| `Compiler/Placement/Core/Channels.py` | `PhysicalDesign/Placement/Core/Channels.py` |
| `Compiler/Placement/Core/Clustering.py` | `PhysicalDesign/Placement/Core/Clustering.py` |
| `Compiler/Placement/Core/Clusters.py` | `PhysicalDesign/Placement/Core/Clusters.py` |
| `Compiler/Placement/Core/Commit.py` | `PhysicalDesign/Placement/Core/Commit/Commit.py` |
| `Compiler/Placement/Core/CommitHelpers.py` | `PhysicalDesign/Placement/Core/Commit/CommitHelpers.py` |
| `Compiler/Placement/Core/CommitPreparation.py` | `PhysicalDesign/Placement/Core/Commit/CommitPreparation.py` |
| `Compiler/Placement/Core/CommitRouting.py` | `PhysicalDesign/Placement/Core/Commit/CommitRouting.py` |
| `Compiler/Placement/Core/CommitState.py` | `PhysicalDesign/Placement/Core/Commit/CommitState.py` |
| `Compiler/Placement/Core/Compactness.py` | `PhysicalDesign/Placement/Core/Compactness.py` |
| `Compiler/Placement/Core/Constraints.py` | `PhysicalDesign/Placement/Core/Constraints.py` |
| `Compiler/Placement/Core/Costs.py` | `PhysicalDesign/Placement/Core/Costs.py` |
| `Compiler/Placement/Core/MandatoryAccess.py` | `PhysicalDesign/Placement/Core/MandatoryAccess.py` |
| `Compiler/Placement/Core/Repair.py` | `PhysicalDesign/Placement/Core/Repair.py` |
| `Compiler/Placement/Core/Search.py` | `PhysicalDesign/Placement/Core/Search.py` |
| `Compiler/Placement/Core/__init__.py` | `PhysicalDesign/Placement/Core/__init__.py` |
| `Compiler/Placement/Flow/AttemptHistory.py` | `PhysicalDesign/Flow/AttemptHistory.py` |
| `Compiler/Placement/Flow/CandidateRouting.py` | `PhysicalDesign/Flow/CandidateRouting.py` |
| `Compiler/Placement/Flow/Candidates.py` | `PhysicalDesign/Flow/Candidates.py` |
| `Compiler/Placement/Flow/Demand.py` | `PhysicalDesign/Flow/Demand.py` |
| `Compiler/Placement/Flow/Feedback.py` | `PhysicalDesign/Flow/Feedback.py` |
| `Compiler/Placement/Flow/PhysicalAssembly.py` | `PhysicalDesign/Flow/PhysicalAssembly.py` |
| `Compiler/Placement/Flow/PhysicalFlow.py` | `PhysicalDesign/Flow/PhysicalFlow.py` |
| `Compiler/Placement/Flow/PlacementAttempts.py` | `PhysicalDesign/Flow/PlacementAttempts.py` |
| `Compiler/Placement/Flow/Portfolios.py` | `PhysicalDesign/Flow/Portfolios.py` |
| `Compiler/Placement/Flow/Preparation.py` | `PhysicalDesign/Flow/Preparation.py` |
| `Compiler/Placement/Flow/Results.py` | `PhysicalDesign/Flow/Results.py` |
| `Compiler/Placement/Flow/RoutingAttempts.py` | `PhysicalDesign/Flow/RoutingAttempts.py` |
| `Compiler/Placement/Flow/Runner.py` | `PhysicalDesign/Flow/Runner.py` |
| `Compiler/Placement/Flow/Setup.py` | `PhysicalDesign/Flow/Setup.py` |
| `Compiler/Placement/Flow/State.py` | `PhysicalDesign/Flow/State.py` |
| `Compiler/Placement/Flow/__init__.py` | `PhysicalDesign/Flow/__init__.py` |
| `Compiler/Placement/Geometry.py` | `PhysicalDesign/Geometry/Placement.py` |
| `Compiler/Placement/PreRouteInterface.py` | `PhysicalDesign/Placement/PreRouteInterface.py` |
| `Compiler/Placement/Rotation.py` | `PhysicalDesign/Geometry/Rotation.py` |
| `Compiler/Placement/__init__.py` | `PhysicalDesign/Placement/__init__.py` |
| `Compiler/Routing/Actions/Geometry.py` | `PhysicalDesign/Redstone/Actions/Geometry.py` |
| `Compiler/Routing/Actions/Repeaters.py` | `PhysicalDesign/Redstone/Actions/Repeaters.py` |
| `Compiler/Routing/Actions/Validation.py` | `PhysicalDesign/Redstone/Actions/Validation.py` |
| `Compiler/Routing/Actions/__init__.py` | `PhysicalDesign/Redstone/Actions/__init__.py` |
| `Compiler/Routing/Authoritative/AssignmentState.py` | `PhysicalDesign/Routing/Global/Assignment/AssignmentState.py` |
| `Compiler/Routing/Authoritative/BoundaryLeaseDomains.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeaseDomains.py` |
| `Compiler/Routing/Authoritative/BoundaryLeaseHelpers.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeaseHelpers.py` |
| `Compiler/Routing/Authoritative/BoundaryLeasePatterns.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeasePatterns.py` |
| `Compiler/Routing/Authoritative/BoundaryLeasePlanning.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeasePlanning.py` |
| `Compiler/Routing/Authoritative/BoundaryLeaseState.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeaseState.py` |
| `Compiler/Routing/Authoritative/BoundaryLeases.py` | `PhysicalDesign/Routing/Global/Leases/BoundaryLeases.py` |
| `Compiler/Routing/Authoritative/CandidateCache.py` | `PhysicalDesign/Routing/Global/Candidates/CandidateCache.py` |
| `Compiler/Routing/Authoritative/CandidateDomains.py` | `PhysicalDesign/Routing/Global/Candidates/CandidateDomains.py` |
| `Compiler/Routing/Authoritative/CandidateGuides.py` | `PhysicalDesign/Routing/Global/Candidates/CandidateGuides.py` |
| `Compiler/Routing/Authoritative/Dependencies.py` | `PhysicalDesign/Routing/Global/Flow/Dependencies.py` |
| `Compiler/Routing/Authoritative/ExteriorConnectors.py` | `PhysicalDesign/Routing/Global/Ports/ExteriorConnectors.py` |
| `Compiler/Routing/Authoritative/Flow.py` | `PhysicalDesign/Routing/Global/Flow/Flow.py` |
| `Compiler/Routing/Authoritative/FlowPhases/AssignmentPreparation.py` | `PhysicalDesign/Routing/Global/Flow/Phases/AssignmentPreparation.py` |
| `Compiler/Routing/Authoritative/FlowPhases/AssignmentSolve.py` | `PhysicalDesign/Routing/Global/Flow/Phases/AssignmentSolve.py` |
| `Compiler/Routing/Authoritative/FlowPhases/Bootstrap.py` | `PhysicalDesign/Routing/Global/Flow/Phases/Bootstrap.py` |
| `Compiler/Routing/Authoritative/FlowPhases/CandidateMaterialization.py` | `PhysicalDesign/Routing/Global/Flow/Phases/CandidateMaterialization.py` |
| `Compiler/Routing/Authoritative/FlowPhases/CandidatePreparation.py` | `PhysicalDesign/Routing/Global/Flow/Phases/CandidatePreparation.py` |
| `Compiler/Routing/Authoritative/FlowPhases/GuidePlanning.py` | `PhysicalDesign/Routing/Global/Flow/Phases/GuidePlanning.py` |
| `Compiler/Routing/Authoritative/FlowPhases/Materialization.py` | `PhysicalDesign/Routing/Global/Flow/Phases/Materialization.py` |
| `Compiler/Routing/Authoritative/FlowPhases/PortalPreparation.py` | `PhysicalDesign/Routing/Global/Flow/Phases/PortalPreparation.py` |
| `Compiler/Routing/Authoritative/FlowPhases/__init__.py` | `PhysicalDesign/Routing/Global/Flow/Phases/__init__.py` |
| `Compiler/Routing/Authoritative/Materialization.py` | `PhysicalDesign/Routing/Global/Materialization.py` |
| `Compiler/Routing/Authoritative/NegotiatedRouting/Preparation.py` | `PhysicalDesign/Routing/Global/Negotiation/Engine/Preparation.py` |
| `Compiler/Routing/Authoritative/NegotiatedRouting/Search.py` | `PhysicalDesign/Routing/Global/Negotiation/Engine/Search.py` |
| `Compiler/Routing/Authoritative/NegotiatedRouting/State.py` | `PhysicalDesign/Routing/Global/Negotiation/Engine/State.py` |
| `Compiler/Routing/Authoritative/NegotiatedRouting/__init__.py` | `PhysicalDesign/Routing/Global/Negotiation/Engine/__init__.py` |
| `Compiler/Routing/Authoritative/NegotiatedTrees.py` | `PhysicalDesign/Routing/Global/Negotiation/NegotiatedTrees.py` |
| `Compiler/Routing/Authoritative/PhysicalGuides.py` | `PhysicalDesign/Routing/Global/Guides/PhysicalGuides.py` |
| `Compiler/Routing/Authoritative/PortPreparation.py` | `PhysicalDesign/Routing/Global/Ports/PortPreparation.py` |
| `Compiler/Routing/Authoritative/PortPreparationFactors.py` | `PhysicalDesign/Routing/Global/Ports/PortPreparationFactors.py` |
| `Compiler/Routing/Authoritative/PortPreparationHelpers.py` | `PhysicalDesign/Routing/Global/Ports/PortPreparationHelpers.py` |
| `Compiler/Routing/Authoritative/PortPreparationInputs.py` | `PhysicalDesign/Routing/Global/Ports/PortPreparationInputs.py` |
| `Compiler/Routing/Authoritative/PortPreparationState.py` | `PhysicalDesign/Routing/Global/Ports/PortPreparationState.py` |
| `Compiler/Routing/Authoritative/PortSolving/Finalization.py` | `PhysicalDesign/Routing/Global/Ports/Solving/Finalization.py` |
| `Compiler/Routing/Authoritative/PortSolving/Search.py` | `PhysicalDesign/Routing/Global/Ports/Solving/Search.py` |
| `Compiler/Routing/Authoritative/PortSolving/Validation.py` | `PhysicalDesign/Routing/Global/Ports/Solving/Validation.py` |
| `Compiler/Routing/Authoritative/PortSolving/__init__.py` | `PhysicalDesign/Routing/Global/Ports/Solving/__init__.py` |
| `Compiler/Routing/Authoritative/Portals.py` | `PhysicalDesign/Routing/Global/Ports/Portals.py` |
| `Compiler/Routing/Authoritative/RunModels.py` | `PhysicalDesign/Routing/Global/Flow/RunModels.py` |
| `Compiler/Routing/Authoritative/RunState.py` | `PhysicalDesign/Routing/Global/Flow/RunState.py` |
| `Compiler/Routing/Authoritative/TrackPortfolio.py` | `PhysicalDesign/Routing/Global/Assignment/TrackPortfolio.py` |
| `Compiler/Routing/Authoritative/__init__.py` | `PhysicalDesign/Routing/Global/__init__.py` |
| `Compiler/Routing/ChannelPlanner.py` | `PhysicalDesign/Routing/Planning/ChannelPlanner.py` |
| `Compiler/Routing/Components/Access.py` | `PhysicalDesign/Routing/Regions/Interfaces/Access.py` |
| `Compiler/Routing/Components/Cache.py` | `PhysicalDesign/Routing/Regions/Cache.py` |
| `Compiler/Routing/Components/Certification.py` | `PhysicalDesign/Routing/Regions/Proofs/Certification.py` |
| `Compiler/Routing/Components/Core.py` | `PhysicalDesign/Routing/Regions/Core.py` |
| `Compiler/Routing/Components/Domains.py` | `PhysicalDesign/Routing/Regions/Domains.py` |
| `Compiler/Routing/Components/DynamicSolver.py` | `PhysicalDesign/Routing/Regions/Solving/DynamicSolver.py` |
| `Compiler/Routing/Components/Fabric.py` | `PhysicalDesign/Routing/Regions/Interfaces/Fabric.py` |
| `Compiler/Routing/Components/Feedthroughs.py` | `PhysicalDesign/Routing/Regions/Interfaces/Feedthroughs.py` |
| `Compiler/Routing/Components/GlobalNoGoods.py` | `PhysicalDesign/Routing/Regions/Proofs/GlobalNoGoods.py` |
| `Compiler/Routing/Components/InterfacePlanning.py` | `PhysicalDesign/Routing/Regions/Planning/InterfacePlanning.py` |
| `Compiler/Routing/Components/LegacySolver.py` | `PhysicalDesign/Routing/Regions/Solving/LegacySolver.py` |
| `Compiler/Routing/Components/NetPlanning.py` | `PhysicalDesign/Routing/Regions/Planning/NetPlanning.py` |
| `Compiler/Routing/Components/NoGoods.py` | `PhysicalDesign/Routing/Regions/Proofs/NoGoods.py` |
| `Compiler/Routing/Components/PhysicalPlanning.py` | `PhysicalDesign/Routing/Regions/Planning/PhysicalPlanning.py` |
| `Compiler/Routing/Components/Pipeline.py` | `PhysicalDesign/Routing/Regions/Pipeline.py` |
| `Compiler/Routing/Components/Portfolios.py` | `PhysicalDesign/Routing/Regions/Planning/Portfolios.py` |
| `Compiler/Routing/Components/Problem.py` | `PhysicalDesign/Routing/Regions/Interfaces/Problem.py` |
| `Compiler/Routing/Components/Reservations.py` | `PhysicalDesign/Routing/Regions/Interfaces/Reservations.py` |
| `Compiler/Routing/Components/Solver.py` | `PhysicalDesign/Routing/Regions/Solving/Solver.py` |
| `Compiler/Routing/Components/SymbolicDomains.py` | `PhysicalDesign/Routing/Regions/Symbolic/SymbolicDomains.py` |
| `Compiler/Routing/Components/SymbolicState.py` | `PhysicalDesign/Routing/Regions/Symbolic/SymbolicState.py` |
| `Compiler/Routing/Components/SymbolicWorkers.py` | `PhysicalDesign/Routing/Regions/Symbolic/SymbolicWorkers.py` |
| `Compiler/Routing/Components/Validation.py` | `PhysicalDesign/Routing/Regions/Proofs/Validation.py` |
| `Compiler/Routing/Components/__init__.py` | `PhysicalDesign/Routing/Regions/__init__.py` |
| `Compiler/Routing/Contracts/Component.py` | `PhysicalDesign/Contracts/Component.py` |
| `Compiler/Routing/Contracts/Core.py` | `PhysicalDesign/Contracts/Core.py` |
| `Compiler/Routing/Contracts/PhysicalInterface.py` | `PhysicalDesign/Contracts/PhysicalInterface.py` |
| `Compiler/Routing/Contracts/Placement.py` | `PhysicalDesign/Contracts/Placement.py` |
| `Compiler/Routing/Contracts/Results.py` | `PhysicalDesign/Contracts/Results.py` |
| `Compiler/Routing/Contracts/__init__.py` | `PhysicalDesign/Contracts/__init__.py` |
| `Compiler/Routing/EligibilityPreparation.py` | `PhysicalDesign/Routing/Workers/EligibilityPreparation.py` |
| `Compiler/Routing/Failures.py` | `PhysicalDesign/Contracts/Failures.py` |
| `Compiler/Routing/Interfaces/BoundaryRelations.py` | `PhysicalDesign/Interfaces/BoundaryRelations.py` |
| `Compiler/Routing/Interfaces/PhysicalClaims.py` | `PhysicalDesign/Interfaces/PhysicalClaims.py` |
| `Compiler/Routing/Interfaces/PortalConstraints.py` | `PhysicalDesign/Interfaces/PortalConstraints.py` |
| `Compiler/Routing/Interfaces/__init__.py` | `PhysicalDesign/Interfaces/__init__.py` |
| `Compiler/Routing/LocalFirst.py` | `PhysicalDesign/Routing/Planning/LocalFirst.py` |
| `Compiler/Routing/Pcb.py` | `PhysicalDesign/Routing/Pcb.py` |
| `Compiler/Routing/Policy.py` | `PhysicalDesign/Policy.py` |
| `Compiler/Routing/Reliability.py` | `PhysicalDesign/Execution/Reliability.py` |
| `Compiler/Routing/ResourceGraph.py` | `PhysicalDesign/Resources/ResourceGraph.py` |
| `Compiler/Routing/Technology.py` | `PhysicalDesign/Redstone/Technology.py` |
| `Compiler/Routing/TemplateAssignment.py` | `PhysicalDesign/Routing/Assignment/TemplateAssignment.py` |
| `Compiler/Routing/TrackAssignment.py` | `PhysicalDesign/Routing/Assignment/TrackAssignment.py` |
| `Compiler/Routing/Workers/DetailedRouting.py` | `PhysicalDesign/Routing/Workers/DetailedRouting.py` |
| `Compiler/Routing/Workers/PinAccess.py` | `PhysicalDesign/Routing/Workers/PinAccess.py` |
| `Compiler/Routing/Workers/__init__.py` | `PhysicalDesign/Routing/Workers/__init__.py` |
| `Compiler/Routing/__init__.py` | `PhysicalDesign/Routing/__init__.py` |
| `Compiler/RunReporting.py` | `App/RunReporting.py` |
| `Compiler/Telemetry.py` | `App/Telemetry.py` |
| `Compiler/TelemetryObserver.py` | `App/TelemetryObserver.py` |
| `Main.py` | `App/Main.py` |
| `RustRouting/Cargo.lock` | `Native/Routing/Cargo.lock` |
| `RustRouting/Cargo.toml` | `Native/Routing/Cargo.toml` |
| `RustRouting/Src/Assignment/Api.rs` | `Native/Routing/Src/Assignment/Api.rs` |
| `RustRouting/Src/Assignment/Domains.rs` | `Native/Routing/Src/Assignment/Domains.rs` |
| `RustRouting/Src/Assignment/Search.rs` | `Native/Routing/Src/Assignment/Search.rs` |
| `RustRouting/Src/Assignment/Witness.rs` | `Native/Routing/Src/Assignment/Witness.rs` |
| `RustRouting/Src/Assignment/mod.rs` | `Native/Routing/Src/Assignment/mod.rs` |
| `RustRouting/Src/Core/Deadline.rs` | `Native/Routing/Src/Core/Deadline.rs` |
| `RustRouting/Src/Core/Models.rs` | `Native/Routing/Src/Core/Models.rs` |
| `RustRouting/Src/Core/Runtime.rs` | `Native/Routing/Src/Core/Runtime.rs` |
| `RustRouting/Src/Core/mod.rs` | `Native/Routing/Src/Core/mod.rs` |
| `RustRouting/Src/Escape/Api.rs` | `Native/Routing/Src/Escape/Api.rs` |
| `RustRouting/Src/Escape/Candidates/Access.rs` | `Native/Routing/Src/Escape/Candidates/Access.rs` |
| `RustRouting/Src/Escape/Candidates/AccessRamps.rs` | `Native/Routing/Src/Escape/Candidates/AccessRamps.rs` |
| `RustRouting/Src/Escape/Candidates/GuideDomain.rs` | `Native/Routing/Src/Escape/Candidates/GuideDomain.rs` |
| `RustRouting/Src/Escape/Candidates/GuideEnumeration.rs` | `Native/Routing/Src/Escape/Candidates/GuideEnumeration.rs` |
| `RustRouting/Src/Escape/Candidates/GuideGeometry.rs` | `Native/Routing/Src/Escape/Candidates/GuideGeometry.rs` |
| `RustRouting/Src/Escape/Candidates/PhysicalGuideEnumeration.rs` | `Native/Routing/Src/Escape/Candidates/PhysicalGuideEnumeration.rs` |
| `RustRouting/Src/Escape/Candidates/PoweredWitness.rs` | `Native/Routing/Src/Escape/Candidates/PoweredWitness.rs` |
| `RustRouting/Src/Escape/Candidates/mod.rs` | `Native/Routing/Src/Escape/Candidates/mod.rs` |
| `RustRouting/Src/Escape/Catalog/BundleDomain.rs` | `Native/Routing/Src/Escape/Catalog/BundleDomain.rs` |
| `RustRouting/Src/Escape/Catalog/Search.rs` | `Native/Routing/Src/Escape/Catalog/Search.rs` |
| `RustRouting/Src/Escape/Catalog/Solver.rs` | `Native/Routing/Src/Escape/Catalog/Solver.rs` |
| `RustRouting/Src/Escape/Catalog/SolverPreparation.rs` | `Native/Routing/Src/Escape/Catalog/SolverPreparation.rs` |
| `RustRouting/Src/Escape/Catalog/mod.rs` | `Native/Routing/Src/Escape/Catalog/mod.rs` |
| `RustRouting/Src/Escape/State.rs` | `Native/Routing/Src/Escape/State.rs` |
| `RustRouting/Src/Escape/Traversal.rs` | `Native/Routing/Src/Escape/Traversal.rs` |
| `RustRouting/Src/Escape/mod.rs` | `Native/Routing/Src/Escape/mod.rs` |
| `RustRouting/Src/Generation/Api.rs` | `Native/Routing/Src/Generation/Api.rs` |
| `RustRouting/Src/Generation/Batches.rs` | `Native/Routing/Src/Generation/Batches.rs` |
| `RustRouting/Src/Generation/DetailedTrees/ClaimAware.rs` | `Native/Routing/Src/Generation/DetailedTrees/ClaimAware.rs` |
| `RustRouting/Src/Generation/DetailedTrees/GuidePreparation.rs` | `Native/Routing/Src/Generation/DetailedTrees/GuidePreparation.rs` |
| `RustRouting/Src/Generation/DetailedTrees/PathGeneration.rs` | `Native/Routing/Src/Generation/DetailedTrees/PathGeneration.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/Finalization.rs` | `Native/Routing/Src/Generation/DetailedTrees/Phases/Finalization.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/FrozenBranches.rs` | `Native/Routing/Src/Generation/DetailedTrees/Phases/FrozenBranches.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/Initialization.rs` | `Native/Routing/Src/Generation/DetailedTrees/Phases/Initialization.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/SearchClosures.rs` | `Native/Routing/Src/Generation/DetailedTrees/Phases/SearchClosures.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/SourceIntegration.rs` | `Native/Routing/Src/Generation/DetailedTrees/Phases/SourceIntegration.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/TargetRouting.rs` | `Native/Routing/Src/Generation/DetailedTrees/Phases/TargetRouting.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Phases/mod.rs` | `Native/Routing/Src/Generation/DetailedTrees/Phases/mod.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Preparation.rs` | `Native/Routing/Src/Generation/DetailedTrees/Preparation.rs` |
| `RustRouting/Src/Generation/DetailedTrees/Search.rs` | `Native/Routing/Src/Generation/DetailedTrees/Search.rs` |
| `RustRouting/Src/Generation/DetailedTrees/mod.rs` | `Native/Routing/Src/Generation/DetailedTrees/mod.rs` |
| `RustRouting/Src/Generation/Factorized.rs` | `Native/Routing/Src/Generation/Factorized.rs` |
| `RustRouting/Src/Generation/SelectedWorldClaims.rs` | `Native/Routing/Src/Generation/SelectedWorldClaims.rs` |
| `RustRouting/Src/Generation/mod.rs` | `Native/Routing/Src/Generation/mod.rs` |
| `RustRouting/Src/Geometry/ExteriorConnectors.rs` | `Native/Routing/Src/Geometry/ExteriorConnectors.rs` |
| `RustRouting/Src/Geometry/RouteClaims.rs` | `Native/Routing/Src/Geometry/RouteClaims.rs` |
| `RustRouting/Src/Geometry/mod.rs` | `Native/Routing/Src/Geometry/mod.rs` |
| `RustRouting/Src/Lib.rs` | `Native/Routing/Src/Lib.rs` |
| `RustRouting/Src/Path/PathRouting.rs` | `Native/Routing/Src/Path/PathRouting.rs` |
| `RustRouting/Src/Path/mod.rs` | `Native/Routing/Src/Path/mod.rs` |
| `RustRouting/Src/PhysicalValidation/Mchprs.rs` | `Native/Routing/Src/PhysicalValidation/Mchprs.rs` |
| `RustRouting/Src/PhysicalValidation/mod.rs` | `Native/Routing/Src/PhysicalValidation/mod.rs` |
| `RustRouting/Src/Planning/AssignmentPlanning.rs` | `Native/Routing/Src/Planning/AssignmentPlanning.rs` |
| `RustRouting/Src/Planning/LeasePlanning.rs` | `Native/Routing/Src/Planning/LeasePlanning.rs` |
| `RustRouting/Src/Planning/mod.rs` | `Native/Routing/Src/Planning/mod.rs` |
| `RustRouting/Src/Python/Bindings.rs` | `Native/Routing/Src/Python/Bindings.rs` |
| `RustRouting/Src/Python/mod.rs` | `Native/Routing/Src/Python/mod.rs` |
| `RustRouting/ThirdParty/Flute3/LICENSE` | `Native/Routing/ThirdParty/Flute3/LICENSE` |
| `RustRouting/ThirdParty/Flute3/MODIFICATIONS.md` | `Native/Routing/ThirdParty/Flute3/MODIFICATIONS.md` |
| `RustRouting/ThirdParty/Flute3/NOTICE` | `Native/Routing/ThirdParty/Flute3/NOTICE` |
| `RustRouting/ThirdParty/Flute3/UPSTREAM.md` | `Native/Routing/ThirdParty/Flute3/UPSTREAM.md` |
| `RustRouting/ThirdParty/Flute3/Upstream/README.md` | `Native/Routing/ThirdParty/Flute3/Upstream/README.md` |
| `RustRouting/ThirdParty/Flute3/Upstream/etc/POST9.dat` | `Native/Routing/ThirdParty/Flute3/Upstream/etc/POST9.dat` |
| `RustRouting/ThirdParty/Flute3/Upstream/etc/POWV9.dat` | `Native/Routing/ThirdParty/Flute3/Upstream/etc/POWV9.dat` |
| `RustRouting/ThirdParty/Flute3/Upstream/flute.cpp` | `Native/Routing/ThirdParty/Flute3/Upstream/flute.cpp` |
| `SVDecoder/Sv.py` | `Compiler/Frontend/Sv.py` |
| `SVDecoder/__init__.py` | `Compiler/Frontend/__init__.py` |
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
| `Tests/Frontend/test_sv_parser_failures.py` | `Tests/Compiler/Frontend/test_sv_parser_failures.py` |
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
| `Tests/Synthesis/__init__.py` | `Tests/Compiler/Synthesis/__init__.py` |
| `Tests/Synthesis/test_adder_arithmetic_oracles.py` | `Tests/Compiler/Synthesis/test_adder_arithmetic_oracles.py` |
| `Tests/Synthesis/test_component_graph.py` | `Tests/Compiler/Synthesis/test_component_graph.py` |
| `Tests/Synthesis/test_logic_optimization.py` | `Tests/Compiler/Synthesis/test_logic_optimization.py` |
| `Tests/Synthesis/test_nand_differential.py` | `Tests/Compiler/Synthesis/test_nand_differential.py` |
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
| `ValidationServerHarness/README.md` | `Validation/Fabric/Harness/README.md` |
| `ValidationServerHarness/Server/PyScripts/Anvil.py` | `Validation/Fabric/Runtime/Anvil.py` |
| `ValidationServerHarness/Server/PyScripts/Main.py` | `Validation/Fabric/Runtime/Main.py` |
| `ValidationServerHarness/Server/PyScripts/Paths.py` | `Validation/Fabric/Runtime/Paths.py` |
| `ValidationServerHarness/Server/PyScripts/Process.py` | `Validation/Fabric/Runtime/Process.py` |
| `ValidationServerHarness/Server/PyScripts/Protocol.py` | `Validation/Fabric/Runtime/Protocol.py` |
| `ValidationServerHarness/Server/PyScripts/__init__.py` | `Validation/Fabric/Runtime/__init__.py` |
| `ValidationServerHarness/__init__.py` | `Validation/__init__.py` |
| `ValidationServerHarness/build.gradle` | `Validation/Fabric/Harness/build.gradle` |
| `ValidationServerHarness/gradle.properties` | `Validation/Fabric/Harness/gradle.properties` |
| `ValidationServerHarness/settings.gradle` | `Validation/Fabric/Harness/settings.gradle` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/HarnessConfiguration.java` | `Validation/Fabric/Harness/src/main/java/dev/redstonecompiler/harness/HarnessConfiguration.java` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/HarnessValidation.java` | `Validation/Fabric/Harness/src/main/java/dev/redstonecompiler/harness/HarnessValidation.java` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/RedstoneCompilerHarness.java` | `Validation/Fabric/Harness/src/main/java/dev/redstonecompiler/harness/RedstoneCompilerHarness.java` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/TraceQuiescenceTracker.java` | `Validation/Fabric/Harness/src/main/java/dev/redstonecompiler/harness/TraceQuiescenceTracker.java` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/mixin/MinecraftServerMixin.java` | `Validation/Fabric/Harness/src/main/java/dev/redstonecompiler/harness/mixin/MinecraftServerMixin.java` |
| `ValidationServerHarness/src/main/java/dev/redstonecompiler/harness/mixin/ServerGamePacketListenerImplMixin.java` | `Validation/Fabric/Harness/src/main/java/dev/redstonecompiler/harness/mixin/ServerGamePacketListenerImplMixin.java` |
| `ValidationServerHarness/src/main/resources/fabric.mod.json` | `Validation/Fabric/Harness/src/main/resources/fabric.mod.json` |
| `ValidationServerHarness/src/main/resources/redstonecompiler-harness.mixins.json` | `Validation/Fabric/Harness/src/main/resources/redstonecompiler-harness.mixins.json` |
| `ValidationServerHarness/src/test/java/dev/redstonecompiler/harness/HarnessValidationProgressTest.java` | `Validation/Fabric/Harness/src/test/java/dev/redstonecompiler/harness/HarnessValidationProgressTest.java` |
| `ValidationServerHarness/src/test/java/dev/redstonecompiler/harness/TraceQuiescenceTrackerTest.java` | `Validation/Fabric/Harness/src/test/java/dev/redstonecompiler/harness/TraceQuiescenceTrackerTest.java` |

</details>

The root `Main.py` implementation moved to `App/Main.py`; a small compatibility
launcher remains at the original path. New grouping-package initializers contain
only their package description unless preserving an existing export surface.

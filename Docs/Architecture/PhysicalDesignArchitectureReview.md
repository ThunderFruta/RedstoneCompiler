# Physical design architecture: findings and proposed boundaries

**Status:** source review and proposed design. The ownership changes and
interfaces below are implementation targets. Their presence in this document
does not mean that the compiler already enforces them.

**Review baseline:** September 2, 2026, America/New_York; static inventory
recorded at 2026-09-03 01:04:38 UTC on branch
`Router-Changes&Parallelism`, HEAD
`91d0f29857db051d8bea961353f35495445a62b1`.

**Main finding:** the compiler has a useful source-tree split and an acyclic
explicit Python import graph, but its modules remain coupled through broad
entrypoints, shared mutable state, implementation-specific contracts, and
widely available service capabilities.

This document connects that finding to the requested routing improvements,
defines proposed responsibility and data boundaries, and gives a migration and
acceptance plan. The existing implementation map remains in
[ProjectTreeDesignDoc.md](../Reference/ProjectTreeDesignDoc.md). Operational
validation requirements remain in
[RunningTests.md](../Testing/RunningTests.md) and
[FabricServerValidation.md](FabricServerValidation.md).

The repository layout has since been migrated. Source paths in this document
follow the current tree; the dated measurements above remain review evidence.
The [layout migration record](../Reference/RepositoryLayoutMigration.md) separates
the completed moves from the proposed architecture changes.

Contents:

- [Evidence and limits](#1-evidence-and-limits)
- [Current responsibilities and assumptions](#2-current-responsibilities-and-implicit-assumptions)
- [Concrete boundary findings](#3-concrete-boundary-findings)
- [Requested improvements](#4-requested-improvements-and-supporting-requirements)
- [Proposed ownership](#5-proposed-ownership-boundaries)
- [Data, state, and capabilities](#6-data-state-and-capability-contracts)
- [R9: objective and shortcuts](#7-r9-objective-ownership-and-shortcut-optimization)
- [R10: redstone model](#8-r10-a-shared-redstone-model-before-validation)
- [Workers, caches, and mutation](#9-worker-cache-and-mutation-ownership)
- [Enforcement and migration](#10-enforcement-and-migration)
- [Acceptance and design choices](#11-acceptance-and-remaining-design-choices)

## 1. Evidence and limits

### 1.1 Source and structural review

The review inspected the working tree, which contained existing Fabric,
control-script, and test edits. It was not a clean-HEAD acceptance run. The
185 inventoried Python source hashes were checked again while preparing this
document and still matched the review inventory.

| Observation | Recorded result |
|---|---|
| Existing structural and routing-schema gates | 11 passed in 2.11 seconds |
| Inventoried production Python files | 185 |
| Distinct explicit import edges | 1,073 |
| Explicit import strongly connected components | 0 |
| Wildcard import sites | 1 |
| `RouteAuthoritativeResources` parameters | 50 |
| Parameters ending in `Only` on that entrypoint | 9 |
| Declared `AuthoritativeRoutingState` fields | 1,078; all annotated `Any` |
| Declared `PlacementFlowState` fields | 8; 7 annotated `Any` |
| Declared `PlacementCommitState` fields | 30; all annotated `Any` |

The AST inventory covered `Compilation/`, `Formats/SystemVerilog/`, `PhysicalDesign/Rendering/`,
`Assets/Templates/`, `Tools/`, `Validation/Mchprs/`, and `Main.py`.
It included explicit imports inside functions and conditional branches.
It did not reconstruct implicit package initialization, dynamic imports,
runtime service lookups, or object aliasing. Native implementation internals,
Java behavior, generated runtime data, and tests were outside that inventory.
Import counts describe references; they are not counts of violations.

The recorded gate command was:

~~~bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  Tests/Structural/test_source_structure.py \
  Tests/PhysicalDesign/Routing/test_routing_contract_schema.py
~~~

Routing acceptance, native behavioral tests, MCHPRS validation, and Fabric
validation were **not-run** for the architecture review. The structural result
establishes properties covered by those tests, not physical circuit correctness.

### 1.2 Saved routing benchmark

The previously completed diagnostic run
`Output/Benchmarks/Telemetry-20260902T231407Z/` recorded the following results.
It used an AMD Ryzen 9 9950X, 32 logical CPUs, and an eight-worker routing limit.
The table records saved evidence, not a new benchmark of this proposal.

| Circuit | Wall time, seconds | Routing average cores | Outcome |
|---|---:|---:|---|
| HalfAdder | 1.624 | 1.05 | PreRouteInterfaceSelection |
| FullAdder | 12.127 | 1.22 | Passed |
| RippleCarryAdder4 | 15.359 | 1.25 | Passed |
| RippleCarryAdder8 | 19.889 | 1.21 | Passed |
| DecimalToBinary4 | 15.061 | 1.02 | PreRouteInterfaceSelection |
| TFlipFlopLatch | 14.331 | 1.16 | PreRouteInterfaceSelection |
| CarryLookaheadAdder4 | 125.331 | 1.29, partial samples | Timeout |

All seven planned cases ran: three passed, four failed, none were skipped.
CLA4's sampled physical-component interface planning occupied 89.570 seconds
at 1.41 average cores; placement-search preparation occupied 14.276 seconds at
1.00 core. Named native worker threads accumulated 0.520 CPU-seconds during
that sampled interface-planning work.

This was one run per circuit on a shared desktop. The observer and external
Fabric service CPU were excluded. A killed process has partial sampled
evidence rather than a completed routing interval. Task lifecycle telemetry
covered the symbolic unary proof pool. These measurements identify investigation
targets; they do not establish a controlled speedup baseline or a precise
breakdown of every queue's serialization and startup cost.

## 2. Current responsibilities and implicit assumptions

The pipeline already contains substantial physical reasoning before rendering:

~~~mermaid
flowchart TD
    I["NAND IR"] --> P["Placement candidates and local routes"]
    P --> B["Pin access and component interfaces"]
    B --> G["Global routes and reservations"]
    G --> L["Component interiors and assembly"]
    L --> R["Route materialization and cleanup"]
    R --> E["Rendered block states"]
    E --> M["MCHPRS validation"]
    M --> F["Fabric canary and settled snapshot"]
    B -. Feedback .-> P
    L -. Feedback .-> B
~~~

Routing consumes the in-memory NAND IR. A historical reference to the
post-DOT stage should not be interpreted as routing reading a Graphviz layout.
The diagnostic NAND format is described in
[NandJson.md](../Formats/NandJson.md).

Cell footprints, pin directions, support, headroom, interference, and
congestion already influence early stages. Complete block-state rendering
happens later. The lazy-representation improvement concerns repeated expansion
and copying during search while preserving those early physical constraints.

The following are operating assumptions inferred from interfaces and rules,
not quotations from the original design:

| Assumption reflected in the code | Consequence | Proposed replacement |
|---|---|---|
| An acyclic import graph and a file split sufficiently separate modules. | Cross-package callers can still depend on concrete implementation helpers. | A declared public API and permitted dependency set for every owner. |
| One routing function can also expose preparation and validation stages. | Boolean mode combinations define different use cases and result behavior. | Separate typed operations with explicit request/result families. |
| Passing a shared dataclass establishes a phase contract. | Every phase can depend on much more state than it actually needs. | Explicit phase inputs, private working data, and explicit outputs. |
| Passing a service object restricts capabilities. | A generic namespace can expose almost every dependency to every phase. | Small service protocols scoped to the operation. |
| The Contracts namespace is sufficient to make a type neutral. | Interchange values carry implementation objects and mutable runtime state. | Separate domain records, snapshots, solver sessions, and runtime services. |
| Shared functionality can remain where it was first needed. | Placement owns local routing; routing imports cell loading from the writer. | Explicit coordination ownership and shared cell/model foundations. |

## 3. Concrete boundary findings

### F1. Existing guards cover a narrow dependency policy

[LowerLayerPrefixes](../../Tests/Structural/test_source_structure.py#L57)
constrains four routing prefixes: Contracts, Interfaces, Components, and
Authoritative. There are documented exceptions for existing placement
geometry/rotation primitives.

The
[public-entrypoint test](../../Tests/Structural/test_source_structure.py#L301)
checks that six exported functions resolve to their expected concrete owners.
It does not require every caller to use only those entrypoints. The broader
repository also lacks equivalent hard ownership rules for all frontend,
rendering, validation, script, and runtime boundaries.

Consequently, passing these gates is compatible with broad internal access.
Most crossings identified here are allowed by the current rules.

### F2. The public router combines different use cases

[RouteAuthoritativeResources](../../PhysicalDesign/Routing/Global/Orchestration/Flow.py#L20)
has 50 parameters. Nine end in `Only`, covering portal geometry, track
assignment, raw domains, foreign-access validation, interface assignment,
component problems, assembly, and port-factor preparation.

A consumer must understand which part of the larger algorithm it is invoking
and which combinations of arguments make sense. Moving all 50 fields into a
request dataclass would preserve that problem.

The replacement should expose coherent operations such as preparing a routing
domain, generating candidates, solving assignments, and materializing a route.
Each operation needs a specific input type, output family, and budget.

### F3. Phase state is broad and weakly specified

[AuthoritativeRoutingState](../../PhysicalDesign/Routing/Global/Orchestration/RunState.py#L44)
declares 1,078 fields, all `Any`. Slots constrain attribute names but do not
establish field types, valid stage transitions, or which phase may mutate a
field.

[PlacementFlowState](../../PhysicalDesign/Orchestration/State.py#L28) declares eight
initial fields and allows dynamic attributes. Its helper uses `setattr`.
The source inventory found 566 distinct `Context.*` assignment names across
Placement/Flow and 411 across the Placement/Core/Commit modules. Those are
source-level assignment inventories, not proof that every name appears in
every runtime instance.

Temporary search variables should stay local to a phase. Shared information
should become a deliberate input/output contract or coordinator-owned state.

### F4. Service injection preserves a broad namespace

[AuthoritativeRoutingServices](../../PhysicalDesign/Routing/Global/Orchestration/RunState.py#L12)
wraps `Mapping[str, Any]`, exposing it through `__getattr__`.
The public flow constructs that table from `globals()`.

This supplies a useful testing seam, but it does not constrain what a phase can
call. It also hides semantic dependencies from a static import graph. A phase
should instead receive only the model queries, kernels, cache operations,
clock, or event sink it needs, with concrete signatures.

### F5. Contracts mix output data with solver sessions

[Contracts/Results.py](../../PhysicalDesign/Contracts/Results.py#L29) imports
types from ChannelPlanner, ResourceGraph, and TrackAssignment.
`RoutedDesign` includes geometry alongside selected planning and diagnostic
objects. `RoutingResources` contains static geometry together with mutable
caches, native contexts, ownership fingerprints, and prepared solver state.

For example,
[boundary-relation code](../../PhysicalDesign/Constraints/BoundaryRelations.py#L1897)
can attach cache fields to the resource object. This lets consumers expand a
shared object's responsibilities.

The proposed split is:

- immutable domain and artifact records;
- immutable geometry and ownership snapshots;
- private mutable solver sessions;
- cache, worker, clock, and telemetry services with explicit owners.

### F6. Physical responsibilities cross placement, routing, and rendering

[Placement/Core/CommitRouting.py](../../PhysicalDesign/Placement/Engine/Commit/CommitRouting.py#L46)
finds local paths, validates local power, builds claims, and freezes local
wires. This means placement results already contain a mixture of positions
and routing decisions.

[Routing/Actions/Geometry.py](../../PhysicalDesign/Redstone/Rules/Geometry.py#L9)
imports `LoadTemplate` from PhysicalDesign.Rendering. The
[writer](../../PhysicalDesign/Rendering/SchemWriter.py#L15) imports cell definitions,
placement transforms, and routing technology helpers.

Coordinated placement and routing are necessary. The boundary improvement is
to give their joint search a coordinator and move reusable cell/template
geometry and redstone semantics into shared foundations.

The physical model also remains distributed. Resource generation, final
physical graphs, power propagation, and wire-state rendering implement related
decisions in different places.
[BuildRoutingResources](../../PhysicalDesign/Redstone/Rules/Geometry.py#L201)
does not receive the selected technology when constructing its resource graph.
That is a concrete propagation gap for a future versioned model contract.

### F7. Compactness and cleanup do not guarantee short connections

The compact-placement score compares access/electrical conflict penalties,
area, maximum dimension, and then estimated wire length. The
[score ordering](../../PhysicalDesign/Placement/Engine/Compactness.py#L357) means that,
with equal conflict penalties, a smaller area wins before connection length
is considered. Some other placement paths also contain distance and capacity
costs; the current implementation is not uniformly footprint-only.

[CompactRoutedTrees](../../PhysicalDesign/Routing/Pcb.py#L99) builds a graph from
existing wire positions and retains paths to required terminals and access
points. It removes loops and unused branches but cannot create a new path
through an empty gap. A winding path can remain necessary within that
restricted graph even when a shorter physical connection could be constructed.

### F8. Documentation and executable policy have drifted

[ProjectTreeDesignDoc.md](../Reference/ProjectTreeDesignDoc.md) says that
source-size ceilings are enforced. [RunningTests.md](../Testing/RunningTests.md)
describes size as advisory, and the inspected structural test file contains no
size gate.

Reconcile the current-facing statements when adopting a new boundary policy.
Preserve historical snapshots and dated evidence. File size is useful review
information; the stronger criterion is whether dependencies, inputs, outputs,
and mutation rights are constrained.

## 4. Requested improvements and supporting requirements

The identifiers below preserve the discussion's requirements. They are target
behavior, except where the status explicitly identifies existing work.

| ID | Requested improvement | Architectural implication |
|---|---|---|
| R1 | Delay expensive physical expansion and copying. | Cache cell/orientation geometry, use compact contracts, expand exact candidate claims on demand, and render full block states after selection. |
| R2 | Make layouts compact from the initial placement. | Choose region shapes, positions, access, and channel capacity together; retain alternatives and expand only bottlenecks. |
| R3 | Consider folding and all four legal directions early. | Rotation and pin access participate in candidate generation. Logical depth expresses dependencies without fixing a Z coordinate. |
| R4 | Carry global routing constraints through the search. | Reserve shared capacity, account for fanout/crossings, and keep local/global choices jointly revisable. |
| R5 | Generalize by topology and reuse compatible work. | Normalize connectivity and ordered port roles; cover shared producers, reconvergence, and multiple outputs without circuit-name cases. |
| R6 | Supply persistent workers with useful independent tasks. | Use immutable work items, retained worker caches, meaningful batches, and one process/native CPU budget. |
| R7 | Put more algorithm policy and coordination in Python. | Keep narrow Rust kernels for measured compute hotspots, with typed and bounded calls. |
| R8 | Enable detailed telemetry without raw terminal dumps. | Collection and concise reporting already exist. Extend coverage to every queue, coordinator phase, transfer/startup cost, cache, and discarded work. |
| R9 | Optimize connection quality and remove avoidable detours. | One objective evaluator must account for routed connection cost and congestion; search and cleanup must be able to propose new legal wire positions. |
| R10 | Revamp the redstone model used before validation. | A versioned block/cell model supplies geometry, influence, directed power, declared timing/state scope, and consistent decisions to all consumers. |

Supporting requirements:

- **N1 — Explicit search outcomes:** distinguish feasibility, completeness,
  optimality, proof scope, timeout, cancellation, and worker failure.
- **N2 — Shared physical rule contract:** placement masks, resource claims,
  final graph checks, and rendering agree. R10 expands this requirement into
  the full model redesign.
- **N3 — Verifiable reuse and commitment:** immutable snapshots, dependency
  fingerprints, stable result selection, and one owner for accepted state.
- **N4 — Bounded execution:** one routing deadline, bounded queues/caches,
  CPU and memory admission, cooperative cancellation, and bounded cleanup.
- **N5 — Explicit acceptance:** HalfAdder and CLA4 work through the same rules,
  existing passing cases remain passing, all planned cases are recorded, and
  the physical validation gates remain authoritative.

## 5. Proposed ownership boundaries

These are logical responsibilities. Broad folder moves are not a prerequisite
for establishing the APIs. Much of the proposed physical-design coordinator
already exists inside Placement/Flow.

| Owner | Responsibility | Boundary |
|---|---|---|
| Application pipeline | Configuration, stage order, validation gates, publication. | Calls public stage APIs and consumes their results; does not inspect private solver state. |
| Physical-design coordinator | Candidate portfolio, feedback, retries, global incumbent, accepted ownership, versions, and overall budget. | Sole writer of accepted design state; delegates heavy independent search. |
| Placement engine | Positions, rotations, pin-access alternatives, spatial metrics, and feasibility requirements. | Returns proposed variants; local wiring is owned by a region solver. |
| Global planner | Shared corridors, capacity, layers, branching demand, and compatible interface alternatives. | Returns provisional plans and scoped conflicts; does not rewrite placement state. |
| Region and detailed-routing engines | Local problems, route trees, exact claims, repair and shortcut proposals. | Consume immutable boundaries/placements and return candidates or feedback. |
| Objective evaluator | Metric definitions, comparison policy, score breakdowns, and ranking. | Consumes immutable metrics/policy. Legality is a prerequisite; bounds and estimates remain identified. |
| Redstone model and cell library | Cell/block roles, templates, transforms, connectivity, influence, power, and supported timing assumptions. | Shared foundation; no global search, live server lifecycle, or publication policy. |
| Domain contracts | Topology, placements, boundaries, claims, candidates, certificates, feedback, artifacts. | Portable immutable records without pools, native handles, mutable caches, or private planner objects. |
| Runtime services | Workers, cache access, CPU/memory limits, clocks, deadlines, events. | Explicit capabilities with controlled lifecycle and mutation. |
| Renderer and serializer | Statically checked geometry to block states; block states to encoded artifacts. | Consumes shared cells/model and final physical contracts; does not own placement or routing policy. |
| Validation adapters | Fixtures, backend execution, observations, failures, settled snapshots. | Consume fixture/artifact contracts; do not inspect search state or decide routing retries. |

Arrows in the following diagram mean permitted calls. Request/result
contracts carry information across each boundary:

~~~mermaid
flowchart TD
    P["Application pipeline"] --> D["Physical-design coordinator"]
    P --> E["Renderer / serializer"]
    P --> V["Validation adapters"]
    D --> A["Placement API"]
    D --> G["Global planning API"]
    D --> R["Region / routing APIs"]
    D --> O["Objective evaluator"]
    D --> W["Workers / caches / budget"]
    A --> M["Redstone model / cell library"]
    G --> M
    R --> M
    E --> M
    R --> K["Bounded native kernels"]
~~~

Routing feedback returns to the coordinator as data. A blocked pin, for
example, identifies its pin, region, conflicting claims, and the scope of the
conclusion. The coordinator may request another placement. The router does not
reach into a placement Context object to change coordinates.

A region solver can coordinate a local placement/routing problem under an
explicit boundary contract and child budget. This supports hierarchy without
making every stage responsible for the entire circuit.

## 6. Data, state, and capability contracts

### 6.1 Distinguish artifact states

~~~mermaid
flowchart LR
    P["Placement variant"] --> R["Provisional route"]
    R --> S["Statically checked design"]
    S --> B["Rendered fixture"]
    B --> V["Physically validated result"]
    V --> A["Published observed artifact"]
    R -. Scoped feedback .-> P
~~~

Each transition needs an explicit result and supporting evidence. A populated
field or a successful local check is insufficient to infer that a later stage
has completed. Static model legality covers its declared scope; behavioral
acceptance still requires the validation backends.

The following contract families are proposed, not existing API declarations:

| Contract family | Essential information | Excluded information |
|---|---|---|
| Placement variant | Stable identity, cell transforms, pin choices, bounds, assumptions, estimated metrics. | Accepted global route state or process objects. |
| Region/boundary contract | Ordered signal roles, directional ports, allowed alternatives, required external reservations and model version. | Unversioned references to another region's mutable solver. |
| Geometry/ownership snapshot | Immutable occupancy, supports, required air, electrical influence, ownership, dependency version. | Cache dictionaries and native execution handles. |
| Work request | Task ID, operation, snapshot/region identity, dependency fingerprint, input records, budget. | A copy of the entire shared routing state. |
| Work result | Task ID, dependency identity, outcome, candidate/proof, work used, diagnostics. | Permission to mutate the accepted design independently. |
| Feedback | Affected region/pins/nets/resources, reason, assumptions, proof scope, completeness. | An unqualified instruction to reject all later placements. |
| Score breakdown | Metric values and units, bound/estimate/measured classification, objective version. | Hidden stage-specific scoring policy. |
| Final physical artifact | Checked geometry, technology/policy identity, provenance and evidence references. | Native contexts and prepared search frontiers. |

Immutable records must also use immutable nested containers. A frozen dataclass
with mutable lists or dictionaries still permits mutation through aliases.

### 6.2 Replace overloaded operations incrementally

Illustrative operation boundaries are:

~~~text
GeneratePlacementVariants(PlacementRequest, PlacementServices) -> PlacementBatch
PrepareRoutingDomain(DomainRequest, DomainServices) -> DomainResult
GenerateRouteCandidates(RouteRequest, RoutingServices) -> CandidateBatch
SolveRouteAssignment(AssignmentRequest, AssignmentServices) -> AssignmentResult
OptimizeRoutes(OptimizationRequest, RoutingServices) -> OptimizationResult
MaterializeCheckedDesign(MaterializationRequest, ModelServices) -> PhysicalResult
~~~

Names can change during implementation. The requirement is that each operation
describes one use case, has a defined output family, and receives only the
capabilities needed for that operation. Internal scratch variables stay local.

### 6.3 Preserve the meaning of incomplete work

A legal witness, an exhaustive proof over a declared domain, and an unfinished
search are different results. A domain may be fully searched without covering
every possible circuit layout.

The current
[portfolio solver](../../PhysicalDesign/Routing/Assignment/TemplateAssignment.py#L616)
returns immediately for an incomplete materialized domain. The proposed
feasibility policy should retain legal incumbents and consider alternatives
while separately reporting search completeness and optimality.

Incomplete work must never become a cached negative proof. A reusable proof
must identify its exact domain, physical assumptions, external dependencies,
technology version, and completeness.

## 7. R9: objective ownership and shortcut optimization

The objective evaluator owns definitions and comparisons; placement, routing,
and cleanup supply measurements. Each call must identify whether a value is a
lower bound, a heuristic estimate, or a measurement of a completed candidate.

Relevant dimensions include:

- total unique routed material and individual source-to-sink connection length;
- shared-tree reuse for fanout without double-counting the same wire;
- bottleneck demand relative to capacity and remaining pin-escape space;
- bends, vertical transitions, supports, and repeaters;
- propagation delay and supported timing constraints;
- combined placed/routed footprint, height, and maximum dimension.

Hard electrical and resource conflicts are validity failures, not merely large
cost penalties. Among feasible alternatives, a slightly larger gate envelope
can justify substantially shorter connections or less congestion. The initial
policy should preserve useful tradeoff alternatives and report its score
breakdown; objective weights should be calibrated against actual accepted
layouts rather than chosen to hide a failing case.

A shortest connection must use actual pin access and technology-valid steps.
Independent shortest paths can compete for the same exclusive resources.
Consequently, candidate generation needs short alternatives and global
assignment needs compatible alternatives.

### 7.1 The winding-route example

The discussion included a screenshot with a winding blue-supported route and
a pink span marking a possible direct connection between nearby net points.
The screenshot establishes a geometric shortcut candidate. It does not by
itself establish net ownership, all required branches, clearance, repeater
requirements, or behavioral equivalence.

~~~mermaid
flowchart LR
    P["Net point P"] --> D["Existing winding route"]
    D --> Q["Net point Q"]
    P -. New shortcut candidate .-> Q
~~~

The required improvement covers both initial search and later optimization:

1. Generate direct and low-bend candidates early, with legal pin access.
2. Search new wire positions when optimizing an existing route.
3. Ask the redstone model to evaluate the candidate's physical effects.
4. Preserve every required sink and the intended signal direction.
5. Rebuild affected support, repeater, and resource-ownership data.
6. Commit the replacement through the coordinator when it improves the chosen
   objective and remains globally compatible.
7. Record why a shorter candidate was rejected, remained unexplored, or could
   not be proven before the deadline.

An unchanged overall bounding box must not erase a reduction in route material
or delay. The acceptance fixture should cover a legal shorter replacement
inside an unchanged surrounding envelope. It should also cover a visually
shorter candidate that is correctly rejected for a documented physical reason.

## 8. R10: a shared redstone model before validation

The model should provide one versioned description of the supported block and
cell palette, with progressively more detailed queries:

| Responsibility | Required scope |
|---|---|
| Geometry | Occupancy, support, headroom, oriented cell geometry, legal step geometry, and port access. |
| Electrical connectivity and influence | Dust connections, relevant block roles, component-facing interactions, powered-block influence, and unwanted coupling. |
| Directed power | Source drive, decay, legal refresh sites, repeater input/output direction, branch sinks, and isolation requirements. |
| Timing and state | Explicitly supported delays, update/state assumptions, and behavior outside the model's proven scope. |
| Diagnostics | Legal, illegal, or unknown decisions with claims, assumptions, affected positions and reasons. |

Current implementations to reconcile include
[Technology.py](../../PhysicalDesign/Redstone/Technology.py),
[ResourceGraph.py](../../PhysicalDesign/Resources/ResourceGraph.py),
[Actions/Geometry.py](../../PhysicalDesign/Redstone/Rules/Geometry.py),
[Actions/Validation.py](../../PhysicalDesign/Redstone/Rules/Validation.py),
[PropagateRoutePower](../../PhysicalDesign/Redstone/Rules/Repeaters.py#L156), and
[BuildWireState](../../PhysicalDesign/Rendering/SchemWriter.py#L517).

The model must be cheap enough for search. Cached orientation masks and local
queries should avoid complete block-map expansion. Exact candidate checks
refine the same contract rather than introduce unrelated interpretations.
The selected technology must reach every consumer and participate in cache
and proof identity.

Conformance fixtures should cover supported cell rotations/mirrors, flat and
vertical dust arrangements, support/headroom boundaries, repeater direction,
signal-strength boundaries, fanout, nearby foreign nets, and relevant
block-powered interactions. Compare predictions with physical observations
from MCHPRS and Fabric, retaining disagreements as regressions.

The model's supported scope must remain explicit. An unknown decision can
trigger further refinement or an unresolved result. It cannot become a
behavioral pass. The existing required validation pipeline remains the final
acceptance gate.

## 9. Worker, cache, and mutation ownership

The coordinator owns accepted design state. Workers compute candidate layouts,
domains, local proofs, route trees, geometry, or repair alternatives against
immutable snapshots. This allows substantial parallel work while keeping
commitment short and deterministic.

Every result carries the input dependency fingerprint. If relevant accepted
state changes, the coordinator revalidates that result against the changed
dependencies or classifies it as stale. Completion order alone must not decide
which candidate wins a completed deterministic search.

Mutable run-local caches and native contexts belong to private services.
Persistent reuse remains available through immutable versioned entries, with
explicit invalidation and eviction. A consumer should not attach new cache
fields to a shared resource object.

The cache key must cover relevant topology, ordered port roles, orientation,
cell/model versions, policy/domain versions, and external reservations.
Cache hits should record which assumptions were reused. Process startup and
large task serialization must not erase the benefit of cached work.

CPU admission must count both Python processes and native threads. Memory
limits cover queued input payloads, speculative candidates, cached geometry,
proofs, and native contexts. Large speculative queues should not grow without
backpressure.

One absolute routing deadline covers planning, search, and routing cleanup.
Tasks need cancellation checkpoints and bounded teardown. The current
[per-batch proof pool](../../PhysicalDesign/Routing/Regions/Symbolic/SymbolicDomains.py#L1178)
and its context-manager lifetime are specific migration targets: a wait on
shutdown must not extend the intended deadline indefinitely.

Telemetry must cover ready, running, completed, cancelled, failed, and stale
work; coordinator compute/dispatch/merge/wait time; transfer/startup costs;
cache reuse; memory; and work discarded during retries. Keep sampled
observations distinct from exact phase counters.

## 10. Enforcement and migration

### 10.1 Proposed enforcement rules

1. Declare public API modules and allowed dependency directions for each owner.
   Cross-owner callers use those APIs; code inside one owner may use its
   implementation modules.
2. Extend import coverage beyond Compiler to all production Python roots.
   Record intentional exceptions by source, target, purpose, and removal or
   relocation condition.
3. Type-check new phase records and service protocols. Avoid `Any`,
   `Callable[..., Any]`, and arbitrary namespace lookup at new boundaries.
4. Give each mutable object one owner. Test that workers and downstream
   consumers cannot mutate accepted snapshots through shared references.
5. Pin schema semantics, units, versions, completeness, and proof scope.
   Field order and serialization compatibility alone are insufficient.
6. Keep algorithms circuit agnostic. Circuit names and benchmark identities
   cannot select special routing behavior.
7. Keep source, generated outputs, native build products, caches, and server
   runtime data under their existing ownership/ignore rules.

### 10.2 Ordered implementation slices

| Slice | Work | Exit evidence |
|---|---|---|
| 1. Record the policy | Adopt an API/dependency matrix and reconcile current-facing documentation with executable gates. | A new cross-owner private import is caught; existing deliberate exceptions are explicit. |
| 2. Shared foundations | Define cell/model and objective contracts; begin moving ownership while preserving behavior. | Placement/routing/rendering consumers use the same versioned model queries; score meanings are explicit. |
| 3. One narrow use case | Extract a preparation operation from the broad router entrypoint and migrate its callers. | Callers receive a defined result without the full shared routing state/resource object. |
| 4. State/session split | Separate immutable geometry and ownership snapshots from mutable services; replace one shared-state phase at a time. | Mutation, stale-result, incomplete-proof, and serialization tests pass. |
| 5. Persistent runtime | Schedule the resulting independent work units with shared CPU/memory/deadline control. | Completed deterministic results agree across worker counts; useful throughput and overhead are measured. |
| 6. Routing improvements | Introduce joint compact planning, broader reuse, revised scoring, and shortcut optimization through the established APIs. | Target correctness cases pass; layout quality and wall time are compared against equivalent accepted outputs. |

These slices may overlap where dependencies permit. Each change should retain
a clear owner and acceptance claim. Extracting interfaces and changing search
policy should have separately identifiable evidence.

The first implementation slice should make one boundary concrete: a consumer
uses a new preparation operation without importing its private implementation
or receiving the entire `RoutingResources`/`AuthoritativeRoutingState`.
Changing folder names or wrapping the old 50-argument interface is insufficient.

## 11. Acceptance and remaining design choices

Required correctness evidence includes HalfAdder's four input combinations
and CLA4's 512 combinations, preserving passing FullAdder/RCA4/RCA8 cases.
The complete seven-case diagnostic matrix must continue to record every case,
including DecimalToBinary4 and TFlipFlopLatch. Stateful examples need a declared
state/sequence protocol; combinational vector counts alone do not establish
their general behavior.

Candidate and static acceptance require complete required connectivity,
capacity-one ownership, support/headroom, electrical isolation, directed
power, rendered-orientation agreement, and correctly scoped proof outcomes.
Model conformance cases must include both accepted and rejected shortcuts.

Physical acceptance retains MCHPRS validation followed by the required
single-fixture Fabric canary. The current Fabric contract targets 1,000 TPS and
requires 40 consecutive observed unchanged ticks within a 200-tick bound.
Preparation is not completed validation progress. Missing infrastructure
remains an infrastructure failure. Final publication uses the observed
settled snapshot with matching fixture/design provenance.

Record source revision, dirty-tree provenance where applicable, native and
server build identity, policy/model versions, commands, output roots, hashes,
and per-case outcomes. Retain Summary.txt, RawDump.txt, manifests, and physical
design or typed-failure artifacts. An absent phase is `not-run`.

Performance comparison should use repeated runs with documented hardware,
worker counts, background load, and warm/cold cache conditions. Measure
compile wall time, useful CPU work, peak memory, route material, congestion,
footprint/height, repeaters, delay, and discarded search work. High CPU use alone
is not a success condition.

Design choices to settle during the first slices include:

- the exact supported scope of the fast redstone model's timing/state rules;
- objective priorities and how useful tradeoff candidates are retained;
- the first overloaded preparation operation to extract;
- versioning and compatibility for existing physical/result schemas;
- deterministic selection rules when wall deadlines interrupt a portfolio;
- persistent cache storage, ownership, and memory/eviction policy.

These are implementation decisions within the proposed architecture. They do
not change the recorded source findings or turn incomplete physical evidence
into a pass.

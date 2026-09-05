# Routing-aware placement and access design

**Status:** Proposed architecture; not implemented and not accepted.

**Priority order:** CLA4 routing completion, routing correctness, compactness,
then speed.

**Working policy name:** `physical-design-v17-routing-aware-placement-access`.
Experimental artifacts produced by the new path must report that exact policy
and their milestone/non-accepted status. V17 is the rewrite branch's development
and acceptance-run default; selecting it by default does not establish accepted
production behavior. Promotion to stable `main` still requires the gates in
this document and the [rewrite workflow](../../Pillars/RewriteWorkflow.md).

**Evidence relationship:** Correctness, typed failure, deterministic execution,
and final validation remain mandatory. The live
`Tools/Routing/RunRouterAcceptance.py`, the matrix below, and fresh output
artifacts control concrete v17 evidence. The existing
[negotiated route-tree router](NegotiatedRouteTreeRouter.md) remains the target
global and detailed router. This proposal replaces the placement-to-access
handoff and simplifies how that router receives physical work; it does not
weaken final validation.

**Current source ownership:** The behavior and evidence below predate the
2026-08-28 clean-break monolith split, but the v17 proposal itself is
unchanged. Current paths in this document follow the domain packages in
[`ProjectTreeDesignDoc.md`](../../Reference/ProjectTreeDesignDoc.md).

## Branch scope: core router rewrite, not CLA4 repair

This scope guidance is recovered from the architectural portion of legacy
commit `14646a9`, separately from its benchmark-archive implementation. Its
placement/access and coordinator scope belongs to `Joint-Physical-Design`,
not to R1's lazy-expansion requirement.

CLA4 is an acceptance pressure and diagnostic fixture, not a circuit-specific
implementation target. Its failures identify missing general placement,
access, ownership, or routing capabilities. A passing named circuit is not a
reason to restore the old cross-stage assumptions.

In-scope work establishes authoritative access domains and immutable witnesses;
explicit placement/interface/channel/claim contracts; stage-owned state and
services; deterministic orchestration; typed proof outcomes; bounded handoffs;
and physically verified topology-driven behavior.

Do not introduce circuit-name conditions, fixture-shaped exceptions, legacy
fallback, or deadline/worker/beam/retry inflation merely to advance a benchmark.
Local symptom repairs do not substitute for changing the defective contract.
Physical validation remains authoritative, and incomplete work never becomes
an unsatisfiability proof.

Develop and test general capabilities at explicit checkpoints under the
[rewrite workflow](../../Pillars/RewriteWorkflow.md). Dependent phases may be
developed before full acceptance, but compactness or speed cannot compensate
for invalid physical behavior. The RAPA milestones remain evidence gates,
not a queue of circuit-specific repairs.

## Decision

Replace the current fixed straight-pin-ray placement contract with a finite,
physically verified pin-access domain solved jointly with cluster placement,
interface slots, and coarse channel capacity.

The solver shall produce exactly one of three typed outcomes:

- `Feasible`: one immutable placement-and-access witness whose geometry and
  exact resource claims are complete;
- `Unsatisfiable`: a complete proof for the finite domain plus a placement
  no-good identifying the conflicting cells, pins, signals, and resources; or
- `Incomplete`: the work or absolute deadline ended before either conclusion.

One feasible witness is frozen and passed unchanged to the existing negotiated
router. Detailed routing may return a complete physical no-good to the
placement solver, but it may not silently regenerate pin access, change the
selected placement, reset the deadline, or reinterpret incomplete work as
unsatisfiable.

The global design is therefore:

```text
NAND IR
  -> verified physical access-template catalog
  -> access-legal cluster-template synthesis
  -> exact placement/access selection
  -> frozen physical placement contract
  -> negotiated global and detailed routing
  -> strengthened claim, connectivity, rendering, and truth-table validation
  -> staged publication and manifest-level finalization
```

## Status and evidence boundary

### Current evidence

This proposal does not carry a current acceptance verdict or a durable dated
failure report. Establish the state of a checkout from fresh artifacts under
`Output/`: the relevant `.RoutingFailure.json`, the matching acceptance
manifest, and the produced physical-design and validation artifacts. A passing
smaller circuit never establishes CLA4 acceptance.

### Confirmed code behavior

The [cell library](../../../PhysicalDesign/Cells/Library.py) declares
`PinAccessPattern`, but `CellMacro.PinAccessPatterns` currently constructs one
straight three-block pattern for each input or output pin. Production
placement does not consume that property as its authoritative option domain.

The current tree already contains a substantial pre-route access subsystem in
`PhysicalDesign/Placement/Access/`. `BuildPlacementAccessFabric` and the attachment
helpers in `Access/Fabric.py`, together with the standalone oracle in
`Access/Capacity.py`, construct immutable escape stubs, terminal domains,
fabric, and assignment representations named
`PlacementAccessEscapeStub`, `PlacementAccessTerminalDomain`,
`PlacementAccessFabric`, and `PlacementAccessAssignment` in
`PhysicalDesign/Contracts/Placement.py`. The standalone
`SolvePlacementAccessFabricCapacity`/`AttachPlacementAccessAssignment` path is
not the current production selector; it is exercised by focused fixtures.
Production `PhysicalDesign/Orchestration/` deliberately leaves stubs unselected and
`SolveRawTrackAssignmentPortfolioWithContext` selects the combined
track/access witness later. The proposal must extend that production selection
seam rather than accidentally reviving the unused standalone capacity solver.

Instead, fixed access is reconstructed independently in several places:

- `PhysicalDesign/Placement/Engine/Clustering.py::PcbGatesConflict` derives straight access rays
  during placement legality;
- `PhysicalDesign/Placement/Engine/MandatoryAccess.py::BuildMandatoryAccessClaims` reconstructs those
  rays and expands them into wire, support, required-air, and electrical
  claims;
- `PhysicalDesign/Routing/Pcb.py::CompactRoutedTrees` reconstructs terminal access
  again while compacting routed trees; and
- later component-interface code constructs a richer physical port-option
  domain after a placement has already survived the fixed-access screen.

The proposed design therefore extends and unifies the existing placement-access
fabric and component-interface seams. It is not a greenfield second access
solver.
The missing behavior is to expose verified cell-local alternatives while
placement variables remain live and to preserve one selected identity through
every later representation.

`PhysicalDesign/Placement/Engine/Search.py::OptimizeJointClusterPlacement` searches
cluster slots and eight rigid transforms, then
`PhysicalDesign/Placement/Engine/Commit/Commit.py::PlacePcbGraph` materializes and exact-
screens retained states.
`PhysicalDesign/Orchestration/PlacementAttempts.py::_TryPlacement` rejects a
complete candidate on mandatory-access conflicts. If no candidate survives,
`PhysicalDesign/Orchestration/Runner.py::_PlaceAndRoutePcbWithPolicy` creates the
observed placement failure before
`PhysicalDesign/Routing/Global/Orchestration/Flow.py::RouteAuthoritativeResources` is
called.

### Hypotheses to test

The current two-resource conflicts are strong evidence that selectable local
access may preserve a compact core that fixed rays reject. They are not proof
that a specific side, vertical, staggered, pin-swap, or layer pattern is legal.
Every pattern family remains a hypothesis until it has exact blocks, exact
claims, a powered witness, transform tests, and rendered physical validation.

It is also possible that the existing CLA4 cluster geometry has no solution
even with a complete useful access catalog. A complete solver result must then
drive a local split, move, or capacity increase. It must not be reported as a
catalog-independent proof that CLA4 is physically impossible.

### Claims this document does not make

- It does not claim a routed or accepted CLA4.
- It does not claim that CP-SAT, the existing Rust lease solver, or a new Rust
  solver will meet the runtime ceiling before measurement.
- It does not claim that a larger access catalog monotonically improves
  runtime; it improves representational completeness and may increase raw
  branching without propagation and symmetry breaking.
- It does not claim projected footprint or speed improvements as achieved
  results.
- It does not authorize relaxed Redstone rules, fallback publication, circuit-
  keyed behavior, or converting a deadline into an UNSAT proof.

## Priority order and success definition

The priorities are lexicographic, not a weighted average.

1. **CLA4 completion.** Produce a native physical design, exact 512-row truth
   table, and litematic using the authoritative strategy.
2. **Routing correctness.** Require zero final resource conflicts, zero
   unresolved claims, exact physical connectivity, repeater legality,
   authoritative simulation, no fallback, and deterministic repeated output.
3. **Compactness.** Minimize the accepted design's physical envelope and block
   count without invalidating priorities one or two.
4. **Speed.** Reduce total and stage runtime without changing the accepted
   physical result or weakening proof status.

A smaller incomplete or electrically invalid candidate never outranks a larger
complete design. A fast typed failure is useful diagnostic evidence but is not
routing success.

## Problem statement

### Fixed access is a placement decision disguised as a constant

A pin's first few blocks determine all of the following:

- whether neighboring cells are electrically isolated;
- which support cells become occupied and which required-air cells must remain
  empty;
- which face and layer can receive the signal;
- whether a powered path can legally leave or enter the cell;
- whether an internal cluster route can merge safely; and
- how much detailed-route capacity remains around the cluster.

Treating that geometry as one immutable straight ray removes those choices
before placement knows which neighboring pins compete for them. Later routing
cannot repair the conflict because the mandatory claims are already fixed.

### Legality is discovered after expensive whole-state work

The joint beam scores cluster arrangements using topology and approximate
interface pressure. Exact materialization, repeated `PcbGatesConflict` checks,
and mandatory resource-claim construction occur after a complete retained
state exists. The current CLA4 primary attempt therefore built a profile of
8,814 claims and rejected the whole placement over two resources.

The required change is not merely to call the existing exact screen earlier.
The screen must expose alternatives and propagate conflicts while placement
variables are still live.

### The same physical fact has multiple representations

Straight access is reconstructed in placement, routing, and compaction. Port
options, aperture factors, selected paths, claims, and fingerprints are then
represented again in the component interface. Equivalent geometry may acquire
different identities, while a later stage may accidentally regenerate a
different domain.

One immutable witness must own the physical access decision. All later stages
consume it or reject it; they do not reinterpret it.

### Gate count is not the right scaling variable

An industry-style router remains tractable by making most work local and by
letting expensive search scale with congested interface width, active spatial
region, and unresolved cut size rather than the Cartesian product of every
gate and every full route tree.

For this compiler that means:

- exact-solving small cell/cluster/interface domains;
- canonical reuse for structurally identical verified macros;
- a coarse placement/channel master rather than full detailed paths;
- shared boundary-state computation instead of rerunning a complete per-net
  tree solver for every seam choice; and
- negotiated, lazy detailed routing only inside active regions.

## Goals

- Make pin access a finite, exact, selectable physical domain.
- Reject or repair access-infeasible geometry before portal and detailed-route
  generation.
- Preserve one selected placement/access identity through routing,
  materialization, validation, telemetry, and publication.
- Return complete local no-goods that can split, move, rotate, or widen only
  implicated physical structures.
- Reuse complete canonical macro work across equivalent NAND topology without
  using circuit names or generated signal names.
- Keep the negotiated router authoritative and strengthen final validation so
  it independently checks the new placement/access contract.
- Obtain a first legal design before optimizing compactness or runtime.
- Create timestamped, hashed evidence so each architectural checkpoint can be
  compared without relying on mutable `/tmp` state.

## Non-goals

- Full-board detailed routing in SAT, ILP, or CP-SAT.
- Unbounded enumeration of all pin patterns, portals, lanes, or route trees.
- A visual-only compactness score that can accept an invalid block map.
- Whole-board relocation when a complete local core identifies a smaller cut.
- A large source-file split performed before behavioral seams exist.
- Removing the established parser, NAND synthesis, simulator, litematic writer,
  failure schema, or staged publication path.
- Treating historical CLA4, focused unit tests, or synthetic PCB DRC as native
  acceptance.

## Non-negotiable invariants

### Physical correctness

1. No pair of cross-signal claims may violate the canonical
   `FindClaimConflicts` compatibility relation. Resources declared capacity-one
   by their specific model remain exclusive; support/support, air/air, or
   electrical/electrical overlap is not forbidden merely by sharing a
   coordinate when the compatibility relation permits it.
2. Same-signal sharing is allowed only where the physical connectivity and
   powered-tree rules allow it.
3. A selected access template contains real materializable blocks and exact
   claims; it is not an abstract promise that later routing must realize.
4. Final validation recomputes physical ownership and connectivity from the
   routed/materialized result rather than trusting the solver's success flag.
5. Exact behavioral validation belongs to the Fabric server.

### Solver meaning

1. `Unsatisfiable` is emitted only after a finite declared domain is completely
   exhausted or an independently checkable complete proof is returned.
2. Deadline, expansion, memory, worker, cancellation, or catalog limits return
   `Incomplete`.
3. A proof is scoped by complete input fingerprints. It cannot be reused after
   geometry, access templates, technology, claims, or domain bounds change.
4. Solver order is deterministic for a fixed problem fingerprint and policy.

### Pipeline behavior

1. One `RoutingDeadline` begins before placement work and is never reset.
2. The selected witness is immutable and identity-stable.
3. Current publication remains staged with exception cleanup and occurs only
   after final validation. A future immutable-incumbent compactor requires a
   separate staging root plus manifest/directory-level commit; sequential file
   replacement is not described as crash-atomic.
4. No decision depends on circuit names, generated NAND suffixes, or benchmark
   identity.
5. The accepted smaller-design gates remain protected while CLA4 is developed.

## Terminology

| Term | Meaning |
| --- | --- |
| Access template | Cell-local, materializable path from a logical pin through its mandatory first leg, with exact claims and power evidence |
| Access option | One access template transformed onto one placed cell/pin/layer context |
| Cluster template | A complete local NAND placement with chosen internal access and optionally sealed internal routes |
| Placement master | Finite solver over cluster template, slot, transform, channel, and interface decisions |
| Access subproblem | Exact compatibility solve for the access options induced by a partial or complete placement |
| No-good | A proof-backed set of selection literals that may not recur under the same domain fingerprint |
| Frozen witness | Immutable placement, access, claim, and identity contract handed to routing |
| Complete domain | Every permitted option under the recorded bounds is known and considered |
| Incomplete domain | Generation or solving stopped before completeness was established |
| Sealed macro | Canonical, validated, reusable component template with explicit boundary ports and exact local claims |

## Proposed architecture

### Stage 1: authoritative access-template catalog

`CellMacro.PinAccessPatterns` owns cell-local pattern seeds and logical pin
roles. `PhysicalDesign/Redstone/Technology.py` remains the single owner of shared
physical rules such as access length, track pitch, routing layers, and repeater
limits. Existing `PhysicalDesign/Placement/Access/Fabric.py` owns fabric and
terminal-domain construction, `PhysicalDesign/Placement/Access/Geometry.py` owns its
derived perimeter shells, and `PhysicalDesign/Geometry/Placement.py` owns pure placed
cell transforms. The production raw track-assignment portfolio
owns final combined selection until the proposed master explicitly replaces
that responsibility. `PhysicalDesign/Resources/ResourceGraph.py` remains the exact
claim authority. No second access-length constant or parallel claim model is
introduced.

An approved access template shall include or deterministically derive the
complete first-leg geometry, not only a connection position and direction.

Candidate pattern families may include straight, legal lateral jog, legal
vertical transition, staggered escape, layer choice, and commutative NAND input
swap. A family is excluded until it can be materialized and verified. The
catalog is finite and versioned.

Every consumer calls one transform/physical-ownership compiler that returns
both resource claims and repeater reservations. Direct arithmetic of the form
`Pin + Direction * Offset` outside that compiler becomes forbidden on the new
strategy.

### Stage 2: access-legal cluster-template synthesis

For a bounded cluster, jointly select:

- local NAND X/Z position;
- rotation and mirror;
- logical-to-physical NAND input mapping where commutativity permits it;
- one access option for each exposed pin;
- internal same-signal merge choices;
- boundary face, slot, layer, and first track; and
- optional sealed internal routes.

Hard constraints reject cell overlap, electrical adjacency, incompatible
claims, unsupported blocks, blocked headroom, invalid power state, interface
capacity overflow, and incomplete terminal coverage.

The compiler retains a bounded Pareto frontier plus explicit completeness
certificates for every catalog/template dimension. A discarded template may
support an UNSAT conclusion only when a proof shows that a retained state
subsumes it for every boundary and claim compatibility relevant to future
work. Pruning solely by envelope, block estimate, HPWL, or a retention count
makes the frontier incomplete.

The retained frontier is ordered by physical feasibility, bounding envelope,
block estimate, maximum layer, access congestion, HPWL, and a stable
fingerprint. Incomplete attrition remains typed and cannot be treated as an
empty complete domain.

### Stage 3: exact placement/access master

The board-level master selects one template and one legal transform/slot per
cluster plus coarse row, deck, corridor, and channel capacities. It does not
contain detailed per-block multi-terminal routes.

The access subproblem receives the induced finite access domains and exact
claims. Domain propagation removes incompatible options early. A complete
conflict returns a no-good over the smallest proven selection set. An
incomplete result returns progress telemetry but cannot remove future states as
if they were impossible.

The architectural interface is solver-neutral. A CP-SAT implementation may be
used as an experimental oracle for the fixed-placement and small-cluster
milestones. The production implementation may extend the native finite CSP,
but it must add domain propagation, resource-occurrence indexing, deterministic
branch ordering, one absolute deadline, and the typed result contract. Solver
choice must not leak into artifact semantics.

### Stage 4: frozen physical placement contract

A feasible solve publishes one `FrozenPhysicalPlacementContract`. It binds:

- the NAND IR and technology fingerprints;
- every cell and cluster transform;
- every logical pin to one exact access option;
- complete selected access blocks, claims, and repeater reservations;
- active boundary faces, slots, layers, roots, first legs, and keepouts;
- coarse channel capacities and legal placement envelope;
- complete domain and solver provenance; and
- a canonical contract fingerprint.

No downstream stage reconstructs or substitutes those decisions. A mismatch
is a typed identity error.

The code dependency remains one-way. Placement constructs the contract using a
neutral immutable schema in `PhysicalDesign/Contracts/Placement.py`,
consistent with the existing Placement-to-Routing contract imports.
Placement-owned validation
recomputes transforms, pin mappings, and blocks through
`PhysicalDesign/Geometry/Placement.py`. Routing independently validates the contract's
claims, reservations, access coverage, leases, and fingerprints without
importing `PhysicalDesign/Placement`. `Compilation/Pipeline.py`, which already sits above
both stages, composes those two validators. In particular,
`PhysicalDesign/Routing/Global/` must not import `PhysicalDesign/Placement/Access/`
or duplicate `BuildPlacedGate` to recover placement state.

### Stage 5: existing negotiated routing

`RouteAuthoritativeResources` remains the end-to-end routing owner beyond the
frozen access contract: it prepares physical portals/factors and global work,
invokes routing, materializes selected routes/repeaters, and performs final
route claim/connectivity checks. Within that flow, `PlanNegotiatedRouteTrees`
remains the detailed per-net negotiated tree/repair substage. It uses sparse
resource regions, present/history congestion costs, branch-preserving repair,
and repeater-aware state. Placement feedback remains owned by the surrounding
`PhysicalDesign/Orchestration/` controller, not by that subroutine.

`RouteAuthoritativeResources` receives selected access roots and reserved
claims as immutable occupancy. The new strategy forbids it from regenerating a
different access domain after validating the handoff.

Detailed routing may return a complete physical cut over selected macro,
access, channel, or layer choices. The placement master may then select a
different complete contract under the same deadline. The router may not
regenerate access options internally.

### Stage 6: independent validation and publication

The current route path performs claim conflict detection and physical
connectivity checks before returning `RoutedDesign`. Later simulation may trust
`ZeroResourceConflicts=True` and skip repeated flat-conflict, template-
isolation, or physical-route checks. Therefore a fully independent recomputation
from rendered/materialized output is a proposed strengthened v17 gate, not a
description of all current behavior.

The v17 validation chain shall perform:

1. exact resource-claim conflict detection;
2. physical graph connectivity and electrical isolation;
3. repeater and signal-strength legality;
4. exact litematic block-map construction with neutral dynamic state;
5. authoritative Fabric-server placement, ticking, and readback;
6. server-produced truth-table comparison;
7. unresolved-claim and provenance checks;
8. staged publication of litematic, server results, and physical design with
   exception cleanup; and
9. manifest/directory-level finalization before an incumbent-preserving
   compactor may replace an accepted result.

The solver's witness is evidence supplied to validation, not a replacement for
validation.

Current `PhysicalDesign/Redstone/Rules/Validation.py::ValidatePhysicalRoutes` proves
that each signal reaches all of its own required targets. It does not explicitly
reject a route that also reaches another signal's logical target. That
foreign-target reachability check is a new v17 route-level validator applied
after materialization, separate from claim-conflict and electrical-isolation
checks; it must not be described as current behavior.

## Proposed typed contracts

The following types are proposed documentation contracts. They are not current
source and may be refined only if all invariants remain explicit.

```python
from dataclasses import dataclass
from enum import Enum


class PlacementAccessSolveStatus(Enum):
    """Meaning of one bounded exact placement/access solve."""

    Feasible = "feasible"
    Unsatisfiable = "unsatisfiable"
    Incomplete = "incomplete"


@dataclass(frozen=True)
class PlacedPinAccessOptionDomain:
    """Typed option domain that cannot hide bounded/incomplete generation."""

    Options: tuple["PlacedPinAccessOption", ...]
    Complete: bool
    IncompleteReason: str | None
    DomainFingerprint: str
    Diagnostics: dict[str, object]


@dataclass(frozen=True)
class PhysicalPinAccessTemplate:
    """One cell-local, materializable and independently verified pin escape."""

    TemplateId: str
    CatalogVersion: str
    CellKind: str
    PinId: str
    LocalBlocks: tuple[object, ...]
    ConnectionNode: tuple[int, int, int]
    RootNode: tuple[int, int, int]
    FirstLegNodes: tuple[tuple[int, int, int], ...]
    AllowedLayerOffsets: tuple[int, ...]
    LocalClaims: object
    LocalRepeaterReservations: tuple[object, ...]
    PoweredWitness: object
    ProofFingerprint: str


@dataclass(frozen=True)
class PlacedPinAccessOption:
    """One exact transformed access template bound to a placed logical pin."""

    OptionId: str
    GateName: str
    LogicalPinId: str
    Signal: str
    TemplateId: str
    TransformFingerprint: str
    Blocks: tuple[object, ...]
    Claims: object
    RepeaterReservations: tuple[object, ...]
    Face: str
    Layer: int
    Slot: tuple[int, int, int]
    RootNode: tuple[int, int, int]
    FirstTrackNode: tuple[int, int, int]
    AnonymousAccessGeometryFingerprint: str
    PlacedAccessBindingFingerprint: str


@dataclass(frozen=True)
class AccessLegalClusterTemplate:
    """Complete local cell geometry and access ownership for one cluster."""

    StructuralFingerprint: str
    TechnologyFingerprint: str
    MemberTransforms: tuple[object, ...]
    SelectedAccessOptions: tuple[PlacedPinAccessOption, ...]
    LocalRoutes: tuple[object, ...]
    Claims: object
    RepeaterReservations: tuple[object, ...]
    Bounds: object
    BoundaryPorts: tuple[object, ...]
    ProofFingerprint: str


@dataclass(frozen=True)
class PlacementAccessProblem:
    """Finite identity-closed master and access domains for one solve."""

    ProblemFingerprint: str
    PlacementEnvelope: object
    ClusterTemplateDomains: tuple[object, ...]
    SlotDomains: tuple[object, ...]
    AccessOptionDomains: tuple[object, ...]
    ChannelDomains: tuple[object, ...]
    ExistingNoGoods: tuple[object, ...]
    DomainCompletenessCertificates: tuple[object, ...]


@dataclass(frozen=True)
class PlacementAccessConflictCore:
    """Complete proof-backed selection clause for one impossible subdomain."""

    ProblemFingerprint: str
    SelectionLiterals: tuple[object, ...]
    GateNames: tuple[str, ...]
    PinIds: tuple[str, ...]
    Signals: tuple[str, ...]
    Resources: tuple[object, ...]
    ProofKind: str
    Minimal: bool
    ProofFingerprint: str


@dataclass(frozen=True)
class FrozenPhysicalPlacementContract:
    """Immutable selected placement and access witness consumed by routing."""

    ProblemFingerprint: str
    ContractFingerprint: str
    CellTransforms: tuple[object, ...]
    ClusterInstances: tuple[object, ...]
    AccessOptions: tuple[PlacedPinAccessOption, ...]
    AccessClaims: object
    AccessRepeaterReservations: tuple[object, ...]
    BoundaryLeases: tuple[object, ...]
    ChannelReservations: tuple[object, ...]
    PlacementEnvelope: object
    SolverProofFingerprint: str


@dataclass(frozen=True)
class PlacementAccessSolveResult:
    """Typed result that never promotes bounded exhaustion to UNSAT."""

    Status: PlacementAccessSolveStatus
    Witness: FrozenPhysicalPlacementContract | None
    ConflictCore: PlacementAccessConflictCore | None
    Diagnostics: dict[str, object]
```

### Type invariants

- `PhysicalPinAccessTemplate.LocalClaims` are relative to the cell origin and
  cover wire, support, required-air, and electrical ownership. Current
  `RoutingResourceClaims` has exactly those four kinds; repeater sites/facings
  remain explicit `LocalRepeaterReservations` rather than being silently
  squeezed into that claim type.
- `PlacedPinAccessOption.Claims` and `RepeaterReservations` are pure transforms
  of the corresponding template records.
- An incomplete `PlacedPinAccessOptionDomain` cannot be consumed as a complete
  empty or reduced domain and cannot support `Unsatisfiable`.
- `AnonymousAccessGeometryFingerprint` includes catalog version, template proof,
  relative cell transform, claims, repeater reservations, and technology, but
  excludes gate names, signals, and global translation.
- `PlacedAccessBindingFingerprint` includes the anonymous identity plus exact
  gate, logical pin, signal, instance transform, layer, and slot binding.
- `AccessLegalClusterTemplate` is complete only if every local/exposed terminal
  has a selected or explicitly deferred boundary contract.
- Missing or incomplete catalog, template, slot, access, channel, or no-good
  completeness certificates forbid a global `Unsatisfiable` result.
- `FrozenPhysicalPlacementContract` is published only for `Feasible` and is
  immutable after publication.
- `PlacementAccessConflictCore` is reusable only for an exact matching problem
  and domain fingerprint. Its reusable proof fingerprint uses canonical
  selection literals; `GateNames`, `Signals`, and displayed resources are
  diagnostic projections unless explicitly part of those literals.

## Access-template generation and proof

### Catalog construction

The catalog compiler starts from a `CellMacro`, routing technology, and a
finite set of approved pattern generators. It shall:

1. construct explicit local block geometry;
2. identify the logical connection, root, and first-track nodes;
3. derive wire, support, required-air, and electrical claims through the
   canonical resource graph and derive explicit repeater reservations with
   position/facing through the established repeater reservation model;
4. reject self-conflicting or unsupported geometry;
5. prove connectivity from the cell pin through the first track;
6. prove the powered/repeater contract for every allowed layer offset;
7. render the isolated cell plus access blocks;
8. simulate the cell's complete logical truth table; and
9. publish an immutable template and proof fingerprint.

Pattern generators may reject a cell, pin, transform, or layer. Rejection is
normal catalog evidence. A generator must never publish an abstract option
whose materialization is deferred to routing.

### Transform closure

For every approved template, tests shall cover all cell rotations and allowed
mirrors. Transformation applies to blocks, connection/root/first-leg nodes,
directions, support, air, electrical exclusions, repeater orientation, and
claims as one operation.

The following metamorphic identities are required:

- rotating four times returns byte-equal canonical geometry and claims;
- mirroring twice returns byte-equal canonical geometry and claims;
- transform then translate equals translate of the transformed relative
  geometry;
- transformed claim compilation equals transformation of compiled relative
  claims; and
- signal renaming does not change geometry or anonymous physical fingerprint.

### NAND input swapping

NAND input pins are logically commutative, but a swap is not a silent signal
rename. The selected template records the logical-to-physical input mapping.
The mapping participates in the placement/access contract fingerprint and is
validated against the NAND IR before publication. It must also update or derive
the exact `PlacedGate.Inputs` to `PlacedGate.InputPins` association consumed by
routing, simulation, and writing. The contract mapping and placed-gate binding
are independently compared; recording a side-table mapping alone is
insufficient.

### Catalog completeness

Catalog completeness is always relative to a version and declared generator
set. A complete solve may prove that no member of `CatalogVersion=X` works; it
does not prove that no physically imaginable access pattern exists. The
failure artifact must distinguish:

- `AccessCatalogUnsatisfiable`: complete for the declared catalog;
- `AccessCatalogIncomplete`: template generation stopped early; and
- `AccessPatternInvalid`: a specific generated template failed proof.

## Joint placement/access solve

### Variables

The finite master may contain:

| Variable | Domain |
| --- | --- |
| `ClusterTemplate[Cluster]` | verified local template IDs |
| `ClusterSlot[Cluster]` | bounded grid/row/deck slots |
| `ClusterTransform[Cluster]` | legal rotations and mirrors |
| `PinMapping[Gate]` | approved logical-to-physical input mappings |
| `AccessOption[Terminal]` | transformed verified access options |
| `BoundaryLease[Signal]` | face, slot, layer, root, and first-track tuples |
| `ChannelWidth[Cut]` | finite capacity-sized track counts |
| `DeckHeight[Region]` | finite physical routing layer choices |

Detailed path nodes outside the selected access first leg are intentionally not
master variables.

### Hard constraints

The solve shall enforce:

1. exactly one template, slot, and transform per cluster;
2. cell and template bounds inside the placement envelope;
3. exact cell/block non-overlap and electrical isolation;
4. one complete access option per required terminal;
5. exact capacity-one claim compatibility across unrelated signals;
6. same-signal sharing only through a certified merge/connectivity rule;
7. logical pin mapping consistency;
8. support, headroom, repeater, layer, and keepout legality;
9. active boundary face and slot consistency;
10. source root, first leg, and target access closure for every selected signal;
11. coarse cut/channel demand no greater than selected realizable capacity;
12. all complete no-goods from prior exact subproblems; and
13. the same technology and resource-model fingerprint across every option.

Approximate HPWL, congestion, and footprint estimates are objectives, not
substitutes for these constraints.

### Propagation

Before branching, build inverted occurrence indices from every physical
resource to the options that claim it. Selecting one option removes
incompatible options for other signals. Empty domains immediately return a
local core. Pairwise arc consistency is applied to access and boundary lease
domains; higher-order capacity constraints are checked from incremental claim
occupancy and cut counters.

The solver should choose the next variable by this deterministic key:

```text
(smallest remaining domain,
 highest exact conflict degree,
 highest boundary demand,
 stable variable identity)
```

Values are ordered by the lexicographic objective lower bound and stable option
fingerprint. Parallel workers may evaluate independent root branches over an
immutable snapshot, but result reduction must preserve deterministic branch
order and one shared deadline.

### Symmetry breaking

At minimum:

- anchor the first canonical cluster to the first equivalent slot/transform;
- order structurally equivalent cluster instances by canonical slot;
- collapse access options with identical anonymous geometry and claims;
- normalize global translation when the envelope permits it;
- normalize equivalent input-pin mappings; and
- retain one canonical representative of mirrored/rotated templates whose
  transformed interface contracts are identical.

Symmetry breaking must preserve at least one representative of every physical
solution and must be covered by differential tests against an unbroken small
oracle.

### Feasible, unsatisfiable, and incomplete

`Feasible` requires every variable assigned, every selected claim present, and
an independently recomputed zero-conflict result for the frozen contract.

`Unsatisfiable` requires:

- complete certificates for every relevant catalog, cluster-template, slot,
  access-option, channel, and no-good domain;
- complete exhaustion or a valid complete proof;
- a core scoped to the exact problem/domain fingerprint; and
- no expired deadline or aborted worker.

`Incomplete` includes deadline expiry, explicit work cap, cancellation,
missing catalog members, native error without a complete proof, and interrupted
parallel work. It records the explored state count, remaining domain sizes,
best lower bound, and latest complete cores, but it does not publish a no-good
for unfinished work.

### Deadline propagation

The original `RoutingDeadline` is passed through catalog selection, cluster
template synthesis, master solving, access subproblems, negotiated routing,
validation, and publication reserve. Native APIs receive the same absolute
expiration. No phase converts remaining duration into a fresh independent
budget.

### Lexicographic objective

Feasibility and correctness are hard constraints. Among complete feasible
solutions, order by:

1. smallest permitted placement envelope;
2. smallest maximum X/Z dimension;
3. smallest XZ footprint;
4. smallest estimated non-air block count;
5. lowest maximum routing layer;
6. smallest peak coarse cut overflow lower bound;
7. lowest HPWL and first-leg length;
8. fewest access bends/vertical transitions; and
9. stable contract fingerprint.

Search scheduling is completion-first. Before an incumbent exists, a
deterministic time-sliced or immutable parallel portfolio must start at least
one conservative feasibility envelope alongside smaller candidates; proving a
tight envelope impossible may not consume the shared deadline before an easier
larger domain is attempted. Once a complete design is validated, it becomes an
immutable incumbent. Remaining time may search smaller envelopes in ascending
order, and may replace the incumbent only with a fully validated better result.

## Cluster split, move, and widening feedback

A complete local conflict core is translated into the smallest applicable
physical response:

| Complete core | Preferred response |
| --- | --- |
| Two pin options collide but alternatives remain | select different access options |
| One cell transform eliminates the core | rotate, mirror, or pin-swap locally |
| Cluster has no access-legal template | split along the proven signal/cell cut |
| Boundary cut demand exceeds every selected channel | increase that channel or move one implicated cluster |
| Layer domain is complete and saturated | add one physically realizable layer if the policy permits it |
| Equivalent geometry repeats the same core | reject before preparation using the complete no-good |
| Detailed routing returns a complete congestion cut | alter only selected macro/access/channel literals in that cut |

Connectivity-only clustering remains a generator, not an authority. A proposed
cluster merge is retained only if at least one complete access-legal local
template exists within its declared bounds. If proof is incomplete, the merge
may remain a candidate but cannot be classified as impossible.

Standard rows and capacity-sized channels are a recovery geometry, not a
permanent whitespace requirement. The first accepted CLA4 may use conservative
channel capacity. Staged incumbent-preserving compaction later attempts channel
removal, row tightening, layer lowering, and local rerouting while preserving
the accepted incumbent.

## Canonical sealed component macros

The existing
`PhysicalDesign/Contracts/Component.py::RoutedComponentTemplate` and
`PhysicalDesign/Routing/Regions/Pipeline.py::CompileClosedComponent` already have
a normalized reusable cache seam.
`PhysicalDesign/Routing/Regions/Cache.py::BuildCompletedComponentTemplateCacheFingerprint`
normalizes origin, signal roles, ports, claims, and technology;
`_InstantiateCachedTemplate` translates coordinates and signal bindings and
then revalidates physical claims. The proposal extends that existing cache with
access-catalog, selected-access, repeater-reservation, completeness, and proof
identities. It must not add a competing cache layer above it. The weaker
placement-local `ClusterLocalRouteTemplate` likewise must not become a
competing source of physical truth.

A canonical macro key includes:

- name-independent NAND DAG structure;
- ordered logical boundary-port roles;
- cell/access catalog and technology versions;
- relative member transforms and local claims;
- local repeater reservations;
- interface contract and powered-proof fingerprints; and
- compiler version for the macro proof.

A sealed macro contains exact internal routes, selected local access, boundary
ports, keepouts, claims, repeater reservations, block geometry,
truth/equivalence proof, and permitted instance transforms. Instantiation is a
pure transform and signal binding, followed by exact handoff validation.

Only complete macros are cached. Existing eligibility restrictions remain in
force. A changed technology, access catalog, selected local access, repeater
reservation, local geometry, port ordering, claim model, completeness proof,
or proof version causes a cache miss. Exterior seams, global channels, sibling
occupancy, negotiated routing, and final validation remain fresh per placement.

`CompileClosedComponent` currently accepts a relative duration. Its macro
adapter must derive any native duration from the one original absolute
`RoutingDeadline`, check that deadline before and after the call, and preserve
typed incomplete behavior. It may not create a fresh independent lease of
time.

## Shared boundary dynamic program

The component interface historically materializes or proves per-signal work
under many boundary/seam choices and then assembles a CSP. Repeating complete
terminal/frontier work for each boundary choice scales poorly.

The replacement dynamic program carries boundary choice in its state. One
state minimally records:

```text
(processed local region,
 terminal coverage,
 same-signal connectivity partition,
 powered/repeater frontier,
 selected face/seam/layer leases,
 exact frontier resource claims,
 accumulated objective lower bound)
```

Equivalent states are merged only when future feasibility is identical under
the canonical frontier signature. Dominance requires no worse objective and a
claim/capacity state that is a true subset or otherwise proven to subsume the
discarded state.

The DP returns the same typed `Feasible`, `Unsatisfiable`, or `Incomplete`
contract. The existing component solver remains a small-case oracle until
differential tests show identical feasible assignments and complete cores.

This DP is phase-two scale work. The first fixed-placement access experiment
must not wait for the full shared-boundary implementation.

## Current and proposed code map

| Current owner | Current responsibility | Proposed change |
| --- | --- | --- |
| [`PhysicalDesign/Cells/Library.py`](../../../PhysicalDesign/Cells/Library.py) | `CellMacro`, one straight cell-local `PinAccessPattern` seed per pin | own logical pin roles and finite cell-local pattern seeds without duplicating technology rules |
| [`PhysicalDesign/Redstone/Technology.py`](../../../PhysicalDesign/Redstone/Technology.py) | shared access length, track, layer, and repeater rules | remain the only physical technology authority consumed by access generation |
| [`PhysicalDesign/Geometry/Placement.py`](../../../PhysicalDesign/Geometry/Placement.py) | transforms cell pins/directions into placed gates | provide pure cell transforms used by `Placement/Access/`; do not compile claims here |
| [`PhysicalDesign/Placement/Access/`](../../../PhysicalDesign/Placement/Access/) | fabric/escape construction and attachment in `Fabric.py`; standalone capacity selection in `Capacity.py` is not the production selector | extend authoritative option/domain construction while integrating final selection with the production raw track-assignment portfolio, then preserve one frozen binding |
| [`PhysicalDesign/Placement/Engine/`](../../../PhysicalDesign/Placement/Engine/) | clustering, joint search, fixed-ray legality, mandatory claims, exact screen, local route templates, and final commit | generate placement domains, consume canonical access claims, and retire duplicate fixed-ray reconstruction on the new strategy |
| New `PhysicalDesign/Placement/Engine/PlacementAccess.py` | not present | master construction, solve dispatch, and core translation; typed neutral records belong in `Routing/Contracts/Placement.py` |
| New `PhysicalDesign/Placement/Engine/ClusterTemplates.py` | not present | access-legal local template synthesis and Pareto retention |
| [`PhysicalDesign/Orchestration/`](../../../PhysicalDesign/Orchestration/) | typed placement/routing lifecycle, attempts, retries, repair epochs, and narrow runner | add explicit v17 phases without reintroducing a monolithic controller; retire superseded control-plane branches only after acceptance |
| [`PhysicalDesign/Contracts/`](../../../PhysicalDesign/Contracts/) | placement-access fabric/assignment, physical port factors/CSP state, and concrete routed component templates | add the neutral immutable handoff schema; Placement constructs it and Routing consumes it without a Routing-to-Placement import |
| [`PhysicalDesign/Routing/Regions/`](../../../PhysicalDesign/Routing/Regions/) | exact component interface CSP, closed-component compilation, normalized template fingerprint, cache, instantiation, and revalidation | expose a narrow identity-stable access/lease subproblem or proof-core projection and extend the existing cache identity with selected-access and proof records; do not add a parallel macro cache |
| [`PhysicalDesign/Routing/Global/`](../../../PhysicalDesign/Routing/Global/) | physical port factors, negotiated routing, materialization, and final route checks | accept the frozen contract, forbid access regeneration, and keep negotiated routing/final DRC |
| [`PhysicalDesign/Resources/ResourceGraph.py`](../../../PhysicalDesign/Resources/ResourceGraph.py) | canonical routing graph and claim construction | remain the single exact claim authority for access and routes |
| [`PhysicalDesign/Routing/Pcb.py`](../../../PhysicalDesign/Routing/Pcb.py) | route compaction and physical route orchestration | consume frozen access instead of reconstructing straight terminal rays |
| New `PhysicalDesign/Placement/Engine/HandoffValidation.py` | not present | recompute placed transforms, pin mappings, blocks, and placement-side fingerprint using existing placement geometry |
| New `PhysicalDesign/Constraints/HandoffValidation.py` | not present | validate claims, repeater reservations, access coverage, leases, and routing-side fingerprint without importing `PhysicalDesign/Placement` |
| [`PhysicalDesign/Redstone/Rules/Validation.py`](../../../PhysicalDesign/Redstone/Rules/Validation.py) | physical connectivity graphs and route validation | retain route connectivity ownership and add only route-level strengthened checks |
| [`Validation/Fabric/`](../../../Validation/Fabric/) | authoritative server-validation contract | own server lifecycle, ticking, readback, and result diagnostics without an in-process redstone model |
| [`Compilation/Pipeline.py`](../../../Compilation/Pipeline.py) | stage orchestration, final validation, failure evidence, staged multi-file replacement with exception cleanup | compose placement-side and routing-side handoff validation, serialize v17 contract/proof telemetry, and add manifest/directory finalization before immutable-incumbent replacement |
| New `Kernels/Routing/Src/PlacementAccess/` domain | not present | optional production finite solver split into state/domain/search/API modules, with propagation and a typed bounded result |

New modules are justified because they create stage boundaries currently
embedded in multi-thousand-line functions. They remain inside the existing
Placement and Routing packages as required by repository structure.

## Function and type documentation

Each implementation must include a docstring and a code-level contract matching
the following records. Signatures may gain explicit type aliases, but may not
hide the deadline, completeness, or identity inputs.

### `EnumeratePlacedPinAccessOptions`

```python
def EnumeratePlacedPinAccessOptions(
    Gate: object,
    SignalBindings: object,
    Catalog: object,
    Technology: object,
) -> PlacedPinAccessOptionDomain:
    """Return a typed complete or incomplete domain for one placed cell."""
```

- **Owner:** extended `PhysicalDesign/Placement/Access/Fabric.py`, consuming
  cell-local seeds and the routing technology.
- **Reads:** immutable placed-cell transform, logical pin binding, catalog,
  technology.
- **Returns:** sorted, duplicate-free exact options plus explicit `Complete`,
  `IncompleteReason`, `DomainFingerprint`, and diagnostics fields.
- **Mutation:** none.
- **Completeness:** `Complete=True` only if the catalog and transform pass
  finished. Every deadline/work/generator bound returns `Complete=False`; no
  consumer may infer completeness from tuple length.
- **Determinism:** sort by logical pin, template ID, layer, transform, and
  option fingerprint.
- **Diagnostics:** generated, rejected, deduplicated, and proof-failed counts.
- **Tests:** transform closure, signal-renaming invariance, exact expected
  blocks/claims/repeater reservations for every approved template.

### `BuildPinAccessOptionOwnership`

```python
def BuildPinAccessOptionOwnership(
    Option: PlacedPinAccessOption,
    ResourceGraph: object,
) -> tuple[object, tuple[object, ...]]:
    """Compile exact claims and separate repeater reservations for one option."""
```

- **Owner:** a narrow physical-ownership adapter called from
  `PhysicalDesign/Placement/Access/Fabric.py`.
  It delegates four-kind claim construction to the authoritative compiler in
  `PhysicalDesign/Resources/ResourceGraph.py` and repeater position/facing construction
  to the established routing reservation model; placement must not maintain
  parallel implementations.
- **Reads:** complete option blocks/path and resource-model identity.
- **Returns:** one immutable `RoutingResourceClaims` value containing wire,
  support, required-air, and electrical cells plus a separate immutable tuple
  of repeater position/facing reservations. Extending `RoutingResourceKind`
  instead requires updating every encoder, conflict consumer, serializer, and
  validator as one explicit migration.
- **Mutation:** none; no cache publication inside the function.
- **Invariant:** every materialized access block, electrical neighbor effect,
  and repeater site/facing is represented in exactly one of the two outputs.
- **Tests:** differential parity with the existing straight pattern, then
  exact fixtures for each new pattern.

### `BuildAccessLegalClusterTemplates`

```python
def BuildAccessLegalClusterTemplates(
    Problem: object,
    Deadline: object,
) -> object:
    """Return a typed local frontier with explicit completeness certificates."""
```

- **Owner:** proposed `PhysicalDesign/Placement/Engine/ClusterTemplates.py`.
- **Calls:** typed option-domain enumeration, physical-ownership compiler,
  local exact solver, and physical validators.
- **Returns:** templates, per-domain completeness certificates, attrition
  proofs, and diagnostics.
- **Mutation:** parent publishes immutable complete templates only.
- **Failure meaning:** deadline/work exhaustion is incomplete; a complete empty
  frontier is catalog/bounds-relative UNSAT.
- **Tests:** small exhaustive oracle, symmetry-breaking parity, invalid pattern
  rejection, deterministic Pareto frontier.

### `SolveJointPlacementAccess`

```python
def SolveJointPlacementAccess(
    Problem: PlacementAccessProblem,
    Deadline: object,
) -> PlacementAccessSolveResult:
    """Select exact placement/access ownership without detailed route paths."""
```

- **Owner:** proposed `PhysicalDesign/Placement/Engine/PlacementAccess.py` with an
  optional native implementation in a nested
  `Kernels/Routing/Src/PlacementAccess/` domain.
- **Calls:** deterministic propagation/search and existing narrow interface
  factor projection where applicable.
- **Returns:** exactly one typed result.
- **Mutation:** no global placement mutation; parent publishes a feasible
  witness or a complete core only after its applicable validation completes.
- **Correctness:** independently recheck the selected claims and repeater
  reservations before returning feasible.
- **Deadline:** accepts and checks the original absolute deadline.
- **Tests:** brute-force oracle on small domains, complete/incomplete boundary,
  deterministic branch order, proof-core replay.

### `ValidateFrozenPhysicalPlacementContract`

```python
def ValidateFrozenPhysicalPlacementContract(
    Contract: FrozenPhysicalPlacementContract,
    Module: object,
    Resources: object,
) -> None:
    """Compose placement-geometry and routing-resource handoff validation."""
```

- **Owner:** orchestration in `Compilation/Pipeline.py`. Proposed
  `PhysicalDesign/Placement/Engine/HandoffValidation.py` recomputes transforms, pin mappings,
  and blocks through placement geometry. Proposed
  `PhysicalDesign/Constraints/HandoffValidation.py` independently checks claims,
  repeater reservations, access coverage, leases, and fingerprints without a
  Routing-to-Placement import. `PhysicalDesign/Redstone/Rules/Validation.py` retains
  route-connectivity responsibility.
- **Reads:** contract, NAND IR, technology/resource model.
- **Returns:** `None` or typed hard error.
- **Mutation:** none.
- **Checks:** complete pin coverage, transform/mapping validity, exact blocks,
  claims, repeater reservations, zero cross-signal conflicts, bounds, leases,
  and fingerprints; both delegated validators must pass.
- **Tests:** one-field corruption for every identity-bearing field.

### `RouteAuthoritativeResources`

Current owner and behavior remain in
`PhysicalDesign/Routing/Global/Orchestration/Flow.py`. The proposed signature gains an
explicit `PlacementContract` argument. On v17 it shall:

- validate the contract once at entry;
- treat selected access roots, claims, and repeater reservations as immutable
  occupancy;
- skip legacy access/portal regeneration for those terminals;
- preserve the same negotiated route-tree and exact final-validation behavior;
- return complete physical cuts in terms of contract selection literals when
  possible; and
- serialize the consumed contract fingerprint in success and failure evidence.

### `CompactRoutedTrees`

The current function in `PhysicalDesign/Routing/Pcb.py` must receive the frozen
contract or explicit immutable access paths. It may remove redundant routed
branches but may not recreate pin rays, delete selected access claims or
repeater reservations, or change access identity.

### Existing completed-component cache seam

```python
def BuildCompletedComponentTemplateCacheFingerprint(
    Problem: object,
) -> str:
    """Return the normalized identity for eligible completed-template reuse."""
```

This function, `_InstantiateCachedTemplate`, and `CompileClosedComponent`
already implement normalized reuse, coordinate/signal binding, and physical
revalidation. Extend their fingerprint and cached `RoutedComponentTemplate`
payload with selected access, repeater reservations, domain completeness, and
proof provenance. Preserve the current eligibility checks and adapt the
existing relative-duration call from the one shared absolute deadline; do not
create a second wrapper/cache with overlapping ownership.

## Fingerprint and cache contract

### Fingerprint layers

| Fingerprint | Must include | Must exclude |
| --- | --- | --- |
| `AccessCatalogFingerprint` | template definitions, claims, repeater reservations, proofs, technology, compiler version | signal names, placement translation |
| `PlacedAccessOptionFingerprint` | catalog member, cell transform, logical pin mapping, layer/slot, relative claims and repeater reservations | unrelated board geometry |
| `ClusterTemplateFingerprint` | canonical NAND topology, member transforms, access choices, local routes/claims/repeater reservations, boundary order | instance gate names and global translation |
| `PlacementAccessProblemFingerprint` | all finite domains, bounds, technology, claims, no-goods, solver semantics | runtime timestamp and worker scheduling |
| `PlacementContractFingerprint` | exact selected transforms/options/claims/repeater reservations/leases/channels and problem identity | telemetry timing |
| `DetailedRouteFingerprint` | placement contract plus exact selected routed trees/repeaters/claims | report formatting |

### Cache rules

- Cache only immutable complete domains, proofs, templates, or results.
- Never cache an incomplete result as an empty domain.
- Access catalog and cluster macro caches may be name-independent only when
  binding and transform validation reconstruct exact instance identity.
- Exterior seams, sibling occupancy, global channels, and detailed-route
  legality are placement-dependent and remain fresh unless their own complete
  input identity matches exactly.
- A cache hit that produces a mismatched contract fingerprint is a hard error.
- Cache diagnostics record lookup key, hit/miss, completeness, reused work,
  rebuilt work, and proof fingerprint.

## Diagnostics and artifact schema

Both `.RoutingFailure.json` and `.PhysicalDesign.json` shall carry a
`PlacementAccess` object with at least:

```text
SchemaVersion
PolicyVersion
CatalogVersion
CatalogFingerprint
ProblemFingerprint
ContractFingerprint
Status
DomainCompletenessCertificates
SolverKind
SolverVersion
ElapsedSeconds
ExploredStateCount
PropagationCount
DomainPruneCount
SymmetryPruneCount
CacheHitCount
ClusterTemplateDomainSizes
AccessOptionDomainSizes
SelectedClusterTemplates
SelectedAccessOptions
SelectedChannels
SelectedMaximumLayer
SelectedFootprintLowerBound
ConflictCore
IncompleteReason
Deadline
```

Stage timing must be explicit rather than folded into one placement number:

| Timer | Meaning |
| --- | --- |
| `AccessCatalogSeconds` | template load/generation and proof validation |
| `ClusterTemplateSeconds` | local access-legal template construction |
| `PlacementMasterSeconds` | board master search excluding subproblem time |
| `AccessSubproblemSeconds` | exact access/lease propagation and search |
| `PlacementContractValidationSeconds` | independent frozen-witness validation |
| `GlobalGuideSeconds` | coarse guide construction after selection |
| `NegotiatedDetailedRoutingSeconds` | route-tree planning and repair |
| `RouteMaterializationSeconds` | block/repeater materialization |
| `FinalPhysicalValidationSeconds` | claims and connectivity validation |
| `TruthTableSimulationSeconds` | exhaustive logical/physical simulation |
| `PublicationSeconds` | staged artifact writes plus manifest/directory finalization |

Every timer records invocation count, inclusive wall time, and whether it
completed. Native child CPU time is recorded separately where workers are
used. A fast failed stage is never included in a successful performance mean.

## Failure taxonomy

Add or map typed reasons with these meanings:

| Reason | Stage | Meaning | Allowed feedback |
| --- | --- | --- | --- |
| `AccessPatternInvalid` | AccessCatalog | one concrete template failed exact proof | remove that template; catalog may continue |
| `AccessCatalogIncomplete` | AccessCatalog | catalog generation stopped before completeness | resume or report incomplete |
| `ClusterTemplateUnsatisfiable` | ClusterTemplate | complete declared local domain has no legal template | split/move/widen implicated cluster |
| `ClusterTemplateSolveIncomplete` | ClusterTemplate | bounded local solve did not conclude | report progress; no UNSAT no-good |
| `PlacementAccessUnsatisfiable` | PlacementAccess | complete board placement/access domain is impossible | apply complete core or expand declared domain |
| `PlacementAccessSolveIncomplete` | PlacementAccess | master/subproblem did not conclude | report incomplete; preserve proofs only |
| `PlacementContractIdentityMismatch` | PlacementHandoff | selected witness changed or was reconstructed incorrectly | hard implementation failure |
| `PlacementContractValidationFailed` | PlacementHandoff | recomputed blocks/claims/repeater reservations/coverage are illegal | reject before success publication |
| existing routing failures | Routing | frozen contract reached negotiated routing but routing failed | return complete physical cut when available |

Existing `PlacementOverlap` remains valid for literal cell/block overlap, but
the generic `no exact-legal placement candidate` terminal detail should be
replaced on v17 by the most specific typed complete or incomplete result.

## Correctness proof obligations

The new strategy is not accepted until all of the following are independently
demonstrated:

1. access templates render to the blocks their claims and separate repeater
   reservations describe;
2. every logical terminal has exactly one selected complete access path;
3. NAND input mapping preserves the synthesized NAND IR;
4. selected access claims have zero self and cross-signal conflicts and every
   repeater reservation has one legal position/facing;
5. frozen contract validation reproduces the same canonical fingerprint;
6. global/detailed routing never mutates selected access geometry;
7. final claims include access, local macro routes, global routes, supports,
   air, and electrical exclusions, while final repeater reservations and
   materialized repeaters are complete and consistent;
8. physical connectivity reaches every required target, and the new v17
   foreign-target validator rejects reachability to another signal's target;
9. exact simulation passes every input row;
10. rendered output agrees with routed simulation where rendered simulation is
    enabled;
11. success artifacts record zero unresolved claims/conflicts and no fallback;
12. repeated runs match placement, contract, routing, and emitted-design
    fingerprints; and
13. failure and incomplete results publish no success artifacts.

## Compactness strategy

Compactness begins only after a feasible exact contract exists.

### During placement/access solve

- before the first incumbent, time-slice a deterministic envelope ladder and
  launch a conservative feasibility envelope early;
- retain Pareto-distinct access topologies rather than only minimum NAND core
  area;
- price channel/layer capacity after exact feasibility;
- prefer reusable sealed macros with measured block counts; and
- after an incumbent exists, search smaller envelopes in ascending order; an
  over-tight incomplete domain never makes smallness look like impossibility.

### Validated incumbent-preserving post-route compaction

Starting from an immutable accepted incumbent, try bounded changes:

1. remove one empty or low-use channel;
2. reduce one row/deck pitch;
3. lower one routing layer;
4. move one macro toward its neighbor;
5. shorten or locally reroute affected branches; and
6. remove redundant support or other material while preserving every
   required-air cell as empty.

Only the affected region is rerouted first, but acceptance of a replacement
requires full exact validation and the complete truth table. A failed attempt
is discarded without changing the incumbent. Remaining time buys quality; it
never risks losing the first legal result.

## Runtime and scaling model

### Expected expensive work

The dominant work after this change is expected to be:

- producing enough distinct access-legal local templates;
- propagating exact cross-signal claim conflicts in dense cluster interfaces;
- solving boundary/cut capacity when interface width is high;
- repeater-aware multi-target search in genuinely congested active regions;
- repairing persistent route-tree overflow; and
- exhaustive physical simulation for large input spaces.

### Work removed or reduced

- repeated construction of identical straight access claims;
- whole-placement rejection for a conflict that a local access option can
  resolve;
- regeneration of access domains after selection;
- repeated local factor work for canonical equivalent macros;
- complete per-boundary recomputation once the shared DP lands; and
- routing detailed trees for placements that lack a legal escape assignment.

### Complexity containment

- exact solve remains cluster/interface/coarse-placement scoped;
- detailed pathfinding remains lazy and spatially bounded;
- access incompatibility uses resource occurrence indices and bitsets;
- canonical templates factor repeated topology;
- no-goods are local and proof-scoped;
- shared boundary DP scales with frontier/interface state rather than every
  full tree combination; and
- accepted incumbents stop feasibility work before optional compaction.

No asymptotic or multiplicative speedup is claimed until timestamped cold and
cached measurements are captured on the protected matrix.

## Test strategy

### Unit tests

- exact catalog members and rejected invalid patterns;
- complete claim contents for each access pattern;
- transform, mirror, translation, layer, and input-swap behavior;
- conflict occurrence indexing and propagation;
- stable fingerprints and cache invalidation;
- typed feasible/unsatisfiable/incomplete boundaries;
- no-good replay and problem-fingerprint scoping;
- frozen-witness serialization/deserialization; and
- one-field corruption rejection by the handoff validator.

### Property and metamorphic tests

- transform closure identities listed above;
- signal renaming preserves anonymous geometry and solve status;
- gate-name permutation preserves canonical macro identity;
- adding a valid unused option cannot invalidate an existing feasible witness;
- increasing channel capacity cannot invalidate a fixed complete witness;
- selected-claim recomputation is byte-equal across ordering permutations;
- cold and cached complete solves select the same canonical witness; and
- worker count does not change deterministic result identity.

### Differential/oracle tests

- current straight-only placement behavior versus a catalog containing only
  the straight template;
- small joint solve versus brute-force enumeration;
- native solver versus an independent CP-SAT or Python oracle on fixtures;
- new shared-boundary DP versus the existing component solver on bounded
  complete cases;
- frozen access handoff versus current straight access on accepted smaller
  designs; and
- compaction before/after exact claims, connectivity, and simulation.

### Physical tests

1. isolated rendered NAND template for every access pattern and transform;
2. two-cell conflict fixtures for every claim kind;
3. small multi-terminal cluster with at least two feasible access selections;
4. complete unsatisfiable cluster with a replayable minimal core;
5. fixed current CLA4 placement access experiment;
6. FullAdder physical smoke;
7. protected FullAdder repeated gate;
8. RCA4 and RCA8 repeated gates;
9. CLA4 twice with exact 512-row simulation; and
10. complete sequential acceptance matrix.

### Acceptance requirements

CLA4 completion requires a current-source `.PhysicalDesign.json`, truth table,
and `.litematic` with:

- 512/512 rows passed;
- zero final conflicts and unresolved claims;
- authoritative exact simulation;
- no fallback;
- runtime within the canonical harness ceiling;
- two deterministic runs with equal placement, contract, route, and emitted-
  design fingerprints; and
- retained passing FullAdder, RCA4, and RCA8 gates.

For CLA4, `512/512` means exhaustive authoritative
`SimulateRoutedTruthTable` evaluation of the routed physical delivery model. It
is distinct from the rendered-Minecraft cross-check, which is currently enabled
only below its input-row ceiling and therefore does not presently cover CLA4.
Expanding that rendered check would add a gate; it does not change the meaning
of the existing 512-row requirement.

The authoritative v17 acceptance shape is the live harness matrix:

| Circuit | Required runs | Truth-table rows per run | Process ceiling |
| --- | ---: | ---: | ---: |
| FullAdder | 5 | 8 | 10 s |
| RippleCarryAdder4 | 3 | 512 | 25 s |
| RippleCarryAdder8 | 3 | 131,072 | 30 s |
| CarryLookaheadAdder4 | 2 | 512 | 120 s |

The first three cases are the normal regression set. CLA4 is the extended
exact-interface case selected by `--include-cla4`; it is mandatory before v17
may be called accepted even though it remains optional for ordinary pre-v17
regression invocations. Every case also retains the harness's artifact,
overflow, simulation, no-fallback, source-provenance, and repeated-fingerprint
requirements.

Focused tests, a feasible access witness, reaching detailed routing, or one
successful CLA4 run are milestones, not full acceptance.

## Evidence protocol

The reproducible capture tool is
`Tools/Routing/CaptureRoutingDesignSnapshot.py`. Each timestamped bundle records:

- UTC and America/New_York timestamps;
- branch, full revision, detailed porcelain status, and status digest;
- staged, unstaged, and nonignored untracked identities;
- explicit routing-source scope, per-file hashes, aggregate hash, line counts,
  and largest Python definition spans;
- exact supplied artifact paths, copied bytes, sizes, and hashes;
- concise typed CLA4 failure facts without timeout reclassification;
- an exact evidence digest that retains raw artifact byte identities;
- a portable semantic evidence digest that excludes raw artifact hashes and
  schema-declared timestamps, absolute paths, and publication locations; and
- a human-readable Markdown projection plus `SHA256SUMS`.

The tool never searches `/tmp` for a latest artifact. Every evidence path is an
explicit argument. It rejects an existing/nonempty output directory and writes
new captures under `Output/DesignSnapshots/RoutingAwarePlacementAccess/`.
Commit neither capture bundles nor dated documentation logs; use the fresh
artifact with the current [testing guidance](../../Testing/RunningTests.md) and
[architecture review](../../Architecture/PhysicalDesignArchitectureReview.md).

Capture at these real milestones only:

| ID | Required evidence |
| --- | --- |
| `RAPA-S0` | pre-implementation current source and CLA4 failure baseline |
| `RAPA-S1` | straight-only catalog parity and frozen-witness handoff |
| `RAPA-S2` | fixed CLA4 placement returns zero-conflict access witness or complete local core |
| `RAPA-S3` | placement-coupled access solve reaches negotiated routing |
| `RAPA-S4` | first native CLA4 routed and simulated artifact |
| `RAPA-S5` | deterministic CLA4 pair and protected full matrix |
| `RAPA-S6` | post-acceptance compactness/runtime comparison |

Do not pre-create successful future entries.

## Implementation phases

The exit gates below establish milestone acceptance, not a requirement to finish
every earlier phase before developing a needed prerequisite in another phase.
Use the [rewrite workflow](../../Pillars/RewriteWorkflow.md) and
[dependency register](../../Pillars/CapabilityDependencies.md) to declare and
test those dependencies. Scope changes need explicit agreement; this does not
enable deferred behavior or waive any RAPA acceptance evidence.

### Phase 0: contracts and baseline

**Changes:** land this design, the capture tool, and focused capture tests.
Add no routing behavior.

**Exit gate:** reproducible `RAPA-S0`, clean documentation links, focused tests,
and unchanged pre-existing worktree content.

### Phase 1: straight-only authoritative catalog

**Changes:** retain `CellMacro.PinAccessPatterns` as the cell-local straight
pattern seed and promote that seed through the production placement-access
option/domain-construction path and current raw track-assignment portfolio
selection seam. Do not route production through the fixture-only standalone
capacity selector. Derive physical rules from `Routing/Technology.py`, exact
claims from `Routing/ResourceGraph.py`, and explicit repeater reservations from
the established reservation model; do not turn the cell property into a second
technology or claim authority. Freeze and serialize the selected straight
witness while preserving existing accepted design identity.

**Exit gate:** differential parity on FullAdder/RCA4/RCA8; every old access
reconstruction reports or consumes the same witness; `RAPA-S1` captured.

### Phase 2: selectable access on fixed placement

**Changes:** add independently validated alternate templates and solve access
selection for the current CLA4 retained placements without changing their core
cell transforms.

**Exit gate:** either one independently validated zero-conflict frozen witness
or one complete domain-scoped local core. Capture `RAPA-S2`. Do not claim CLA4
completion.

### Phase 3: couple implicated cluster geometry

**Changes:** expose cluster template, rigid transform, input mapping, access,
and local channel choices to the master. Translate complete cores to local
rotate/move/split/widen actions.

**Exit gate:** one complete contract passes handoff and enters negotiated
routing without access regeneration. Capture `RAPA-S3`.

### Phase 4: first valid CLA4

**Changes:** route the frozen contract through the current negotiated router;
repair only complete physical cuts; preserve all validation.

**Exit gate:** first native CLA4 physical artifact whose authoritative routed
simulation passes 512/512 rows inside the ceiling. The separately row-gated
rendered-Minecraft cross-check is not implied. Capture `RAPA-S4` immediately,
but classify it as a milestone pending repeat and matrix gates.

### Phase 5: scale and reuse

**Changes:** extend the existing normalized completed-component fingerprint,
cache, `_InstantiateCachedTemplate` binding, and revalidation seam with selected
access/repeater/proof identities; then add shared boundary DP, complete local
factor reuse, and bounded standard-row/channel recovery where measured.

**Exit gate:** deterministic CLA4 pair plus protected matrix, `RAPA-S5`.

### Phase 6: compact and optimize

**Changes:** staged incumbent-preserving channel/row/layer compaction,
incremental local reroute, source/native profiling, and safe parallelism only
at immutable work boundaries.

**Exit gate:** `RAPA-S6` reports accepted before/after dimensions, blocks,
route material, wall/internal times, CPU, memory, and identical correctness
gates.

### Phase 7: retire superseded active paths

Remove duplicate fixed-ray reconstruction, obsolete whole-placement retry
epochs, unused access representations, and legacy candidate regeneration only
after differential tests and the complete matrix demonstrate that the new
strategy owns their behavior. Keep the old solver as an explicit small-case
oracle until replacement proof coverage is complete.

## Risks and mitigations

| Risk | Consequence | Mitigation |
| --- | --- | --- |
| Access catalog branches explode | slower placement solve | small verified catalog, duplicate collapse, propagation, canonical macros, measured domain sizes |
| New templates are physically unsound | false feasibility | materialized blocks, canonical claims, isolated rendering/simulation, independent final validator |
| Solver reports false UNSAT | valid placement discarded | per-domain catalog/template/slot/access/channel completeness certificates, proof-backed subsumption for every discarded frontier state, independent small oracle, proof-core replay, and incomplete on every cap |
| Cache leaks stale legality | incorrect selected witness | exact layered fingerprints, cache complete work only, fresh exterior/global validation |
| Placement/access solver becomes full router | state explosion | forbid detailed external path variables; use coarse channels and local exact windows only |
| Compactness objective hides feasibility | repeats current failure | deterministic first-feasible envelope portfolio, conservative envelope launched early, compactness search only after immutable incumbent |
| Macro reuse binds wrong signals | logical miscompile | canonical boundary roles, explicit instance binding, NAND IR validation, full truth table |
| Parallelism changes results | nondeterministic artifacts | immutable branch snapshots, deterministic reduction, fixed sorted domains, fingerprint tests |
| Monolith refactor causes broad regression | long recovery | strangler path beside current strategy, phase gates, delete only after acceptance |
| Design becomes circuit-specific | no general scaling | topology/geometry/resource predicates only; rename and permutation tests |

## Rejected approaches

### Increase deadlines, workers, or retry counts first

Rejected because current CLA4 fails structurally with substantial budget
remaining and never reaches detailed routing. These controls do not add a
missing physical access choice.

### Rewrite the detailed router before placement/access

Rejected as the first move because the current blocker occurs before router
invocation. Keep and later simplify the existing negotiated router.

### Full-board SAT/ILP/CP-SAT detailed routing

Rejected because multi-terminal Steiner connectivity multiplied by
directional Redstone movement, support, air, electrical exclusions, layers,
signal strength, and repeaters creates an unnecessarily large exact model.
Use exact solving for finite local/interface/coarse decisions and negotiated
search for detailed spatial paths.

### Add whitespace everywhere

Rejected as a final architecture. Capacity-sized rows/channels are allowed as
a first-legal recovery, but exact cores must identify where capacity is needed
and staged incumbent-preserving compaction must recover avoidable space.

### Split source files without behavioral contracts

Rejected as a breakthrough strategy. File extraction follows immutable data
and typed stage boundaries; it does not substitute for them.

## Open implementation decisions

These are deliberately unresolved until Phase 1/2 evidence exists:

1. whether the production master uses a new Rust solver, extends the lease CSP,
   or retains CP-SAT as more than an oracle;
2. the smallest alternate access pattern set that resolves or completely
   explains the current CLA4 local conflicts;
3. whether selected access blocks belong directly to
   `FrozenPhysicalPlacementContract` or are also stored in the extended existing
   completed-component cache/template and rebound per instance;
4. the initial cluster template size/cut limits;
5. the exact frontier signature and tree decomposition for shared boundary DP;
6. when standard rows/channels outperform free cluster slots;
7. whether full rendered Minecraft simulation should expand beyond its current
   input-row ceiling; and
8. the compactness metrics and ceilings to adopt after first acceptance.

Every decision requires fresh measured evidence in its output artifacts. None
may be resolved by a CLA4-name special case.

## Implementation checklist

Checklist evidence is scoped by the [R2 Stage 1 conformance ledger](../../Pillars/R/R2/Notes.md#stage-1-conformance-ledger).
Checked source/test behaviors below do not mean Stage 1 or R2 is accepted:
the clean `2024d7d` live Stage-1 matrix fails before finalization.

- [x] Add typed access catalog and proof version.
- [x] Make straight access use the canonical typed option/ownership compiler.
- [ ] Remove direct fixed-ray derivation from new-strategy consumers.
- [ ] Add frozen placement/access contract plus composed placement/routing
      validators.
- [x] Round-trip Stage-1 solve, witness, domains, and scoped cores with strict
      identity/corruption checks; unit-test the narrow five-stage handoff.
- [x] Implement `PlanningContracts.PlacementAccess` publication with the full
      selected witness; verify the serializer/adapter in tests. Live successful
      artifact publication remains unverified.
- [ ] Serialize contract identity and detailed timers in failures/successes.
- [ ] Add alternate access templates with isolated physical proofs.
- [x] Add fixed-placement exact access solver and small oracle parity.
- [ ] Translate complete cores into local placement actions.
- [ ] Pass one frozen witness unchanged to negotiated routing.
- [ ] Produce first native CLA4 physical artifact and snapshot it.
- [ ] Pass deterministic CLA4 pair and protected matrix.
- [ ] Extend existing completed-template canonical reuse with access/proof
      identity, and add shared boundary DP where profiling proves value.
- [ ] Add staged incumbent-preserving compaction and manifest/directory
      finalization.
- [ ] Delete superseded retry/reconstruction paths only after acceptance.

## References

- [Negotiated route-tree router](NegotiatedRouteTreeRouter.md)
- [Incremental physical factor reuse](IncrementalPhysicalFactorReuse.md)
- [Resource graph](ResourceGraph.md)
- [Hierarchical routing regions](HierarchicalRegions.md)
- [Track assignment and boundary capacity](TrackAssignment.md)
- [Failure catalog](FailureCatalog.md)
- [Physical design architecture review](../../Architecture/PhysicalDesignArchitectureReview.md)
- [Running tests](../../Testing/RunningTests.md)

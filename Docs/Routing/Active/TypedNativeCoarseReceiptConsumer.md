# Typed coarse routing receipt consumer

## Bounded promise and dependency

This Joint checkpoint implements issue #3 through the existing coarse candidate
path. It starts at `f64c92f21bb64ce22a5340cbbb78576e7e633198`, including Runtime
`553d09665e9004292027a746c7902060ce40cf7f`, and intentionally finishes the fourteen
existing Joint working-tree files. It does not merge the separately delivered
parser `455d656e` or regression-CI `845e8d97` changes; neither is a dependency of
this consumer. The complete original snapshot remains preserved separately.

The production `PlaceAndRoutePcb` path reaches
`ExecuteNativeRouteBatchOutcomesV1` from coarse candidate preparation. Native
results remain commitment-ineligible. Physical materialization, capacity-one
assignment, current selected-access validation, and final coordinator
publication retain their existing authority.

The detailed negotiation caller is unchanged. Its failed-frontier and
no-repeater diagnostic needs are not carried by the current typed API, so a
mechanical migration would discard retry semantics. This checkpoint makes no
persistent-worker, in-flight cancellation, reuse, or full-router claim.

## Safety boundaries

- An execution has exact caller, graph, request, ordinal, absolute deadline,
  expansion-cap and receipt identities. Equivalent payloads may share one
  invocation-local native submission, while each physical origin is admitted
  separately with its exact selected access and immutable fragments
- Current placement, policy, selected access, model, occupancy, derived admission
  claims, context and deadline are re-attested before completed-result publication
  and before physical admissions or complete-negative evidence are published.
  Each origin independently checks its exact descriptor, immutable nodes and live
  deadline; physical materialization consumes an owned, deeply frozen preparation
  epoch rather than the mutable source state. The epoch includes profiles, portal
  metadata, graph inputs, technology, policy, guides, budgets and physical claim
  maps. Its fresh resource graph inherits no source query caches. Provisional
  positive and negative admissions cannot escape the live-state publication gate.
  Drift produces an explicit incomplete stale-preparation failure
- Origin descriptors and retained physical evidence deeply own immutable data;
  exported documents cannot mutate their identities. Native nodes are retained
  as immutable tuples under their receipt scope
- `CanonicalExecuted` in `joint-typed-route-batch-counters-v2` counts canonical
  submissions with returned native receipts. The containing batch document uses
  `joint-typed-native-route-consumer-v3`. It is not the native search-start count:
  pre-cancelled receipts have `Started=False` and zero expansions
- Incomplete/cancelled results carry no geometry or impossibility proof, never
  enter physical materialization, and cannot create complete staged-starvation
  evidence. Cancellation coverage is the native pre-dispatch snapshot contract
- Physical wire, support, required-air and electrical claims are checked by the
  existing shared predicates despite native success. Rejection evidence uses
  the first decisive self-conflict capture rather than a duplicate predicate
- A staged seed rejection is evaluated once per origin. Selected-access
  evidence enumerates individual owner/claim pairs
- Route-level self-conflict ownership and fragment hashes do not identify the
  pre-owned contributors that caused a conflict. They cannot authorize direct-only
  recovery. The separate exact placement-access-core recovery remains intact

## Evidence and limitations

The original unchanged WIP baseline passed 303 tests and 22 subtests. The
source-local extension was rebuilt from this exact native source; its imported
SHA-256 is `fff276d998a534c0d00fe47119278e7a76e7253a8347e338ce54588d4a4d3bea`.
Native boundary tests passed 55 cases, Rust release tests passed 91 cases, and
the structural/schema gate passed 7 cases. The first Rust test link failed
because the bundled Python reports `/install/lib`; retaining that log and
setting `LIBRARY_PATH`/`LD_LIBRARY_PATH` to its actual `lib` directory recovered
the unchanged source. The
checkpoint adds real production mutation, resource-conflict, incomplete,
staged-rejection and legacy-equivalence controls; these call the actual native
implementation. Unit contract checks challenge immutable data and corrupt
origin/receipt mappings independently.

Source-bound commands, exit statuses, native identity, raw output, and the
clean-legacy/current single-NAND geometry comparison are retained under
`Output/Issue3/Final/`. The subsequent frozen-epoch regression and paired serial
17-NAND timing evidence is retained under `Output/Issue3/Epoch/`. Native
submission and physical admission remain separate, and buffered physical
admissions are serialized once at the publication boundary. These controls
establish only the tested coordinator-acceptance domain.
The seven-circuit physical acceptance matrix, live Fabric, scale, and performance
promotion gates are not run and are not established by these tests.

Commit and integration readiness require independent review of the exact final
source, scoped regression checks, and resolution of the introduced 17-NAND
timing regression without changing its runtime bounds. Any combined integration
with parser and regression-CI changes requires fresh review and testing of that
combined source; this document does not establish protected-main promotion.

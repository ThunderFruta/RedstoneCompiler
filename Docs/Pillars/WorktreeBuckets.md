# Dependency-aligned worktree buckets

Use these five buckets for parallel physical-design development. They group
changes that repeatedly need the same evolving state and APIs, rather than
giving every R/N identifier an isolated branch. See the
[workflow](RewriteWorkflow.md) for readiness gates and the
[dependency register](CapabilityDependencies.md) for exact consumed checkpoints.

Each requirement has one primary implementation bucket below. This is ownership
of its shared mechanism, not exclusive applicability: all relevant N1-N6
constraints still apply in every bucket. A bucket is a coordination boundary,
not a claim that its code is independent of the rest of the router.

## Base checkout: no feature bucket

`/mnt/Projects/RedstoneCompiler` is the shared base checkout for `main` and
`Router-Refactor(R10-N5)`. Git checks out only one of them there at a time;
switch deliberately for release or integration work after checking local state.
Neither branch owns an R/N feature bucket. The historical `(R10-N5)` suffix is
not an assignment of R10 and N5 to the base checkout.

The capability-owned histories were rebuilt from the shared architectural
checkpoint `b82b8ee`; Router Refactor retains the cross-cutting workflow,
bucket map, and worktree-environment setup. `main` remains a separate stable
line and is not advanced by bucket-history work. Old-layout runtime/build
directories remain present and may appear untracked under this revision's
ignore rules. Do not commit or delete those payloads to make the base look clean;
resolve their generated/ignore status separately before provenance-sensitive runs.

## Primary buckets

| Bucket / branch | Primary requirements | Why these belong together | Main code ownership |
|---|---|---|---|
| `Physical-Rules` | R10, N2 | The physical model and its query/claim contract must evolve together; separate implementations would recreate disagreeing legality rules. | Cells, Redstone, Resources, model-facing Geometry/Rendering, physical-rule contract types |
| `Joint-Physical-Design` | R2, R3, R4, R9, N3 | Placement alternatives, access/capacity choices, global routing, completed-candidate ranking, and coordinator commitment share one evolving candidate state. | Placement, Orchestration, global planning/assignment, Objectives, candidate/commitment contracts |
| `Reuse-And-Salvage` | R1, R5, N6 | Lazy/exact representation, normalized regions, cache dependencies, and selective reuse must agree on what a retained claim proves. | Region routing, immutable caches/dependency manifests, lazy expansion, salvage certificates/revalidation |
| `Runtime-And-Kernels` | R6, R7, N1, N4 | Persistent workers, narrow native calls, result/lifecycle semantics, admission, deadlines, cancellation, and shutdown form one execution contract. | Runtime, Kernels/Routing, task/result/lifecycle APIs, Python/native adapters |
| `Telemetry-And-Acceptance` | R8, N5 | Events, provenance, retained evidence, backend checks, and concise reports share one observation pipeline without changing routing decisions. | App reporting/telemetry, Tools/Routing evidence/acceptance, Validation and backend fixtures |

R9's comparison contract belongs beside joint candidate selection even while
large-scale optimization remains deferred. N1's shared result/state definitions
belong beside the worker lifecycle, but every algorithm must preserve their
meaning. N3's accepted-state authority stays with physical-design orchestration;
reuse supplies certificates and workers supply results, not a second commit
authority. Keep heavily coupled capabilities serial within their bucket.

## Recorded worktree locations and starting source

| Branch | Worktree |
|---|---|
| `Physical-Rules` | `/mnt/Projects/RedstoneCompiler-Worktrees/Physical-Rules` |
| `Joint-Physical-Design` | `/home/bananawewe/.codex/worktrees/634f/RedstoneCompiler` (existing worktree reused) |
| `Reuse-And-Salvage` | `/mnt/Projects/RedstoneCompiler-Worktrees/Reuse-And-Salvage` |
| `Runtime-And-Kernels` | `/mnt/Projects/RedstoneCompiler-Worktrees/Runtime-And-Kernels` |
| `Telemetry-And-Acceptance` | `/mnt/Projects/RedstoneCompiler-Worktrees/Telemetry-And-Acceptance` |

All five start from the same capability-neutral Router Refactor tip, including
the common workflow, bucket map, and worktree setup. Capability commits are
then carried only on their owner branch. `Physical-Rules` provides the access
catalog, physical realization, proof hardening, and domain-query checkpoints;
`Joint-Physical-Design` explicitly merges those dependencies and owns policy,
placement/orchestration consumers, candidate commitment, R2/N3 records, and
the v17-default behavior. Telemetry owns harness and snapshot integration;
Runtime and Reuse own their N1/N4 and N6 attribution ledgers. The other buckets
do not inherit R2 implementation or evidence merely because they are feature
branches. Keep one explicit Physical-to-Joint dependency merge and one narrow
Joint-policy-to-Telemetry dependency merge. Record later dependencies with an
explicit tested producer revision rather than repeated Router synchronization
merges.

The four new worktrees contain tracked source only. This setup does not copy
worlds, secrets, generated output, virtual environments, or native binaries,
and does not start implementation tasks or validation servers. Establish each
worktree's interpreter/native provenance before running it. Avoid editable
installs into a shared environment that could redirect another worktree's
imports. Serialize jobs that mutate a shared Fabric runtime or contend for
benchmark CPU resources; separate Git worktrees do not isolate those resources.

The pre-split R1/R2 identities remain historical evidence references and in
the pre-rewrite recovery bundles, not as the shared ancestry of every bucket. New
R1/R5/N6 work goes in `Reuse-And-Salvage`; new R2/R3/R4/R9/N3 work goes in
`Joint-Physical-Design`. Reconcile unique old R1 changes deliberately rather
than starting another independently evolving copy of shared prerequisites.
`Cla4-Verification` and its worktree are unchanged and outside this setup.
The three existing detached S0/S1 evidence worktrees are retained, not buckets.

## Dependency boundaries and serial work

| Producer | Consumers | Checkpoint that permits independent work |
|---|---|---|
| Physical-Rules | Joint-Physical-Design, Reuse-And-Salvage, Telemetry-And-Acceptance | Versioned legality/query scope, claims, transforms, and model compatibility fixtures |
| Runtime-And-Kernels | Joint-Physical-Design, Reuse-And-Salvage, Telemetry-And-Acceptance | Immutable work/results, outcome/lifecycle axes, budget/cancellation and event contracts |
| Joint-Physical-Design | Reuse-And-Salvage, Telemetry-And-Acceptance | Candidate/domain identity, scoped dependencies, commitment validation, measured-objective records |
| Reuse-And-Salvage | Joint-Physical-Design | Certified reusable subclaims revalidated through the existing coordinator gate; integrate this adapter as a coordinated slice |
| Every producer | Telemetry-And-Acceptance | Typed events/artifact identities and independent physical fixtures, without reporting imports in lower layers |

These are contract relationships, not circular Git merge instructions. Initial
joint-design work can use strict current-identity/no-salvage behavior; the later
reuse adapter consumes that fixed coordinator interface. A cyclic API change
requires one coordinated checkpoint or a shared prerequisite, not mutual merges
of moving branch tips.

Parallel starting work is practical in physical model fixtures, worker/result
contracts, evidence tooling, and controlled joint-design fixtures. Reuse can
develop normalized-region and small-oracle cases against pinned interfaces,
but production salvage waits for sufficient dependency evidence and ordinary
current-state revalidation. No bucket may weaken another bucket's contract to
avoid waiting for a prerequisite.

## Shared-file coordination

- One active writer per bucket by default. Serial/heavily related capabilities
  stay together; parallel tasks within a bucket need explicitly disjoint scope.
- `Contracts/` is shared by type: Physical-Rules leads legality/model types,
  Runtime-And-Kernels leads work/outcome/lifecycle types, Joint-Physical-Design
  leads candidate/commit authority, and Reuse-And-Salvage leads certificates.
  Agree shared fields with consumers before changing them; do not duplicate types.
- Global candidate caching overlaps joint search. Reuse leads cache semantics;
  Joint-Physical-Design leads the search-state consumer. Agree affected files
  or serialize that slice rather than editing the same search code concurrently.
- Telemetry leads event schemas and sinks; the producing bucket owns event
  emission in its control flow. Physical-Rules owns model decisions; Validation
  checks them independently and must not become another routing-rule authority.
- Record exact producer/consumer commits and run real boundary tests when
  advancing dependencies. Move ready checkpoints into the existing integration
  branch only through an explicitly authorized, verified operation.
- Do not merge unfinished bucket tips in all directions. If a boundary repeatedly
  cannot stabilize, temporarily combine the bounded feature slice under one
  owner; preserve the requirement ledger and historical evidence.

Bucket assignment and clean source checkouts do not establish capability or
production acceptance. Keep all outstanding R2/N evidence claims unchanged.

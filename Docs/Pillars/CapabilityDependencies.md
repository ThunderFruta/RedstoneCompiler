# Capability and branch dependency register

This is the canonical development-dependency register for the
[rewrite workflow](RewriteWorkflow.md). R/N ledgers remain the source of truth
for requirement claims. Exact checkpoints identify tested inputs; branch names
identify ongoing work. An entry is not merge approval or production acceptance.

## Worktree buckets

The [bucket map](WorktreeBuckets.md) is the canonical assignment of all ten R
and six N requirements to five primary workstreams, including exact worktree
paths and shared-file boundaries. `main` and `Router-Refactor(R10-N5)` use the
base checkout at `/mnt/Projects/RedstoneCompiler`, outside feature buckets.
The five bucket branches share one capability-neutral Router Refactor tip. R2
source and evidence are owned
by `Joint-Physical-Design`, while the exact access catalog is owned by
`Physical-Rules`. A consumer receives another bucket's implementation only
through an explicit dependency merge; branch setup alone is not such a merge.

## Branch roles and initial inventory

Recorded from local refs on 2026-09-04 before the workflow documentation commit.
These are inventory revisions, not automatically approved integration bases.
Refresh them from Git before an operation. This record intentionally does not
try to contain the final hash of its own commit.

| Branch | Recorded revision | Role / state |
|---|---|---|
| `main` | `d29eecffd53f2f82917b3894e258aa5bd1007949` | Protected stable line; no promotion performed by this record |
| `Router-Refactor(R10-N5)` | `b82b8ee1331b2b1fa17aa4353710650df3fa51cc` | Existing shared rewrite integration branch; reuse it |
| `R2-Joint-Placement-And-Routing` | `fc7887d50a353f002b4ff9eec7c1f3ef2a30bc0c` | R2 umbrella; contains Stage-1 hardening and evidence records, not yet integrated into the shared branch |
| `R1-Routability-By-Construction` | `22d112f6aea02ab7b995b562230f971ab08119e2` | Existing workstream with shared-prerequisite history requiring reconciliation |

Task assignees are not established by branch names. Record the actual assignee
and worktree when scheduling parallel work; do not infer active ownership from
this inventory. `Cla4-Verification` and its uncommitted work are outside this
documentation commit and have not been reviewed for admission.

## Capability checkpoints

| ID / capability | Provider and primary code owner | Required checkpoint / relationship | Contract provided | Readiness and evidence | Remaining dependency or action |
|---|---|---|---|---|---|
| `Shared-Access-Catalog` | `Physical-Rules`; catalog, realization, proof hardening, and domain query | Explicitly merged into `Joint-Physical-Design` before its consumer checkpoint | Typed physical templates, realization/legality, exact claims, codecs, domain construction, and proof identities | Extracted from pre-split `96d9604`, `1fb7db6`, and `2024d7d`; see the Physical R10/N2 history | Preserve Physical-Rules authority and avoid private or duplicate legality implementations |
| `Selected-Straight-Access` | `Joint-Physical-Design`; policy and placement/routing consumer | Code dependency on the Physical-Rules access checkpoint | Placement/orchestration selects one option; global and detailed routing consume its immutable identity | Extracted from pre-split `789fbd3` and `1fb7db6`; [R2 ledger](R/R2/Notes.md#stage-1-conformance-ledger) | Real end-to-end handoff and larger integration coverage remain separate gates |
| `Access-Transport-Handoff` | `Joint-Physical-Design`, consuming the Physical-Rules proof/domain checkpoint | Code dependency on `Selected-Straight-Access` | Joint result/candidate transport and five-stage commitment validation over Physical-owned proof codecs and domain queries | Historical evidence was captured at pre-split `2024d7d`; snapshot ownership is `Telemetry-And-Acceptance` | Demonstrate a small real five-stage production path; classify dependent capabilities independently of full-matrix acceptance |
| `R1-Shared-Prerequisites` | R1 `22d112f6aea02ab7b995b562230f971ab08119e2`; global routing, with shared contracts/policy prerequisites | Existing parallel history, not a new dependency stack on R2 | [R1 history](R/R1/CommitHistory.md) records prerequisite/supporting work, not completed R1 behavior | Reconciliation pending | Map unique changes and tests before any merge or new dependent checkpoint |

Only code dependencies with actual recorded providers are asserted above. New
joint-selection, alternate-access, worker, or capacity branches are not implied
to exist. Add their code/interface/acceptance dependencies when their scope and
provider checkpoints are agreed; do not invent an implementation order from
pillar numbering.

## Initial reconciliation facts

- R1 and R2's inspected common ancestor is
  `d578b9b4e1cb1464f6480cd9b81a86f332db725e`.
- R1 catalog commit `7c68af4` and pre-rewrite catalog commit `96d9604` have the same
  stable patch ID: `86b9e35fa6dd9e44c9492db039d5c05f564f7222`.
- R1 policy commit `b8160bb` and R2 policy commit `789fbd3` have different
  stable patch IDs. Similar subjects do not establish equivalent behavior.
- Patch equivalence does not establish runtime compatibility or authorize a
  merge. Inspect the complete ancestry, relevant differences, and tests first.
- The v17-default behavior is committed only on `Joint-Physical-Design`. It is
  not part of capability-neutral Router history and does not
  establish production acceptance.

These facts are reproducible with `git merge-base`, `git log --left-right`,
and `git show <revision> | git patch-id --stable`. No branch was moved,
reconciled, merged, or rewritten while recording this register.

## Updating a dependency

For each update, use the [checkpoint record](RewriteWorkflow.md#checkpoint-record)
to identify the old/new revisions, dependency type, changed contract assumptions,
affected consumers, verification, and remaining acceptance dependencies. Update
this register and link the detailed evidence from the owning pillar. Preserve
historical test inputs and snapshots; never relabel old evidence as a test of a
new combination. Mark a checkpoint integrated only after that operation and its
combined verification actually succeed.

# Rewrite readiness and dependent-branch workflow

This is the agreed development workflow for the physical-design rewrite.
It separates verified architectural progress from production acceptance.
The [dependency register](CapabilityDependencies.md) records actual capability
checkpoints; the [pillar catalog](Readme.md) and its R/N ledgers retain
requirement scope and evidence. Recording this policy does not merge branches,
certify existing work, or change runtime defaults.

## Branch roles

| Branch | Role | Admission gate |
|---|---|---|
| `main` | Stable production line; keep the known-good router protected | Explicit promotion with the required physical/runtime acceptance |
| `Router-Refactor(R10-N5)` | Existing shared rewrite integration branch; do not create a duplicate integration branch | Commit-ready changes plus the relevant cross-feature integration checks |
| Pillar/capability branches | Parallel development in separate worktrees; umbrella branches may contain multiple bounded checkpoints | Declared scope, dependencies, tests, and known gaps |

`main` and `Router-Refactor(R10-N5)` share the base checkout at
`/mnt/Projects/RedstoneCompiler`, one checked out at a time; neither owns a
feature bucket. Use the [five dependency-aligned buckets](WorktreeBuckets.md)
for primary R/N ownership, worktree locations, and shared-file coordination.

Integration means compatible, verified development progress, not a finished
router. A correctly classified missing experimental capability can remain open
on integration. Production acceptance remains required before promotion to
`main`. Changing an experimental branch's default strategy is a separate,
explicit behavior decision, not an implication of this workflow.

## Three readiness decisions

| Decision | Required evidence | What it does not establish |
|---|---|---|
| Commit-ready | A coherent architectural promise; scoped correctness and regression checks; reviewed ownership/dependency changes; classified limitations | Complete pillar or full-router acceptance |
| Capability-proven | The claimed capability works through real production consumers on controlled problems, with independent correctness checks | Large-circuit coverage or unrelated capabilities |
| Promotion-ready | Required full acceptance matrix, physical truth, MCHPRS/Fabric, provenance, and operational/performance gates | Permission to bypass approval or rewrite history |

These are Git/development readiness decisions. They do not replace the R/N
statuses `Target`, `Implemented`, `Accepted`, `Inherited failure`, and `Not-run`,
and are not N1's runtime commit-eligibility axis for accepted physical state.
A WIP checkpoint can preserve unfinished work on a feature branch, but must be
labeled as such and is not thereby ready for integration.

### Commit-readiness review

1. State the architectural promise, supported domain, explicit non-goals, and
   forbidden behavior before implementation. A passing circuit must not justify
   reintroducing cross-stage mutation, private physical rules, or legacy fallback.
2. Establish soundness within that domain. Every feasible result must satisfy
   its claims; unsatisfiability needs a complete scoped proof. Work exhaustion,
   stale input, and missing capabilities must not manufacture a negative proof.
3. Test the applicable boundary: independent small oracles, corruption and
   mutation checks, deterministic input/completion permutations, resource
   conflicts, and work/deadline exhaustion. Test bounds without weakening them.
4. For a consumer migration or integration claim, exercise the real changed
   producer/consumer path on a small controlled problem. Mocking the boundary
   away, copying matching fingerprints, or choosing a fixture that bypasses the
   stage does not establish integration. A serialization-only commit need not
   claim a production-path migration it does not perform.
5. Run the relevant structural and deterministic regression gates from
   [Running tests](../Testing/RunningTests.md), including native rebuild/parity
   checks when applicable. Preserve a separately testable protected stable path.
   Documentation-only changes require link/content and diff checks; unrelated
   runtime acceptance need not be rerun for prose changes.
6. Record the exact source/dependency revisions, commands, results, evidence,
   and remaining gaps. Scope every R/N claim; test counts alone are not an
   architectural proof.

For the access handoff, the middle-layer test should use the smallest legal
placed problem that actually traverses placement, access fabric, raw assignment,
detailed routing, and compaction. Observe the real selected geometry and stage
execution; require no regeneration/leaks and final physical legality. Use
MCHPRS and Fabric where the claimed physical behavior requires those backends.
Do not weaken their policy or replace their required evidence with unit tests.

### Classify integration failures

| Observation | Readiness consequence |
|---|---|
| False success, invalid accepted output, stale commitment, false proof, forbidden fallback, or violated architectural boundary | Blocks readiness, even if benchmark pass counts improve |
| New regression in the capability's declared supported scope or protected stable behavior | Blocks readiness until fixed or the architectural scope is explicitly reconsidered |
| Identified missing capability outside the claimed scope | May remain open on the experimental path with a tracked dependency; blocks the corresponding acceptance claim |
| Reproduced inherited failure outside the change's scope | Does not automatically block the commit; inheritance is attribution, not proof of harmlessness |
| Unexplained new failure | Investigate before claiming verified readiness |

Keep full-circuit runs non-fail-fast and retain every outcome. They diagnose
integration coverage and gate promotion; they are not the architecture
specification. Do not compare an early failure's speed with a successful route.
Contractual execution bounds still apply during a rewrite; label overruns and
missing phases honestly rather than waiving them as experimental noise.

Milestone exit criteria remain binding for claiming that milestone accepted.
They are not a blanket ban on developing its prerequisite capabilities in a
later phase or another pillar. Record that dependency and agree the scope
explicitly; do not silently enable deferred behavior or retroactively relabel
an unmet milestone as complete.

## Parallel dependency workflow

Pillars organize goals, not isolated code silos. Use bounded capabilities as
integration units, including cross-pillar changes when ownership requires it.

1. Normally branch from a recorded integration checkpoint. If a prerequisite
   is not integrated, a dependent branch may start from its tested commit.
   Record the exact revision and required contract, not just a moving branch tip.
2. Distinguish code dependencies (implementation needed now), interface
   dependencies (development can use an agreed contract), and acceptance
   dependencies (implementation can proceed but promotion must wait).
3. Establish shared contracts once where practical, then develop producers and
   consumers in parallel. If capabilities need each other, extract a shared
   prerequisite or implement one bounded joint slice; avoid circular stacks.
4. Give each active task a branch/worktree, scope, assignee, and primary code
   areas. Record overlaps in central contracts, policy, and orchestration before
   competing edits. Primary ownership coordinates changes; it is not a ban on
   legitimate cross-cutting work. Never consume another task's uncommitted files.
5. At a dependency update, inspect interface/semantic changes, advance the
   checkpoint deliberately, and run the producer's contract tests plus the
   relevant real producer-to-consumer integration tests. Record the tested
   revision combination; the existence of two green independent suites is not
   compatibility evidence.
6. Prepare integration in a dedicated clean worktree using approved committed
   inputs. Resolve conflicts according to the target contracts, run combined
   gates, and publish the resulting checkpoint only after verification. Failed
   trial combinations remain explicit diagnostics, not the shared baseline.
7. Integrate coherent checkpoints frequently; do not wait for an entire pillar
   to be production-ready. Dependents can then advance to the shared integration
   checkpoint and record the new base.

## History, authority, and evidence

- Prefer new scoped commits and ordinary merges for shared history. Do not amend
  or rebase a checkpoint other branches or evidence depend on. Rewriting private
  history requires explicit agreement; preserve already-recorded identities.
- Avoid blanket cherry-picking of shared prerequisites. Reconcile ancestry,
  patch equivalence, and semantic differences before combining existing branches.
- A task may prepare changes and report readiness; this policy does not grant
  automatic commit, merge, push, deletion, or production-promotion authority.
  Follow the user's authorization for the actual operation and target.
- Clean status is not readiness. Preserve unrelated staged/dirty work, keep
  generated runtime/evidence ignored, and use WIP checkpoints honestly when
  unfinished work needs a durable record.
- Keep requirements and dependencies in documentation, not new requirement-
  tracking fields in acceptance manifests or snapshot schemas. Retain raw
  evidence under the existing artifact/archive rules and link it from records.

## Checkpoint record

Use the [register](CapabilityDependencies.md) for dependency relationships and
the owning pillar's notes/history for evidence details. Reference the canonical
entry rather than maintaining duplicate dependency lists.

```text
Capability / branch / task assignee:
Architectural promise and primary code areas:
Supported scope and forbidden behavior:
Starting revision and required dependency checkpoints:
Dependency type; contracts consumed and provided:
R/N requirements affected (scoped claims):
Independent correctness evidence:
Real production consumers and cross-feature tests:
Protected existing behavior:
Known failures, missing capabilities, and acceptance dependencies:
Exact commands, results, and evidence locations:
Readiness decision and review rationale:
Integrated revision, when actually integrated:
```

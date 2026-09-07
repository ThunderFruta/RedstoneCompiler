# Capability and branch dependency register

This is the canonical development-dependency register for the
[rewrite workflow](RewriteWorkflow.md). R/N ledgers remain the source of truth
for requirement claims. Exact checkpoints identify tested inputs; branch names
identify ongoing work. An entry is not merge approval or production acceptance.

## Worktree buckets

The [bucket map](WorktreeBuckets.md) is the canonical assignment of all ten R
and six N requirements to five primary workstreams, including exact worktree
paths and shared-file boundaries. `main` remains in the protected base checkout
at `/mnt/Projects/RedstoneCompiler`. `Router-Refactor(R10-N5)` uses the permanent
integration worktree at
`/mnt/Projects/RedstoneCompiler-Worktrees/Router-Integration/RedstoneCompiler`.
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
| `R10-N2-Current-Selected-Access-Validation` | `Physical-Rules`; placement-access contract, catalog identity and live validator | Reviewed producer `36413d442d989746d08a7b56c55c1018e7e6866f`, parent `e8ff123128913ba1846b7f97f18b6d428e1f19ef`, integrated as Router `0918c179741707d0b9c52a728ee85a514d89abde`; state population extended by the next row | Immutable typed re-attestation of supplied current terminals, graph semantics, technology, frozen wires and selected witness/solve; resource-model-v2 includes canonical finite block states; drift cannot publish Verified | Exact seven producer blobs; 81 focused, 8 existing envelope replay, 7 structural/schema; full 1,809 passed / 4 skipped / 378 subtests; seven MCHPRS fixture vectors passed | Narrow supplied-graph consistency only. Joint envelope and local graph consumer migration, reuse authority and full acceptance remain separate |
| `R10-N2-Placed-Template-Routing-States` | `Physical-Rules`; public placed-template state construction and graph semantics | Reviewed producer `86d1ee5f05684b7fc042d704c8b772847ecddd12`, parent `36413d442d989746d08a7b56c55c1018e7e6866f`, integrated as Router `e6dca965f2ee944120d57ea0c4db98acb56fcf2c` | Public routing-resource construction populates canonical transformed template states, including explicit air; routing-resource-graph-v3 and current-access validation retain state-sensitive identity | Ten exact producer blobs plus one Reuse compatibility test; 90 Physical, 16 Reuse with 42 subtests, 8 envelope, 7 structural; full 1,988 passed / 4 skipped / 378 subtests; seven MCHPRS fixture vectors passed | Declared placed-template coverage only. Joint local adoption is delivered by the next row; current-access envelope, full world coverage, reuse authority and combined acceptance remain open |
| `R10-N2-Supported-Stair-Mchprs-Fixture` | `Physical-Rules`; declarative fixture, checker/observer regression and owner notes | Reviewed producer `61bd602acd3583f93cd23faa883aa034282712c4`, parent `86d1ee5f05684b7fc042d704c8b772847ecddd12`, integrated as Router `d1395ca31585f0803b6c838d7ec207f70200cb23` | Literal supported clear +X/+Y stair has exact static geometry and two fresh MCHPRS Boolean transfer observations; expected-answer mutation cannot alter checker or observer | Seven exact producer blobs; Router 40 source/contracts, 5 observer, 9 fixture vectors, 7 structural; full 1,992 passed / 4 skipped / 378 subtests on unchanged combined Router native | No production/API change. Only lower-root power is measured; upper analog power, blocked stair, general strength/timing/ownership, Fabric and full R10/N2 acceptance remain unproved |
| `R10-N2-Blocked-Headroom-Fixture` | `Physical-Rules`; declarative fixture and observer provenance | Producer `73a8c653bc988799771b9adc524b1e8bb99920e6` plus correction `af4984b28025d45a53f82895f8ed55a71ef5f1bc`, integrated as Router `111de5b63c0cf29a9d4a6c851a4c14aa18acb9d4` | Literal all-stone blocked stair issues no route claim; aggregate Legal reflects the removed edge. Fresh Boolean/root observations are independent of expectations; native provenance is checked against actual imported bytes | Seven exact producer blobs; fresh Terra integration PASS; 42 source, 6 observer, 7 structural, 11 cases across 5 definitions; full 1,995 passed / 4 skipped / 378 subtests | Scoped fixture conformance only; no upper analog, general strength/timing, Fabric or full R10/N2 acceptance |
| `R10-N2-DustStrength-Boundary-Fixtures` | `Physical-Rules`; literal at-limit and beyond-limit fixture conformance | Producer `3de1dbf9ee78c6ab9e84832422a7f385296aed98`, parent `af4984b28025d45a53f82895f8ed55a71ef5f1bc`; Router intake pending | F15 transfers Boolean on to the endpoint, F16 does not; both off transitions retain false, with independently observed root power | Ten exact owner paths; independent review and parent authentication passed; 44 source, 7 observer, 15 cases across 7 definitions; source-bound full 1,572 passed / 4 skipped / 257 subtests | Finite literal Boolean endpoint/root observations only; overlapping later full runs/JUnit are functional corroboration, with no performance or general physical/Fabric acceptance claim |

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
- The v17-default behavior enters Router only through the exact reviewed
  `c6d6a81` dependency checkpoint. It remains an experimental development
  default and does not establish production acceptance.

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

## 2026-09-07 physical fixture checkpoints integrated into Router

Physical producer `8461a27c0ce184e9c715cf94c1f16dedfdd15228` adds the bounded
fixture checker, isolated expectation-free MCHPRS observer, declarative inputs,
and conformance harness. Its child `e9a4cdc34b14dc14aab24bb1c5040a4bc5ca9af1`
separately commits the reviewed clear/solid-headroom stair test and owning notes.
The combined Physical tree is `7eea68f484989393963e0beb85d288de5fa22ab1`;
it matches the frozen combined source used for its final verification. Earlier
verification with the stair candidate present is not a rerun of the intermediate
27-path-only producer tree.

Both exact commits were integrated by scoped cherry-pick as Router
`f2a4b1d7764d43354a99f6a1206d14e014ab0fc8`, parent
`6c0a9aad6b6c67df3e6eb1c99d8490a573137b19`, tree
`52099dc00775a3249bd005c7c944d2a15edf2718`. All 28 changed file blobs match
Physical `e9a4cdc` exactly. The combined patch SHA-256 is
`d886dc13512808848cfad4314628167a082eafd5372656c7cc262ee4cd6d8875`.
The existing aggregate MCHPRS validator and selected-access semantics remain
unchanged, and no Cargo dependency or MCHPRS pin changed. The pinned engine is
`fe2172101e0f025afc0760f4920e0631c4854880`.

Fresh Router verification passed Rust formatting, 51 release tests, and the
release Python-extension build. The actually imported library, release artifact,
and Physical producer library all have SHA-256
`c5938ff83eadb6a814ecd430d24687687689db439025855a714855adfe099fc2`.
The combined focused set passed 64 tests and 5 subtests; structural/schema checks
passed 7 tests; all 7 declarative fixture cases passed. Collection found 1,730
tests, and the full deterministic suite passed 1,726 tests with 4 expected skips
and 271 subtests in 73.72 seconds. Independent integration review found no
material findings, and post-commit readback confirmed all hashes and clean status.
Exact commands, raw output, fixture receipts, identities, review and checksums
are retained under `Output/PortfolioGoal/PhysicalToRouter/`.

Readiness is integrated bounded R10/N2 infrastructure and ordinary stair
conformance. Electrical conformance is conditional on independently observed
root powers of exactly 0 or 15 in the supported flat transfer domain. General
device truth, model-derived timing, cross-net ownership, stateful behavior,
wall-torch headroom, live Fabric, scale routing, and full physical acceptance
remain unproved. No producer or consumer may infer those claims from the fixture
pass count. Protected `main` remains
`193e2838050ee111245b5431484ad44112b26156`; no push or promotion occurred.
Recovery uses a scoped revert of the Router integration, retaining source-bound
evidence and both original producer commits.

## 2026-09-07 placed-template routing state producer integrated into Router

Physical producer `86d1ee5f05684b7fc042d704c8b772847ecddd12`, parent
`36413d442d989746d08a7b56c55c1018e7e6866f`, tree
`5fab03263354af494151127667a64b5a604d0dd6`, was integrated as Router
`e6dca965f2ee944120d57ea0c4db98acb56fcf2c`, parent
`fabd1729b93a3640bd6438905e1989b48514e108`, tree
`420355b2132a5ba2a84655ed1475551a7ea5f359`. The eleven-path commit contains
ten exact producer blobs and one Reuse-owned test compatibility correction;
its binary patch SHA-256 is
`0cfa41732650aaec01187a23ac6c245b459c2f5ff96740eba9b3e308630a9c1e`,
and its full-index encoding is
`2673a9b3f670ebffa3deaba34ea8c280bd1305debe093b2633e02ec266eb6be1`.
The exact path list and producer/test identities are retained in
`IntegratedSourceIdentity.json` under the evidence root below.

The public placed-template snapshot and `BuildRoutingResources` populate
canonical states after template placement and transformation, including explicit
air entries without treating them as occupied blocks. Resource graph semantics
are versioned as `routing-resource-graph-v3`; the current selected-access
validator consumes the same state-sensitive model. The Reuse test correction
preserves eager public errors for malformed recursive template content after
the Physical path began rejecting that content earlier. It changes no cache
production code and does not weaken the Physical input contract.

Fresh independent Router integration and Reuse test reviews passed. All ten
planned commands passed: imports/native identity, compilation, whitespace,
90 Physical tests, 16 Reuse tests with 42 subtests, 8 existing envelope replay
tests, 7 structural/schema tests, collection of 1,992 tests, seven MCHPRS
vectors across three fixture definitions, and the full deterministic suite
with 1,988 passed, 4 skipped and 378 subtests in 181.98 seconds. The existing
Router native artifact remains
`d1b5cbda5ede49428cebf47af342b6153aa3d862310317c38a4aaf212ad85d1f`;
no native rebuild, Fabric, scale or full routed acceptance was performed.

Evidence is retained at
`Output/PortfolioGoal/PhysicalPlacedStateToRouter/Integration-CorrectedReuse/`.
The original failed ten-path integration remains separately preserved in
`../Integration/`. The corrected candidate's 63-entry precommit seal is
unchanged; the post-commit 65-entry manifest SHA-256 is
`ec68c3fc45b7e5f25609aaa522be9a3c69ee5f18d604c6cf298ba7e8d6df3f7c`.
Readback confirmed the expected tree, eleven paths and a clean Router checkout.
All five capability coordinators received the immutable checkpoint.

This provides declared placed-template states in the public global resource
path. Joint's eighteen-path source and local-consumer candidate is separately
reviewed but uncommitted at this record; it must receive its own exact commit
and integration record. A current-access envelope, Ready lifecycle, three-point
revalidation, Runtime caller migration, full world coverage, certified reuse
and combined acceptance are not established by this integration. Recovery is
a scoped local revert of `e6dca965`; protected `main` remains
`193e2838050ee111245b5431484ad44112b26156`, and no push occurred.

## 2026-09-07 supported-stair fixture integrated into Router

Router consumed all seven exact Physical `61bd602a` fixture/test/note blobs as
`d1395ca31585f0803b6c838d7ec207f70200cb23`, parent
`64a328e67683da71951ad24e0b08b9405d1a5b7a`, tree
`b167f36211247c42818a88bba0b4e4d6e255e7f2`. Every preimage matched the
producer parent; there was no source adaptation. The 413-addition binary patch
is `66d328b8dfa12cceab86a4aa7cb6947c07cd12230f7f569f3f8914c85d4f5105`;
its full-index encoding is
`8115e6160d7a091fd597916b8922fafc2265639881b06ca5ca6deffb13db4270`.

Fresh independent integration review passed against the unchanged source and
Router's actual combined native `d1b5cbda`. All nine commands passed:
imports/native identity, compilation, whitespace, 40 source/checker/contracts
tests, 5 real observer tests, 7 structural/schema tests, collection of 1,996
tests, all nine MCHPRS vectors across four fixture definitions, and the full
deterministic suite with 1,992 passed, 4 skipped and 378 subtests in 186.86
seconds. The expected tree, all seven exact producer blobs and clean checkout
were confirmed after commit. No native rebuild, Fabric, scale or routed
acceptance matrix was run.

The exact commands, source manifests, raw observations, JUnit, independent
review and commit receipt are retained under
`Output/PortfolioGoal/PhysicalSupportedStair/Integration/`. The 67-entry
precommit seal is preserved; the 69-entry final manifest SHA-256 is
`3a9255febdc46c3ee7413b9d2fedbd52ae969e86f818900a34bf2617e3cfc873`.
This supersedes the earlier pending Router fixture intake status only.
Supported-clear P0 geometry, logical Y and lower-root power are proven for the
literal fixture; upper analog power, blocked N0, general device behavior and
full R10/N2 acceptance remain outside the claim. All five coordinators received
the immutable checkpoint. Recovery is a scoped local revert of `d1395ca3`;
protected `main` remains `193e2838050ee111245b5431484ad44112b26156` with no push.


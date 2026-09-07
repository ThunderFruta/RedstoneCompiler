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

## 2026-09-06 reviewed Physical-to-Joint checkpoint

The reviewed and verified Joint checkpoint is merge commit
`2902d1dab52ea5681bc9ecc531045ff5179d1165`, whose ordered parents are exact
Joint revision `46775f776b71d148fd261e86df8c6c59ec4edd10` and Physical revision
`f23293a18487cde6638c8b05be18ebd71afc0d30`. Their merge base is
`2f69160a85f1ac800243698aebec4f172164b9f8`. Physical `f23293a` is the direct
child of original selected-access pin
`1d9e8995d0fed2ed1afa6f2eb6b20524aa8a89ab`; its six-file binary patch digest
is `21cf686b16ce2081c110efdf6d4db7555317f0c82992569cddcaca5b7ff65d31`.
Independent review closed the typed-error and regeneration-observation findings,
independently reran the focused consumer coverage, and admitted the local merge
commit without replacing either exact tested input revision.

The candidate combines Physical's complete selected-terminal, technology,
resource-model, and stale supplied-resource-graph checks with Joint's whole
selected paths, immutable preparation observations, candidate-specific
envelopes, and coordinator-only commitment. Joint rebuilds the routing-resource
graph from the current placed state at both pre-route fabric construction and
raw descriptor materialization. It calls Physical's public binding and identity
helpers in a narrow preflight and translates only those explicit failures to
`ClusterInterfaceInvariantViolation` at `PlacementPinAccessHandoff` or
`PlacementAccessFabricHandoff`. Ordinary fabric-construction `ValueError`
continues to propagate unchanged for enabled and disabled selected access.

The live `1d9e899` checkpoint and historical `dc95e349` checkpoint have the
same parent and identical `PhysicalDesign/` and `Tests/` content; their only
differences are the four Physical R10/N2 history and notes files. The binary
patch digest `411ba33bf1edeea565d4aafeea99be2efd01e5ea403acf090ff7a34f8cde856e`
belongs to the original `dc95e349` patch. It is not the digest of the later
expanded `1d9e899` commit or its `f23293a` correction.

Current candidate evidence includes the public unchanged fanout route plus
moved-terminal, same-terminal changed-face, selected-claim foreign-wire, and
changed-technology rejections; the exact raw materializer is also challenged
with a foreign wire derived from an actual selected electrical claim. Guards
at witness construction, catalog enumeration, and solving allow legitimate
pre-mutation planning but reject any post-mutation regeneration. The integration
module passed 8 tests, the three affected Physical suites passed 63 tests, the
combined handoff/fingerprint/fanout set passed 69 tests, and the structural/
schema gate passed 7 tests. Collection found 1,551 tests. The full non-scale
suite retained exactly the inherited pre-Telemetry provenance failure and
otherwise passed 1,546 tests with 4 skips and 257 subtests; evidence is under
`Output/Pytest/20260906T133932.283110Z-P2/`.

This Physical-to-Joint checkpoint is locally committed and independently
verified. That establishes the tested dependency combination and controlled
consumer behavior, not full R2 capability or production acceptance. Its
inherited provenance failure is historical checkpoint evidence and must not be
attributed to the later Telemetry combination.

## Capability checkpoints

| ID / capability | Provider and primary code owner | Required checkpoint / relationship | Contract provided | Readiness and evidence | Remaining dependency or action |
|---|---|---|---|---|---|
| `Shared-Access-Catalog` | `Physical-Rules`; catalog, realization, proof hardening, and domain query | Explicitly merged into `Joint-Physical-Design` before its consumer checkpoint | Typed physical templates, realization/legality, exact claims, codecs, domain construction, and proof identities | Extracted from pre-split `96d9604`, `1fb7db6`, and `2024d7d`; see the Physical R10/N2 history | Preserve Physical-Rules authority and avoid private or duplicate legality implementations |
| `Selected-Straight-Access` | `Joint-Physical-Design`; policy and placement/routing consumer | Code dependency on the Physical-Rules access checkpoint | Placement/orchestration selects one option; global and detailed routing consume its immutable identity | Extracted from pre-split `789fbd3` and `1fb7db6`; [R2 ledger](R/R2/Notes.md#stage-1-conformance-ledger) | Real end-to-end handoff and larger integration coverage remain separate gates |
| `Access-Transport-Handoff` | `Joint-Physical-Design`, consuming the Physical-Rules proof/domain checkpoint | Code dependency on `Selected-Straight-Access` | Joint result/candidate transport and five-stage commitment validation over Physical-owned proof codecs and domain queries | Historical evidence was captured at pre-split `2024d7d`; snapshot ownership is `Telemetry-And-Acceptance` | Demonstrate a small real five-stage production path; classify dependent capabilities independently of full-matrix acceptance |
| `R1-Shared-Prerequisites` | R1 `22d112f6aea02ab7b995b562230f971ab08119e2`; global routing, with shared contracts/policy prerequisites | Existing parallel history, not a new dependency stack on R2 | [R1 history](R/R1/CommitHistory.md) records prerequisite/supporting work, not completed R1 behavior | History reconciliation completed at `0cbb17916fe3239adee04762c0fddd7cea3d2a4c`; no R1/R5/N6 implementation claim | Keep Reuse consumer integration deferred until a concrete certified-salvage adapter and tested dependency checkpoint exist |
| `R1-Exact-Current-Static-Geometry` | `Reuse-And-Salvage`; `BuildRoutingResources` and its fork consumer boundary | Reviewed producer `75a62f87d2df127c46c08ee27b892e59432fb735`, integrated by scoped cherry-pick as Router `4c9bfdb0e9119f5bbc88a36b6e663a4b960d9842` | Within one unchanged eager placement lineage, siblings share immutable `RoutingStaticGeometry` sets and the same current-lineage mutable resource graph; its private pure region/claim memoization is intentionally graph-owned and shared, while every other top-level field starts at its dataclass default and populated native/proof/portal/candidate/assignment/prepared state is not inherited | Independent nonempty public-template transform, frozen-wire, region/claim, and populated-state mutation-isolation coverage; observed current `WorkCheck` output is retained only as diagnostic evidence | Integrated conformance only; deferred expansion, quantitative copying reduction, cross-placement or topology reuse, R5, N6, physical acceptance, and promotion readiness remain unproved |
| `R1-Portfolio-Oriented-Geometry` | `Reuse-And-Salvage`; Placement Engine cache, packed-cluster portfolio and conflict consumer | Reviewed producer `10fafc5c30cef12817e6bb81584be263a77bd6b8`, parent `75a62f87d2df127c46c08ee27b892e59432fb735`, integrated as Router `30dfda0dd95040c57eb18df14c46b1a8b724e75e`; consumes the public Physical producer/technology without changing them; patches `23537ac8` and `0aaed23b` are rejected history only | The complete deterministic macro/technology/schema value plus exact key/class/Size/full Blocks/state for every loaded template and exact loaded mapping/object generation bind every process-global portfolio key before lookup. Extra templates are permitted but never ignored. Cached and eager portfolio misses use identity-attesting current resolvers that bypass legacy transform-only geometry/exclusion/access LRUs; only attested results may publish. A true same-context global hit performs zero oriented work. The cached resolver stores four immutable relative masks and returns a strict non-semantic receipt | Source-bound reconvergent Joint controls cover in-place extra-template content, reversed-mode all-air NAND change without clearing global/helper LRUs, current eager/cached parity, current AccessLength failure, exact raw/state/index/objective/fingerprint/materialization/final validation, and restored zero-work hits; prior macro/template/schema/technology/recursive/receipt/full-domain evidence is reverified at `Output/ReuseBatch3/20260907T055754Z/` | Bounded reconstruction reuse only. No route/proof/ownership laziness, topology reuse, stale salvage, coordinator migration, wall-time/copy-volume claim, independent acceptance, or promotion readiness |
| `R10-N2-Current-Selected-Access-Validation` | `Physical-Rules`; placement-access contract, catalog identity and live validator | Reviewed producer `36413d442d989746d08a7b56c55c1018e7e6866f`, parent `e8ff123128913ba1846b7f97f18b6d428e1f19ef`, integrated as Router `0918c179741707d0b9c52a728ee85a514d89abde`; state population extended by the next row | Immutable typed re-attestation of supplied current terminals, graph semantics, technology, frozen wires and selected witness/solve; resource-model-v2 includes canonical finite block states; drift cannot publish Verified | Exact seven producer blobs; 81 focused, 8 existing envelope replay, 7 structural/schema; full 1,809 passed / 4 skipped / 378 subtests; seven MCHPRS fixture vectors passed | Narrow supplied-graph consistency only. Joint envelope and local graph consumer migration, reuse authority and full acceptance remain separate |
| `R10-N2-Placed-Template-Routing-States` | `Physical-Rules`; public placed-template state construction and graph semantics | Reviewed producer `86d1ee5f05684b7fc042d704c8b772847ecddd12`, parent `36413d442d989746d08a7b56c55c1018e7e6866f`, integrated as Router `e6dca965f2ee944120d57ea0c4db98acb56fcf2c` | Public routing-resource construction populates canonical transformed template states, including explicit air; routing-resource-graph-v3 and current-access validation retain state-sensitive identity | Ten exact producer blobs plus one Reuse compatibility test; 90 Physical, 16 Reuse with 42 subtests, 8 envelope, 7 structural; full 1,988 passed / 4 skipped / 378 subtests; seven MCHPRS fixture vectors passed | Declared placed-template coverage only. Joint local adoption is delivered by the next row; current-access envelope, full world coverage, reuse authority and combined acceptance remain open |
| `R2-Current-Template-State-Local-Consumer` | `Joint-Physical-Design`; committed local routing resource construction | Joint `6fac0892f148cf4f4b0641cd72c3df775a0f62a4`, parent `567f30a32a927e29309a9851aa1a8d9f0b8b48c6`; local consumer integrated as Router `234b8dc65e65427a0147bd90ee727179e86e5e51` | Actual local routing uses public BuildRoutingResources with current placement, technology and work checks; transformed states, explicit air and frozen-wire effects reach its graph | Joint: 15 exact Physical prerequisites plus three owned paths, full 1,767 passed / 4 skipped / 271 subtests. Router: two exact code/test blobs plus preserving R2 note, 68 focused with 14 subtests, full 1,989 passed / 4 skipped / 378 subtests | Source and local state adoption only. Current envelope, Ready lifecycle, three-point revalidation, native caller/emission, reuse authority and combined acceptance remain separate |
| `R10-N2-Supported-Stair-Mchprs-Fixture` | `Physical-Rules`; declarative fixture, checker/observer regression and owner notes | Reviewed producer `61bd602acd3583f93cd23faa883aa034282712c4`, parent `86d1ee5f05684b7fc042d704c8b772847ecddd12`, integrated as Router `d1395ca31585f0803b6c838d7ec207f70200cb23` | Literal supported clear +X/+Y stair has exact static geometry and two fresh MCHPRS Boolean transfer observations; expected-answer mutation cannot alter checker or observer | Seven exact producer blobs; Router 40 source/contracts, 5 observer, 9 fixture vectors, 7 structural; full 1,992 passed / 4 skipped / 378 subtests on unchanged combined Router native | No production/API change. Only lower-root power is measured; upper analog power, blocked stair, general strength/timing/ownership, Fabric and full R10/N2 acceptance remain unproved |
| `R10-N2-Blocked-Headroom-Fixture` | `Physical-Rules`; declarative fixture and observer provenance | Producer `73a8c653bc988799771b9adc524b1e8bb99920e6` plus correction `af4984b28025d45a53f82895f8ed55a71ef5f1bc`, integrated as Router `111de5b63c0cf29a9d4a6c851a4c14aa18acb9d4` | Literal all-stone blocked stair issues no route claim; aggregate Legal reflects the removed edge. Fresh Boolean/root observations are independent of expectations; native provenance is checked against actual imported bytes | Seven exact producer blobs; fresh Terra integration PASS; 42 source, 6 observer, 7 structural, 11 cases across 5 definitions; full 1,995 passed / 4 skipped / 378 subtests | Scoped fixture conformance only; no upper analog, general strength/timing, Fabric or full R10/N2 acceptance |
| `R5-Construction-Generic-Claims` | `Reuse-And-Salvage`; component-net construction and completed-template cache | Reviewed producer `fe7400c971f614d2e6e1a0ece3bacd849d53c45b`, parent `10fafc5c30cef12817e6bb81584be263a77bd6b8`; integrated as Router `655ad196617343692e439ebb328e09040304b84c` | Immutable generic claims come from the solver that built each net; category/ordinal and translations are retained; current four-field equality gates warm reuse, malformed positions miss before translation, and cold errors propagate | Fresh Sol producer review plus fresh Terra Router integration PASS. Router: 139 owning, 175 adjacent with 24 subtests, 7 structural, 2,024 collected; full 2,020 passed / 4 skipped / 378 subtests. Nine exact producer blobs | Bounded supplied-graph cache safety only; no world/access/current-envelope admission, N6 or full acceptance authority |
| `R7-N1-Native-Batch-Outcomes` | `Runtime-And-Kernels`; native coarse/detailed outcome execution and proof/identity validation | Reviewed producer `cfd529e19d774e7bdf106b89628a54c8b8f3b6b4`, parent `24047bd495d73d8bb1d7613124bb13648bb0ba37`, integrated as Router `a354ed40ed532c277f227603cc05049d2d532b37` and as Joint source-only checkpoint `567f30a32a927e29309a9851aa1a8d9f0b8b48c6` | Additive v1 immutable request/receipt API; exact ordinal association, typed Found/request-scoped ProvenNoPath/Incomplete, native identities, shared route/proof expansion cap and original absolute cutoff | 22 producer-exact blobs and 2 Physical-observer-preserving facades; combined native rebuilt; 88 Rust and 1,887 Python tests passed, with 4 skips and 378 subtests; seven MCHPRS fixture vectors passed | Joint has the 24 native source paths; live identity binding and evidence emission remain separate; no persistent-worker, live-cancellation/shutdown, global infeasibility, physical acceptance or promotion claim |
| `N1-N4-Runtime-Work-Authority-Contracts` | `Runtime-And-Kernels`; work/result contracts and cleanup authority policy | Policy `fbd7c81050bb2b54c03348adeee54695b59d4474` then contract `142c288cadcad448139db63b95c193424cf23d7a`; seven full blobs integrated as Router `691daa142b5cd30e348f6c87ee07479844061cbc` | Independent N1 state axes; immutable explicit useful-work and cleanup cutoffs, exact Boolean force grant; closed authority codec without defaults or ordering policy | Full imported module and tests independently reviewed; 52 contract tests, 7 structural/schema, full 1,939 passed / 4 skipped / 378 subtests on unchanged Router native | Source contracts only. Bounded/spawned adapters, actual authority propagation, process supervision and R6/R7/N4 lifecycle behavior remain separate; Joint native import does not include this Python closure |
| `R6-N4-Owned-OneShot-Supervision` | `Runtime-And-Kernels`; explicit one-shot process and resource ownership | Producer `a264da03033120d54c05c375bbd28a0b57fdb154` plus test correction `880a1a359f5569c940eb218abeb8020c5c23bfc5`, integrated as Router `86ee2206ebd47458f541e745a7cd36c917192930` | Bounded encoded-byte transport, per-invocation result binding, explicit cancellation/force/reap/close and retained uncertain ownership; corrected test proves body entry and the same live child across the original cleanup cutoff | Exact two postimages; fresh independent Router review; single regression, 40 owning cases, 7 structural and full 2,060 passed / 5 skipped / 378 subtests; all observed resources closed without fallback | Linux/spawn supervision after Process.start only; startup bound, live caller, pools, persistent workers, full R6/N4 and physical acceptance remain open |
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

## Reuse Batch 1 interface context

Consumer: `Reuse-And-Salvage`, candidate based on
`4de7305380f91995a842e955f2b9bdedced0f61a`; common Router base
`c0aaf5f00bbba7c0aefe6733c7d1150a3bb76a1d`. These are pinned **interface**
relationships, not dependency merges or combined physical verification.

- Joint context: `96e66797ee2f957a3279d71b0985653988bceb5e`.
  `Compilation/Ir/ComponentGraph.py` blob
  `b4979c722af51fdcf89cb2a96318f6525f9fd376` is shared with the consumer.
  Normalize a chosen real component using originating module edges and explicit
  caller-ordered external signal bindings. Gate terminal indexes are semantic;
  preserve repeated edges and cross-output sharing. Extraction partition
  invariance and production caller migration are not claimed.
- Physical context: `b65847043c7ba354ea3835486df60840f9510cc2`.
  Technology identity binds the full technology; placed-model identity binds
  graph/model version, current resource inclusion, owned/unowned exclusions and
  foreign frozen wires. Ordered binding, access-domain and witness identities
  remain distinct. Selected paths, ownership, support, required air, electrical
  influence, bounds and external reservations must be completely covered.
  Newer producer builders/validators are not imported or merged into Reuse.

The [R5 candidate](R/R5/Notes.md) compares immutable topology and complete opaque
selected-access dependency declarations only. Unknown coverage is unresolved;
equal declarations do not validate physical claims or authorize reuse. Later
producer validation and coordinator integration require their own explicit
checkpoints and real boundary tests. R1 lazy expansion and N6 salvage remain
outside this candidate.

## 2026-09-07 normalized-region checkpoint integrated into Router

Reuse producer `e4609e7f96a65a437ff33c04300f1111204e4081`, parent
`4de7305380f91995a842e955f2b9bdedced0f61a`, provides the bounded R5 normalized
ordered-NAND-region and complete declared selected-access dependency contract.
It was integrated by scoped cherry-pick as Router commit
`6da79433900dd87ceb9bb5a0d6e573d9ca1073ed`, whose sole parent is
`08a713cd87d7d46797f090c523687717f0f0048a` and tree is
`997d9ab3c4923426a54705d83624a902b2545c78`. The common ancestry of the producer
and original consumer is `c0aaf5f00bbba7c0aefe6733c7d1150a3bb76a1d`.
Only the reviewed eight-file producer commit was selected; older branch-wide
cleanup variants were not replayed. The four added Python source/test blobs
are byte-identical to the producer, and the shared extraction blob remains
`b4979c722af51fdcf89cb2a96318f6525f9fd376` on both inputs. The combined patch
SHA-256 is `1470c1fc64a44f98064f0f2556ac83296b6aac66f0130e261ce87718719ab554`.

Fresh verification on that exact Router combination passed 71 owning tests,
7 structural/schema tests, compileall, and collection of 1,688 tests. The full
deterministic suite passed 1,684 tests with 4 expected skips and 271 subtests
in 73.29 seconds. Independent integration review found no material findings;
post-commit readback confirmed all eight expected file hashes and clean status.
Evidence and exact commands are retained under
`Output/PortfolioGoal/ReuseToRouter/`, including `Candidate.json`,
`FocusedCommands.json`, `FullCommand.json`, `Full.log`, `IndependentReview.json`,
`CommitReceipt.json`, and `SHA256SUMS`. The imported native library was unchanged
at SHA-256 `c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`.

This establishes integration of a bounded source contract only. The normalization
supports 1–18 scalar acyclic ordered NAND gates; dependency comparison reports
`ProducerValidation=NotPerformed` and grants no physical validity, cache reuse,
salvage, ranking, or commitment authority. No production caller migration,
R1 lazy/eager equivalence, full R5 reuse, N6 salvage, or physical/scale acceptance
is established. The earlier R5 candidate notes describe the frozen producer
verification; this entry records the later committed producer and consumer.
Protected `main` remains at `193e2838050ee111245b5431484ad44112b26156`; no push
or main promotion occurred. Recovery uses a scoped revert of the integration
commit while preserving its evidence and original producer.

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

## 2026-09-07 state-aware stair and oriented-geometry integrations

Two further reviewed producer checkpoints were consumed sequentially:

| Capability | Producer and parent | Router integration and parent | Router tree |
|---|---|---|---|
| State-aware dust-stair query, R10/N2 | `e8ff123128913ba1846b7f97f18b6d428e1f19ef`, parent `e9a4cdc34b14dc14aab24bb1c5040a4bc5ca9af1` | `d4e3e46d667c2dd8dec97da4e910f6657d044d91`, parent `ff15b3a598ba72462d39028cb6517e4cbcbdc878` | `f75741fbf511af9fa4e95e585fec8e4ab8eebd76` |
| Current oriented geometry cache, bounded R1 reconstruction | `10fafc5c30cef12817e6bb81584be263a77bd6b8`, parent `75a62f87d2df127c46c08ee27b892e59432fb735` | `30dfda0dd95040c57eb18df14c46b1a8b724e75e`, parent `d4e3e46d667c2dd8dec97da4e910f6657d044d91` | `3d4f44ee0c81771004309a15bc73c8cb8a314cbe` |

The stair integration preserves all 13 producer blobs exactly. Its patch SHA-256
is `9f2f50f215a233f5c4a020562ad96c2cb5661a8cfe363f9d90c2872f99fc9016`.
The shared query separates geometric connection, headroom classification, and
routing-claim legality. Supported clear-air stairs are legal; solid headroom
blocks connection. The independently observed exact east-facing, lit wall-torch
case remains geometrically connected but has `Unknown` routing legality because
electrical ownership is unavailable. The resource graph withholds a legal route
edge while final geometry and rendering preserve the observed connection.
Owned immutable block-state snapshots and complete canonical accepted-input
identity prevent stale caches and decision/identity collisions. Unsupported or
malformed state remains non-success; this is not general wall-torch ownership.

The geometry-cache integration changes nine paths. Eight blobs are identical to
the producer; the sole register conflict preserves Router's completed R1 rows
and adds the new producer entry with its actual committed identity. Its patch
SHA-256 is `9cef86c4b4fb5e4471dbd08503b25cc1fa6db9f78533efc02eae06d6fb42017c`.
An uncached portfolio evaluation may share immutable origin-relative actual,
electrical, solid, and explicit keep-out masks by legal orientation. Complete
macro, technology, loaded-template content and generation identity is checked
before global lookup and publication. The explicit cache-off portfolio resolver
reconstructs current geometry without legacy transform-only helper-cache hits.
Valid global hits perform zero current oriented reconstruction; strict receipts
describe bounded work against the explicit eager reference without affecting
portfolio equality, ordering, objectives, fingerprints, or final validation.

Fresh combined Router verification and independent integration review settled
against each exact patch:

| Router source | Focused / owning | Structural and collection | Full deterministic suite | Additional checks |
|---|---|---|---|---|
| `d4e3e46` | 32 focused; 68 owning with 14 subtests | 7 passed; 1,754 collected | 1,750 passed, 4 skipped, 305 subtests; 140.44 s | Compileall and all 7 vectors across 3 declarative fixtures passed |
| `30dfda0` | 16 focused with 42 subtests; 283 Placement with 66 subtests | 7 passed; 1,767 collected | 1,763 passed, 4 skipped, 347 subtests; 154.97 s | Exact scope and conflict resolution passed |

Fresh Terra/high integration reviews found no material findings. Commands, raw
logs, exact source/patch identities, full-suite XML, review, clean post-commit
receipts and checksums are retained under
`Output/PortfolioGoal/PhysicalBatch2ToRouter/` and
`Output/PortfolioGoal/ReuseBatch3ToRouter/`. The native library remained
`c5938ff83eadb6a814ecd430d24687687689db439025855a714855adfe099fc2`;
neither Python-only transfer ran a Rust rebuild/release suite or live Fabric.
These suite durations are verification observations, not performance comparisons.

Readiness is integrated bounded R10/N2 behavior and R1 reconstruction reuse.
Full model coverage, deferred final physical expansion, measured copying or
wall-time reduction, authenticated cross-placement reuse, N6 salvage, and
combined physical acceptance remain open. Typed current-access validation and
the Joint current-selection envelope are still proposed producer dependencies,
not delivered `FrozenPhysicalPlacementContract` or reuse authority.

Later adversarial Telemetry review also reproduced source-directory replacement
and symlink traversal in legacy archive publication. The R8/N5 checkpoint above
retains its original source-bound verification, with this additional known
limitation. The separate descriptor-observation/snapshot correction is not yet
an integrated checkpoint; no race-free archive claim is made for current Router.
The latest retained seven-case matrix remains 0 passed / 7 failed / 0 skipped.

Protected `main` remains `193e2838050ee111245b5431484ad44112b26156`. No push or
promotion occurred. Recovery uses a scoped revert of the relevant integration
while preserving the original producers and their distinct sealed evidence.


## 2026-09-07 bounded native batch outcomes integrated with the Physical observer

Runtime producer `cfd529e19d774e7bdf106b89628a54c8b8f3b6b4`, parent
`24047bd495d73d8bb1d7613124bb13648bb0ba37`, tree
`b968e058a26bd20890fcacd7b1ba990ac4836d1a`, was integrated as Router
`a354ed40ed532c277f227603cc05049d2d532b37`, parent
`b6eccc778fb42677a23d437a19a5cbbda305e0f4`, tree
`edd3248aeecfd28440e482d9f3c722f3d155f006`. Their merge base is
`c0aaf5f00bbba7c0aefe6733c7d1150a3bb76a1d`. The canonical producer patch is
`c737a5f35be89737e49535f501ca9b3ffbce9dbf667db9ffc81cd979cc3c32ae`;
the reviewed ordered five-part patch is
`ded71f4f14886b858899167f80f2ddf04f451e1fa58a0b74c2fc3e0b9ca4cf67`.
The Router patch is
`153753fa69a38114c0f44808a164bde886c18f5e4084711e7925a2c0e2546fc6`.
All 24 intended paths were transferred. Twenty-two blobs are producer-exact;
the Python bindings and stub carry the exact Runtime logical changes while
preserving Physical's observation exports. Physical's observer implementation
and module remain unchanged. At this integration boundary, the seven inherited Runtime authority/policy files
were separate uncommitted owner work and were not imported by `a354ed4`.
Their subsequent committed contract integration is recorded below.

The additive `native-route-batch-outcomes/v1` coarse and detailed entrypoints
accept an exact built-in batch string, immutable request tuple and one absolute
monotonic cutoff. Receipts preserve original ordinal association and duplicate
request IDs. `Found` is a validated native candidate with no commitment authority.
`ProvenNoPath` requires a complete closed relaxed-connectivity proof for one exact
admitted request/domain; it does not prove global placement infeasibility.
`Incomplete` carries no candidate or proof. Native canonical JSON/digest pairs
and explicit availability states preserve authentic completed work and prevent
unavailable or contradictory evidence from acquiring claim authority.
Route and proof share the exact `ExpandedSearchState` cap; other validation and
identity work uses the same cutoff but is not an expansion unit. Cancellation
is a captured pre-start request, not a live cancellation or shutdown protocol.
Caller-echo strings remain supplied bindings which Joint must validate against
its actual current objects.

Independent Runtime source review and Joint's exact source-contract suitability
decision passed. The latter is retained with SHA-256
`71fad2b90a97de773f056b3b84a8b7ae2463b54432fbd811914b437e1c13591f`.
Producer evidence includes 86 Rust tests, 78 native Python tests, 219 routing
tests with 29 subtests, and an outcome-only scratch run of 86 Rust and 297 Python
tests with 29 subtests. Its full 1,560-pass/4-skip/257-subtest result includes
the seven protected inherited changes; full outcome-only scratch pytest was
not run. These source-specific evidence scopes remain distinct.

Router rebuilt the combined source using its resolved Cargo target directory.
The release artifact and actual imported extension both have SHA-256
`d1b5cbda5ede49428cebf47af342b6153aa3d862310317c38a4aaf212ad85d1f`.
Both outcome entrypoints and `ObserveMchprsFixture`/`ValidateMchprsFixture` were
verified. The Runtime-only `36c7d562` native remains separate producer evidence.
All 16 Router commands passed: formatting/build, 88 Rust tests, 78 native outcome
tests, 221 routing tests with 29 subtests, 44 observer tests with 5 subtests,
8 existing envelope tests, 7 structural/schema tests, and 1,891 collected tests.
The full deterministic suite passed 1,887 tests with 4 skips and 378 subtests
in 176.11 seconds. Seven vectors across three fixtures passed fresh MCHPRS
observations on the combined native. Fresh Terra/high integration review found
no material finding, and source hashes remained unchanged throughout verification.

Exact commands, raw output, full-suite XML, build inputs, native publication and
import identities, fixture observations, review and clean commit receipt are
retained in 91 sealed artifacts under
`Output/PortfolioGoal/RuntimeOutcomesToRouter/Integration/`. This establishes
the combined bounded native source contract. Joint's actual source consumption,
current Physical binding/CommitRouting migration and deterministic success/failure
receipt emission remain subsequent consumer work; Telemetry projection depends
on that real emitted record. Each later consumer must retain its own exact
source and rebuilt native identity, not reuse the Router hash as a constant.
No full R6/N4 lifecycle, Joint routing capability, new seven-case acceptance
matrix, live Fabric, scale, performance or promotion result is claimed.
Protected `main` remains `193e2838050ee111245b5431484ad44112b26156`; no push
occurred. Recovery uses a scoped integration revert followed by rebuild and
import verification of the restored combined source.

## 2026-09-07 Runtime authority contracts and Joint native source consumption

Runtime committed two independently reviewed prerequisite scopes, in order:

- `fbd7c81050bb2b54c03348adeee54695b59d4474`, parent
  `cfd529e19d774e7bdf106b89628a54c8b8f3b6b4`, tree
  `0da368135d55cee5c163d88f9ff2d7ad9b09c7d8`: five policy documents define
  distinct useful-work and cleanup authority, explicit force permission, and
  capacity release only after actual worker exit. Its binary patch is
  `173498a984a109e44d98c0aa82992ca2781416325a3ab9021a9df805484849a1`.
- `142c288cadcad448139db63b95c193424cf23d7a`, parent the actual first commit,
  tree `02594e516e3572f2e50a7d27ff07154de203cd3d`: the Runtime contract,
  owning tests and factual N4 note. Its relative binary patch is
  `80b7270292488453ab701eee98d90791f4cfa633bd8e02a0383229e0516c98b3`.

The producer's combined binary patch is
`181c998c4a6d0f3690bce7d0fa3d7a9ad3939c61c09200065fd54b44d5985d9f`.
Both local commits were authenticated after execution. Producer verification
retains 85 focused tests, 7 structural checks, 1,569 collected tests and a full
result of 1,565 passed, 4 skipped and 257 subtests in 62.31 seconds. Fresh
independent review repeated the focused and structural checks. Native
`36c7d562` was unchanged; no native rebuild or caller/lifecycle capability is
claimed. The original numeric-conversion rejection evidence and the later
negative-adjacent-float/custom-numeric regression coverage remain distinct.

Router imported seven complete producer blobs as
`691daa142b5cd30e348f6c87ee07479844061cbc`, parent
`5067b23528517285b5c066eafb9b4aae42b3a091`, tree
`7d247dc8743eacb119010b19516a873a93ea5e1d`. Its binary patch is
`9c7665ff6202449b8c01cde8030243683d32c522b1723205ecdb1bbbef5a7faa`;
its full-index encoding is
`14495bc9a6fe4b7ed7f62960f0d95b534281b2c4f3c5d9a63bc3594e8e6e98bd`.
The five documentation preimages already matched Runtime. Router previously
lacked `PhysicalDesign/Contracts/Runtime.py` and its owning test, so this
integration also includes the complete existing N1 request/result/proof and
work-product contract from producer history `f083554ee5967e69540e6647ee543c8e92747141`
through `9f432cd2712745d2b749a5c089564844a5229368`. The new module imports
only the standard library. Its test needs pytest and that module; no worker,
spawned adapter, symbolic caller or package-facade change is required for this
source contract. The complete imported module and tests received independent
Terra/high review.

All seven Router commands passed: exact import/native identity, compile and
whitespace checks, 52 contract tests, 7 structural/schema tests, 1,943 collected
tests and a complete deterministic result of 1,939 passed, 4 skipped and
378 subtests in 175.69 seconds. Source hashes and the staged patch remained
unchanged; clean commit readback matched all seven producer blobs. Router's
existing native remains
`d1b5cbda5ede49428cebf47af342b6153aa3d862310317c38a4aaf212ad85d1f`.
The 35-entry seal under
`Output/PortfolioGoal/RuntimeAuthorityToRouter/Integration/` retains exact
commands, raw logs, source manifests, JUnit, reviews, commit readback and
prerequisite identity clarification; its earlier 31-entry precommit seal is
preserved. Rust/native rebuild, live MCHPRS, Fabric, scale and production
caller integration were not run for this Python contract import.

Separately, Joint consumed exactly 24 native producer `cfd529e` blobs as
`567f30a32a927e29309a9851aa1a8d9f0b8b48c6`, parent
`96e66797ee2f957a3279d71b0985653988bceb5e`, tree
`cb3d2c21b6ef832470e3c567ea74651994936e07`. All 24 blobs, including the two
producer notes, are exact. Its full-index patch is
`4ce7ae85a2c22252ff17c888db9e275c654ed168de9fa1877f77f1c5cabfe0b2`;
the separately recorded abbreviated-index patch is
`61eb519f4a68eb566ca2468aefde954ceea2dda5583ca652b8163e6f1c1ecfac`.
Joint rebuilt its own native artifact, with build/package/import SHA-256
`36c7d5628604b4605084865a76c5172cbb208d278ac06b2efcf5c344fb836a39`.
Fresh independent review accepted the exact source import. Retained checks
passed formatting, 86 Rust tests, the locked release extension build, 78 native
outcome tests, 835 routing/reliability tests with 109 subtests, 7 structural
checks, 1,695 collected tests and full pytest with 1,691 passed, 4 skipped and
271 subtests in 73.26 seconds. The unchanged preparation seal has 29 entries
and is retained under Joint
`Output/JointPhysicalDesign/RuntimeSourceConsumption/20260907T104602Z-cfd529-PREP/`.
Parent readback is under `Output/PortfolioGoal/RuntimeToJoint/PostCommit/`.
The earlier broad no-commit merge attempt was aborted after exceeding scope;
the accepted candidate was reconstructed through exact producer-blob import.
The incident and clean final scope remain recorded.

These are exact path-based consumptions, not full Runtime branch ancestry.
Joint `567f30a` has no Runtime Python contract, bounded/spawned worker module,
or bounded symbolic adapter from that producer. Before a future adapter or
supervisor delta is consumed, each target must verify its complete required
module/blob closure against its actual checkout; a `cfd529e..successor` delta
alone cannot supply prerequisites absent from the target. Joint still must bind
native receipt echoes to current objects, consume reviewed Physical state and
access contracts, and emit actual success/failure receipts before Telemetry
can project them. No live worker cleanup, persistent-worker behavior, reuse
admission authority, new seven-case acceptance, or promotion readiness follows.
Protected `main` remains `193e2838050ee111245b5431484ad44112b26156`; no push
occurred. Recovery uses scoped reverts of the particular local integrations,
with their distinct source and native evidence preserved.

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

## 2026-09-07 local consumer integrated and supported-stair fixture committed

Joint committed `6fac0892f148cf4f4b0641cd72c3df775a0f62a4`, parent
`567f30a32a927e29309a9851aa1a8d9f0b8b48c6`, tree
`79cc0b55475eb04af49f42bc75571d3fccff92da`. Its eighteen paths contain
fifteen exact Physical `86d1ee5` prerequisites plus Joint's `CommitRouting.py`,
the actual local-consumer regression, and an R2 note. The real local path now
uses `BuildRoutingResources` with current placement, technology and work checks.
Fresh independent review passed 97 focused tests with 14 subtests, compilation,
7 structural/schema tests, collection of 1,771 tests, and the full deterministic
suite with 1,767 passed, 4 skipped and 271 subtests in 144.52 seconds. Joint's
existing native `36c7d562` was unchanged; no native parity or backend acceptance
is inferred. The complete precommit stream, including all six new files, is
distinct from the actual commit diff. The latter's full-index SHA-256 is
`0dcdf4a13ae852efeb1619c85709beb1e42e6a901f25e449390f7b0e11482717`.
The earlier incomplete seventeen-path encoding remains diagnostic history.

Router already had all fifteen Physical prerequisite blobs exactly. It consumed
the two exact Joint code/test blobs and the producer's ten-line R2 note addition,
preserving existing Router notes, as
`234b8dc65e65427a0147bd90ee727179e86e5e51`, parent
`d699df7f066d5cba75998cff3dbe598d4b46a9ed`, tree
`004df569c2dabd3058ee14bb9669d9f804492488`. The three-path integration has
126 additions and 15 deletions. Its binary patch SHA-256 is
`08c9c90f036cb456aa7044abf62bc1aa481861b186ecc440f205bbdf193d192b`;
the full-index encoding is
`d17488e046b898a4052e16062a698fcfbaec20ced7ed9b1ea22e564de1ac2822`.

All nine combined commands passed: exact native/import identity, compilation,
whitespace, 68 focused tests with 14 subtests, 8 existing envelope replay tests,
7 structural/schema tests, collection of 1,993 tests, seven MCHPRS vectors across
three fixture definitions, and the full suite with 1,989 passed, 4 skipped and
378 subtests in 182.65 seconds. Source was unchanged across the run. Fresh
independent Terra integration review accepted the final receipts. The existing
Router native remains `d1b5cbda`; there was no native rebuild, Fabric, scale or
full routed acceptance run. The 59-entry precommit seal is preserved; the
61-entry final seal under
`Output/PortfolioGoal/JointLocalStateToRouter/Integration/` has SHA-256
`7078629ddc3cd5c7ba9a19b1377feda6767a4d3671a3f1c8bd977e44c6584f65`.
The expected tree, exact three paths and clean checkout were read back.

Separately, Physical committed
`61bd602acd3583f93cd23faa883aa034282712c4`, parent
`86d1ee5f05684b7fc042d704c8b772847ecddd12`, tree
`3193d9e0ced38150a22ba8a90ef495469a56614a`. Seven fixture/test/note paths add
413 lines for the literal supported clear +X/+Y stair. The static checker
requires exact bidirectional dust edges, supports and clear headroom. Fresh
off-to-on and on-to-off native cases retain complete input vectors and ticks
0 through 7; the comparison layer alone consumes expected answers. An
expectation-only mutation preserves raw prediction/observation and produces a
mismatch. Only logical Y and lower route-root power are observed; upper-dust
analog power is not observed or inferred.

Physical's unchanged-source evidence passed 40 source/contracts tests, 5 observer
tests, all nine fixture vectors, 7 structural/schema tests, collection of 1,570
tests, and the full deterministic suite with 1,566 passed, 4 skipped and
257 subtests in 99.46 seconds. Fresh owner review and a separate parent audit
passed. The existing Physical native remains `c5938ff8`; it is a distinct
qualified artifact from Router's combined native. The fourteen-entry producer
commit receipt seal has SHA-256
`435b754ddc9a2b267ac03a20d5f4fa7a78e0d428aa81afb74e1a7be140eb00a3`
at Physical `Output/PhysicalRulesBatch1Evidence/20260907T134122Z-CommitReceipt/`.
Parent readback is retained at `Output/PortfolioGoal/PhysicalSupportedStair/PostCommit/`.
This fixture checkpoint changes no production API and is not required for the
Joint envelope source closure; its Router fixture intake remains a separate
operation.

Neither checkpoint establishes a current-access envelope, Ready lifecycle,
Runtime caller receipts, cache authority, general physical-rule completeness or
combined acceptance. All five coordinators received the exact checkpoints.
Recovery uses scoped local reverts, with each source/native/evidence lineage
preserved. Protected `main` remains
`193e2838050ee111245b5431484ad44112b26156`; no push occurred.

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

## 2026-09-07 blocked-headroom fixture integrated and generic claims delivered

Router `111de5b63c0cf29a9d4a6c851a4c14aa18acb9d4`, parent
`55cb276b4047dc63fd30f557caa8efd34b162dfd`, has tree
`e2dd4d817cee7ada7d581a37de2c27214849ff9c`. It consumes the seven
Physical fixture/test/note paths from `73a8c653` with the observer test at
`af4984b2`. The 447-addition patch is
`becd41fc11e7d0595cb5ae67f0d4db0604e26a305fb098832eb3519213035a94`;
its full-index encoding is
`334059bbaa9bd5871fd0970d8ca889f92145e0ba7d2a041c25130c31b033208b`.

The first intake failed only because a portable observer test fixed the
Physical binary hash instead of checking its actual imported binary. That
failed 73-entry archive remains unchanged under
`Output/PortfolioGoal/PhysicalBlockedHeadroom/Integration/`, seal
`b0f046410f0b2470c7c62bdc9a9ee739bd240da68183995d13cf85de4722f2ca`.
The one-file producer correction preserves every behavior assertion and uses
an independent direct import, resolved path and file-byte hash oracle.

Fresh corrected integration passed all nine commands: compilation and import
provenance, whitespace, 42 source tests, 6 observer tests, 7 structural/schema
tests, 1,999 collected tests, all 11 fixture cases across five definitions,
and the full 1,995 passed / 4 skipped / 378 subtests in 183.01 seconds. Fresh
Terra review found no integration blocker. The candidate stayed unchanged;
the committed tree, exact seven blobs, clean checkout and unchanged Router
native `d1b5cbda` were read back. The 78-entry final evidence seal under
`Output/PortfolioGoal/PhysicalBlockedHeadroom/Integration-CorrectedNativeProvenance/`
is `532ea508697cf44e8b71e19380c1b7350d23df29b32a2706092d0c7bc972dc23`.

The Reuse producer in the table is independently reviewed and locally
committed, but is not yet a Router dependency. Its complete source manifest,
primary reviewer commands/outputs/exit codes and postcommit receipt are
retained under `Output/PortfolioGoal/ReuseConstructionProvenance/`. Generic
claim provenance supplies no current-world or selected-access authority.

No native rebuild, Fabric, scale or routed acceptance matrix was run for this
fixture integration. Physical and Telemetry received the exact checkpoint.
Recovery is a scoped revert of `111de5b6`; protected `main` remains
`193e2838050ee111245b5431484ad44112b26156` with no push.

## 2026-09-07 solver-created generic claims integrated

Router `655ad196617343692e439ebb328e09040304b84c`, parent
`f377c0d0325c46d655ad22a83a4fa1b2437a11d8`, has tree
`30fee06a5b5f61c0d5c60d2d1ad10b312d133e07`. It consumes the exact nine
postimages from Reuse `fe7400c971f614d2e6e1a0ece3bacd849d53c45b`, whose parent
is `10fafc5c30cef12817e6bb81584be263a77bd6b8`. All nine Router preimages
matched that producer parent; there were no conflict resolutions or extra edits.

The component-net contract retains immutable solver-created `GenericClaims`.
Cache storage and reconstruction preserve category, original ordinal, and
coordinate translation independently from public claims. Current four-field
generic equality is required for a warm hit; malformed positions miss before
translation, and errors from the subsequent cold solve remain observable.
This is bounded supplied-graph cache safety, with no current-world or selected-
access admission, N6 certificate, salvage, or full R5 acceptance authority.

Fresh independent Terra integration review passed on the exact producer patch.
Router verification passed 139 owning tests, 175 adjacent tests with 24 subtests,
seven structural/schema tests, collection of 2,024 tests, and the full suite:
2,020 passed, four skipped, 378 subtests in 207.83 seconds. All eight planned
commands returned zero; actual verification ran from 17:23:50.531489 to
17:27:40.284519 UTC on 2026-09-07. Source, protected main, and the imported Router
native artifact remained unchanged. Native rebuild, Fabric, scale, and the
routed acceptance matrix were not run for this intake.

The ignored evidence root is
`Output/PortfolioGoal/ReuseConstructionProvenance/Integration-20260907T171700Z`.
It contains the exact source and approval records, commands and exits, raw logs,
JUnit, independent review, and post-commit receipt. Its final 36-entry manifest
SHA-256 is `2525907b25c35babaa78c03e3a50a55cf974e6b543ac97b3fa57023712ffbebc`.
The canonical full-index patch SHA-256 is
`adfbe0528877307cb037176badceea8575249de5dbe3a6f6d27a779af61f95e0`.

Reuse, Joint, and Telemetry were notified of this exact checkpoint. R1 deferred
candidate refinement remains separate and must start from the aligned live Joint
caller source. Recovery is a scoped local revert of `655ad196`; protected main
remains `193e2838050ee111245b5431484ad44112b26156` with no push.

## 2026-09-07 owned process supervision integrated

Router `86ee2206ebd47458f541e745a7cd36c917192930`, parent
`38b137faac34b6467628ad2fe9e7b6c7c158b968`, has tree
`f956c12f00d935f404c2d843ead67dde7914affb`. Its two added paths are the exact
production `OneShotProcess.py` from Runtime `a264da03` and the corrected owner
test from `880a1a35`. The existing Runtime contract blob remained identical in
both producers and Router; no contract reconciliation or caller migration was
included. The canonical combined full-index patch SHA-256 is
`0d7f4181ff2d74464e81fd4008e5bbb6d9d03d1d18d8e4ea6dd5526eed0ea9c5`.

The first Router intake exposed a missing test precondition: readiness did not
prove that the uncooperative operation entered before the 100 ms work deadline.
That failed source-bound packet remains sealed separately. The test correction
uses a flushed/fsynced operation-entry marker, exact PID/start liveness, an
initial three-second work allowance and the original cleanup cutoff 0.15 seconds
later. It never renews either cutoff and records actual facts before assertions.
The production implementation remained unchanged through the test repair.

Fresh independent integration review passed. All eight corrected Router commands
returned zero: imports/compile/diff checks, seven structural/schema tests,
collection of 2,065 tests, the single corrected regression, 40 owning cases, and
the full suite of 2,060 passed, five skipped and 378 subtests in 213.46 seconds.
Actual verification ran from 18:51:13.272066 to 18:55:06.125181 UTC. The regression,
owning file and full suite retained 1/40/40 closed ownership witnesses with no
fallback and clean final watchdog, script and GNU-time exits. Source, imported
Router native and protected main remained unchanged during verification.

The final ignored evidence root is
`Output/PortfolioGoal/RuntimeOneShotIntegration-CleanupBreachCorrection`; its
56-entry manifest SHA-256 is
`5cd4cace9d58628fb97a4a8fa26a88dc88f27dafc9923183d70d1f74f3c10f99`.
The preserved failed 50-entry packet under `RuntimeOneShotIntegration` has
manifest SHA-256
`d268ec2eaaf9d68760573213d9d4811c457e91c3d5c447df48367e3f3ed5cdc4`.
The owner 120-second and Router 420-second watchdog limits identify separate
verification runs; neither establishes a performance acceptance result.

This delivers a standalone Linux/spawn supervision primitive after Process.start.
It does not bound startup or integrate a live caller, pool, persistent scheduler,
native cancellation, Fabric, scale or routed acceptance. Recovery is a scoped
revert of `86ee2206`; protected main remains
`193e2838050ee111245b5431484ad44112b26156`, with no push.

The same milestone also authenticated Physical producer `3de1dbf9` at tree
`819eb395df1406113b7c166fdd2d20f3d047c93f`; its ten fixture/test/note paths remain
unconsumed by Router. Joint's six-path policy correction is frozen and its direct
policy regressions passed independent review, but it is uncommitted and has a
separate exact-cluster `MissingReadyPredecessor` handoff finding. Neither is
silently included in this Runtime integration or treated as full acceptance.

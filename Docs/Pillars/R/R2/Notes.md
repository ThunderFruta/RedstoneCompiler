# R2 notes

Working notes for [R2](R2.md). This file is non-normative; the requirement file
controls when the two disagree.

## Current context

- History/checkpoint branch: `R2-Joint-Placement-And-Routing`.
- Ongoing implementation bucket: `Joint-Physical-Design`, shared with
  R3/R4/R9/N3 under the [bucket map](../../WorktreeBuckets.md). The R2 history
  ref is preserved; moving work to this bucket does not imply integration.
- The branch starts from capability-neutral Router Refactor history and merges
  the Physical-Rules access-contract and domain-query checkpoints explicitly.
- `f946ffc` is the first Joint-owned implementation commit intentionally
  attributed to R2; physical realization remains independently owned.
- `7a88051` supplies the opt-in policy and Physical-Rules supplies the catalog
  prerequisite. These are supporting history rather than an R2-completion claim.

## Decisions

- Keep R2 as the umbrella for coordinated placement and routing work. Do not
  name the branch after its first pin-access feature.
- Use v17 as the rewrite branch's development and acceptance-run default.
  Both `default` and `routing-aware-placement-access` select
  `physical-design-v17-routing-aware-placement-access`, without fallback.
  This supersedes the earlier opt-in-only development decision; it is not
  Stage-1 acceptance or promotion to stable `main`.
- Use an explicitly pinned stable/control checkout for baseline comparisons.
  There is currently no v16 CLI strategy selector; the internal v16 policy and
  its explicit control test remain available. Do not present a default run on
  this branch as a v16 benchmark.
- Treat selected pin access as placement-owned physical authority. Downstream
  routing and compaction must consume the selected witness and may not rebuild,
  shorten, or substitute its first-leg geometry.
- Preserve the three independent solver outcomes: `Feasible`, `Unsatisfiable`,
  and `Incomplete`. Incomplete domain generation or search is not an
  infeasibility proof and cannot become a reusable placement rejection.
- Carry catalog, technology, domain, and witness identities across the handoff.
  A missing or mismatched witness is a typed failure, not permission to fall
  back to legacy access generation.
- Admit only the straight access family in the current slice. Non-straight
  alternatives must pass the same exact physical checks before joining the
  production domain.

## Open questions

- What is the smallest joint candidate state that can select cluster transform,
  pin-access option, boundary slot, and channel capacity without embedding
  detailed route nodes?
- Which complete conflict cores justify changing a placement or widening a
  channel, and how will the coordinator prove that unrelated clusters remain
  unchanged?
- Which live acceptance evidence is sufficient to promote the experimental
  strategy after the full R2 behavior is present?

## Working notes

- The current implementation compiles complete straight-only option domains at
  each fixed placement, selects exactly one option per logical terminal, and
  freezes the resulting witness.
- Mandatory-access checks, access-fabric construction, raw track assignment,
  detailed routing, compaction, and final diagnostics consume that selected
  witness or its fingerprint.
- The routing entry rejects an absent, stale, incomplete, or identity-mismatched
  witness. Portal preparation exposes only selected access geometry and reports
  access-regeneration and unselected-portal-leak counts.
- `2024d7ddfde26195221414d1d7ee6567567a179d` hardens the serialized Stage-1
  boundary and implements the narrow five-stage finalization contract. Its
  Python gates pass, but its clean live opt-in run fails all four cases before
  successful finalization. Stage 1 is **not accepted**.
- A green unit suite alone does not justify planar-jog admission. Under the
  [rewrite workflow](../../RewriteWorkflow.md), dependent capabilities may be
  developed before Stage-1 acceptance when their scope and checkpoints are
  explicit. This does not enable jogs, waive the inherited integration blockers,
  or allow weakened claims, regenerated access, or fallback.
- The canonical Stage-1 sidecar entry is implemented and unit-tested. The full
  `FrozenPhysicalPlacementContract`, joint transform/access/slot/channel state,
  scoped geometry repair, and expanded/repeated acceptance remain future work.

## Stage 1 conformance ledger

Status vocabulary: **Target** means required but not demonstrated;
**Implemented** means source plus the named tests; **Accepted** requires the
specified live evidence; **Inherited failure** means reproduced before this
hardening change; **Not-run** means the phase was not reached or executed.
Statuses below apply only to the stated Stage-1 slice, never to an entire N
requirement. No Stage-1 requirement is promoted to globally complete.

| Requirement | Stage-1 claim | Status | Code/test evidence | Runtime evidence | Remaining beyond S1 |
|---|---|---|---|---|---|
| **R2** | One straight access choice per terminal is selected once and consumed unchanged through finalization. | Implemented; Inherited failure in live routing; Not-run for successful finalization. | [Contracts][contracts], [catalog][catalog], [handoff][handoff], [adapter/publication][adapter]; [serialization/handoff tests][boundary-tests] plus placement and authoritative-routing gates. | FA selects witness `1d239291906bc36a`, then interface selection fails. No smaller-case success artifact or five-stage final handoff exists. | Alternate access; shared transform/slot/channel selection; scoped geometry repair; full R2 acceptance. |
| **N1** | Placement-access results distinguish feasible/unsatisfiable/incomplete, complete domain, completed search, and proven optimality. | Implemented for Stage-1 records. | `PlacementAccessSolveResult`, strict codec, scoped core invariants; all three status round trips and malformed/cap tests. Production placement-access branches use `Status`, not `Success`. | 25 serialized solve occurrences round-trip: 3 Feasible, 22 Unsatisfiable, 21 distinct result identities. No live Incomplete solve-record example is claimed. Outer interface incompleteness remains distinct from fixed-domain unsatisfiability. | Repository-wide lifecycle, freshness, claim-strength, and commit-eligibility axes. |
| **N2** | Templates, transformed claims, support/air/electrical scope, and repeater direction use the same technology/resource model and pass physical validation. | Implemented and fixture-tested; Not-run for opt-in live physical validation. | [Catalog][catalog] and [catalog tests][catalog-tests]; codec reconstructs exact claims/reservations and verifies proof/model identities; finalization recomputes the current model. | Opt-in cases fail before physical/MCHPRS/Fabric validation. Clean v16 controls pass, but do not accept the Stage-1 slice. | Repository-wide shared-rule migration. |
| **N3** | The witness is immutable, deterministically selected, identity-bound, and attached only by physical-design orchestration. | Implemented; Not-run for accepted end-to-end commitment. | [Selection/solve tests][solver-tests], immutable domain/template preimages, [five-stage validator][handoff], mutation and ordering checks. | FA's placement, access, witness, and raw-candidate identities match the pre-hardening replay. No live detailed-routing/compaction acceptance evidence exists. | Full joint-candidate dependency manifests, reusable subclaims, and coordinator commitment across workers. |
| **N4** | Domain generation and assignment have explicit work caps, deadline checkpoints, bounded result cardinality, and incomplete classification on exhaustion. | Implemented in Stage-1 generation/search tests; Target for end-to-end runtime bounds. | Serialized generation/assignment limits and expansion count; capped search has no core; deadline checks retain their typed failure. | CLA4 retains a completed scoped access core but takes 124.278 s against the 120 s process ceiling. The 4.278 s overrun is a failed runtime observation, not an accepted bound or a new infeasibility proof. | Shared worker admission, memory limits, cancellation lifecycle, shutdown/collection guarantees. |
| **N5** | Run the Stage-1 FA/RCA4/RCA8 subset non-fail-fast with retained MCHPRS/Fabric evidence. | Implemented runner execution; Inherited failure for the three smaller cases; Not-run for their opt-in MCHPRS/Fabric phases. | [Acceptance harness][harness] and snapshot gates pass. Documentation records every planned result without adding requirement-tracking schema fields. | Candidate: 0 passed, 4 failed, 0 skipped, including the CLA4 diagnostic. RAPA-S1 is a diagnostic capture, not Accepted. | Full seven-case matrix, repeated CLA4, eventual 5/3/3/2 v17 shape, and final R2 acceptance. |
| **N6** | Reject mismatched or old identities on the direct path; no Stage-1 stale salvage. | Implemented and negative-tested; Not-run for live successful finalization. | Strict missing/unknown/reordered/duplicate/stale field checks; every handoff stage rejects mismatched policy/catalog/technology/resource/domain/witness identity with `ClusterInterfaceInvariantViolation`. | No stale result is salvaged or promoted. Live failures do not establish successful five-stage preservation. | Coordinator-certified selective salvage and current-snapshot revalidation. |

## Implementation and Python verification

Implementation commit: `2024d7ddfde26195221414d1d7ee6567567a179d`, parent
`b0cced5cebcde3f7579c5559e78d72f710dcfa6d`. The earlier R2 implementation is
`1fb7db6a97bd8b083bcd4e8348497fb4ff5f0781`. `b0cced5` was not amended.

The schema is `placement-access-solve-result-v1`. Domain/template preimages make
the solve and selected witness independently round-trippable. Decoding verifies
internal consistency; it does not replace current-model or physical validation.
The pure validator takes immutable observations in exactly this order:
`Placement`, `AccessFabric`, `RawAssignment`, `DetailedRouting`, `Compaction`.
Current identities and the feasible solve are explicit inputs, not hidden
context. The context adapter is the sole success-publication path for
`PlanningContracts.PlacementAccess = {SolveResult, SelectedWitness, HandoffEvidence}`.
Failure/incomplete paths publish typed evidence, not that success contract.

From the repository root, in order:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q \
  Tests/PhysicalDesign/Placement \
  Tests/Integration/test_local_first_router.py \
  Tests/PhysicalDesign/Routing/test_authoritative_portals.py \
  Tests/PhysicalDesign/Routing/test_authoritative_assignments.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q \
  Tests/Structural/test_source_structure.py \
  Tests/PhysicalDesign/Routing/test_routing_contract_schema.py \
  Tests/Structural/test_routing_design_snapshot.py \
  Tests/Tools/test_router_acceptance_harness.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q
```

| Gate | Result | Retained output under `Output/Acceptance/RAPA-S1/Verification/` |
|---|---|---|
| Placement/routing | 432 passed, 52 subtests; 22.88 s | `PlacementRouting.txt` |
| Structural/schema/snapshot/harness | 92 passed, 71 subtests; 18.92 s | `StructuralEvidence.txt` |
| Full Python suite | 1,558 passed, 3 skipped, 257 subtests; 66.02 s | `FullSuiteFinal.txt` |
| Rust formatting/release/rebuild | Not-run: Rust source unchanged | No native rebuild claimed |
| Gradle/Java | Not-run: harness source unchanged | Live use of the existing runtime is separate from its build gate |

After the records edit, the exact requested three-file structural/snapshot/
harness command (without the extra schema test file) passed again: 90 tests,
71 subtests, 18.78 s. `git diff --check` passed, and source/test/tool files were
unchanged from `2024d7d`.

The focused boundary file contains 60 tests, including one-field corruption of
all serialized option leaves, claim/reservation categories, completeness,
identity, ordering, duplicates, mutable-input detachment, each handoff stage,
and the finalization adapter. Existing catalog/solver tests retain physical
fixtures, deterministic selection, and small-oracle parity.

## Live evidence, 2026-09-04

The runs below predate the v17-default decision and retain their exact original
source, strategy, and policy identities. In particular, the v16 control at
`2024d7d` does not describe the current default. No new passing live acceptance
claim follows from changing the development default.

Machine: AMD Ryzen 9 9950X (16 cores, 32 logical CPUs), Python 3.12, 16 routing
threads, seed 0. Native module SHA-256:
`c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`.
No native source/rebuild, deadline inflation, extra retries, jog admission, or
validation-policy weakening was introduced. Fabric retains 40 unchanged ticks
within its 200-tick bound; MCHPRS remains exhaustive for these smaller cases.

All paths below are repository-relative unless marked absolute. Each live root
retains its `AcceptanceManifest.json`, `Summary.txt`, `RawDump.txt`, per-case
reports, and generated/typed artifacts. Every planned case ran once.

| Case | Clean S0, v16, `b82b8ee` | Pre-hardening v17, `b0cced5` | S1 candidate v17, `2024d7d` | Clean v16 control, `2024d7d` |
|---|---|---|---|---|
| FullAdder | Accepted; 7.592 s | Failed; 2.499 s | Inherited failure; 2.388 s | Accepted; 7.850 s |
| RCA4 | Accepted; 10.281 s | Failed; 5.699 s | Inherited failure; 5.895 s | Accepted; 10.842 s |
| RCA8 | Accepted; 15.599 s | Failed; 24.890 s | Inherited failure; 25.764 s | Accepted; 16.086 s |
| CLA4 diagnostic | Process timeout; 125.304 s | Scoped `NoPinAccessPattern`; 115.331 s | Scoped `NoPinAccessPattern`; 124.278 s, runtime ceiling exceeded | Not-run in the three-case control |

The two v16 runs pass physical truth tables and exhaustive MCHPRS for
8 / 512 / 131,072 vectors and Fabric canaries for 8 / 20 / 36 vectors, with
40 unchanged ticks, zero final conflicts, and zero unresolved claims. The
current v16 artifacts contain **no** Stage-1 `PlacementAccess` contract.
Fixture SHA-256, footprint, and resource-graph fingerprints match S0 exactly.
Candidate/placement fingerprints differ across `b82b8ee` and `2024d7d`; this is
physical/behavioral parity, not a claim of identical metadata or cache identity.

Opt-in failure details:

- FA: `PreRouteInterfaceSelection:ClusterInterfaceSolveIncomplete`; selected
  witness `1d239291906bc36a`, domain `29b9aaa94f69baa9`, placement
  `08633f8194dba589`. Raw candidate domains `fdb1929151eab420` (one layer) and
  `5213b426c03bb397` (two/three layers) match the previous R2 revision. The
  reported fixed-track incompatibility includes `NandNet0` / `NandNet3`.
- RCA4: the same typed interface-incomplete reason; candidate
  `Placement-bb7dc1844e6f` has no complete `NandNet0` track candidate. Preparation
  is incomplete and carries no admitted Stage-1 witness, not a success handoff.
- RCA8: `PlacementAccessSolve:NoPinAccessPattern`; problem
  `68f7bb553dbac860`, complete scoped core `21a79a73dc757834`. The retained fixed
  placement has eight empty straight-access domains; the first core identifies
  `NandGate27/Input1` on `B3`. Later bounded placement-generation failures are
  retained separately and do not enlarge the core's scope.
- CLA4: the same typed access-domain reason; problem `761b80a2f426cc88`, core
  `ff2e700e2b0ec801`. This compiler failure is retained as-is, not relabeled a
  timeout despite its process-envelope overrun.

The matching failure stage/reason, preparation identities, and problem/core
fingerprints were checked against the isolated `b0cced5` replay. That replay's
only untracked item was a temporary `.venv` symlink; tracked source was unchanged,
native hashes matched, and provenance stayed stable. Its manifest truthfully
records `Dirty=true`; it is diagnostic attribution, not a clean acceptance run.
The symlink was removed afterward, not retrospectively hidden in evidence.

No opt-in case reaches successful detailed routing, compaction, physical
validation, MCHPRS, or Fabric. Those phases are **Not-run**, not passing and not
waived. The live failure JSON contains 25 exact round-trippable solve occurrences
(3 Feasible / 22 Unsatisfiable, 21 distinct identities), checked with:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B \
  Output/Acceptance/RAPA-S1/Verification/InspectLiveEvidence.py
```

Output: `Output/Acceptance/RAPA-S1/Verification/LiveEvidenceInspection.json`.
This checks failed-run serialization and inherited identities; it cannot stand
in for the missing successful `.PhysicalDesign.json` handoff contract.

### Exact live commands and roots

Candidate command from clean `2024d7d`; dry-run used the same arguments with
`--dry-run` and output root `Output/Acceptance/RAPA-S1/DryRun`:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B \
  Tools/Routing/RunRouterAcceptance.py \
  --python .venv/bin/python \
  --routing-strategy routing-aware-placement-access \
  --matrix default --include-cla4 --routing-threads 16 \
  --output-root Output/Acceptance/RAPA-S1/Live
```

For the isolated checkouts, the interpreter is external so no `.venv` symlink
makes the checkout dirty. The clean S0 command, run with cwd
`/tmp/RedstoneCompiler-RAPA-S0-kCCtFR/Source` at `b82b8ee`, was:

```bash
PYTHONDONTWRITEBYTECODE=1 \
  /home/bananawewe/.codex/worktrees/634f/RedstoneCompiler/.venv/bin/python -B \
  Tools/Routing/RunRouterAcceptance.py \
  --python /home/bananawewe/.codex/worktrees/634f/RedstoneCompiler/.venv/bin/python \
  --matrix default --include-cla4 --routing-threads 16 \
  --output-root /home/bananawewe/.codex/worktrees/634f/RedstoneCompiler/Output/Acceptance/RAPA-S0/CleanReplay/Live
```

The clean v16 control uses the identical interpreter/arguments without
`--include-cla4`, cwd `/tmp/RedstoneCompiler-RAPA-S0-kCCtFR/DefaultVerification`
at `2024d7d`, and output root
`Output/Acceptance/RAPA-S1/DefaultVerification/Live` (absolute under this repo).
S0/control dry-runs used their parent roots and `--dry-run` before the live run.
The earlier `b0cced5` diagnostic used cwd
`/tmp/RedstoneCompiler-RAPA-S0-kCCtFR/BeforeHardening`, `.venv/bin/python`, the
candidate's strategy/matrix/thread arguments, and the absolute root
`Output/Acceptance/RAPA-S1/BeforeHardening` under this repo.

### Snapshot reconciliation and checksums

An exact existing `b82b8ee` capture was not found. S0 was reproduced from that
revision, never relabeled from current R2 output. An initial S0 run/capture had
the untracked interpreter symlink; it remains retained at
`Output/Acceptance/RAPA-S0/Live/2026-09-04` and
`Output/DesignSnapshots/RoutingAwarePlacementAccess/RAPA-S0/20260904T193044Z`,
but is superseded for clean-source provenance by the clean replay below.

| Bundle | Source revision | Directory under `Output/DesignSnapshots/RoutingAwarePlacementAccess/` | ExactEvidenceSha256 |
|---|---|---|---|
| Clean S0 diagnostic baseline | `b82b8ee1331b2b1fa17aa4353710650df3fa51cc` | `RAPA-S0/20260904T194616Z` | `96cff3cccb8653101e8f3f13216c443c4433ced4a311a21642eed221e7c3d4b6` |
| S1 failed candidate diagnostic | `2024d7ddfde26195221414d1d7ee6567567a179d` | `RAPA-S1/20260904T194039Z` | `6b4b9012db05a2c13e70db7eb4048c7abf3ad2673d2957ec12e1ff4074f68e41` |

Both bundles have `Dirty=false`, empty staged/unstaged patches, and the clean
porcelain SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
All ten snapshot cross-checks pass: revision/policy/input, matrix, source,
benchmark inputs, templates, native extension, build inputs, and current policy.
`sha256sum -c SHA256SUMS` passes for every copied artifact and snapshot file in
both directories. These checks establish provenance, **not acceptance**.

S0 has an explicit `ACCEPTANCE_PROCESS_TIMEOUT` observation with return code 124
and no CLA4 compiler failure JSON. Its checksum-valid clean capture therefore
does **not** satisfy the plan's separate requirement for a typed compiler CLA4
failure. It must not be treated as a structural no-good. S1 copies the actual
nested CLA4 compiler failure and the three smaller-case failure files; no
successful Stage-1 physical-design artifacts exist to capture.

Manifest SHA-256 values (each manifest is under its root's `2026-09-04/`):

- S0 `Output/Acceptance/RAPA-S0/CleanReplay/Live`:
  `33d6597e9d2625453b0e2c74fc8a024adb356961fbf332a0df788ad8014f09b1`.
- S1 `Output/Acceptance/RAPA-S1/Live`:
  `512c65d418246221866bd737e8c9653b9edafd7187b5abf8cd765650586a900b`.
- Pre-hardening `Output/Acceptance/RAPA-S1/BeforeHardening`:
  `0c3a1a488254ccb77f3102ab9104a0002233b737e2b67a002296911a3f5432c2`.
- v16 control `Output/Acceptance/RAPA-S1/DefaultVerification/Live`:
  `7bf208637eddda8b03c53588a3faa2f60f3ff9406b3a20d3f3ce38f3aeaa52d1`.

The S0 and S1 `SHA256SUMS` files themselves hash to
`3162962d076b96e69dd8f139407c2d2e04e370b370c67b3cf70807b1f6103331`
and `896fa8523a14b7d0c0b3c057bb6a95c585f16ec184e7ed5a52a34a2c21f0161b`,
respectively. The full test output hashes to
`308932281f43403d08a70e179a8b9751e3cc2609e289bd3190fe95571b907721`.

`CaptureRoutingDesignSnapshot.py` generated both bundles. For S1 the explicit
inputs were the candidate manifest; the four nested `.RoutingFailure.json`
files under the case `Runs/` directories; session `Summary.txt`/`RawDump.txt`;
and the three verification TXT files above. S0 uses the same current capture
tool with `SnapshotConfiguration.RepositoryRoot` pointing to the clean
`b82b8ee` checkout, imports the native module from that checkout, and records
the current generator's hash separately. Its inputs are the clean S0 manifest,
the three successful physical-design/fixture/litematic sets, and session reports.
The existing snapshot schema is unchanged; no R/N tracking fields were added.

The S1 capture command from the clean implementation checkout was:

```bash
.venv/bin/python -B Tools/Routing/CaptureRoutingDesignSnapshot.py \
  --output-root Output/DesignSnapshots/RoutingAwarePlacementAccess/RAPA-S1 \
  --cla4-failure Output/Acceptance/RAPA-S1/Live/2026-09-04/CarryLookaheadAdder4Run1/Runs/20260904T193405.681497Z-P903142/CarryLookaheadAdder4Run1.RoutingFailure.json \
  --acceptance-manifest Output/Acceptance/RAPA-S1/Live/2026-09-04/AcceptanceManifest.json \
  --artifact Output/Acceptance/RAPA-S1/Live/2026-09-04/FullAdderRun1/Runs/20260904T193331.597108Z-P902221/FullAdderRun1.RoutingFailure.json \
  --artifact Output/Acceptance/RAPA-S1/Live/2026-09-04/RippleCarryAdder4Run1/Runs/20260904T193333.993841Z-P902331/RippleCarryAdder4Run1.RoutingFailure.json \
  --artifact Output/Acceptance/RAPA-S1/Live/2026-09-04/RippleCarryAdder8Run1/Runs/20260904T193339.896027Z-P902503/RippleCarryAdder8Run1.RoutingFailure.json \
  --artifact Output/Acceptance/RAPA-S1/Live/2026-09-04/Summary.txt \
  --artifact Output/Acceptance/RAPA-S1/Live/2026-09-04/RawDump.txt \
  --artifact Output/Acceptance/RAPA-S1/Verification/PlacementRouting.txt \
  --artifact Output/Acceptance/RAPA-S1/Verification/StructuralEvidence.txt \
  --artifact Output/Acceptance/RAPA-S1/Verification/FullSuiteFinal.txt
```

The clean S0 capture was invoked from this repository with the following
Python command (the explicit import/root override allows the newer capture
tool to inspect old, unmodified source):

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c 'import importlib.util,sys; from pathlib import Path; from datetime import datetime,timezone; tool=Path("Tools/Routing/CaptureRoutingDesignSnapshot.py").resolve(); spec=importlib.util.spec_from_file_location("RapaCapture",tool); capture=importlib.util.module_from_spec(spec); sys.modules[spec.name]=capture; spec.loader.exec_module(capture); root=Path("/tmp/RedstoneCompiler-RAPA-S0-kCCtFR/Source"); sys.path.insert(0,str(root)); live=Path("Output/Acceptance/RAPA-S0/CleanReplay/Live/2026-09-04").resolve(); artifacts=tuple(live / name / (name+suffix) for name in ("FullAdderRun1","RippleCarryAdder4Run1","RippleCarryAdder8Run1") for suffix in (".PhysicalDesign.json",".PhysicalFixture.json",".litematic"))+(live/"Summary.txt",live/"RawDump.txt"); config=capture.SnapshotConfiguration(RepositoryRoot=root,OutputRoot=Path("Output/DesignSnapshots/RoutingAwarePlacementAccess/RAPA-S0").resolve(),CapturedAtUtc=datetime.now(timezone.utc).replace(microsecond=0),Cla4FailurePath=None,AcceptanceManifestPath=live/"AcceptanceManifest.json",ArtifactPaths=artifacts); result=capture.BuildRoutingDesignSnapshot(config); path=capture.WriteSnapshotStaged(config,result); print(path); print("ExactEvidenceSha256="+result["ExactEvidenceSha256"]); print(result["Checkout"]); print(result["AcceptanceManifest"]["CrossChecks"])'
```

## Next completion gate

The following gates remain required to claim **R2 Stage 1 acceptance**. They
do not prevent commit-ready prerequisite work in another phase or pillar under
the [rewrite workflow](../../RewriteWorkflow.md). Record code, interface, and
acceptance dependencies in the [shared register](../../CapabilityDependencies.md)
instead of restoring legacy assumptions solely to satisfy a circuit gate.

1. First demonstrate the smallest real five-stage production handoff on a
   controlled placed problem, without mocking or bypassing the consumers.
   Replay the selected straight witness against raw interface assignment and
   explain the FA fixed-track conflict and RCA4 incomplete candidate domain.
2. Reconcile packed/preowned geometry with the straight access compiler using
   RCA8's scoped empty-domain evidence. Keep geometry immutable after selection;
   do not manufacture choices, weaken electrical claims, or route via fallback.
3. If the remedy needs alternate access or coordinated geometry repair, make
   that scope decision explicitly: those features are deferred to Stages 2/3,
   not silently included in contract hardening. Preserve the CLA4 overrun as an
   unresolved runtime observation as well.
4. After an in-scope integration fix, rerun all Python gates and the four-case
   live matrix from a new clean commit. Require FA/RCA4/RCA8 physical + MCHPRS +
   Fabric passes and inspect all five unchanged witness observations, zero
   regeneration/leaks, and compaction preservation in the success artifacts.
5. Retain a separately identifiable typed compiler CLA4 S0 diagnostic if one can
   be produced at the exact base without changing its policy; otherwise keep
   that original evidence requirement explicitly unmet. Capture a new S1
   bundle, update only demonstrated ledger claims, then make another records
   commit. Do not overwrite these failed captures.

No global R2 or N1–N6 acceptance, selective salvage, full seven-case gate, or
eventual repeated v17 acceptance is claimed by this records update.

[contracts]: ../../../../PhysicalDesign/Contracts/PlacementAccess.py
[catalog]: ../../../../PhysicalDesign/Placement/Access/Catalog.py
[handoff]: ../../../../PhysicalDesign/Contracts/PlacementAccessHandoff.py
[adapter]: ../../../../PhysicalDesign/Orchestration/Results.py
[boundary-tests]: ../../../../Tests/PhysicalDesign/Placement/test_placement_access_serialization.py
[catalog-tests]: ../../../../Tests/PhysicalDesign/Placement/test_pin_access_catalog.py
[solver-tests]: ../../../../Tests/PhysicalDesign/Placement/test_fixed_pin_access_solver.py
[harness]: ../../../../Tools/Routing/RunRouterAcceptance.py

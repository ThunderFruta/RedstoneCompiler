# Routing-aware placement and access snapshots

**Log policy:** Append-only. Never delete or rewrite an earlier observation,
measurement, failure, interpretation, or artifact identity. When later evidence
changes a conclusion, append a timestamped correction that cites the earlier
snapshot ID.

The [design document](RoutingAwarePlacementAccessDesign.md) owns normative
requirements. This file records what a particular source and artifact snapshot
actually proved. A snapshot is evidence, not acceptance by itself.

## Status vocabulary

- `Captured`: source/evidence identity and concise facts were recorded.
- `Reproduced`: the recorded command was rerun from the recorded source and
  produced equivalent typed evidence.
- `Implemented`: the named implementation slice passes its focused gates.
- `Verified`: the snapshot's declared physical acceptance gate passes.
- `Failed`: a required gate produced a typed failure or invalid artifact.
- `Incomplete`: the declared work ended without feasible or complete UNSAT.

## Timestamp and source-identity rules

1. Record both `CapturedAtUtc` and the America/New_York local timestamp.
2. Record the full Git revision, branch, and
   `git status --porcelain=v1 --untracked-files=all` output.
3. Hash the exact porcelain bytes, relevant source files, and every evidence
   artifact. Record the exact-byte evidence identity separately from the
   portable-semantic evidence identity; neither identity substitutes for the
   per-file hashes.
4. Distinguish the artifact's recorded source state from the checkout state at
   snapshot capture.
5. Supply artifact paths explicitly. Never search `/tmp` or `Output/` for a
   file named “latest.”
6. Record failures by exact stage, reason, detail, deadline state, and artifact
   gate. `PlacementOverlap` is not a timeout.
7. Do not record a future milestone until its real artifact exists.
8. A dirty tree is valid evidence when its complete identity is recorded. It
   is not equivalent to a clean artifact-source checkout.
9. Snapshot generation is read-only while it gathers evidence. Publication
   builds a fresh hidden staging directory beneath the requested output root,
   verifies its contents, and then renames that directory to a fresh final
   name. This is not a claim of transactional, crash-durable, or filesystem-
   atomic publication: a process or host crash can leave a hidden staging
   directory. The generator never stages, resets, checks out, cleans, or
   updates the Git index.
10. An existing/nonempty snapshot target is rejected to prevent stale evidence
    from being mixed with a new run.
11. Immediately before creating the output root, recompute both aggregate
    identities and the derived snapshot ID from the complete in-memory
    document. Reject a post-build mutation instead of publishing a bundle whose
    identities no longer describe its semantic contents.

## Machine-readable bundle

`Scripts/CaptureRoutingDesignSnapshot.py` captures a full timestamped bundle.
Its output contains:

```text
<CapturedAtUtc>/
  Snapshot.json
  Snapshot.md
  SHA256SUMS
  Artifacts/
    <explicitly supplied evidence files>
```

`Snapshot.json` is the authoritative machine projection. `Snapshot.md` is a
deterministic human summary. `SHA256SUMS` covers the exact JSON, Markdown, and
copied artifact bytes.

Schema v2 records two different aggregate identities:

- **Exact-byte evidence identity:** binds the exact captured checkout records,
  source records, generator record, semantic summaries, and copied artifact
  byte identities defined by the schema. Any byte-relevant change to that
  evidence changes this identity.
- **Portable-semantic evidence identity:** binds the normalized routing-source,
  artifact, and typed-result meaning while excluding only schema-declared
  capture-time, absolute-location, snapshot-name, and publication-path fields.
  It is used to recognize equivalent evidence captured at another permitted
  location or time.

The schema name and normalization contract are part of both identities. A v1
`CanonicalEvidenceSha256` is neither a v2 exact-byte identity nor a v2
portable-semantic identity and must not be compared to either one. Per-file
`SHA256SUMS` remains the authority for verifying the exact files in a bundle.

Suggested capture command for the current baseline:

```bash
python3 Scripts/CaptureRoutingDesignSnapshot.py --output-root Docs/Routing/Snapshots/RoutingAwarePlacementAccess --cla4-failure /tmp/redstone-timegraph-20260825-2128/Diagnostics/CarryLookaheadAdder4Run2/CarryLookaheadAdder4Run2.RoutingFailure.json --acceptance-manifest /tmp/redstone-timegraph-20260825-2128/2026-08-25/RouterRegression/StandaloneAcceptance/AcceptanceManifest.json --artifact /tmp/redstone-timegraph-20260825-2128/Diagnostics/CarryLookaheadAdder4Run2/CarryLookaheadAdder4Run2.Nand.json --artifact /tmp/redstone-timegraph-20260825-2128/Diagnostics/CarryLookaheadAdder4Run2/CarryLookaheadAdder4Run2.Nand.dot
```

Full raw bundles are versioned only when they are named evidence for a design
milestone. Disposable exploratory captures belong under
`Output/DesignSnapshots/RoutingAwarePlacementAccess/`.

## Snapshot entry template

```text
### <UTC timestamp> - <snapshot ID> - <title>

Status:
CapturedAtUtc:
CapturedAtLocal:
Milestone:
Source revision:
Branch:
Working-tree identity:
Machine bundle:
Schema version:
Exact-byte evidence identity:
Portable-semantic evidence identity:
Publication state and crash caveat:
Commands:
Artifact identities:
Code identities:
Timers and resource evidence:
Failure or success boundary:
Correctness and publication gates:
What this proves:
What this does not prove:
Interpretation:
Decision or deviation:
Next action:
```

## Snapshots

### 2026-08-28T00:03:58Z - RAPA-S0-pre-design - current CLA4 baseline

**Status:** Captured; native matrix passes FullAdder/RCA4/RCA8 but CLA4 is
failed and the complete design remains not accepted.

**CapturedAtUtc:** `2026-08-28T00:03:58Z`

**CapturedAtLocal:** `2026-08-27T20:03:58-04:00`

**Milestone:** `RAPA-S0`, before routing-aware placement/access implementation.

**Source revision:**
`1681514368979f2cca1635b90b7f27062a966e33`
(`1681514 Restore proven pre-route routing stack`).

**Branch:** `main`.

**Artifact source state:** The CLA4 failure artifact records the same full
revision and `Dirty=false`. Its source state therefore predates the current
untracked benchmark and visualization additions.

**Capture working-tree identity:** Tracked source was unchanged. The complete
porcelain listing contained 14 nonignored untracked files belonging to the
existing Freerouting benchmark and synthetic-placement visualization work:

```text
?? Docs/Testing/FreeroutingBenchmark-2026-08-25.md
?? Scripts/RunFreeroutingBenchmark.py
?? Tests/test_freerouting_benchmark.py
?? Tools/ExternalRouters/Freerouting/.gitignore
?? Tools/ExternalRouters/Freerouting/LICENSE-GPL-3.0
?? Tools/ExternalRouters/Freerouting/README.md
?? Tools/ExternalRouters/Freerouting/UPSTREAM.md
?? Tools/ExternalRouters/Freerouting/Upstream.json
?? Tools/ExternalRouters/Freerouting/Upstream/.gitkeep
?? Tools/Visualizations/SyntheticPlacements/.gitignore
?? Tools/Visualizations/SyntheticPlacements/README.md
?? Tools/Visualizations/SyntheticPlacements/index.html
?? Tools/Visualizations/SyntheticPlacements/package-lock.json
?? Tools/Visualizations/SyntheticPlacements/package.json
```

SHA-256 of the exact newline-terminated output from
`git status --porcelain=v1 --untracked-files=all`:
`63ce42227e30495fd9666156be89be4b9e6fb64364fa50a088c806ce148bef19`.

**Commands:**

```bash
git rev-parse HEAD
git branch --show-current
git status --porcelain=v1 --untracked-files=all
sha256sum /tmp/redstone-timegraph-20260825-2128/Diagnostics/CarryLookaheadAdder4Run2/CarryLookaheadAdder4Run2.RoutingFailure.json
sha256sum /tmp/redstone-timegraph-20260825-2128/Diagnostics/CarryLookaheadAdder4Run2/CarryLookaheadAdder4Run2.Nand.json
sha256sum /tmp/redstone-timegraph-20260825-2128/Diagnostics/CarryLookaheadAdder4Run2/CarryLookaheadAdder4Run2.Nand.dot
sha256sum /tmp/redstone-timegraph-20260825-2128/2026-08-25/RouterRegression/StandaloneAcceptance/AcceptanceManifest.json
```

**Artifact identities:**

| Artifact | SHA-256 |
| --- | --- |
| `CarryLookaheadAdder4Run2.RoutingFailure.json` | `c4f57aad994f168e47fb6165f6858bbb3898ec3b31507503cb394b7e95736ebb` |
| `CarryLookaheadAdder4Run2.Nand.json` | `5593b4536c985b3d99a9aa9ff20a94e6ddcb105741f21df21a694b1f980b424f` |
| `CarryLookaheadAdder4Run2.Nand.dot` | `dd06752150a1bad7972e3adb461aea2c56655395a41c1f3fbfccda153fdb1207` |
| native `AcceptanceManifest.json` | `787d8adff005466fc9da866dc930b1b7973365c96a3049f039ebcaabddfcadae` |

**Relevant code identities:**

| File | SHA-256 | Current responsibility |
| --- | --- | --- |
| `Compiler/Cells/Library.py` | `fc8353e104a8c1e334b470388139bae76150a065f40bb43362ebd4b2ef93f09b` | cell and straight pin-access declarations |
| `Compiler/Placement/Pcb.py` | `ee4111b3399363e8bc8c4293f289931f5746ea5ee09d7bd7f1447c2af6a91870` | clustering, joint placement, exact access screen |
| `Compiler/Placement/PcbFlow.py` | `e8e2fc6c43d905e57391ab0cc6ede6eb84da94486cc5522b2f8876c862c7de03` | placement/routing lifecycle and current terminal failure |
| `Compiler/Routing/Models.py` | `e9b426773d8bf777fcb8bbc5ddff7bb20cd096deac2ceeecc3942ed8438db81f` | physical factor, port, CSP, and routed-macro models |
| `Compiler/Routing/ComponentPlanning.py` | `695cb8d9c322f9c6ac13d93ec3fd083c35c454fd60977d5897aa8b8767412777` | component interface CSP |
| `Compiler/Routing/AuthoritativePlanner.py` | `0e9c10b5d9247cf80f5ac79e311da9cdc6921d91b3c8aefb988ccea516007895` | physical port factors, negotiated routing, materialization |
| `Compiler/Routing/ResourceGraph.py` | `60be1999fd8af6bf2698e9ad2c5f1ded76375128af703772ae84640e5889bfbd` | physical resource graph and claims |
| `Compiler/Routing/Actions/Validation.py` | `b27a8d3f5b4a896c31d026159fc3e0a9afb2b91b08a81fe2b38b8b644fb39230` | physical route connectivity validation |
| `Compiler/Simulation/Redstone.py` | `3b680ae33b5e1bc018e2d3da91e8dd4915d26cdc51467ed06d8019131066a869` | physical delivery and truth-table simulation |
| `Compiler/Pipeline.py` | `84416b084cf66e4d0a6100d7d6648bd92141f684d03e9762ba996f2f59362316` | final validation, evidence, and staged multi-file publication |

**Audited implementation-source metrics:** The explicit core scope comprised 67
Python/Rust files, 171,085 physical lines and 165,424 nonblank lines. Python
accounted for 57 files and 141,883 physical lines; Rust accounted for 10 files
and 29,202 physical lines. The aggregate source-content digest was
`ce592607e9eef6ffc2d3b2527659dc0c1271644b37db39eac360a52806f9d88e`
under `routing-implementation-source-v1`.

Largest Python AST definition spans:

| Definition | File | Span lines |
| --- | --- | ---: |
| `_PlaceAndRoutePcbWithPolicy` | `Compiler/Placement/PcbFlow.py` | 16,947 |
| `RouteAuthoritativeResources` | `Compiler/Routing/AuthoritativePlanner.py` | 16,002 |
| `PlacePcbGraph` | `Compiler/Placement/Pcb.py` | 5,402 |
| `SolvePreparedPhysicalComponentPortFactorDomain` | `Compiler/Routing/AuthoritativePlanner.py` | 4,126 |
| `PlanNegotiatedRouteTrees` | `Compiler/Routing/AuthoritativePlanner.py` | 3,973 |

These are deterministic AST source spans, not cyclomatic complexity,
executability, or proof that every line is active.

**Failure and timing evidence:**

| Attempt | Elapsed | Claims | Exact conflict |
| --- | ---: | ---: | --- |
| `row-beam` | `13.810852 s` | 8,814 | two electrical resources, `NandNet0` / `Propagate0`, at `(16,1,5)` and `(17,1,5)` |
| `row-beam-direct-only` | `2.488325 s` | 8,808 | two electrical resources, `NandNet0` / `NandNet2`, at `(6,1,5)` and `(7,1,5)` |

The final artifact records runtime `16.376594 s`, stage `Placement`, reason
`PlacementOverlap`, and detail
`no exact-legal placement candidate was generated`. The overall routing
deadline was not expired and had `101700 ms` remaining. Native request counts
and stage timings are empty because detailed routing was not entered.

**Correctness and publication gates:** No CLA4 `.PhysicalDesign.json`, truth
table, or litematic was published. There is therefore no CLA4 resource-conflict,
connectivity, simulation, rendered-block, fallback, or determinism success to
report. The protected native evidence did accept FullAdder 5/5, RCA4 3/3, and
RCA8 3/3 with exact truth-table artifacts; those gates do not substitute for
CLA4.

**What this proves:**

- The current CLA4 blocker is placement/fixed-access legality before detailed
  routing, not timeout exhaustion.
- Both attempted current placements are close in the narrow sense that exactly
  two mandatory electrical resources conflict in each measured profile.
- Improving detailed A*, negotiated rerouting, worker count, or route deadline
  alone cannot affect this failure boundary.
- Current fixed-access screening constructs thousands of exact claims before
  rejecting the complete candidate.

**What this does not prove:**

- that an alternate access pattern exists for either placement;
- that either placement is globally routable after access repair;
- that the declared access catalog would be complete;
- that a complete CLA4 placement is impossible;
- that the proposed solver is fast enough; or
- that external synthetic PCB routing validates native Redstone correctness.

**Interpretation:** Make access geometry selectable and exact while cluster
placement variables remain live. Freeze one selected witness and route it once.
If the complete local domain is impossible, split, move, or widen only the
proof-core structure.

**Decision or deviation:** This baseline changes documentation only. It does
not modify the compiler, policy, router, simulator, or acceptance harness.

**Next action:** Implement Phase 1 from the design: make the existing straight
pattern travel through one authoritative option/claim/frozen-witness path and
prove parity before adding alternate patterns.

### 2026-08-28T00:17:26Z - RAPA-S0-documentation-capture - durable machine bundle

**Status:** Captured. Documentation and snapshot tooling are implemented;
routing behavior remains unchanged and CLA4 remains failed.

**CapturedAtUtc:** `2026-08-28T00:17:26Z`

**CapturedAtLocal:** `2026-08-27T20:17:26-04:00`

**Milestone:** Durable machine-readable projection of `RAPA-S0` after adding
the proposed design, append-only record, generator, and focused tests.

**Machine bundle:**
[20260828T001726Z/Snapshot.md](../Snapshots/RoutingAwarePlacementAccess/20260828T001726Z/Snapshot.md)
with raw copied artifacts and
[Snapshot.json](../Snapshots/RoutingAwarePlacementAccess/20260828T001726Z/Snapshot.json).

**Snapshot ID:** `20260828T001726Z-5cf76bd100006383`.

**Canonical evidence SHA-256:**
`5cf76bd100006383702ac28c9b7e3e86f10b5cb579f58ae053f9429ca87a0956`.

**Source identity:** revision
`1681514368979f2cca1635b90b7f27062a966e33`, 67 core implementation files,
171,085 physical lines, aggregate
`ce592607e9eef6ffc2d3b2527659dc0c1271644b37db39eac360a52806f9d88e`.
The capture reads detailed Git state before publishing its own fresh output
directory, so the bundle does not claim its newly created files were already
present in the captured status.

**Copied evidence:** native acceptance manifest, CLA4 failure JSON, NAND JSON,
and NAND DOT. `SHA256SUMS` validates all copied artifacts plus the generated
JSON and Markdown projections.

**Focused verification:** `python3 -m unittest
Tests.test_routing_design_snapshot -v` passed 9/9 in `0.915 s`; the bundle's
`sha256sum --check SHA256SUMS` passed 6/6.

**What this proves:** The documented baseline is durably tied to exact source,
checkout, failure, netlist, and acceptance-manifest identities. The generator
preserves typed `PlacementOverlap` with remaining deadline as non-timeout
evidence and requires a fresh target.

**What this does not prove:** No compiler or routing implementation changed,
and no new physical acceptance gate ran. This snapshot is not `RAPA-S1`.

**Next action:** Begin the straight-only authoritative access catalog and frozen
witness parity slice defined as Phase 1.

### 2026-08-28T00:41:31Z - RAPA-S0-v1-identity-correction - schema-v2 correction capture

**Status:** Captured and checksum-verified. This corrects the v1 identity
interpretation; it is not a new routing implementation milestone.

**Corrects:** The identity interpretation attached to
`20260828T001726Z-5cf76bd100006383` above. The v1
`CanonicalEvidenceSha256`
`5cf76bd100006383702ac28c9b7e3e86f10b5cb579f58ae053f9429ca87a0956`
is a valid digest of the reduced v1 canonical JSON projection. It does not bind
the exact bytes of the complete published bundle, and its retained checkout
and generator records make its portability narrower than the explicit v2
portable-semantic contract. More importantly, that projection retained hashes
of raw JSON artifacts whose bytes embed absolute checkout and output paths, so
path-only changes can change the v1 digest. The v1 capture also lacked the v2
double-read source/runtime guard, acceptance-manifest validation, and explicit
success-artifact absence check. It must not be described as either v2
identity.

The v1 bundle and its `SHA256SUMS` remain preserved evidence. This correction
does not rewrite or invalidate their individual byte hashes, typed CLA4
failure, source metrics, or recorded checkout state.

**Replacement v2 capture:**

- Captured at UTC: `2026-08-28T00:41:31Z`
- Captured locally: `2026-08-27T20:41:31-04:00`
- Bundle directory:
  [`20260828T004131Z`](../Snapshots/RoutingAwarePlacementAccess/20260828T004131Z/Snapshot.md)
- Snapshot ID: `20260828T004131Z-ef7006f0d411380b`
- Exact-byte evidence SHA-256:
  `23fd874ccc015a9629feb54eec4aeda1a2c550ba44869467268f8efc27b3f5e4`
- Portable-semantic evidence SHA-256:
  `ef7006f0d411380b6d581b37300f808de5d23e5a52f26695c9901e2511e7e30f`
- `SHA256SUMS` verification: `6/6 passed`
- Focused v2 tests: `13/13 passed`

The v2 acceptance summary validated schema v2, the full authoritative
5/3/3/2 case matrix, failure/manifest source revision, failure/manifest policy,
CLA4 input identity, current routing source, benchmark inputs, tracked physical
templates, Cargo/build inputs, loaded native extension, and current default
policy. Every recorded cross-check is `true`. The failure remains
`Placement / PlacementOverlap`, runtime `16.376594 s`, deadline unexpired with
`101700 ms` remaining, and no detailed routing or success artifact.

**Required interpretation after replacement:** The exact-byte identity answers
whether the schema-defined captured evidence bytes are identical. The
portable-semantic identity answers whether two captures express the same
normalized routing evidence despite permitted time/location/publication
differences. Neither identity proves routing acceptance, and a completed final
directory does not prove crash-durable or transactional publication.

### 2026-08-28T00:54:27Z - RAPA-S0-v2-publication-identity-guard - hardened final capture

**Status:** Captured and checksum-verified. Snapshot-tool hardening only; no
routing implementation or acceptance result changed.

**Corrects:** The schema-v2 generator captured at `20260828T004131Z` computed
correct identities, and that bundle still recomputes and verifies. However,
its staged writer trusted the mutable in-memory document after those identities
were built. An API caller could alter a semantic field before publication and
produce an internally inconsistent new bundle.

`WriteSnapshotStaged` now calls `ValidateSnapshotIdentities` before creating
the output root. It recomputes exact and portable-semantic identities, validates
the schema/timestamps, derives the expected snapshot ID, and rejects any
mismatch. A focused adversarial test mutates `Cla4Failure.Detail` after build
and proves that publication is rejected and no output root is created.

**Final hardened bundle:**

- Captured at UTC: `2026-08-28T00:54:27Z`
- Captured locally: `2026-08-27T20:54:27-04:00`
- Bundle directory:
  [`20260828T005427Z`](../Snapshots/RoutingAwarePlacementAccess/20260828T005427Z/Snapshot.md)
- Snapshot ID: `20260828T005427Z-cbf53696cb418ae4`
- Exact-byte evidence SHA-256:
  `4e2c1c0b6bc5a3d56e2d8754c4853735495f23b48030c1045a4e9c5692c84b12`
- Portable-semantic evidence SHA-256:
  `cbf53696cb418ae444b25423fe3d219d20dc7c367f901d2a3ee6d93d4a1d9a68`
- `SHA256SUMS` verification: `6/6 passed`
- Aggregate identity recomputation: exact and portable both matched
- Focused v2 tests: `14/14 passed`
- Acceptance-manifest cross-checks: `10/10 true`

The evidence boundary is unchanged: CLA4 remains
`Placement / PlacementOverlap`, `16.376594 s`, not timed out, with no detailed
routing and verified absence of litematic, physical-design, and truth-table
success artifacts. This is still `RAPA-S0`, not `RAPA-S1`.

### 2026-08-28T15:16:09Z - monolith-split-final - behavior-preserving ownership split

**Status:** Implemented and structurally verified. FullAdder, RCA4, and RCA8
retain exact physical acceptance; CLA4 retains its typed placement failure, so
the larger routing-aware-placement design remains unaccepted and this is not a
v17 implementation milestone.

**CapturedAtUtc:** `2026-08-28T15:16:09Z`

**CapturedAtLocal:** `2026-08-28T11:16:09-04:00`

**Milestone:** Clean-break Python/Rust monolith split with nested Rust domain
folders, exact semantic parity, and the final five-percent runtime verdict.

**Source revision and state:** Revision
`1681514368979f2cca1635b90b7f27062a966e33` on `main`, with the complete dirty
working tree captured. The implementation-source scope contains 217 files and
132,941 physical lines: 160 Python files / 103,143 lines and 57 Rust files /
29,798 lines. Its aggregate source SHA-256 is
`057655f65674ce1c1f6e46c38cc6e6ac458ae0903b32406e7e5402da7f1169f9`.

**Final machine bundle:**
[`20260828T151609Z`](../Snapshots/RoutingAwarePlacementAccess/20260828T151609Z/Snapshot.md),
with [Snapshot.json](../Snapshots/RoutingAwarePlacementAccess/20260828T151609Z/Snapshot.json)
and the primary and performance-rerun manifests. The immediately preceding
`20260828T151513Z` bundle is preserved as an intermediate exact capture; it did
not copy the required RCA8 rerun manifest and is not the final performance
evidence bundle.

- Snapshot ID: `20260828T151609Z-2c2132084dda3215`
- Exact-byte evidence SHA-256:
  `06d4b8149212ab611814af1dad6af25181b75f782f4d3a3b6b86f7b87136a539`
- Portable-semantic evidence SHA-256:
  `2c2132084dda3215ab17b96b22e639e804ba0f2dd3d1ca3e89496866811f34df`
- Primary acceptance-manifest SHA-256:
  `b5668b6e67bb7fd52cfe045181e9305226ca6cfe790092dc889585a6d5f180ec`
- RCA8 rerun-manifest SHA-256:
  `15a1383f08559570d8fa9768b72ad7f0fa465c4fcc65be99f0b3b3b51097a27f`
- `SHA256SUMS`: `7/7 passed`
- Acceptance cross-checks: `10/10 true`

**Structural evidence:** The former placement, routing-model, component,
authoritative-planner, and flat Rust paths are deleted without forwarding
modules. The Compiler import graph is acyclic and the dependency layers are
one-way. The largest Python implementation module is 2,942 lines and largest
Python function is 999 lines. The largest Rust implementation module is 2,599
lines, largest Rust function is 988 lines, and largest extracted phase macro is
873 lines. The largest declared orchestrator is 355 lines; authoritative and
placement public runners are 24 and 58 lines, and native `Lib.rs` is 19 lines.

Rust now has real nested `Escape/Candidates/`, `Escape/Catalog/`,
`Generation/DetailedTrees/`, and `Generation/DetailedTrees/Phases/` folders.
The rebuilt extension was both installed and loaded from
`RedstoneCompiler/RustRouting.cpython-312-x86_64-linux-gnu.so`, SHA-256
`9750ecb2752be302ecf789e1bbc739f19886a0a9529d3895144d2e39435c956e`.
Rust release tests passed 56/56.

**Python parity:** Collection grew from 1,301 to 1,316 with every originally
tracked test identity retained. The final suite result is 1,284 passed, eight
baseline-known behavior/fixture failures, 24 skipped, and 221 passing subtests
in 47.04 s. The pre-refactor result was 1,266 passed, 11 failures, 24 skipped,
and 221 passing subtests in 47.06 s. Three old source-owner/deadline assertions
now pass after migration; no new semantic failure appeared. Contract schema,
signature, cache, worker-import, source-structure, and snapshot tests pass. The
three exact source-independent fixture hashes remain byte-identical.

**Physical acceptance and wall medians:**

| Circuit | Runs | Baseline median | Post-refactor verdict median | Change |
| --- | ---: | ---: | ---: | ---: |
| FullAdder | 5 | `5.231127 s` | `5.270685 s` | `+0.756%` |
| RCA4 | 3 | `6.978186 s` | `7.082595 s` | `+1.496%` |
| RCA8 | 3 + one required complete-case rerun | `10.671757 s` | `10.692462 s` combined | `+0.194%` |

All accepted runs have exact truth tables, zero conflicts, zero unresolved
claims, `FallbackUsed=false`, stable repeated fingerprints, and fingerprints
identical to the pre-refactor matrix. Reported-runtime medians changed by
`-0.050%`, `+1.098%`, and `-0.172%` for FullAdder, RCA4, and combined RCA8.

The first RCA8 sample set had a `+5.686%` aggregate internal timer, which
triggered the specified one-time complete-case rerun. Its combined six-run
medians are assignment `+2.329%`, candidate generation `+0.320%`, and total
`+3.256%`. FullAdder's qualifying internal medians are `-0.210%` and
`-0.159%`; RCA4's are `+2.862%`, `-6.316%`, and `+3.380%`. Every wall median
and every internal stage whose baseline median is at least 100 ms is therefore
within the five-percent ceiling.

**CLA4 boundary:** The primary post-refactor run failed after `16.394414 s`
wall / `16.187578 s` reported runtime at
`Placement / PlacementOverlap`: `no exact-legal placement candidate was
generated`. `TimedOut=false`; the overall deadline was unexpired with 101,888
ms remaining, detailed routing did not start, and no success artifact was
published. Its two mandatory-access attempts preserve the same two exact
electrical conflicts and signal pairs as the baseline.

**What this proves:** Ownership, dependency direction, public/native API shape,
typed failures, deterministic physical results, artifact semantics, and the
declared performance bound survived the clean break. The Rust source is a real
multi-level domain tree rather than a renamed flat monolith.

**What this does not prove:** It does not make CLA4 routable, implement
routing-aware placement/access v17, eliminate the eight known fixture failures,
or establish that the remaining files are the final optimal decomposition.

**Next action:** Treat the clean-break tree as the new owner map. Address CLA4
placement/access legality and the eight existing failures only in separately
scoped behavioral changes with fresh acceptance evidence.

# Routing benchmarks

| Benchmark | Matrix | Purpose | Runs | MCHPRS vectors | Fabric canaries | Wall ceiling |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| HalfAdder | Expanded | Small two-input arithmetic check | 1 | 4 | 4 | 10 s |
| FullAdder | Default and expanded | Small correctness and deterministic overhead gate | 1 | 8 | 8 | 15 s |
| RippleCarryAdder4 | Default and expanded | Repeated-stage congestion and regression gate | 1 | 512 | 20 | 25 s |
| RippleCarryAdder8 | Default and expanded | 8-bit carry ripple scalability gate | 1 | 131072 | 36 | 30 s |
| DecimalToBinary4 | Expanded | One-hot decimal encoder check | 1 | 1024 | 22 | 30 s |
| TFlipFlopLatch | Expanded | Explicit-state toggle/latch logic check | 1 | 8 | 8 | 15 s |
| CarryLookaheadAdder4 | Expanded | Exact-interface proof check | 1 | 512 | 20 | 120 s |

Full acceptance requires zero final conflicts, zero unresolved claims,
identical repeated fingerprints, no fallback, a durable Fabric fixture, and
a passed MCHPRS record plus the required Fabric-server canaries. The retired
`*.TruthTable.txt` simulator artifact is not an acceptance gate. MCHPRS is
exhaustive through 20 inputs; wider designs use deterministic edge cases plus
4,096 samples. Fabric remains the final Minecraft correctness gate.

## Saved run reports

Compiler runs write immutable evidence beneath
`Output/<Circuit>/Runs/<UTC run id>/`. `Summary.txt` always starts with result,
total time and CPU utilization. Compiler summaries then report the bounded
routing interval, each named routing sub-stage, and authoritative validation
time before the optional CPU breakdown, one-line output, and raw-report path.
The terminal closes the routing progress bar before opening separate MCHPRS
validation and Fabric-canary bars. Each bar starts at authoritative `0/N` and
advances only after a vector has settled and been compared; it never presents
placeholder or inferred progress.
`RawDump.txt` retains full
stdout/stderr, Git and runtime provenance, stage telemetry, typed failure
evidence, validation gates, and an artifact size/hash inventory. Only a fully
successful run atomically refreshes the stable artifacts directly under
`Output/<Circuit>/`.

## Acceptance matrix

The default acceptance matrix is exactly FA/RCA4/RCA8. Select
`--matrix expanded` to run all seven bundled examples: HalfAdder, FullAdder,
RCA4, RCA8, DecimalToBinary4, TFlipFlopLatch, and CLA4. CLA4 retains its
fixture-backed exact-interface proof checkpoint. Both matrices execute every
scheduled repetition even after an earlier failure; the overall session fails
if any required run or correctness gate fails. Expanded mode is standalone and
cannot capture or compare the historical regression baseline.

Normal default and expanded acceptance execute each selected circuit exactly
once. Specialized baseline capture/comparison retains its historical repeated
sampling contract so existing version-one baseline evidence remains readable.

## Immutable benchmark archives


Every executed `redstone-benchmark` or `RunRouterAcceptance.py` default or
expanded matrix automatically writes into a new immutable-by-contract archive:

```text
<output-root>/<date>/Archives/
  <YYYYMMDDTHHMMSS.ffffffZ>-<12-character-commit>/
```

The archive ID gains `-dirty-<12-character-status-sha256>` when the checkout
has staged, unstaged, or ordinary untracked changes. The digest is computed
from Git porcelain-v2 NUL-delimited status bytes. Ignored build caches,
`Runtime/FabricServer/`, and prior `Output/` evidence therefore do not make a
checkout dirty. The full commit, full status digest, branch or detached state,
source-content digest, and start/end identities remain in
`ArchiveManifest.json`.

Ordinary runs execute directly in the unique archive, so a same-day run cannot
inherit stale evidence. The top-level `Summary.txt` is the surface view headed
by `RESULT`, `TIME`, optional `CPU`, and `OUTPUT`. `RawDump.txt`,
`AcceptanceManifest.json`, circuit reports and logs, compiler-run telemetry,
typed failures, fixtures, and schematics preserve the complete session.
`ArchiveManifest.json` uses schema `router-benchmark-archive-v1`; its compact
run surface distinguishes passed, failed, skipped, timed-out, validation, and
missing-artifact states. `SHA256SUMS` covers every regular archive file except
itself. Archive sealing rejects symlinks and never overwrites or merges an
existing archive directory.

Routing identity is preserved separately from the benchmark verdict. The
configured request, exact command alias, resolved used strategy, fallback state,
and complete canonical policy snapshot identity flow from source provenance into
each evaluator receipt and the archive run surface. A failed backend can
therefore retain a consistent routing receipt without becoming accepted.
Missing success/failure artifacts and process timeouts retain configured facts
but leave observed identity unknown. Stored boolean checks never override a
contradictory raw receipt. Source-content and template aggregates are recomputed
from their complete listed records before snapshot use.

A benchmark failure remains a failure even when its evidence seals correctly.
An otherwise successful benchmark becomes nonzero when archive publication
fails and prints `Archiving: write-failed`. Interrupted or unexpected harness
failures retain their unique directory with an `INTERRUPTED` or `PARTIAL`
manifest whenever best-effort finalization succeeds. The command always prints
the absolute archive path for an attempted archived run.

Use `--no-archive` only for deliberately disposable execution. `--dry-run`
never creates an archive. Archives are uncompressed and have no automatic
retention or pruning policy.

The automatic archive is separate from the promotable v15 regression baseline.
`--capture-baseline` and `--compare-baseline` keep their fixed recovery paths,
sampling, compatibility policy, reference promotion, and overwrite protection;
after completion, that session is mirrored wholesale into a separate
commit-stamped archive.

This functionality was extracted from the archive portion of legacy R1 commit
`14646a9`, including its later archive-aware strategy integration in `b8160bb`.
It belongs to the Telemetry-And-Acceptance bucket (R8/N5), not R1 lazy expansion.
Joint-Physical-Design owns the v17-default policy. The archive mechanism is
policy-neutral and records the strategy supplied by its checkout. Historical
baseline modes retain their original policy/interpreter checks; use the matching
source checkpoint rather than presenting a current-policy run as an older baseline.

## Versioned routing-design acceptance profiles

`CaptureRoutingDesignSnapshot.py` publishes
`routing-design-snapshot-v3`. The acceptance producer remains on
`router-acceptance-manifest-v2` and does not declare a snapshot profile.
Instead, the exporter selects `routing-design-acceptance-profile-v1` only when
the complete existing manifest has one of two exact interpretations:

- `legacy-four`: FullAdder, RippleCarryAdder4, RippleCarryAdder8, and
  CarryLookaheadAdder4 under `MatrixMode=default`. Standalone runs use one
  occurrence per case and the current 15/13 second FullAdder ceiling/deadline;
  historical capture/compare evidence retains 5/3/3/2 measured runs and the
  original 10/8 second FullAdder ceiling/deadline. Historical modes retain the
  single FullAdder warmup occurrence separately from measured runs.
- `expanded-seven`: HalfAdder, FullAdder, RippleCarryAdder4,
  RippleCarryAdder8, DecimalToBinary4, TFlipFlopLatch, and
  CarryLookaheadAdder4 under `MatrixMode=expanded`, sequential execution,
  null baseline mode, literal `FailFast=false`, and one measured occurrence per
  case.

The exporter authority record contains the selected profile ID, canonical
profile SHA-256, normalized case table and count, matrix mode, and baseline
mode. Its case table states the independently interpreted MCHPRS rows and
Fabric canary counts; it does not claim that the older producer emitted those
new field names. Inputs cannot supply or override a profile ID. A versioned
profile summary requires a complete verified archive seal; an unsealed manifest
cannot supply legacy compatibility, a typed failure, stage/reason, or downstream
`not-run` evidence.

Selection validates exact case metadata, run occurrences, input/top/deadline/
strategy command options, producer source and policy provenance, and routing
receipts. Run status and acceptance remain their exact producer values; numeric
or other truthy substitutes are rejected. Every evaluator failure and artifact
presence/type/size/hash record is projected. A passed downstream backend state
requires a complete accepted receipt whose Fabric count matches the selected
profile. A hash-bound typed routing failure proves downstream `not-run`;
missing evidence remains `unknown`. Profile validity alone never changes a
benchmark verdict.

Per-run failure stage and reason come only from the evaluator-recorded
`FailureArtifactResolution.Path` when it exactly matches the evaluator's
`RoutingFailure` size and SHA-256. The selected bytes must be a regular,
non-symlink file at a normalized relative path inside the archive; empty, dot,
dot-dot, absolute, or otherwise escaping suffixes are rejected. The exporter
neither searches run trees nor chooses a newest failure. Sealed archive inputs
are checksum-verified and retained separately from current exporter checkout,
generator, source, and runtime provenance.

Archive publication and acceptance evaluation use owner-local safe readers
with the same contract: every ancestor is opened through a retained directory
descriptor with `O_DIRECTORY|O_NOFOLLOW`, and the leaf uses `O_NOFOLLOW`,
nonblocking open, and regular-file `fstat` validation. Platforms without those
safe primitives fail closed. Validation, hashes, and artifact records consume
the bytes and identity from that one descriptor; they do not reopen the
pathname after validation. A concurrent leaf or parent substitution therefore
cannot redirect evidence to outside bytes.

Archive sealing captures its complete inventory through one retained archive-
root descriptor. `ArchiveManifest.json` inventory entries and `SHA256SUMS`
lines are derived from the same captured bytes, followed by a complete safe
verification before publication returns `SEALED`; a changed member leaves the
publication non-sealed and raises. Legacy capture/compare trees are mirrored by
recursive descriptor-relative, non-following reads rather than a scan followed
by pathname-copy traversal. Their source root remains lexical until every
ancestor and the root itself have been opened with no-follow directory flags;
a source-root symlink is rejected. Replacing that pathname after acquisition
cannot redirect the retained root descriptor to a different real directory.
The staging root is likewise retained while generated files are written,
inventory and checksums are derived, and the complete seal is verified; no
source or staging tree is re-resolved, recursively scanned, or pathname-copied
after root selection.

Rejected mirroring also does not perform unconditional pathname cleanup after
the staging descriptor has been acquired. If the staging name no longer
identifies that opened directory, the replacement path is left untouched. A
rejected operation may therefore leave its original unsealed, hidden
`.tmp-P<process>` staging directory for ordinary ignored-artifact cleanup; that
is preferred to traversing or deleting a different filesystem object that was
substituted at the lexical name.

Snapshot v3 likewise observes `ArchiveManifest.json`, `SHA256SUMS`,
`AcceptanceManifest.json`, and every sealed member through one retained root
descriptor. Its process-local observation cache supplies JSON parsing,
selected-failure projection, artifact size/hash records, and staged artifact
bytes. Replacing a leaf, ancestor, or the archive-root pathname after that
observation cannot change the snapshot, and a deserialized snapshot without
the retained byte cache cannot publish artifacts.

## Historical acceptance sweep — 2026-08-03

The following retained evidence is historical and must not be presented as the
current checkout result. Establish current status with a fresh output root and
manifest.

### Execution commands

- Default sequence:
  - `python Tools/Routing/RunRouterAcceptance.py --output-root Output/Acceptance/Pass3 --date 2026-08-03 --python .venv/bin/python`
- Extended sequence with CLA4:
  - `python Tools/Routing/RunRouterAcceptance.py --output-root Output/Acceptance/Pass3Compat --date 2026-08-03 --python .venv/bin/python --include-cla4`

### Results

| Circuit | Mode | Runs | Runtime s (min/mean/max) | Routing length | Routing bends | Routing vias | Conflicts | Footprint | Full footprint | Status |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| FullAdder | Default | 5 | 1.077084 / 1.101263 / 1.116426 | 111 | 31 | 24 | 0 | 544 | 2720 | PASS |
| RippleCarryAdder4 | Default | 3 | 8.537964 / 8.748257 / 8.876852 | 595 | 237 | 203 | 0 | 2400 | 16800 | PASS |
| RippleCarryAdder8 | Default | 3 | 8.181562 / 8.311903 / 8.409309 | 1068 | 327 | 312 | 0 | 4768 | 33376 | PASS |
| CarryLookaheadAdder4 | Extended | 1 | Failed | n/a | n/a | n/a | n/a | n/a | n/a | FAIL |

Strict acceptance manifest: `Output/Acceptance/Pass3/2026-08-03/RouterRegression/StandaloneAcceptance/AcceptanceManifest.json`

Extended CLA4 acceptance manifest: `Output/Acceptance/Pass3Compat/2026-08-03/RouterRegression/StandaloneAcceptance/AcceptanceManifest.json`

### Failure notes

- The extended CLA4 run failed on `CarryLookaheadAdder4Run1` with:
  - process exit code 1
  - `ClusterInterfaceSolveIncomplete`
  - routing stage reserve timeout (`98.568s` elapsed in routing reserve window)
  - missing required artifacts: `Schematic`, `FabricFixture`, `PhysicalDesign`

CarryLookaheadAdder4 remains an explicit extended gate and is not part of the
default runtime path.

Current diagnosis must use fresh `.RoutingFailure.json` artifacts and an
acceptance manifest produced for the checkout under test. The
[physical-design architecture review](../Architecture/PhysicalDesignArchitectureReview.md)
and [running-tests guide](RunningTests.md) describe the current boundaries and
commands.

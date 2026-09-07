# R8 notes

Working notes for [R8](R8.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- Source-content stability in retained benchmark reports is tri-state and is
  computed only from two complete observations. Each observation requires a
  canonical SHA-256 digest and an exact nonnegative integer file count. Missing,
  empty, incomplete, or invalid observations remain unknown; complete hash or
  count drift is unstable. The final provenance-check entry is the authoritative
  end observation; malformed newest evidence cannot be replaced by an older
  valid entry.
- Public routing-design snapshots independently validate the raw routing receipt
  and preserve applicable evaluator rejections. Exact boolean `false` is the
  only valid no-fallback value; integer zero is not equivalent evidence.
- Routing-design snapshot v3 selects its acceptance profile in the exporter.
  A producer cannot declare or override `legacy-four` or `expanded-seven`.
  Producer manifest/archive provenance and current exporter provenance remain
  distinct records and are cross-checked without rewriting producer identity.

## Open questions

- None recorded.

## Working notes

- **Versioned acceptance and validated reads (committed and consumed).** Telemetry
  commit `e274e79` is the current committed checkpoint; Router commit `7c7b52b`
  consumes it exactly. The bounded R8/N5 exporter
  update recognizes only the exact existing four-case legacy and seven-case
  expanded manifest interpretations and publishes
  `routing-design-acceptance-profile-v1` authority inside
  `routing-design-snapshot-v3`. It retains v2 fields, exact statuses and
  booleans, complete evaluator failure/artifact projections, routing receipts,
  and separate producer/exporter provenance. Every profile summary requires a
  complete verified archive seal. Per-run stage/reason is read only from the
  evaluator-selected, size/hash/seal-bound regular in-archive failure;
  absent evidence stays unknown and a typed upstream stop marks downstream
  validation `not-run`; an unsealed manifest is rejected. Snapshot, archive,
  and acceptance-runner artifact readers now use stable fd-relative,
  non-following ancestor and leaf opens and carry descriptor-derived bytes,
  size, and hash through validation and recording. Archive publication derives
  its inventory and checksum rows from the same captured bytes, verifies the
  complete result before sealing, and mirrors legacy trees without pathname-
  following copy traversal. A legacy source root remains lexical until its
  complete ancestor chain and root are opened without following symlinks; the
  opened source and staging roots then remain the sole authorities through
  mirroring, inventory, checksum generation, and final verification. Real-root
  replacement retains the original bytes, while a root symlink is rejected.
  Rejection cleanup never follows the staging pathname after descriptor
  acquisition: a substituted directory is left untouched, and the original
  unsealed hidden staging object may remain for ordinary ignored-output
  cleanup rather than risking deletion of a different object.
  Snapshot v3 observes the complete seal below one retained archive-root
  descriptor and uses its process-local byte cache for parsing, projection,
  hashes, and staged output; lexical escapes and later leaf/ancestor/root
  replacements cannot redirect evidence. Unsupported safe primitives fail
  closed. Routing, acceptance policy, the historical sealed archive, and its
  artifacts are unchanged. Final source hashes, replay identity, and
  verification totals are recorded in the task evidence root
  `Output/TelemetrySnapshotV3/20260907T060450Z-P1Correction/`; this is reporting
  capability, not new physical acceptance or promotion readiness.
- **Bounded acceptance-reporting repair.** The technically cleared pre-notes
  patch `aee4473d3e8f9b7d31a36920fcc67b26e4a1156b5afe6088c692335e48901420`
  keeps CLA4's 512-row MCHPRS requirement separate from its 20 Fabric canaries.
  It binds direct or nested failures to one fresh invocation using the complete
  lexical command, full output/source/effective-policy identity, and literal
  boolean `false` fallback. Evaluator, archive, exact-proof, and the supported
  per-run public snapshot projection share that authoritative receipt. Rejected
  regular bytes remain inventoried; symlinks and non-regular entries are
  classified without reading targets. Focused verification passed 147 tests
  and 117 subtests; the frozen
  deterministic suite passed 1,624 tests with 4 skips and 305 subtests in
  74.21 s, the structural/schema gate passed 7 tests, and collection found
  1,628 tests. All 14 completeness checks passed.
- **Fresh corrected matrix.** The expanded run completed at
  `2026-09-07T03:52:41.716480Z` after 181.573 s under
  `Output/TelemetryBatch1/20260907T034837Z-corrected-expanded-aee4473/`.
  Its archive sealed with 110/110 checks, stable source/native/code-test hashes,
  no in-archive extraneous log, and every failure resolved as one nested receipt
  with strategy, reproduction, effective-policy, and literal-false-fallback
  checks true. All seven cases failed with exit code 1 and no timeout; none
  reached detailed routing, rendering, final claims, MCHPRS, or Fabric. The
  whole-manifest snapshot CLI rejected the seven-case expanded manifest because
  its fixed historical four-case guard reported `case set is not authoritative`;
  no production expansion of that guard is claimed. The final independent
  archive/per-run-projection/ledger audit passed 1,228 checks with zero errors,
  establishing the reporting capability on this failed-run chain. This is not
  full R8 lifecycle or physical acceptance.
- **Evidence identity.** `AcceptanceManifest.json` is
  `82ea8d983cae05431872a0c32e6d0411c31b9db229c9fc19e7fac408f0e8885b`,
  `ArchiveManifest.json` is
  `a5a5ce5e8e685eb41e94d4e2b68df6e6160031e536c1bd2ff43358c47adb1a93`,
  and `SHA256SUMS` is
  `5904d0c879f61e065f5efb5a8b796174b8de4d5c01d5b028bd8b844f2180d17b`.
  The final audit JSON is `149cd7d8...39f87`, its review is
  `b275a670...eed3d`, and the public ledger is `b2c9e34f...3c06a`. Loaded
  native and release bytes match at `c086...`, but this run does not prove
  native source-to-binary build provenance. Installed harness JAR `04f501...`
  was unchanged and its source build remains `UNVERIFIED`. The original
  `08a713c` archive and its
  disclosed 16-byte pre-seal review log remain unchanged.
- The completed repair is Telemetry commit `b8c1d4f6f75c3a29a931aeeea4205951fe80a7e2`,
  whose sole parent is the exact audited Joint checkpoint
  `c6d6a81d5bbdf920a51b8a899748671d731cecea`. Its source and tests were consumed
  unchanged by Joint `64efbe13c14c8c7445cba6dd6111256ce06f9a36` and then Router
  `bd3b9349d79d6b5794070c4bdf731d5231636de2`; those consumer records do not make
  this branch an integration owner.
- The retained committed Telemetry verification record is 1,613 passed, 4
  skipped, and 271 subtests in 71.65 s; it was not newly run for this
  documentation frontier. The corrected archive/harness/snapshot/real-chain
  set passed 149 tests and 83 subtests. Evidence is under
  [the corrected owner summary](/mnt/Projects/RedstoneCompiler-Worktrees/Telemetry-And-Acceptance/RedstoneCompiler/Output/TelemetryReportingRepair/20260906T183031Z-ReviewerCorrection/Summary.txt)
  and [the final closure report](/mnt/Projects/RedstoneCompiler-Worktrees/Router-Integration/RedstoneCompiler/Output/RepairClosure/20260906T180628Z/Report.md).
- This is not full CLI `Main.py` acceptance, live-Fabric physical validation,
  scale routing, or production-performance acceptance. The nine historical
  assignments remain `UNVERIFIED`.

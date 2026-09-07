# N5 notes

Working notes for [N5](N5.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- Missing or environment-blocked acceptance phases are reported as `not-run`,
  never as passed.
- A public acceptance snapshot may report a routing receipt consistent only when
  the raw actual or typed-failure receipt independently matches the configured
  identity, records `FallbackUsed` as the exact boolean `false`, and has no
  applicable evaluator check explicitly rejecting that identity. Integer zero
  does not prove fallback was disabled.
- Source-content stability requires two complete valid SHA-256 and file-count
  observations. Missing or malformed observations are unknown, not stable. The
  newest provenance-check entry is authoritative; invalid newest evidence is
  never erased by older valid history.
- Snapshot profile validity is not acceptance. A downstream backend is passed
  only by an explicit accepted receipt checked against the selected literal
  case profile; a retained typed upstream stop is `not-run`, and otherwise the
  backend remains unknown.

## Open questions

- None recorded.

## Working notes

- **Versioned manifest and safe artifact reads (committed and consumed).** Telemetry
  commit `e274e79` is the current committed checkpoint; Router commit `7c7b52b`
  consumes it exactly. Snapshot schema v3 infers
  `legacy-four` or `expanded-seven` from exact existing manifest modes, case
  metadata, run occurrences and commands, source/policy provenance, receipts,
  and archive identity. It requires sequential non-fail-fast expanded evidence,
  exact one-run expanded occurrences, historical legacy 5/3/3/2 sampling, and
  the literal MCHPRS/Fabric counts without adding a producer manifest field.
  Every profile interpretation requires a complete verified archive seal.
  Failure stage/reason can come only from the evaluator-recorded path and its
  matching sealed artifact size/hash; unsealed, missing, lexically escaped,
  symlink, directory, FIFO, or mismatched evidence fails closed. Archive and
  evaluator reads retain directory handles across every ancestor and carry one
  opened file's exact bytes and descriptor identity through validation and
  recording, with no unsafe platform fallback. The snapshot's complete seal is
  observed below one retained archive-root descriptor, and the same cached
  bytes feed manifest parsing, selected-failure projection, artifact records,
  and staged output without a later pathname reopen. Archive publication uses
  the same captured bytes for its inventory and checksums, verifies the final
  tree before sealing, and safely mirrors legacy inputs descriptor-relatively.
  Legacy source paths are never resolved through symlinks: the complete lexical
  root is opened once through no-follow ancestor handles, and that source-root
  descriptor plus one retained staging-root descriptor govern all later copy,
  inventory, checksum, and verification work. A real-directory root swap keeps
  the original opened bytes and a root symlink fails closed. A rejected mirror
  performs no unconditional pathname cleanup after staging acquisition:
  replacement directories remain untouched, while the original private
  unsealed staging object may be left for ordinary ignored-output cleanup. The
  sealed seven-
  case replay remains seven failed cases, zero accepted cases, and downstream
  `not-run`; no acceptance matrix, MCHPRS, Fabric, native build, or performance
  comparison was rerun. Candidate evidence is under
  `Output/TelemetrySnapshotV3/20260907T060450Z-P1Correction/`.
- **Bounded evaluator and evidence repair.** The technically cleared pre-notes
  patch `aee4473d3e8f9b7d31a36920fcc67b26e4a1156b5afe6088c692335e48901420`
  requires CLA4's documented 20 Fabric canaries separately from its 512 MCHPRS
  rows and makes every authoritative failure receipt fresh, invocation-bound,
  full-path/source/effective-policy exact, and literal-boolean nonfallback.
  Complete command identity retains interpreter, entrypoint, every token/value,
  multiplicity, and order. Rejected regular files remain byte-retained, while
  symlinks and non-regular entries are inventoried without reading targets.
  Focused verification passed 147 tests and 117 subtests; the deterministic
  suite passed 1,624 tests with 4 skips and 305 subtests in 74.21 s, structural/
  schema passed 7 tests, collection found 1,628 tests, and all 14 completeness
  checks passed.
- **Fresh seven-case outcome.** The corrected expanded matrix completed at
  `2026-09-07T03:52:41.716480Z` after 181.573 s and sealed its archive with
  110/110 checks. Every planned case ran, failed with exit code 1, and retained
  one nested failure with all identity checks true: HalfAdder 1.562240 s at
  `PlacementAccessSolve:NoPinAccessPattern`; FullAdder 2.315126 s, RCA4
  5.197050 s, DecimalToBinary4 19.897348 s, and TFlipFlopLatch 2.357462 s at
  `PreRouteInterfaceSelection:ClusterInterfaceSolveIncomplete`; RCA8
  25.145891 s at `PlacementAccessSolve:NoPinAccessPattern` on `B3`; and CLA4
  123.558389 s at `PlacementAccessSolve:NoPinAccessPattern` on `NandNet42`.
  CLA4 exceeded its 120 s ceiling by 3.558389 s and had no unique exact proof.
- **Limits and evidence.** No case reached detailed routing, rendering, final
  claims, MCHPRS, or Fabric, and no performance comparison was possible. The
  supported public evidence is the seven per-run projections. Whole-manifest
  `CaptureRoutingDesignSnapshot` rejected this expanded manifest with
  `case set is not authoritative` because its fixed historical guard expects
  four cases; that guard was not expanded. The independent archive/per-run-
  projection/ledger audit passed 1,228 checks with zero errors and classifies
  reporting as capability-proven on the failed chain, physical acceptance as
  0/7, and promotion readiness as no. Evidence is under
  `Output/TelemetryBatch1/20260907T034837Z-corrected-expanded-aee4473/`; the
  acceptance/archive/checksum hashes are respectively `82ea8d98...e8885b`,
  `a5a5ce5e...b1a93`, and `5904d0c8...0d17b`; the audit JSON, review, and public
  ledger are `149cd7d8...39f87`, `b275a670...eed3d`, and `b2c9e34f...3c06a`.
  Loaded native and release bytes match at `c086...`, but this run does not
  prove native source-to-binary build provenance. Installed JAR `04f501...`
  was unchanged, but its source build is `UNVERIFIED`.
  The original `08a713c` archive and disclosed 16-byte pre-seal review log are
  preserved unchanged.
- The completed repair is `b8c1d4f6f75c3a29a931aeeea4205951fe80a7e2`, directly
  parented by audited Joint `c6d6a81d5bbdf920a51b8a899748671d731cecea`. Joint
  consumed it as `64efbe13c14c8c7445cba6dd6111256ce06f9a36`; Router then consumed
  that exact Joint checkpoint as `bd3b9349d79d6b5794070c4bdf731d5231636de2`.
- The repair independently covers missing/malformed/unknown archive observations,
  valid matching and contradictory observations, malformed-newest authority,
  literal-`False` fallback evidence, raw contradictions, preserved rejections,
  valid actual/failure receipts, and honest unknown controls. Evidence is under
  [the corrected owner summary](/mnt/Projects/RedstoneCompiler-Worktrees/Telemetry-And-Acceptance/RedstoneCompiler/Output/TelemetryReportingRepair/20260906T183031Z-ReviewerCorrection/Summary.txt)
  and [the final closure report](/mnt/Projects/RedstoneCompiler-Worktrees/Router-Integration/RedstoneCompiler/Output/RepairClosure/20260906T180628Z/Report.md).
- No full CLI `Main.py` execution, fresh live-Fabric physical acceptance, scale
  routing, or production-performance acceptance was run. Router's Gradle result
  was incremental/up-to-date, not fresh Java execution. The nine historical
  assignments remain `UNVERIFIED`.

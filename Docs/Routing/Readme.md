# Routing docs

Contains routing architecture, failure catalog, and operational diagnostics.

Current verdict: **NOT ACCEPTED**. The final RRF-073 nine-run
`new-router-first` manifest verifies FullAdder 5/5 and keeps every process
inside its immutable wall ceiling, but RCA4 and CLA4 both remain physically
unrouted 0/2. The current durable source is
`Output/Acceptance/2026-07-21/RouterV10Recovery/AcceptanceManifest.json`.

## Canonical current documents

- [Router reliability guide](RouterReliabilityGuide.md) -- current acceptance
  verdict, operator commands, evidence requirements, and troubleshooting.
- [Router reliability design](RouterReliabilityDesignDoc.md) -- normative v10
  architecture, invariants, interfaces, and acceptance contract.
- [Router reliability implementation notes](RouterReliabilityImplementationNotes.md)
  -- append-only implementation and verification journal.

## Tools and acceptance evidence

- [`Scripts/RunRouterAcceptance.py`](../../Scripts/RunRouterAcceptance.py) --
  canonical sequential 5+2+2 physical matrix, immutable per-circuit wall
  ceilings, explicit publication reserve, incremental
  `router-acceptance-manifest-v1`, artifact hashes, and deterministic
  repeated-run comparison.
- [`Tests/test_router_acceptance_harness.py`](../../Tests/test_router_acceptance_harness.py)
  -- focused dry-run, sequencing, rejection-shape, determinism,
  publication-reserve, and immutable-ceiling coverage.
- [`RustRouting/Src/`](../../RustRouting/Src/) -- the native router's exact
  eight-file responsibility split. The approved default-feature release gate
  currently passes 25/25 tests.

The final lightweight checkpoint passes compileall, 95/95 focused routing
tests, the 2/2 explicit terminal/transactional proof, the 14-module
scale-excluded Python gate at 159/159 in 37.312s, Rust formatting, the 25/25
Rust release gate, and the diff check.

Inspecting the matrix is safe and does not launch a physical compile:

```bash
python3 Scripts/RunRouterAcceptance.py --date 2026-07-21 \
  --output-root Output/Acceptance --python /usr/bin/python3 --dry-run
```

The physical form of that command, without `--dry-run`, must be run only after
the lightweight gates pass. The completed RRF-073 command uses 8/23/118-second
router deadlines inside unchanged 10/25/120-second wall ceilings; the watchdog
is ceiling plus two seconds for capture only. A terminal observation without
the manifest, per-run logs, and hashed artifacts is diagnostic evidence, not
acceptance.

## Historical design record

The documents below preserve prior proposals, implementation checkpoints, and
dated measurements. Where they conflict with the canonical reliability design,
the canonical design controls; historical measurements remain evidence for the
policy version that produced them.

- [Router rewrite implementation plan](RouterRewriteDesignDoc.md)
- [Open-source-informed staged router design](OpenSourceRouterPortingDesignDoc.md)
- [Organized NAND routing design](OrganizedNandRoutingDesignDoc.md)
- [Organized NAND routing goals](OrganizedNandRoutingGoals.md)
- [Measured compatibility comparison](RouterRewriteComparisonDoc.md)
- [Router service-level objectives](RouterSLO.md)
- [RCA/CLA conflict-remediation proposal](RcaClaRoutingConflictRemediationDesign.md)

Planned next documents:
- ResourceGraph.md
- HierarchicalRegions.md
- TrackAssignment.md
- FailureCatalog.md

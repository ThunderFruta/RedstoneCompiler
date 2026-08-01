# Routing docs

Contains routing architecture, failure diagnostics, active operations, and historical design records.

Current verdict (2026-07-23): **NOT ACCEPTED**. The negotiated route-tree
implementation is active, but the current RCA4 checkpoint is still unresolved in
diagnostic RRF-078; earlier focused RCA4 evidence still includes ten
capacity-one electrical conflicts after overflow progression
`[124, 10, 10, 10, 10]`. CLA4 remains intentionally gated behind RCA4
passing its 2/2 acceptance requirement. The RRF-073 nine-run manifest remains the
last complete durable acceptance matrix, not a statement that the current working
tree passes RCA4.

## Active routing docs

- [Negotiated route-tree router](Active/NegotiatedRouteTreeRouter.md) -- current
  architecture, implemented interfaces, known gaps, and acceptance sequence.
- [Current routing failures](Active/CurrentRoutingFailures.md) -- current RCA4
  evidence, proven observations, hypotheses, and required fix evidence.
- [Router reliability guide](Active/RouterReliabilityGuide.md) -- current
  acceptance verdict, operator commands, evidence requirements, and troubleshooting.
- [Router reliability design](Active/RouterReliabilityDesignDoc.md) -- normative
  v10 architecture, invariants, interfaces, and acceptance contract.
- [Router reliability implementation notes](Active/RouterReliabilityImplementationNotes.md)
  -- append-only implementation and verification journal.
- [Router research and inspiration](Active/RouterResearchAndInspiration.md) -- the
  concrete algorithms and source projects behind the redesign.
- [Router execution prompt](Historical/RouterExecutionPrompt.md) -- expert review
  and solution-planning prompt.

## Routing reference

- [Resource graph](Active/ResourceGraph.md) -- claims, capacities, lazy regions,
  and cache reuse.
- [Hierarchical regions](Active/HierarchicalRegions.md) -- coarse tiles, detailed
  halos, expansion triggers, and failure cuts.
- [Track assignment](Active/TrackAssignment.md) -- the capacity-one
  detailed-routing contract and the isolated legacy exact-assignment path.
- [Failure catalog](Active/FailureCatalog.md) -- typed failures, required evidence,
  and placement feedback.

## Acceptance evidence and tools

- [`Scripts/RunRouterAcceptance.py`](../../Scripts/RunRouterAcceptance.py) --
  canonical sequential 5+2+2 physical matrix, immutable per-circuit wall
  ceilings, explicit publication reserve, incremental
  `router-acceptance-manifest-v1`, artifact hashes, and deterministic
  repeated-run comparison.
- [`Tests/test_router_acceptance_harness.py`](../../Tests/test_router_acceptance_harness.py)
  -- focused dry-run, sequencing, rejection-shape, determinism,
  publication-reserve, and immutable-ceiling coverage.
- [`RustRouting/Src/`](../../RustRouting/Src/) -- the native router's exact
  eight-file responsibility split.

Inspecting the matrix is safe and does not launch a physical compile:

```bash
python3 Scripts/RunRouterAcceptance.py --date 2026-07-21 \
  --output-root Output/Acceptance --python /usr/bin/python3 --dry-run
```

The physical form of that command, without `--dry-run`, must be run only after
the lightweight gates pass.

## Historical design record

The documents below preserve prior proposals, implementation checkpoints, and
measured baselines. Where they conflict with the canonical reliability design,
the canonical design controls; historical measurements remain evidence for the
policy version that produced them.

- [Router rewrite implementation plan](Historical/RouterRewriteDesignDoc.md)
- [Open-source-informed staged router design](Historical/OpenSourceRouterPortingDesignDoc.md)
- [Open-source root analysis](Historical/OpenSourceRouterRootAnalysis.md)
- [Organized NAND routing design](Historical/OrganizedNandRoutingDesignDoc.md)
- [Organized NAND routing goals](Historical/OrganizedNandRoutingGoals.md)
- [Measured compatibility comparison](Historical/RouterRewriteComparisonDoc.md)
- [Router service-level objectives](Historical/RouterSLO.md)
- [RCA/CLA conflict-remediation proposal](Historical/RcaClaRoutingConflictRemediationDesign.md)

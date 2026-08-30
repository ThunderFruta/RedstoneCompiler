# Routing docs

Contains routing architecture, failure diagnostics, active operations, and historical design records.

Current verdict (2026-08-25 source snapshot): FullAdder, RCA4, and RCA8 pass
their native artifact/correctness gates, while CLA4 fails before routing at
`Placement / PlacementOverlap`; the CLA4-inclusive design is **NOT ACCEPTED**.
The negotiated route-tree implementation is active, deterministic policy
selection is profile-driven instead of circuit-keyed, and the canonical
harness runs CLA4 only when `--include-cla4` is supplied.

Structural update (2026-08-28): placement, component routing, authoritative
routing, and native kernels now use the clean-break domain ownership documented
in [the project-tree design](../Reference/ProjectTreeDesignDoc.md). This refactor does
not change the acceptance verdict: CLA4 remains a typed
`Placement / PlacementOverlap` failure unless a fresh post-refactor matrix
independently proves otherwise.

## Active routing docs

- [Routing-aware placement and access design](Active/RoutingAwarePlacementAccessDesign.md)
  -- proposed routability-by-construction replacement for fixed pin access,
  including typed contracts, code seams, rollout gates, and acceptance rules.
- [Routing-aware placement and access snapshots](Active/RoutingAwarePlacementAccessSnapshots.md)
  -- append-only timestamped source, failure, artifact, and implementation
  evidence for that proposal.
- [Negotiated route-tree router](Active/NegotiatedRouteTreeRouter.md) -- current
  architecture, implemented interfaces, known gaps, and acceptance sequence.
- [Current routing failures](Active/CurrentRoutingFailures.md) -- dated
  2026-07-23 RCA4 evidence, proven observations, hypotheses, and required fix
  evidence; the newer CLA4 baseline is in the snapshot record above.
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
  canonical sequential strict matrix, immutable per-circuit wall ceilings,
  explicit publication reserve, incremental
  `router-acceptance-manifest-v2`, artifact hashes, deterministic
  repeated-run comparison, and explicit optional CLA4 checkpoints.
- [`Tests/test_router_acceptance_harness.py`](../../Tests/test_router_acceptance_harness.py)
  -- focused dry-run, sequencing, rejection-shape, determinism,
  publication-reserve, and immutable-ceiling coverage.
- [`Scripts/CaptureRoutingDesignSnapshot.py`](../../Scripts/CaptureRoutingDesignSnapshot.py)
  -- explicit timestamped source/artifact capture with separate exact-byte and
  portable-semantic evidence identities and no implicit latest-artifact
  discovery.
- [`Tests/test_routing_design_snapshot.py`](../../Tests/test_routing_design_snapshot.py)
  -- focused snapshot identity, typed failure, source/runtime consistency,
  manifest validation, and staged-publication coverage.
- [`RustRouting/Src/`](../../RustRouting/Src/) -- the native router's exact
  implementation-source responsibility split.

Inspecting the matrix is safe and does not launch a physical compile:

```bash
python3 Scripts/RunRouterAcceptance.py --date 2026-07-21 \
  --output-root Output/Acceptance --python /usr/bin/python3 --dry-run
```

The physical form of that command, without `--dry-run`, must be run only after
the lightweight gates pass.

To append the `CarryLookaheadAdder4` runs and exact-interface proof evidence,
add `--include-cla4`.

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

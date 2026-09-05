# Routing docs

This directory contains the current routing architecture, physical contracts,
failure taxonomy, and validation references. A design document does not
establish an acceptance result: assess a checkout from fresh output artifacts
and the acceptance manifest produced for that run.

## Current routing design

- [Routing-aware placement and access design](Active/RoutingAwarePlacementAccessDesign.md)
  — the proposed routability-by-construction placement/access handoff.
- [Negotiated route-tree router](Active/NegotiatedRouteTreeRouter.md) — the
  current global and detailed routing design.
- [Incremental physical factor reuse](Active/IncrementalPhysicalFactorReuse.md)
  — reuse and invalidation rules for physical work.
- [Physical-design architecture review](../Architecture/PhysicalDesignArchitectureReview.md)
  — current boundaries, crossover findings, and proposed module seams.

## Physical routing reference

- [Resource graph](Active/ResourceGraph.md)
- [Hierarchical regions](Active/HierarchicalRegions.md)
- [Track assignment](Active/TrackAssignment.md)
- [Failure catalog](Active/FailureCatalog.md)
- [Router research and inspiration](Active/RouterResearchAndInspiration.md)

## Validation and evidence

- [Rewrite readiness and branch workflow](../Pillars/RewriteWorkflow.md) separates
  commit/integration gates from production promotion; the
  [dependency register](../Pillars/CapabilityDependencies.md) tracks tested
  capability checkpoints across parallel branches.
- [Running tests](../Testing/RunningTests.md) and
  [benchmarks and acceptance gates](../Testing/Benchmarks.md) define current
  commands and gates.
- [`Tools/Routing/RunRouterAcceptance.py`](../../Tools/Routing/RunRouterAcceptance.py)
  writes the acceptance manifest for a selected matrix.
- [`Tools/Routing/CaptureRoutingDesignSnapshot.py`](../../Tools/Routing/CaptureRoutingDesignSnapshot.py)
  captures explicit inputs under
  `Output/DesignSnapshots/RoutingAwarePlacementAccess/`.

Typed failures are recorded as `.RoutingFailure.json`; cite them together with
the matching acceptance manifest when reporting a current routing result.

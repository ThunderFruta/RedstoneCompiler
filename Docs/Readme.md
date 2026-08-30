# Compiler Documentation

This directory is the durable documentation home. Implementation details should stay
in this tree instead of being duplicated in the repository root.

## Index

- [Project Tree](Reference/ProjectTree.md)
- [Ownership and structural gates](Reference/ProjectTreeDesignDoc.md)
- [Routing design](Reference/RoutingDesignDoc.md)
- [Architecture]
  - [Readme](Architecture/Readme.md)
  - [Compiler pipeline](Architecture/CompilerPipeline.md)
  - [Data contracts](Architecture/DataContracts.md)
  - [Architecture decisions](Architecture/Decisions.md)
- [Development]
  - [Readme](Development/Readme.md)
  - [Legacy routing and shim retirement](Development/LegacyRetirement.md)
- [Formats]
  - [Readme](Formats/Readme.md)
- [Guides]
  - [Readme](Guides/Readme.md)
- [Routing]
  - [Readme](Routing/Readme.md)
  - [Current routing design](Routing/Active/RouterReliabilityDesignDoc.md)
  - [Routing-aware placement and access design](Routing/Active/RoutingAwarePlacementAccessDesign.md)
  - [Routing-aware placement and access snapshots](Routing/Active/RoutingAwarePlacementAccessSnapshots.md)
  - [Negotiated route-tree router](Routing/Active/NegotiatedRouteTreeRouter.md)
  - [Current routing failures](Routing/Active/CurrentRoutingFailures.md)
  - [Router reliability guide](Routing/Active/RouterReliabilityGuide.md)
  - [Router research and inspiration](Routing/Active/RouterResearchAndInspiration.md)
  - [Resource graph](Routing/Active/ResourceGraph.md)
  - [Hierarchical regions](Routing/Active/HierarchicalRegions.md)
  - [Track assignment](Routing/Active/TrackAssignment.md)
  - [Failure catalog](Routing/Active/FailureCatalog.md)

- [Testing]
  - [Readme](Testing/Readme.md)
  - [Test strategy](Testing/TestStrategy.md)
  - [Running tests](Testing/RunningTests.md)
  - [Benchmarks and acceptance gates](Testing/Benchmarks.md)

## Archive

- [Historical route-tree performance plan](Perf/PerformanceImprovementDesignDoc.md)
- [Historical structured router design](Routing/Historical/StructuredPCBRouterDesignDoc.md)
- [Legacy router execution prompt](Routing/Historical/RouterExecutionPrompt.md)
- [Router service-level objectives](Routing/Historical/RouterSLO.md)
- [Router rewrite implementation plan](Routing/Historical/RouterRewriteDesignDoc.md)
- [Open-source-informed staged router design](Routing/Historical/OpenSourceRouterPortingDesignDoc.md)
- [RCA/CLA conflict-remediation proposal](Routing/Historical/RcaClaRoutingConflictRemediationDesign.md)

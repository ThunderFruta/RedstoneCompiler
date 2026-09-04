# Compiler Documentation

This directory is the durable documentation home. Implementation details should stay
in this tree instead of being duplicated in the repository root.

## Index

- [Project Tree](Reference/ProjectTree.md)
- [Ownership and structural gates](Reference/ProjectTreeDesignDoc.md)
- [Repository layout migration and file crosswalk](Reference/RepositoryLayoutMigration.md)
- [Routing design](Reference/RoutingDesignDoc.md)
- [Physical-design pillars and necessary requirements](Pillars/Readme.md)
- [Architecture]
  - [Readme](Architecture/Readme.md)
  - [Compiler pipeline](Architecture/CompilerPipeline.md)
  - [Data contracts](Architecture/DataContracts.md)
  - [Physical design architecture overview](Architecture/PhysicalDesignArchitectureReview.md)
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
  - [Routing-aware placement and access design](Routing/Active/RoutingAwarePlacementAccessDesign.md)
  - [Negotiated route-tree router](Routing/Active/NegotiatedRouteTreeRouter.md)
  - [Incremental physical factor reuse](Routing/Active/IncrementalPhysicalFactorReuse.md)
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

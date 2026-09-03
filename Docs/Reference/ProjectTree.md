# Project Tree

The compiler now uses a stage-aligned runtime layout:

- Parsing and elaboration are under `Compiler/Frontend/`.
- Core compiler behavior is under `Compiler/` by phase.
- Litematic encoding is in `PhysicalDesign/Rendering/SchemWriter.py`.
- Templates are in `Assets/Templates/`.
- User-visible artifacts are under `Output/`.
- Disposable runtime state is under `Cache/`.
- Project references are under `Docs/`.

## Ownership at a glance

- Frontend parsing belongs to `Compiler/Frontend/`.
- Core compiler behavior belongs to `Compiler/` by phase.
- Litematic encoding belongs in `PhysicalDesign/Rendering/SchemWriter.py`.
- Templates are in `Assets/Templates/`.
- Tests remain in `Tests/` and generated artifacts in `Output/`.
- Disposables are isolated under `Cache/`.

## Placement and routing ownership (2026-08-28)

- `PhysicalDesign/Placement/Access/` owns access geometry and the standalone capacity
  oracle; `Core/` owns placement search/repair/commit; `Flow/` owns run-local
  orchestration and publication.
- `PhysicalDesign/Contracts/` and `Interfaces/` are neutral lower layers.
  `Components/` owns local component solving, and `Authoritative/` owns the
  global physical route.
- `Validation/Fabric/` owns fixtures, live validation, mismatch failure
  traces, schematic testing, and settled-server snapshots. `Validation/Fabric/Harness/` owns the tracked mod
  source; its `Server/` runtime is local and intentionally ignored.
- `Native/Routing/Src/` is split into nested `Core`, `Geometry`, `Path`,
  `Assignment`, `Escape`, `Generation`, `Planning`, and `Python`
  domains. Escape candidates/catalogs and generated detailed-tree phases are
  further split into their own subdirectories. `Lib.rs` is registration-only.
- The clean-break retired paths and executable structural limits are listed in
  [the project-tree design](ProjectTreeDesignDoc.md) and enforced by
  `Tests/Structural/test_source_structure.py`.

## Documentation ownership

- `Docs/Architecture/` explains stage boundaries and cross-stage contracts.
- `Docs/Routing/Active/RoutingAwarePlacementAccessDesign.md` owns the proposed
  fixed-access replacement and its typed placement/access handoff contracts.
- `Docs/Routing/Active/NegotiatedRouteTreeRouter.md` owns the current router design.
- `Docs/Routing/Active/RouterResearchAndInspiration.md` records external algorithmic
  sources and the parts adopted here.
- `Docs/Routing/Active/FailureCatalog.md` owns the typed routing failure taxonomy.
- `Docs/Architecture/PhysicalDesignArchitectureReview.md` owns the boundary
  findings and proposed module seams.
- `Docs/Testing/` owns test commands, layers, and physical acceptance gates.

[`ProjectTreeDesignDoc.md`](ProjectTreeDesignDoc.md) defines the completed
clean-break ownership contract and structural gates.

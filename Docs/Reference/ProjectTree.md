# Project Tree

The compiler now uses a stage-aligned runtime layout:

- Parsing and elaboration are under `SVDecoder/`.
- Core compiler behavior is under `Compiler/` by phase.
- Litematic encoding is in `SchemEncoder/SchemWriter.py`.
- Templates are in `Templates/`.
- User-visible artifacts are under `Output/`.
- Disposable runtime state is under `Cache/`.
- Project references are under `Docs/`.

## Ownership at a glance

- Frontend parsing belongs to `SVDecoder/`.
- Core compiler behavior belongs to `Compiler/` by phase.
- Litematic encoding belongs in `SchemEncoder/SchemWriter.py`.
- Templates are in `Templates/`.
- Tests remain in `Tests/` and generated artifacts in `Output/`.
- Disposables are isolated under `Cache/`.

## Placement and routing ownership (2026-08-28)

- `Compiler/Placement/Access/` owns access geometry and the standalone capacity
  oracle; `Core/` owns placement search/repair/commit; `Flow/` owns run-local
  orchestration and publication.
- `Compiler/Routing/Contracts/` and `Interfaces/` are neutral lower layers.
  `Components/` owns local component solving, and `Authoritative/` owns the
  global physical route.
- `Compiler/FabricServer/` owns fixtures, live validation, mismatch failure
  traces, schematic testing, and settled-server snapshots. `ValidationServerHarness/` owns the tracked mod
  source; its `Server/` runtime is local and intentionally ignored.
- `RustRouting/Src/` is split into nested `Core`, `Geometry`, `Path`,
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
- `Docs/Routing/Active/RoutingAwarePlacementAccessSnapshots.md` is append-only
  and owns timestamped evidence for that proposal.
- `Docs/Routing/Active/NegotiatedRouteTreeRouter.md` owns the current router design.
- `Docs/Routing/Active/RouterResearchAndInspiration.md` records external algorithmic
  sources and the parts adopted here.
- `Docs/Routing/Active/RouterReliabilityGuide.md` owns the operational verdict and
  acceptance evidence.
- `Docs/Routing/Active/RouterReliabilityImplementationNotes.md` is append-only and
  records dated implementation checkpoints.
- `Docs/Testing/` owns test commands, layers, and physical acceptance gates.

[`ProjectTreeDesignDoc.md`](ProjectTreeDesignDoc.md) defines the completed
clean-break ownership contract,
structural gates, and timestamped refactor evidence.

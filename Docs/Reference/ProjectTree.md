# Project Tree

The compiler now uses a stage-aligned runtime layout:

- Parsing and elaboration are under `SVDecoder/`.
- Core compiler behavior is under `Compiler/` by phase.
- Litematic encoding is under `SchemEncoder/`.
- Templates are in `Templates/`.
- User-visible artifacts are under `Output/`.
- Disposable runtime state is under `Cache/`.
- Project references are under `Docs/`.

## Ownership at a glance

- Frontend parsing belongs to `SVDecoder/`.
- Core compiler behavior belongs to `Compiler/` by phase.
- Litematic encoding belongs to `SchemEncoder/`.
- Templates are in `Templates/`.
- Tests remain in `Tests/` and generated artifacts in `Output/`.
- Disposables are isolated under `Cache/`.

## Documentation ownership

- `Docs/Architecture/` explains stage boundaries and cross-stage contracts.
- `Docs/Routing/Active/NegotiatedRouteTreeRouter.md` owns the current router design.
- `Docs/Routing/Active/RouterResearchAndInspiration.md` records external algorithmic
  sources and the parts adopted here.
- `Docs/Routing/Active/RouterReliabilityGuide.md` owns the operational verdict and
  acceptance evidence.
- `Docs/Routing/Active/RouterReliabilityImplementationNotes.md` is append-only and
  records dated implementation checkpoints.
- `Docs/Testing/` owns test commands, layers, and physical acceptance gates.

`ProjectTreeDesignDoc.md` defines the migration contract and sequencing.

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

`ProjectTreeDesignDoc.md` defines the migration contract and sequencing.

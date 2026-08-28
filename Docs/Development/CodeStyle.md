# Code style

Keep the pipeline stage-aligned: frontend parsing in `SVDecoder`, synthesis and
IR in `Compiler`, placement in `Compiler/Placement`, routing in
`Compiler/Routing`, and output encoding in `SchemEncoder`.

Source identifiers use PascalCase, including functions, methods, classes, and
public members. Prefer explicit stage names over generic helpers. Preserve
deterministic iteration by sorting externally derived sets and mappings before
they affect placement, routing, diagnostics, or fingerprints.

Routing changes must preserve one production strategy, typed hard failures,
the shared deadline, capacity-one final validation, and transactional output.
Never select behavior from circuit names, generated gate names, or fixed net
counts. Add focused tests beside the owning stage and avoid unrelated
refactors.

## Source ownership and size gates

Routing dependencies are one-way: contracts/resources → physical interfaces →
components → authoritative routing → placement flow → compiler pipeline. A
lower layer must not import a higher layer, and the Python compiler import graph
must remain acyclic. The source-structure test records the two existing neutral
`Placement.Geometry`/`Placement.Rotation` primitive imports explicitly; they do
not authorize a dependency on placement search or flow.

Implementation modules are limited to 3,000 physical lines, orchestrators to
fewer than 500, and Python/Rust functions to fewer than 1,000. New split
implementation files must be at least 150 lines unless
`Tests/test_source_structure.py` records a concrete API, binding, worker,
state/schema, cache-identity, or phase-contract reason. Prefer merging short
same-purpose helpers over inventing another module.

Package APIs stay narrow. Import internal helpers from their concrete owner;
do not recreate a broad forwarding module or restore an old monkeypatch target.
Run state and injectable services belong to their flow boundary, while
process-global caches and workers each have one stable importable owner.

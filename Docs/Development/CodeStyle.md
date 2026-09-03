# Code style

Keep the pipeline stage-aligned: frontend parsing in `Compiler/Frontend`, synthesis and
IR in `Compiler`, placement in `PhysicalDesign/Placement`, routing in
`PhysicalDesign/Routing`, and output encoding in `PhysicalDesign/Rendering`.

Source identifiers use PascalCase, including functions, methods, classes, and
public members. Prefer explicit stage names over generic helpers. Preserve
deterministic iteration by sorting externally derived sets and mappings before
they affect placement, routing, diagnostics, or fingerprints.

Routing changes must preserve one production strategy, typed hard failures,
the shared deadline, capacity-one final validation, and transactional output.
Never select behavior from circuit names, generated gate names, or fixed net
counts. Add focused tests beside the owning stage and avoid unrelated
refactors.

## Source ownership and size review

Routing dependencies are one-way: contracts/resources → physical interfaces →
components → authoritative routing → placement flow → compiler pipeline. A
lower layer must not import a higher layer, and the Python compiler import graph
must remain acyclic. The source-structure test records the two existing neutral
`PhysicalDesign.Geometry.Placement`/`PhysicalDesign.Geometry.Rotation` imports explicitly; they do
not authorize a dependency on placement search or flow.

Implementation size targets are advisory: 3,000 physical lines per module,
500 per orchestrator, and 1,000 per Python/Rust function. A split implementation
file normally targets at least 150 lines unless it has a concrete API, binding, worker,
state/schema, cache-identity, or phase-contract reason. Prefer merging short
same-purpose helpers over inventing another module.

Package APIs stay narrow. Import internal helpers from their concrete owner;
do not recreate a broad forwarding module or restore an old monkeypatch target.
Run state and injectable services belong to their flow boundary, while
process-global caches and workers each have one stable importable owner.

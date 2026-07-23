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

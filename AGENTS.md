# Repository Guidelines

## Project Structure & Module Organization

Keep code in the domain that owns its behavior: application entrypoints in
`App/`, compilation in `Compilation/`, physical implementation in
`PhysicalDesign/`, validation in `Validation/`, native routing in
`Kernels/Routing/`, and domain-aligned tests in `Tests/`. Treat runtime and
generated directories as runtime data, not source.

`Docs/` is the authoritative source for the detailed repository layout and
ownership rules. Start with `Docs/Readme.md`, then consult
`Docs/Reference/ProjectTree.md` for the directory map,
`Docs/Reference/ProjectTreeDesignDoc.md` for structural ownership and gates,
`Docs/Architecture/CompilerPipeline.md` for stage boundaries, and
`Docs/Testing/Readme.md` for test documentation.

## Build, Test, and Development Commands

Run commands from the repository root.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
redstone-compiler
python3 -m pytest -q
cargo fmt --manifest-path Kernels/Routing/Cargo.toml -- --check
cargo test --manifest-path Kernels/Routing/Cargo.toml --release
./Validation/Fabric/ServerHarness/gradlew -p Validation/Fabric/ServerHarness test build
```

The editable install builds the Python/PyO3 package; the CLI runs a guided or argument-driven compile. Pytest covers Python contracts and integration. Rust changes require formatting plus release tests and a rebuilt extension before Python parity checks. The Fabric harness requires Java 25; its checked-in wrapper downloads and verifies Gradle 9.5.1.

## Coding Style & Naming Conventions

Use four-space indentation and concise module docstrings. Production Python and Rust identifiers follow the repository’s PascalCase convention; test functions use descriptive `test_*` names. Keep Java code consistent with the existing harness. Sort externally derived sets and mappings before they influence routing, diagnostics, or fingerprints. Preserve one-way stage dependencies, typed failures, deterministic behavior, and narrow package APIs. Avoid circuit-name or gate-name special cases. Run `cargo fmt`; no automatic Python formatter is prescribed.

## Testing Guidelines

Add focused tests beside the owning domain. Define each test from the actual required, observable behavior and its contract—not from the current implementation or from an assertion chosen merely because the existing code passes it. Do not write pass-oriented tests that mirror implementation details without independently proving the desired behavior. Before broad runs, use the structural gates documented in `Docs/Testing/RunningTests.md`; run the complete suite with pytest, not unittest alone. Physical truth tables and final capacity-one claim validation are authoritative. Acceptance runs must use a fresh output root and retain `Summary.txt`, `RawDump.txt`, manifests, hashes, and physical-design or typed-failure JSON.

## Commit & Pull Request Guidelines

`Docs/Pillars/` is required reading and the governing workflow for all rewrite
work. Read `Docs/Pillars/WorktreeBuckets.md` and
`Docs/Pillars/RewriteWorkflow.md` before beginning work, then use the bucket
assigned to the capability. Coordinate shared-contract edits and consume
explicit dependency checkpoints from
`Docs/Pillars/CapabilityDependencies.md`; do not bypass these documents based
on local branch conventions or apparent implementation convenience. The base
checkout serves `main` and `Router-Refactor(R10-N5)` for integration/release,
not feature ownership.

Follow recent history with short, imperative subjects such as `Add MCHPRS validation harness and Fabric canary gate`. Keep commits scoped to one coherent change. Pull requests should explain behavior and architecture impact, list exact verification commands and results, link relevant issues, and identify generated evidence. Do not commit `Output/`, `Cache/`, `.venv/`, `RustRouting/target/`, native `.so` files, or `Runtime/FabricServer/` secrets, worlds, logs, and downloaded JARs.

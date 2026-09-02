# Repository Guidelines

## Project Structure & Module Organization

`Main.py` and `Compiler/Main.py` provide the CLI; `Compiler/Pipeline.py` coordinates SystemVerilog parsing, NAND synthesis, placement, routing, schematic writing, and physical validation. Keep stage-specific work in its owner: `SVDecoder/`, `Compiler/{Ir,Synthesis,Cells,Placement,Routing}/`, or `SchemEncoder/`. The PyO3 routing backend lives in `RustRouting/Src/`. `ValidationServerHarness/` contains the tracked Java/Fabric harness, while its ignored `Server/` directory is local runtime state. Put tests under the matching domain in `Tests/`; shared fixtures belong in `Tests/Fixtures/`. Examples, templates, documentation, and automation are in `Examples/`, `Templates/`, `Docs/`, and `Scripts/`.

## Build, Test, and Development Commands

Run commands from the repository root.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
redstone-compiler
python3 -m pytest -q
cargo fmt --manifest-path RustRouting/Cargo.toml -- --check
cargo test --manifest-path RustRouting/Cargo.toml --release
gradle -p ValidationServerHarness test
```

The editable install builds the Python/PyO3 package; the CLI runs a guided or argument-driven compile. Pytest covers Python contracts and integration. Rust changes require formatting plus release tests and a rebuilt extension before Python parity checks. The Fabric harness requires Java 25 and Gradle 9.5.1.

## Coding Style & Naming Conventions

Use four-space indentation and concise module docstrings. Production Python and Rust identifiers follow the repository’s PascalCase convention; test functions use descriptive `test_*` names. Keep Java code consistent with the existing harness. Sort externally derived sets and mappings before they influence routing, diagnostics, or fingerprints. Preserve one-way stage dependencies, typed failures, deterministic behavior, and narrow package APIs. Avoid circuit-name or gate-name special cases. Run `cargo fmt`; no automatic Python formatter is prescribed.

## Testing Guidelines

Add focused tests beside the owning domain. Before broad runs, use the structural gates documented in `Docs/Testing/RunningTests.md`; run the complete suite with pytest, not unittest alone. Physical truth tables and final capacity-one claim validation are authoritative. Acceptance runs must use a fresh output root and retain `Summary.txt`, `RawDump.txt`, manifests, hashes, and physical-design or typed-failure JSON.

## Commit & Pull Request Guidelines

Follow recent history with short, imperative subjects such as `Add MCHPRS validation harness and Fabric canary gate`. Keep commits scoped to one coherent change. Pull requests should explain behavior and architecture impact, list exact verification commands and results, link relevant issues, and identify generated evidence. Do not commit `Output/`, `Cache/`, `.venv/`, `RustRouting/target/`, native `.so` files, or `ValidationServerHarness/Server/` secrets, worlds, logs, and downloaded JARs.

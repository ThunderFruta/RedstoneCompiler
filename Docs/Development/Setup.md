# Development setup

Use Python 3.12 or a compatible recent Python and a Rust toolchain for the
native router.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m compileall -q Compiler SVDecoder SchemEncoder Tests
cargo test --manifest-path RustRouting/Cargo.toml --release
```

Run commands from the repository root. Generated user artifacts belong under
`Output/`; disposable frontend and test state belongs under `Cache/`. Do not
commit virtual environments, Rust build output, or transient `/tmp` evidence.

Before a scale compile, run the focused tests and confirm no other heavy
RedstoneCompiler process is active. See [Running tests](../Testing/RunningTests.md).

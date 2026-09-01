# Running tests

## Structural and contract gates

```bash
python3 -m compileall -q Compiler/Placement Compiler/Routing
python3 -m pytest -q \
  Tests/Structural/test_source_structure.py \
  Tests/Routing/test_routing_contract_schema.py
python3 -m pytest --collect-only -q
```

The collected identity/count is compared with the captured baseline before a
test file is moved or split. Run the complete suite with pytest, not unittest
alone, because many routing checks are module-level pytest functions:

```bash
python3 -m pytest -q
```

Guided-menu pytest runs stream their normal output and then print a concise
result headed by `RESULT`, `TIME`, optional `CPU`, and `OUTPUT`. Complete stdout, stderr,
runtime provenance, Git identity, and artifact evidence are retained under
`Output/Pytest/<UTC run id>/{Summary.txt,RawDump.txt}`.

## Focused routing checks

```bash
python3 -m unittest \
  Tests.test_authoritative_planner \
  Tests.test_router_reliability \
  Tests.test_placement_boundary_feasibility
```

## Rust router

```bash
cargo fmt --manifest-path RustRouting/Cargo.toml -- --check
cargo test --manifest-path RustRouting/Cargo.toml --release
cargo build --manifest-path RustRouting/Cargo.toml --release --features python-extension
```

After any Rust source change, copy the release extension into the Python
package, then verify the path and SHA-256 of the module actually imported before
running Python parity tests. Process success without loaded-path/hash evidence
does not prove the rebuilt native code was exercised.

## Acceptance plan without execution

```bash
python3 Scripts/Routing/RunRouterAcceptance.py \
  --date 2026-08-28 \
  --output-root /tmp/RedstoneCompilerMonolithPostRefactor \
  --python .venv/bin/python \
  --matrix expanded \
  --dry-run
```

Remove `--dry-run` only after fast tests pass and no other scale routing job is
running. The default matrix runs FA, RCA4, and RCA8 once each. Expanded mode
runs HalfAdder, FullAdder, RCA4, RCA8, DecimalToBinary4, TFlipFlopLatch, and
CLA4 once each. Use a fresh, empty
output root. The harness does not fail fast: it attempts every scheduled run,
preserves each failure independently, and rejects the overall session if any
required gate fails. CLA4's current `PlacementOverlap` is structural and must
not be reported as timeout exhaustion.

Every wall-time median and every internal stage whose baseline median is at
least 100 ms must be at most `1.05 ×` its baseline. If one exceeds 5%, rerun the
complete case once and judge the combined median. Preserve existing wall
ceilings and require exact truth tables, zero conflicts/unresolved claims, no
fallback, and stable repeated fingerprints.

The acceptance harness defaults to `Output/Acceptance/<date>/`. Each executed
circuit run retains `Summary.txt`, `RawDump.txt`, `stdout.log`, and
`stderr.log`; the dated directory also contains an overall summary/raw report
next to `AcceptanceManifest.json`. Baseline capture and comparison use named
subdirectories under the same date. An explicit `--output-root` still selects
a fresh alternative evidence root.

## Evidence

Keep stdout, stderr, `.PhysicalDesign.json` or `.RoutingFailure.json`, truth
table, schematic hash, source identity, and acceptance manifest. Terminal text
without retained artifacts is diagnostic evidence only.

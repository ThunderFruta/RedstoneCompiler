# Running tests

## Structural and contract gates

```bash
python3 -m compileall -q Compiler/Placement Compiler/Routing
python3 -m pytest -q \
  Tests/test_source_structure.py \
  Tests/test_routing_contract_schema.py
python3 -m pytest --collect-only -q
```

The collected identity/count is compared with the captured baseline before a
test file is moved or split. Run the complete suite with pytest, not unittest
alone, because many routing checks are module-level pytest functions:

```bash
python3 -m pytest -q
```

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
python3 Scripts/RunRouterAcceptance.py \
  --date 2026-08-28 \
  --output-root /tmp/RedstoneCompilerMonolithPostRefactor \
  --python .venv/bin/python \
  --include-cla4 \
  --dry-run
```

Remove `--dry-run` only after fast tests pass and no other scale routing job is
running. The refactor comparison uses the fixed 5/3/3/2 matrix and a fresh,
empty output root. The harness stops judging acceptance when a required gate
fails; CLA4's current `PlacementOverlap` is structural and must not be reported
as timeout exhaustion.

Every wall-time median and every internal stage whose baseline median is at
least 100 ms must be at most `1.05 ×` its baseline. If one exceeds 5%, rerun the
complete case once and judge the combined median. Preserve existing wall
ceilings and require exact truth tables, zero conflicts/unresolved claims, no
fallback, and stable repeated fingerprints.

## Evidence

Keep stdout, stderr, `.PhysicalDesign.json` or `.RoutingFailure.json`, truth
table, schematic hash, source identity, and acceptance manifest. Terminal text
without retained artifacts is diagnostic evidence only.

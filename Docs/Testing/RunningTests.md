# Running tests

## Fast routing checks

```bash
python3 -m unittest \
  Tests.test_authoritative_planner \
  Tests.test_router_reliability \
  Tests.test_placement_boundary_feasibility
```

## Rust router

```bash
cargo test --manifest-path RustRouting/Cargo.toml --release
```

## Acceptance plan without execution

```bash
python3 Scripts/RunRouterAcceptance.py \
  --date 2026-07-22 \
  --output-root Output/Acceptance \
  --python /usr/bin/python3 \
  --dry-run
```

Remove `--dry-run` only after fast tests pass and no other scale routing job is
running. The harness runs FullAdder, RCA4, and CLA4 sequentially and stops
judging acceptance when a required gate fails.

## Evidence

Keep stdout, stderr, `.PhysicalDesign.json` or `.RoutingFailure.json`, truth
table, schematic hash, source identity, and acceptance manifest. Terminal text
without retained artifacts is diagnostic evidence only.


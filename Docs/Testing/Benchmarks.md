# Routing benchmarks

| Benchmark | Purpose | Runs | Truth rows | Wall ceiling |
| --- | --- | ---: | ---: | ---: |
| FullAdder | Small correctness and deterministic overhead gate | 5 | 8 | 10 s |
| RippleCarryAdder4 | Repeated-stage congestion and regression gate | 3 | 512 | 25 s |
| RippleCarryAdder8 | 8-bit carry ripple scalability gate | 3 | 131072 | 30 s |
| CarryLookaheadAdder4 (compatibility) | Optional exact-proof compatibility check | 2 | 512 | 120 s |

All successful runs require zero final conflicts, zero unresolved claims,
authoritative physical simulation, identical repeated fingerprints, and no
fallback.

## Current checkpoint

The strict acceptance sequence is FA/RCA4/RCA8 with reproducible determinism and
strict no-fallback evidence.
Use acceptance compatibility mode to include the optional compatibility circuit check for
CarryLookaheadAdder4 and fixture-backed exact proof evidence.

## Latest acceptance sweep (2026-08-03)

### Execution commands

- Strict mode:
  - `python Scripts/RunRouterAcceptance.py --output-root Output/Acceptance/Pass3 --date 2026-08-03 --python .venv/bin/python`
- Compatibility mode:
  - `python Scripts/RunRouterAcceptance.py --output-root Output/Acceptance/Pass3Compat --date 2026-08-03 --python .venv/bin/python --compatibility-mode`

### Results

| Circuit | Mode | Runs | Runtime s (min/mean/max) | Routing length | Routing bends | Routing vias | Conflicts | Footprint | Full footprint | Status |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| FullAdder | Strict | 5 | 1.077084 / 1.101263 / 1.116426 | 111 | 31 | 24 | 0 | 544 | 2720 | PASS |
| RippleCarryAdder4 | Strict | 3 | 8.537964 / 8.748257 / 8.876852 | 595 | 237 | 203 | 0 | 2400 | 16800 | PASS |
| RippleCarryAdder8 | Strict | 3 | 8.181562 / 8.311903 / 8.409309 | 1068 | 327 | 312 | 0 | 4768 | 33376 | PASS |
| FullAdder | Compatibility mode | 5 | 1.103630 / 1.130082 / 1.168162 | 111 | 31 | 24 | 0 | 544 | 2720 | PASS |
| RippleCarryAdder4 | Compatibility mode | 3 | 8.529482 / 8.621784 / 8.669935 | 595 | 237 | 203 | 0 | 2400 | 16800 | PASS |
| RippleCarryAdder8 | Compatibility mode | 3 | 8.246019 / 8.365640 / 8.427790 | 1068 | 327 | 312 | 0 | 4768 | 33376 | PASS |
| CarryLookaheadAdder4 | Compatibility | 1 | Failed | n/a | n/a | n/a | n/a | n/a | n/a | FAIL |

Strict acceptance manifest: `Output/Acceptance/Pass3/2026-08-03/RouterRegression/StandaloneAcceptance/AcceptanceManifest.json`

Compatibility acceptance manifest: `Output/Acceptance/Pass3Compat/2026-08-03/RouterRegression/StandaloneAcceptance/AcceptanceManifest.json`

### Failure notes

- Compatibility mode failed on `CarryLookaheadAdder4Run1` with:
  - process exit code 1
  - `ClusterInterfaceSolveIncomplete`
  - routing stage reserve timeout (`98.568s` elapsed in routing reserve window)
  - missing required artifacts: `Schematic`, `TruthTable`, `PhysicalDesign`

CarryLookaheadAdder4 remains the explicit compatibility gate and is not part of the strict runtime path.

Attempt-by-attempt behavior, evidence boundaries, and current hypotheses are
maintained in
[Current routing failures](../Routing/Active/CurrentRoutingFailures.md).

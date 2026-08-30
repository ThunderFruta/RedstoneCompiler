# Routing benchmarks

| Benchmark | Purpose | Runs | Truth rows | Wall ceiling |
| --- | --- | ---: | ---: | ---: |
| FullAdder | Small correctness and deterministic overhead gate | 5 | 8 | 10 s |
| RippleCarryAdder4 | Repeated-stage congestion and regression gate | 3 | 512 | 25 s |
| RippleCarryAdder8 | 8-bit carry ripple scalability gate | 3 | 131072 | 30 s |
| CarryLookaheadAdder4 (extended) | Optional exact-interface proof check | 2 | 512 | 120 s |

The recorded matrix predates the Fabric-server cutover. New full acceptance
requires zero final conflicts, zero unresolved claims, identical repeated
fingerprints, no fallback, and an authoritative Fabric-server result. Until
that stage is connected, the router-only subset can run but cannot claim
Minecraft behavioral acceptance.

## Current checkpoint

The default acceptance sequence is FA/RCA4/RCA8 with reproducible determinism
and strict no-fallback evidence. Add `--include-cla4` to append the two CLA4
runs and the fixture-backed exact-interface proof checkpoint. CLA4 uses the
same default routing strategy and no-fallback gate as the default sequence.

## Latest acceptance sweep (2026-08-03)

### Execution commands

- Default sequence:
  - `python Scripts/Routing/RunRouterAcceptance.py --output-root Output/Acceptance/Pass3 --date 2026-08-03 --python .venv/bin/python`
- Extended sequence with CLA4:
  - `python Scripts/Routing/RunRouterAcceptance.py --output-root Output/Acceptance/Pass3Compat --date 2026-08-03 --python .venv/bin/python --include-cla4`

### Results

| Circuit | Mode | Runs | Runtime s (min/mean/max) | Routing length | Routing bends | Routing vias | Conflicts | Footprint | Full footprint | Status |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| FullAdder | Default | 5 | 1.077084 / 1.101263 / 1.116426 | 111 | 31 | 24 | 0 | 544 | 2720 | PASS |
| RippleCarryAdder4 | Default | 3 | 8.537964 / 8.748257 / 8.876852 | 595 | 237 | 203 | 0 | 2400 | 16800 | PASS |
| RippleCarryAdder8 | Default | 3 | 8.181562 / 8.311903 / 8.409309 | 1068 | 327 | 312 | 0 | 4768 | 33376 | PASS |
| CarryLookaheadAdder4 | Extended | 1 | Failed | n/a | n/a | n/a | n/a | n/a | n/a | FAIL |

Strict acceptance manifest: `Output/Acceptance/Pass3/2026-08-03/RouterRegression/StandaloneAcceptance/AcceptanceManifest.json`

Extended CLA4 acceptance manifest: `Output/Acceptance/Pass3Compat/2026-08-03/RouterRegression/StandaloneAcceptance/AcceptanceManifest.json`

### Failure notes

- The extended CLA4 run failed on `CarryLookaheadAdder4Run1` with:
  - process exit code 1
  - `ClusterInterfaceSolveIncomplete`
  - routing stage reserve timeout (`98.568s` elapsed in routing reserve window)
  - missing required artifacts: `Schematic`, `TruthTable`, `PhysicalDesign`

CarryLookaheadAdder4 remains an explicit extended gate and is not part of the
default runtime path.

Attempt-by-attempt behavior, evidence boundaries, and current hypotheses are
maintained in
[Current routing failures](../Routing/Active/CurrentRoutingFailures.md).

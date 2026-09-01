# Routing benchmarks

| Benchmark | Matrix | Purpose | Runs | Fabric validation vectors | Wall ceiling |
| --- | --- | --- | ---: | ---: | ---: |
| HalfAdder | Expanded | Small two-input arithmetic check | 1 | 4 | 10 s |
| FullAdder | Default and expanded | Small correctness and deterministic overhead gate | 1 | 8 | 10 s |
| RippleCarryAdder4 | Default and expanded | Repeated-stage congestion and regression gate | 1 | 512 | 25 s |
| RippleCarryAdder8 | Default and expanded | 8-bit carry ripple scalability gate | 1 | 4132 | 30 s |
| DecimalToBinary4 | Expanded | One-hot decimal encoder check | 1 | 1024 | 30 s |
| TFlipFlopLatch | Expanded | Explicit-state toggle/latch logic check | 1 | 8 | 15 s |
| CarryLookaheadAdder4 | Expanded | Exact-interface proof check | 1 | 512 | 120 s |

Full acceptance requires zero final conflicts, zero unresolved claims,
identical repeated fingerprints, no fallback, a durable Fabric fixture, and
a passed authoritative Fabric-server validation record. The retired
`*.TruthTable.txt` simulator artifact is not an acceptance gate: the server
checks the FullAdder/RCA4/CLA4 vectors exhaustively and uses 4,132
deterministic wide vectors for RCA8.

## Saved run reports

Compiler runs write immutable evidence beneath
`Output/<Circuit>/Runs/<UTC run id>/`. `Summary.txt` always starts with result,
time and CPU utilization, optional CPU breakdown, a one-line output, and the
raw-report path. `RawDump.txt` retains full
stdout/stderr, Git and runtime provenance, stage telemetry, typed failure
evidence, validation gates, and an artifact size/hash inventory. Only a fully
successful run atomically refreshes the stable artifacts directly under
`Output/<Circuit>/`.

## Current checkpoint

The default acceptance matrix is exactly FA/RCA4/RCA8. Select
`--matrix expanded` to run all seven bundled examples: HalfAdder, FullAdder,
RCA4, RCA8, DecimalToBinary4, TFlipFlopLatch, and CLA4. CLA4 retains its
fixture-backed exact-interface proof checkpoint. Both matrices execute every
scheduled repetition even after an earlier failure; the overall session fails
if any required run or correctness gate fails. Expanded mode is standalone and
cannot capture or compare the historical regression baseline.

Normal default and expanded acceptance execute each selected circuit exactly
once. Specialized baseline capture/comparison retains its historical repeated
sampling contract so existing version-one baseline evidence remains readable.

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
  - missing required artifacts: `Schematic`, `FabricFixture`, `PhysicalDesign`

CarryLookaheadAdder4 remains an explicit extended gate and is not part of the
default runtime path.

Attempt-by-attempt behavior, evidence boundaries, and current hypotheses are
maintained in
[Current routing failures](../Routing/Active/CurrentRoutingFailures.md).

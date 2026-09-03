# Running tests

## Structural and contract gates

```bash
python3 -m compileall -q Compiler/Placement Compiler/Routing
python3 -m pytest -q \
  Tests/Structural/test_source_structure.py \
  Tests/Routing/test_routing_contract_schema.py
python3 -m pytest --collect-only -q
```

These gates enforce objective dependency, import, API-owner, and schema
contracts. Source size and implementation shape are advisory review signals:

```bash
python3 Tools/Routing/ReviewSourceStructure.py
```

The review command reports ownership and the largest files/definitions but
does not fail when a size target is exceeded. Run the complete deterministic
suite with pytest, not unittest alone, because many routing checks are
module-level pytest functions:

```bash
.venv/bin/python -m pytest -q Tests
```

The guided `Run pytest` action runs this deterministic tier, explicitly
disables `RC_RUN_SCALE_TESTS`, streams output, and retains `Summary.txt` and
`RawDump.txt` beneath `Output/Pytest/<UTC run id>/`. Its concise terminal
result is headed by `RESULT`, `TIME`, optional `CPU`, and `OUTPUT`; the saved
report also records runtime provenance, Git identity, and artifact evidence.

## Routing CPU telemetry

Detailed routing telemetry is enabled by default for CLI compiles, including
guided compiles and acceptance runs. Terminal output keeps the result, total,
routing and validation timings, and overall CPU summary. Per-stage timing,
sample records, task events, and stacks are retained in run files without
being dumped to the terminal.

Use `--no-routing-telemetry` or `RC_ROUTING_TELEMETRY=0` to disable detailed
collection; explicit CLI flags override the environment. Telemetry does not
change thread limits, search policy, validation gates, or deadlines.

```bash
.venv/bin/python Main.py --input Examples/HalfAdder.sv \
  --output Output/Telemetry/HalfAdder.litematic
.venv/bin/python Tools/Routing/RunRouterAcceptance.py \
  --matrix expanded --routing-threads 8 --output-root Output/TelemetryAcceptance
```

Each compiler run directory retains:

- `RoutingTelemetry.events.jsonl`: stage and placement subphase transitions,
  symbolic proof task lifecycle, coordinator waits, and pool/cache decisions.
- `RoutingTelemetry.samples.jsonl`: 250 ms observations of compiler and child
  CPU time, main-thread CPU, native-thread CPU, positive-CPU thread counts,
  process counts, thread states, and instrumented queue state.
- `RoutingTelemetry.stacks.txt`: main-thread Python snapshots requested every
  second under the GIL, avoiding asynchronous traversal of Python frames.
- `RoutingTelemetry.json` and `RoutingTelemetry.txt`: aggregate evidence and
  explicit coverage limits. `RawDump.txt` includes the structured telemetry.
  `Summary.txt` keeps the full per-stage timing breakdown for inspection.

The CPU observer runs in a separate interpreter so a GIL-held native call
cannot stop CPU sampling. It excludes itself and external Fabric service CPU.
Its own CPU cost is recorded in telemetry metadata. Busy thread counts mean
positive CPU ticks during an interval, not simultaneous execution. Intervals
crossing phase boundaries are marked mixed; use the existing exact stage
wall/CPU deltas alongside these sampled measurements. Sub-100 ms stage core
ratios are omitted because CPU tick resolution makes them misleading.

Task queue metrics cover the symbolic unary proof pool, not every router
queue. Queue-to-start time includes both waiting and process startup/transfer;
it is not a separate serialization measurement. Python stack dumps do not
contain native backtraces and can be delayed by a GIL-held native call; use
their monotonic timestamps to identify gaps. A killed run retains a partial sample stream, which
can be summarized afterward without treating it as routing success:

```bash
.venv/bin/python App/TelemetryObserver.py --directory <compiler-run-directory>
```

## Focused routing checks

```bash
.venv/bin/python -m pytest -q \
  Tests/Routing/test_authoritative_*.py \
  Tests/Routing/test_component_pipeline_*.py \
  Tests/Routing/test_physical_assembly_*.py \
  Tests/Integration/test_router_reliability.py \
  Tests/Placement/test_placement_boundary_feasibility.py
```

## MCHPRS physical validation

```bash
.venv/bin/python -m pytest -q Tests/test_mchprs_validation.py
```

MCHPRS fixture tests use tracked inputs under `Tests/Fixtures/Mchprs/`; they
must not depend on ignored `Output/` artifacts. MCHPRS is exhaustive through
20 inputs. Wider designs use deterministic edge cases plus 4,096 samples.

## Opt-in scale routing

```bash
RC_RUN_SCALE_TESTS=1 .venv/bin/python -m pytest -q \
  Tests/Integration/test_scale_routing.py
```

The scale tier attempts RCA4, RCA8, and CLA4 independently. It is not part of
the guided deterministic run and must not be marked successful when CLA4
returns a typed routing failure. CLA4's current `PlacementOverlap` is a
structural failure and must not be reported as timeout exhaustion.

## Rust router and MCHPRS backend

```bash
cargo fmt --manifest-path RustRouting/Cargo.toml -- --check
cargo test --manifest-path RustRouting/Cargo.toml --release
cargo build --manifest-path RustRouting/Cargo.toml --release --features python-extension
```

After any Rust source change, copy the release extension into the Python
package, then verify the path and SHA-256 of the module actually imported before
running Python parity tests. Process success without loaded-path/hash evidence
does not prove the rebuilt native code was exercised.

## Validation harness

```bash
gradle -p ValidationServerHarness test
```

Harness unit tests do not substitute for a live Fabric acceptance run.

## Acceptance plan without execution

```bash
python3 Tools/Routing/RunRouterAcceptance.py \
  --date 2026-08-28 \
  --output-root /tmp/RedstoneCompilerMonolithPostRefactor \
  --python .venv/bin/python \
  --matrix expanded \
  --dry-run
```

Remove `--dry-run` only after fast tests pass and no other scale routing job is
running. The default matrix runs FA, RCA4, and RCA8 once each. Expanded mode
runs HalfAdder, FullAdder, RCA4, RCA8, DecimalToBinary4, TFlipFlopLatch, and
CLA4 once each. Use a fresh, empty output root. The harness does not fail
fast: it attempts every scheduled run, preserves each failure independently,
and rejects the overall session if any required gate fails.

Every wall-time median and every internal stage whose baseline median is at
least 100 ms must be at most `1.05 ×` its baseline. If one exceeds 5%, rerun the
complete case once and judge the combined median. Preserve existing wall
ceilings and require exact MCHPRS truth tables, the required Fabric canaries,
zero conflicts/unresolved claims, no fallback, and stable repeated
fingerprints.

The acceptance harness defaults to `Output/Acceptance/<date>/`. Each executed
circuit retains `Summary.txt`, `RawDump.txt`, `stdout.log`, and `stderr.log`;
the dated directory also contains an overall report and
`AcceptanceManifest.json`.

## Evidence

Keep stdout, stderr, `.PhysicalDesign.json` or `.RoutingFailure.json`, physical
fixture, MCHPRS/Fabric result records, schematic hash, source identity, and
acceptance manifest. Terminal text without retained artifacts is diagnostic
evidence only.

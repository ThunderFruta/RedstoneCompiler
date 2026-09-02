# MCHPRS validation and Fabric final check

The compiler emits `<design>.PhysicalFixture.json` from the exact neutral block
map used by its private staging litematic. The shared fixture names the
already-rendered input levers and output lamps; signs are presentation only and
are never used as a protocol. `ValidationServerHarness/Mchprs/` first validates
the physical block map with MCHPRS/Redpiler. It exhaustively enumerates designs through 20
inputs (including all 131,072 RCA8 vectors) and uses deterministic sampling for
wider designs. A failed MCHPRS result stops before Fabric.

After MCHPRS passes, `Compiler/FabricServer/` sends a deterministic canary set
(zero, all-one, one-hot, and one-cold inputs) through one fixture on the live
Minecraft 26.2 server. This Fabric check remains required: mismatch, timeout,
infrastructure failure, or missing server fails compilation. After it passes,
the pipeline forces every input low, waits for the live world to settle, and
publishes the observed block states as `<design>.litematic`. The final artifact
stores Minecraft's dust power, torch/lamp lighting, and repeater state for the
all-zero vector rather than static neutral properties. The private staging file
is never published; snapshot failure clears all success artifacts. Legacy
`.FabricFixture.json` files remain readable, but new runs publish only the
shared `.PhysicalFixture.json` artifact.

The one canonical local runtime is
`ValidationServerHarness/Server/`. It contains the Fabric 26.2 launcher, world,
backups, logs, installed harness JAR, and private control configuration, and is
intentionally ignored by Git. Compiler validation and the Fabric importer
default to this root. `RC_FABRIC_SERVER_ROOT` remains an explicit override for
an alternate local test runtime in those components. The lifecycle command
always manages the canonical project runtime.

Manage the canonical runtime from the repository root with:

```bash
# Guided lifecycle menu; Enter selects a safe status check.
python3 Scripts/Fabric/ControlFabricServer.py
python3 Scripts/Fabric/ControlFabricServer.py start
# Open the authenticated manual command console against the running server.
python3 Scripts/Fabric/ConsoleFabricServer.py
python3 Scripts/Fabric/ControlFabricServer.py stop
# Clear every persisted non-air simulation block without restarting the server.
python3 Scripts/Fabric/ControlFabricServer.py clear
```

The tracked control command delegates to the modular, runtime-local
implementation in `ValidationServerHarness/Server/PyScripts/` (`Paths`,
`Protocol`, `Anvil`, `Process`, and `Main`). On start it verifies the launcher and
accepted EULA, refreshes the installed harness JAR from the project build when
needed, creates a fresh loopback token, and waits for authenticated control
readiness. A new runtime receives a local-only creative 26.2
`minecraft:the_void` flat world with no terrain, features, lakes, or generated
structures. After readiness the harness persists disabled spawning, all mob,
block, and entity drops, daylight progression, and weather progression using
the typed 26.2 gamerule API.

## Updating the local harness runtime

`ValidationServerHarness/Server/` is not a Git deployment target. When tracked
Fabric harness Java changes, build `ValidationServerHarness` with the available
Gradle installation, then run `Scripts/Fabric/ControlFabricServer.py start`.
The manager copies a newer built JAR into the local runtime and only restarts a
healthy running server when that JAR needs refreshing. The runtime-local
`PyScripts` modules survive a branch merge on this canonical host, but are not
synced by Git; a future fresh-host provisioning flow needs a tracked bootstrap
or template outside `Server/`.

`clear` is a destructive live block operation. It preserves the existing
`ValidationServerHarness/Server/world/` directory, `level.dat`, world identity,
generator settings, gamerules, and region files. While the managed server
remains running, the manager reads each persisted Anvil region header, derives
every saved Overworld chunk, and decodes its block-state palette. It first
flushes live chunk state, then sends only actual non-air positions to the
authenticated `WorldSetBlocks` harness action in bounded batches; it never
writes, replaces, or deletes Anvil data directly. It reports scanned regions
and chunks plus the non-air blocks removed. The server is neither stopped nor
restarted when its control endpoint is healthy. The local server disables
Nether access; if saved non-Overworld chunks ever appear, the operation fails
closed rather than claiming a complete clear. The guided menu requires the
operator to type `CLEAR` before this destructive action.

`ConsoleFabricServer.py` is a separate, authenticated manual command console
for the already-running canonical server. It forwards each single-line command
as `WorldRunCommand` through the private loopback capability, with server
console permission; `:help` and `:quit` are local console commands. It never
attaches to Java stdin, exposes a remote console, or starts a competing JVM.
`--command "say hello"` runs one command for automation. This console reports
the harness acknowledgement and is intended for diagnostics and world setup,
not compiler validation.

Before every compiler or tester validation paste, the harness clears the
incoming fixture arena and pastes the new fixture in the same server-thread
operation. It does not pause ticks, change automatic saving, flush the world,
or scan persisted chunks. Fixture arenas are the canonical validation location;
their bounds must contain every block that can influence the fixture. The
harness then forces neighbor updates before it starts validation.

The server sets a 1,000 TPS target, force-loads every chunk intersecting the
single fixture arena (up to 1,024 chunks), applies canary vectors sequentially,
and forces updates at each lever and its six direct neighbors. There are no
Fabric validation copies, lanes, stacks, or capacity workers.

Each vector is sampled atomically on the Minecraft server thread and is settled
only after all traced dust, repeaters, torches, lamps, comparators, and levers
remain unchanged for 40 consecutive observed game ticks (up to 200 ticks
total). Per-tick comparisons use compact immutable block-state lists; the
harness serializes full coordinate/state JSON only for a settled or failed
snapshot. Only then does the harness compare output lamps. It returns `passed`,
`mismatch`, `timeout`, or
`infrastructure-failure`; absence of the configured server is an infrastructure
failure, never a functional pass.

The harness streams authoritative `Completed`/`Total` JSON progress at the
start of the canary set and after every fully tested vector. The terminal bar
therefore starts at `0/N` and reflects actual settled and compared Fabric
canaries. Results are committed in canary order, and mismatch/timeout
diagnostics carry the global vector index. Fixture and vector preparation do
not render a placeholder validation bar. Existing-world validation uses the
same observer and restores the original input states.

Skipped game ticks do not count toward the 40-tick proof: the unchanged counter
resets whenever the harness cannot observe the next consecutive game tick.
Mismatch traces are serialized from the same atomic snapshot used for output
comparison, so a continuing server clock cannot move the trace past the failed
state.

The control client separates server-startup retries from a submitted validation
request. Connection/readiness failures may retry for the configured startup
window, but a validation response receives one long-running response deadline
(900 seconds by default) and is never resubmitted after a response timeout.
Each vector still fails independently at the server's 200-game-tick settle
limit.

Validation invokes the manager rather than opening a competing server. A
healthy authenticated endpoint is reused for the live clear and validation;
the manager starts the server only when it is stopped or unavailable. It never
restarts a healthy server just to clear blocks. An explicit alternate root must
include the same runtime manager; validation fails closed rather than silently
running against a world it cannot fully clear.

All vectors are exhaustive through 16 inputs. Wider circuits use zero/one,
one-hot, one-cold, and 4,096 deterministic SHA-256-seeded vectors. Expected
bits come from the synthesized logic IR only; physical behavior comes solely
from Fabric.

## Failure traces

Compiler-produced fixtures use schema version 2 and carry a deterministic
`Trace` map. The map relates the flattened top-level circuit to every placed
gate subcircuit, every routed signal, and the exact dynamic Minecraft blocks
that can expose redstone state. Validation vectors retain the ideal value of
every internal synthesized signal for diagnostics, while the output contract
remains unchanged.

Every validation path reads those probes to establish trace-wide quiescence,
but only a mismatch or settle timeout retains the complete serialized snapshot
as diagnostic evidence. The harness records the failing input vector, output,
expected and last-observed values from the same game tick. Each probe
includes both its fixture-relative coordinate and exact world coordinate plus
the complete live block state. The Python boundary compares wire power and
powered/lit properties with the ideal internal signals, walks the producer
graph from the failed output through every contributing gate, and adds a
`FailureTrace` diagnostic containing:

- `SubcircuitTrace`: the deterministic output-to-input gate and signal path,
  including every live dynamic block inside each visited gate cell;
- `FirstFailingSubcircuit`: the earliest causal gate whose inputs match but
  whose output trace does not;
- `FirstFailingBlock`: the first producer-to-consumer route probe with the
  wrong live state, including fixture and world coordinates.

Pipeline failures preserve this under
`FabricServerValidation.Diagnostics.FailureTrace` in the routing-failure
artifact. Imported schema-version-1 schematics remain testable, but they do
not have compiler gate/signal ownership metadata and therefore cannot produce
this source-linked trace. The current frontend is a flattened scalar
combinational IR, so `CircuitPath` is expressed as top module plus placed gate;
future hierarchical IR can extend that path without changing the live probe
protocol.

## Importing a Sponge schematic

With the dedicated server already running, load a Sponge v2/v3 `.schem` or the
compiler's `.litematic` artifact into the local world with:

```bash
python3 Scripts/Fabric/ImportSchemToFabricServer.py /path/to/build.schem \
  --origin 0 64 0 --replace
```

The importer turns the palette and X/Z/Y varint block stream into the same
canonical, SHA-256-verified fixture protocol used by validation. `--replace`
clears the whole source schematic bounding box before placement; omit it to
preserve existing blocks. After each load, the harness force-loads the fixture
chunks and forces neighbor updates for every pasted block and its six direct
neighbors before settling. Imported entities and block entities are rejected
rather than silently discarded.
Powered, lit, and dust-strength block properties are reset to neutral before a
live load, so Fabric recomputes redstone state rather than inheriting stale
values saved in a litematic. The harness remains bound to localhost and
requires the run token in its local configuration.

For compiler-produced litematics, run the paired tester after import:

```bash
python3 Scripts/Fabric/TestSchemInFabricServer.py /path/to/build.litematic
```

It reads the imported fixture and the adjacent `<build>.Nand.json` oracle,
then reloads the exact fixture, exhaustively drives up to 16 labeled input
levers (or uses the deterministic wide-vector policy), waits for redstone to
settle, and compares every labeled output lamp. Compiler `IN` and `OUT` sign
annotations are used only to recover ports for this post-export path; the
normal compiler fixture remains template-derived. The tester rejects an
unlabeled or portless imported fixture rather than reporting a vacuous pass.
It also rejects a multi-region litematic rather than silently testing only one
region; current compiler output contains exactly one region.

Choose either `--all` to run the complete derived table (also the default), or
`--one N` / `--one-at-a-time N` / `--vector-index N` to test exactly one
zero-based row. `--all-one-at-a-time` runs every row as its own Fabric
validation, prints a per-row outcome as it completes, and aggregates the
result only after every row has run. It requires Enter after each row before
testing the next one (but does not pause after the final row). This mode is
useful for small truth tables and failure diagnosis; `--all` is faster for
wide deterministic tables. The focused result reports both the selected row
and total row count; all-mode preserves exhaustive or deterministic-wide
validation.

There is no unauthenticated or Java-stdin manual console. The lifecycle
`clear` action removes all persisted non-air simulation blocks without
replacing the saved world, while the authenticated console is limited to
explicit manual commands. Compiler Fabric validation
continues to own fixture placement, input switching, settling, and output
observation through its authenticated loopback boundary, keeping simulation
automatic and reproducible rather than dependent on interactive server flags.

# Fabric server validation

The compiler emits `<design>.FabricFixture.json` from the exact neutral block
map used by its private staging litematic. The fixture names the
already-rendered input levers and output lamps; signs are presentation only and
are never used as a protocol. After truth-table validation passes, the pipeline
forces every input low, waits for the live world to settle, and publishes the
observed block states as `<design>.litematic`. This means the final artifact
stores Minecraft's dust power, torch/lamp lighting, and repeater state for the
all-zero vector rather than static neutral properties. The private staging file
is never published; snapshot failure clears all success artifacts.

The one canonical local runtime is
`FabricServerHarness/Server/`. It contains the Fabric 26.2 launcher, world,
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
implementation in `FabricServerHarness/Server/PyScripts/` (`Paths`,
`Protocol`, `Anvil`, `Process`, and `Main`). On start it verifies the launcher and
accepted EULA, refreshes the installed harness JAR from the project build when
needed, creates a fresh loopback token, and waits for authenticated control
readiness. A new runtime receives a local-only creative 26.2
`minecraft:the_void` flat world with no terrain, features, lakes, or generated
structures. After readiness the harness persists disabled spawning, all mob,
block, and entity drops, daylight progression, and weather progression using
the typed 26.2 gamerule API.

`clear` is a destructive live block operation. It preserves the existing
`FabricServerHarness/Server/world/` directory, `level.dat`, world identity,
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

Before every compiler or tester validation paste, the Python boundary invokes
the canonical manager's live `clear` action. Therefore no persisted non-air
block from an earlier fixture, failed validation, or manual import can survive
into the new test. The manager must report the regions/chunks inspected and
non-air blocks cleared; otherwise validation fails closed. The harness then clears the
incoming fixture arena immediately before placement as a second, idempotent
pre-paste guard.

The server sets a 1,000 TPS target, force-loads every chunk intersecting the
fixture arena (up to 256 chunks), places the exact fixture states, drives
levers, forces updates at each lever and its six direct neighbors, waits for a
50-tick propagation guard plus two unchanged sampled ticks (up to 200 ticks
total), and reads output lamps. It returns `passed`, `mismatch`, `timeout`, or
`infrastructure-failure`; absence of the configured server is an infrastructure
failure, never a functional pass.

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

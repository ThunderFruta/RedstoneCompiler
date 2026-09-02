# RedstoneCompiler validation server harness

This is the sole runtime mod required by the compiler's local validation
server. It is server-only and deliberately has no Fabric API, Litematica,
WorldEdit, Carpet, or client dependency.

Build it with Gradle 9.5.1 and Java 25:

```sh
gradle build
```

The repository-owned local server runtime is always
`ValidationServerHarness/Server/`. Place the Fabric 26.2 launcher at
`ValidationServerHarness/Server/fabric-server-launch.jar` and accept the Minecraft
EULA there. This runtime is intentionally ignored because it holds downloaded
JARs, worlds, backups, and logs.

From the repository root, manage it with:

```sh
python3 Scripts/Fabric/ControlFabricServer.py start
# Sends commands through the authenticated harness; :quit leaves the console.
python3 Scripts/Fabric/ConsoleFabricServer.py
# No arguments show the guided lifecycle menu.
python3 Scripts/Fabric/ControlFabricServer.py
# Clears every persisted non-air simulation block without restarting the server.
python3 Scripts/Fabric/ControlFabricServer.py clear
python3 Scripts/Fabric/ControlFabricServer.py stop
```

The manager updates the installed harness at
`ValidationServerHarness/Server/mods/redstonecompiler-harness.jar` from the Gradle
build when necessary, writes a private loopback token and 1,000-TPS
configuration, and waits until the server is ready. The compiler uses MCHPRS
for the exhaustive truth table and sends only a deterministic canary set to
this server as the required final Minecraft 26.2 check. Fabric validates one
fixture in place; it has no duplicated validation lanes, stacks, or capacity
workers.
On its first start it
creates a localhost-only creative `minecraft:the_void` flat world with no
terrain or generated structures, then persists no-spawning, no-drops, frozen
time, and frozen weather rules through the 26.2 typed server API. Where the
local user service manager is available, it keeps the Java server independent
of the terminal that started it. Compiler validation uses this root by default;
`RC_FABRIC_SERVER_ROOT` is only an explicit alternate runtime override.

`clear` is deliberately narrower than deleting `Server/`, but broader than a
fixture-bounds erase: it preserves the existing `Server/world/` directory,
including its level identity, gamerules, generator settings, and region files,
while clearing every non-air block in every persisted simulation chunk. The
manager flushes live chunk state, parses the saved Anvil block-state palettes,
and sends only actual non-air positions through bounded live `WorldSetBlocks`
requests. It reports exact scanned region/chunk and non-air-block counts; it
does not write, delete, or replace region files. A healthy server remains
running throughout; it is started only if it was already stopped. The
simulation server has Nether disabled; if any other dimension has saved block
chunks, clear fails closed. The guided lifecycle menu requires typing `CLEAR`
before it performs this destructive block clear.

`ConsoleFabricServer.py` is the supported manual command console. It sends
each entered single-line Minecraft command through the harness's private
loopback capability with server-console permission, rather than attaching to
the Java process or exposing a network console. The server must already be
running; use `:help` and `:quit` in the interactive console, or
`--command "say hello"` to execute one command for automation.

The private compiler server disables vanilla player and vehicle speed-based
position corrections, preventing `moved too quickly`/`moved wrongly`
rubberbanding while retaining invalid numeric packet rejection and collision
checks. `allow-flight=true` remains enabled separately for creative flight.

Each loaded fixture force-loads its intersecting chunks (up to 1,024), so its
redstone keeps ticking even when no player is near it. Truth-table validation
places one compiler fixture at its declared origin and applies canary vectors
sequentially. The harness samples that fixture on the server thread at each
observed game tick and requires 40
consecutive unchanged ticks within the 200-tick settle ceiling. A trace change
or skipped tick resets that proof window. This avoids declaring a long routed
signal settled before it has reached its output lamp.
Per-tick settlement compares compact immutable block-state lists. Full JSON
block states and coordinates are serialized only for the settled or failed
snapshot, retaining exact diagnostics without paying that cost on every tick.
Results and progress are committed in canary order after each vector settles
and compares. `ValidateExisting` uses the same single-fixture observer while
preserving and restoring the manually edited circuit's input states.

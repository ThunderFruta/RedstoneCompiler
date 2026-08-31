# Scripts

Every command is runnable from the repository root. Run it with no flags for
an interactive, safe-by-default guide, or use `--help` for explicit automation
flags.

## Fabric

- `Fabric/ControlFabricServer.py` is the one lifecycle entry point for the
  canonical runtime at `FabricServerHarness/Server/`. It delegates to the
  modular runtime manager under `FabricServerHarness/Server/PyScripts/`; use
  it with `start`, `stop`, `clear`, or no action for a guided menu. `clear`
  preserves the existing `world/` directory and its level identity while
  flushing and palette-scanning every persisted simulation chunk, then clearing
  only its non-air positions through the running authenticated harness. It does
  not stop or restart a healthy server, never deletes or replaces the world
  directory, and refuses to claim a full clear if saved chunks exist outside
  the simulation Overworld. The guided menu requires typing `CLEAR` before it
  dispatches this action.
- `Fabric/ConsoleFabricServer.py` opens an authenticated Minecraft command
  console for the already-running canonical server. Enter `:help` or `:quit`
  in its interactive mode, or pass `--command "say hello"` for one command.
  It uses the same private loopback capability as validation and reports the
  command acknowledgement; it does not attach to Java stdin or start a server.
- `Fabric/ImportSchemToFabricServer.py` imports a Sponge v2/v3 `.schem` or a
  compiler `.litematic` into the already-running localhost Fabric server.
  Repeat it after saving to hot-reload; use `--replace` to clear its bounds
  first. A successful import explicitly resets every fixture input lever to
  `0`, waits for the server state to settle, then reads the world blocks back
  through the Fabric harness and writes `<schematic>.ServerUpdated.litematic`
  beside the source. That snapshot preserves the complete fixture bounds,
  live redstone properties, and compiler I/O labels. Use
  `--server-updated-output` to choose its path or
  `--no-server-updated-litematic` for a load-only run.
- `Fabric/TestSchemInFabricServer.py` runs the imported compiler schematic's
  exhaustive-or-deterministic truth-table vectors in the running server. It
  uses the matching `.Nand.json` as the logic oracle, flips every input lever,
  waits for settlement, and reads the labeled output lamps. Pass
  `--one N` / `--one-at-a-time N` (or `--vector-index N`) to run exactly one
  zero-based truth-table row, `--all` to run every row in one batch, or
  `--all-one-at-a-time` to run every row as a separate observable Fabric run,
  requiring Enter before each next row.

```bash
python3 Scripts/Fabric/ControlFabricServer.py start
# Open an interactive authenticated Minecraft command console.
python3 Scripts/Fabric/ConsoleFabricServer.py
# Or issue exactly one server command.
python3 Scripts/Fabric/ConsoleFabricServer.py --command "say RedstoneCompiler ready"
python3 Scripts/Fabric/ImportSchemToFabricServer.py build.litematic \
  --origin 0 64 0 --replace
# Run every row (also the default when neither option is supplied).
python3 Scripts/Fabric/TestSchemInFabricServer.py build.litematic --all
# Run only row 3 of the same derived truth table.
python3 Scripts/Fabric/TestSchemInFabricServer.py build.litematic --one 3
# Run the complete table, with one independent Fabric validation per row.
# Press Enter after each completed row to advance.
python3 Scripts/Fabric/TestSchemInFabricServer.py build.litematic --all-one-at-a-time
# Erase all imported circuit blocks while preserving the same saved world.
python3 Scripts/Fabric/ControlFabricServer.py clear
python3 Scripts/Fabric/ControlFabricServer.py stop
```

## Routing

- `Routing/RunRouterAcceptance.py` runs the compiler's physical acceptance
  matrix. Its no-flag guide defaults to `--dry-run` before offering full runs.
- `Routing/RunFreeroutingBenchmark.py` benchmarks the separate synthetic PCB
  comparison; it is not Redstone validation.
- `Routing/CaptureRoutingDesignSnapshot.py` captures explicit routing evidence
  in a new timestamped output directory.

```bash
python3 Scripts/Routing/RunRouterAcceptance.py
python3 Scripts/Routing/RunFreeroutingBenchmark.py --case FullAdder --runs 1
python3 Scripts/Routing/CaptureRoutingDesignSnapshot.py --help
```

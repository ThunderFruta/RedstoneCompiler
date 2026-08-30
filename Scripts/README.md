# Scripts

Every command is runnable from the repository root. Run it with no flags for
an interactive, safe-by-default guide, or use `--help` for explicit automation
flags.

## Fabric

- `Fabric/ImportSchemToFabricServer.py` imports a Sponge v2/v3 `.schem` or a
  compiler `.litematic` into the already-running localhost Fabric server.
  Repeat it after saving to hot-reload; use `--replace` to clear its bounds
  first.
- `Fabric/ControlFabricServer.py` configures the controlled world and pauses,
  steps, resumes, or clears all blocks in the imported fixture bounds without
  recreating the world.

```bash
python3 Scripts/Fabric/ImportSchemToFabricServer.py build.schem \
  --server-root .runtime/fabric-26.2 --origin 0 64 0 --replace
python3 Scripts/Fabric/ControlFabricServer.py \
  --server-root .runtime/fabric-26.2 --pause
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

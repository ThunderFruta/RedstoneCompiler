# Fabric server validation

The compiler emits `<design>.FabricFixture.json` from the exact neutral block
map used to write `<design>.litematic`. The fixture names the already-rendered
input levers and output lamps; signs are presentation only and are never used
as a protocol.

Set `RC_FABRIC_SERVER_ROOT` to a local Fabric 26.2 server directory containing
`fabric-server-launch.jar` and
`mods/redstonecompiler-harness.jar`. The compiler creates the private loopback
control configuration in that directory, launches the server with Java 25,
and supplies a fresh 256-bit token for the run.

The server sets a 1,000 TPS target, clears the fixture arena, places the exact
fixture states, drives levers, waits for two unchanged sampled ticks (up to
200 ticks), and reads output lamps. It returns `passed`, `mismatch`,
`timeout`, or `infrastructure-failure`; absence of the configured server is an
infrastructure failure, never a functional pass.

All vectors are exhaustive through 16 inputs. Wider circuits use zero/one,
one-hot, one-cold, and 4,096 deterministic SHA-256-seeded vectors. Expected
bits come from the synthesized logic IR only; physical behavior comes solely
from Fabric.

## Importing a Sponge schematic

With the dedicated server already running, load a Sponge v2/v3 `.schem` or the
compiler's `.litematic` artifact into the local world with:

```bash
python3 Scripts/Fabric/ImportSchemToFabricServer.py /path/to/build.schem \
  --server-root .runtime/fabric-26.2 --origin 0 64 0 --replace
```

The importer turns the palette and X/Z/Y varint block stream into the same
canonical, SHA-256-verified fixture protocol used by validation. `--replace`
clears the whole source schematic bounding box before placement; omit it to
preserve existing blocks. After each load, the harness forces neighbor updates
for every pasted block and its six direct neighbors before settling. Imported
entities and block entities are rejected rather than silently discarded. The
harness remains bound to localhost and requires the run token in its local
configuration.

The local server can also be made deterministic for manual inspection:

```bash
python3 Scripts/Fabric/ControlFabricServer.py --server-root .runtime/fabric-26.2 --configure-quiet-world
python3 Scripts/Fabric/ControlFabricServer.py --server-root .runtime/fabric-26.2 --pause
python3 Scripts/Fabric/ControlFabricServer.py --server-root .runtime/fabric-26.2 --step 20
python3 Scripts/Fabric/ControlFabricServer.py --server-root .runtime/fabric-26.2 --resume
python3 Scripts/Fabric/ControlFabricServer.py --server-root .runtime/fabric-26.2 --clear
```

The quiet-world operation persistently disables natural and spawner mob
creation, mob/block/entity drops, daylight progression, and weather
progression. Pause, step, and resume are authenticated loopback equivalents
of the vanilla `/tick` controls.

`--clear` does not recreate the world. It clears all block positions inside
every `.FabricFixture.json` imported into the server's `fixtures/` directory,
which removes every imported circuit while preserving server and world setup.

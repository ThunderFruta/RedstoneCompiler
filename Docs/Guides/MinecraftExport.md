# Export to Minecraft

Compilation writes a Litematica `.litematic` file. To compile and copy it to a
Minecraft client's schematics directory, pass `--push` and optionally
`--minecraft-directory`:

```bash
python3 Main.py \
  --input Examples/FullAdder.sv \
  --output Output/FullAdder/FullAdder.litematic \
  --push \
  --minecraft-directory /path/to/.minecraft/schematics
```

To push an already generated file without compiling:

```bash
python3 Main.py \
  --push-file Output/FullAdder/FullAdder.litematic \
  --minecraft-directory /path/to/.minecraft/schematics
```

Only successful, transactionally published litematics should be exported.
The writer emits one Litematica region using the exact rendered block map;
supports may be colored per signal with `--trace-support-blocks` and a
comma-separated list of Minecraft block IDs.

Confirm the destination and open the design in Litematica before building it.
The `.PhysicalDesign.json` beside the source litematic is the provenance and
validation record; keep it with shared artifacts.

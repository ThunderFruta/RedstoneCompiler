# Litematic output

`<Name>.litematic` is the final Minecraft build artifact. The writer targets
the Litematica NBT format and emits one region from the canonical block map.
The map includes standard cells, redstone routes, supports, repeaters, and
annotations.

The artifact is binary and should be inspected through Litematica or NBT-aware
tools. Do not edit it as text. Its companion `.PhysicalDesign.json` records
dimensions, exact non-air blocks, material counts, routing provenance, and
validation.

Publication is transactional: routing or simulation failure must not leave a
new litematic that appears successful. Use the compiler's `--push` or
`--push-file` workflow described in the
[Minecraft export guide](../Guides/MinecraftExport.md).

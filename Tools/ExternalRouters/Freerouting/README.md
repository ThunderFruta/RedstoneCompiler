# Freerouting benchmark integration

Freerouting v2.3.0 is installed here as an independent, pinned PCB autorouter.
The upstream JAR and license are kept separate from RedstoneCompiler code; see
`UPSTREAM.md` and `Upstream.json` for the exact release, commit, size, hash, and
repair command.

Run the complete external matrix from the repository root:

```bash
python3 Scripts/Routing/RunFreeroutingBenchmark.py
```

The defaults mirror `Scripts/Routing/RunRouterAcceptance.py`: FullAdder 5 times,
RippleCarryAdder4 3 times, RippleCarryAdder8 3 times, and
CarryLookaheadAdder4 2 times. Results are written below
`Output/Benchmarks/Freerouting/<UTC timestamp>/`. A one-run smoke check can be
selected with `--case FullAdder --runs 1 --output-dir /tmp/freerouting-smoke`.

## What the adapter preserves

The harness runs the repository's normal parse, logic optimization, NAND
transform, and NAND validation stages. It then maps every NAND artifact gate to
one fixed component and every produced signal to one PCB hyperedge containing
the producer pin and all consumer pins. It checks that Freerouting's initial
unrouted-item count equals the NAND sink-connection count.

The generated DSN uses a deterministic topological-column placement, four PCB
signal layers, 0.2 mm traces, 0.2 mm clearance, through-layer terminals, and
90-degree routing. Freerouting fanout and optimization are disabled so the
timed result isolates its sequential autoroute stage. `-mt 1`, `-us greedy`,
and `-is sequential` remove documented sources of variation.

## Evidence and status

Each run preserves the input DSN, output SES, application log, GNU `time -v`
record, DRC log, DRC JSON, parsed run result, and normalized route hash. The
harness measures NAND preparation, DSN adaptation, route process wall, internal
autoroute stage, route CPU/RSS, SES parsing, independent DRC process wall, DRC
CPU/RSS, geometry, bends, vias, and repeat determinism.

Freerouting v2.3.0 requires routing and DRC as separate processes. The DRC
process exits zero even for dirty boards, so `PCB_DRC_CLEAN` requires all of:

- route process exit zero and a non-empty SES;
- router job state `COMPLETED`;
- zero router-reported unrouted items and violations;
- successful DSN+SES import by the independent DRC process; and
- empty `unconnected_items`, `violations`, and `schematic_parity` arrays in the
  DRC JSON.

## Comparison boundary

This is a logical-suite, synthetic-PCB-placement benchmark. It does not reuse
the native Minecraft placement, resource graph, or physical rules. In
particular, Freerouting does not validate cell support and required-air claims,
dust adjacency, repeater direction or power distance, step legality,
materialization, or physical truth-table simulation. CLA4 currently has no
accepted native placement, so its external result is explicitly
`SYNTHETIC_PLACEMENT_ONLY`; it cannot resolve the native `PlacementOverlap`
failure.

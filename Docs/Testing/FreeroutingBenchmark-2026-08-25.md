# Freerouting v2.3.0 external routing benchmark

Date: 2026-08-25 America/New_York (2026-08-26 UTC)

## Outcome

The official Freerouting v2.3.0 release was installed as an isolated external
router under `Tools/ExternalRouters/Freerouting/`. Its pinned release JAR is
62,995,156 bytes with SHA-256
`3cf18d608437740bc497db6b8ef5888e2e60a08de0def20691d1bad0c0e0ee24`.
The upstream tag is commit
`2d4de019aa89e9fa3dc1dc44e09bf509760cafc1`, and the unmodified GPL-3.0
license is preserved beside the integration metadata.

The same FullAdder, RCA4, RCA8, and CLA4 NAND graphs and the same 5/3/3/2
repetition matrix were routed sequentially. All 13 external runs completed,
all expected sink connections were routed, every emitted SES was imported by
the independent DRC process, and every DRC JSON reported empty
`unconnected_items`, `violations`, and `schematic_parity` arrays. Repeated
routes were byte-identical and semantically identical for every circuit.

## Method and comparison boundary

`Scripts/Routing/RunFreeroutingBenchmark.py` runs the normal frontend, logic
optimization, NAND transform, and NAND validation stages. It maps every NAND
gate to one fixed PCB component and each consumed/routable signal to one
hyperedge containing its producer pin and all consumer pins. Dangling output
sentinels are not PCB nets. The generated Specctra DSN uses deterministic
topological columns, four signal layers, 0.2 mm traces, 0.2 mm clearance,
through-layer terminals, and 90-degree routing.

Freerouting was run in route-only mode with fanout and route optimization
disabled. `-mt 1`, `-us greedy`, and `-is sequential` were pinned. Every route
was checked by a second Freerouting process loading both the original DSN and
the emitted SES. This matters because v2.3.0 disables routing when `-drc` is
present and exits zero even when its DRC JSON contains failures.

This preserves logical topology, not physical-domain parity. The external
problem does not reuse native Redstone placement, cell geometry, resource
claims, or routing capacity. It does not validate Minecraft supports,
required air, dust adjacency, repeater direction and power distance,
materialization, or physical truth-table simulation. External lengths are PCB
millimeters and cannot be compared to native Redstone route-length units.

## Exact input identity

| Circuit | Gate kinds (input/NAND/output) | Routable nets | Sink connections | Max fanout | NAND SHA-256 |
| --- | ---: | ---: | ---: | ---: | --- |
| FullAdder | 3 / 9 / 2 | 12 | 20 | 3 | `8bbd1241f373a47b9657a95340c85787dce17f36c6e47b427aa2e63eafd3450e` |
| RippleCarryAdder4 | 9 / 36 / 5 | 45 | 77 | 3 | `4bedb5069991cfa34b928db0974a22eabe7579f11fc7e8aaf2d2f3ba91a2ce3d` |
| RippleCarryAdder8 | 17 / 72 / 9 | 89 | 153 | 3 | `89a13c918174cdd943d7479f9d492b51edc39113e13cfb7ac9b301363bc919ca` |
| CarryLookaheadAdder4 | 9 / 72 / 5 | 81 | 149 | 6 | `5593b4536c985b3d99a9aa9ff20a94e6ddcb105741f21df21a694b1f980b424f` |

These hashes match the current native sweep's pre-placement NAND artifacts.

## External timing and process results

Route wall and DRC wall each include their own JVM/application startup plus an
unsuccessful background release check made by Freerouting. Router core is the
internal autoroute-stage timer; DRC wall is the full separate DSN+SES
validation process, not pure checking time. Total is route wall plus DRC wall.
NAND preparation and DSN adaptation took less than 2.3 ms per circuit and were
outside repeated router timing.

| Circuit | Clean runs | Route wall s, mean (min-max) | Router core mean s | DRC wall mean s | Route + DRC mean s | Route user CPU mean s | Route peak RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FullAdder | 5/5 | 3.179 (3.159-3.197) | 0.298 | 2.222 | 5.401 | 2.574 | 659.3 |
| RippleCarryAdder4 | 3/3 | 3.705 (3.659-3.732) | 0.963 | 2.242 | 5.947 | 6.600 | 1089.3 |
| RippleCarryAdder8 | 3/3 | 5.734 (5.718-5.751) | 2.803 | 2.310 | 8.044 | 14.670 | 1370.0 |
| CarryLookaheadAdder4 | 2/2 | 4.735 (4.726-4.743) | 2.185 | 2.309 | 7.044 | 10.940 | 1562.3 |

The host exposed 32 logical CPUs, used the `powersave` governor, and had load
averages 1.65/1.50/1.29 at capture start. Java was OpenJDK 25.0.3. Routing was
sequential on compiler base commit
`1681514368979f2cca1635b90b7f27062a966e33`; the new integration files were
untracked during capture and are recorded in the manifest's starting status.

### External preparation and artifact parsing timers

| Circuit | Parse ms | Optimize ms | NAND + validate ms | NAND write ms | DSN adapt + write ms | SES parse mean ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| FullAdder | 0.628 | 0.389 | 0.029 | 0.232 | 0.162 | 0.324 |
| RippleCarryAdder4 | 0.372 | 0.046 | 0.187 | 0.318 | 0.374 | 0.968 |
| RippleCarryAdder8 | 0.417 | 0.065 | 0.232 | 0.445 | 0.685 | 1.842 |
| CarryLookaheadAdder4 | 0.402 | 0.051 | 0.690 | 0.422 | 0.476 | 1.380 |

GNU `time -v` also records system CPU, context switches, filesystem I/O, and
per-run peak RSS for both the route and DRC processes in the JSON manifest.

## External route results

| Circuit | Passes | Routed nets | Wires / segments | Length mm | Bends | Vias | Layers used | DRC failures | Repeat route |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |
| FullAdder | 2 | 12 | 23 / 50 | 181.1434 | 27 | 0 | L1, L2 | 0 | identical |
| RippleCarryAdder4 | 2 | 45 | 84 / 227 | 875.5126 | 143 | 0 | L1, L2, L3 | 0 | identical |
| RippleCarryAdder8 | 3 | 89 | 165 / 489 | 2509.9653 | 324 | 5 | L0-L3 | 0 | identical |
| CarryLookaheadAdder4 | 4 | 81 | 163 / 414 | 1560.8033 | 251 | 0 | L0-L3 | 0 | identical |

FullAdder and RCA4 reached zero unrouted items during pass 1, followed by a
zero-work confirming pass. RCA8 reported 5 unrouted items after pass 1 and 0
after pass 2. CLA4 reported 3, then 2, then 0 unrouted items across its first
three passes. No pass reported a violation. The final extra pass confirmed the
clean score.

## Native comparison

The native numbers below are full compiler process wall times and include
physical placement, Redstone routing, materialization, and authoritative
truth-table simulation. The external route wall omits those contracts and is
shown only as a topology/capacity timing reference.

| Circuit | Native result | Native wall mean s | External route wall mean s | External route + DRC mean s |
| --- | --- | ---: | ---: | ---: |
| FullAdder | 5/5 accepted | 5.515 | 3.179 | 5.401 |
| RippleCarryAdder4 | 3/3 accepted | 7.131 | 3.705 | 5.947 |
| RippleCarryAdder8 | 3/3 accepted | 10.859 | 5.734 | 8.044 |
| CarryLookaheadAdder4 | failed before routing | 16.694 first failure | 4.735 | 7.044 |

The native accepted designs also produced truth-table proofs and litematics:
FullAdder 8/8 rows with a 19x7x32 design, RCA4 512/512 with a 73x7x33 design,
and RCA8 131072/131072 with a 149x7x32 design. The external outputs do not
provide equivalent evidence.

The successful native artifacts expose the following internal routing means.
These timers cover the named routing-resource stages; the larger reported
pipeline and process-wall times also include work such as placement,
materialization, publication, and simulation that is not split into equivalent
per-stage fields.

| Circuit | Guide ms | Portals ms | Candidates ms | Claim pre-screen ms | Resource graph ms | Foreign exclusion ms | Assignment prep ms | Assignment ms | Routing telemetry total s | Physical simulation s | Reported pipeline s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FullAdder | 0.611 | 1.725 | 361.366 | 5.317 | 0.766 | 0.085 | 0.078 | 48.164 | 0.422 | 0.055 | 5.191 |
| RippleCarryAdder4 | 0.238 | 34.665 | 899.503 | 87.484 | 1.231 | 5.770 | 0.218 | 174.812 | 1.140 | 0.006 | 6.788 |
| RippleCarryAdder8 | 0.224 | 32.004 | 775.922 | 2.325 | 2.004 | 4.198 | 0.233 | 187.805 | 1.028 | 0.601 | 10.386 |

Native CLA4 failed twice at `Placement / PlacementOverlap` with detail
`no exact-legal placement candidate was generated`. The two internal failure
times were 16.511 s and 16.377 s, with about 101.6 s of routing budget still
remaining. This is a structural placement/interface failure, not a routing
timeout. The clean external CLA4 result bypasses that failed stage with its
synthetic placement and therefore does not fix or disprove the native failure.

## Artifacts and reproduction

- External manifest:
  `Output/Benchmarks/Freerouting/2026-08-25-v2.3.0-logical-suite-final/BenchmarkManifest.json`
  (128,644 bytes, SHA-256
  `05f2039f74c1f694d730e414c9fd6a87e3d13356ec05feb8ae103c3a1e312c9b`)
- Generated concise report:
  `Output/Benchmarks/Freerouting/2026-08-25-v2.3.0-logical-suite-final/Report.md`
- Native comparison manifest:
  `/tmp/redstone-timegraph-20260825-2128/2026-08-25/RouterRegression/StandaloneAcceptance/AcceptanceManifest.json`
- Second native CLA4 diagnostic failure:
  `/tmp/redstone-timegraph-20260825-2128/Diagnostics/CarryLookaheadAdder4Run2/CarryLookaheadAdder4Run2.RoutingFailure.json`
- Pinned install and interpretation guide:
  `Tools/ExternalRouters/Freerouting/README.md`

The final manifest binds the exact adapter source SHA-256
`9b1826162a166b36c97b47dc7e939ba0d04b67b113de6674430e511a2d6b6886`,
native acceptance-matrix source SHA-256
`314f2ce0128405228f8a75b6c2853942d86f121b9ef0b62147e71aa83ef48c55`,
and upstream metadata SHA-256
`ceec3bfd5dd8a09d5a30827032e0730efc7e845fd954a5730b29560ad0657c6e`.

Focused verification after the final adapter changes passed 66 tests plus 63
subtests across the external benchmark and native acceptance harness.

Reproduce the complete external matrix with:

```bash
python3 Scripts/Routing/RunFreeroutingBenchmark.py
```

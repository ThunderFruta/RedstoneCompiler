# Routing design snapshot

- Snapshot ID: `20260828T001726Z-5cf76bd100006383`
- Captured UTC: `2026-08-28T00:17:26Z`
- Captured local: `2026-08-27T20:17:26-04:00`
- Canonical evidence: `5cf76bd100006383702ac28c9b7e3e86f10b5cb579f58ae053f9429ca87a0956`
- Revision: `1681514368979f2cca1635b90b7f27062a966e33`
- Branch: `main`
- Dirty: `true`
- Status SHA-256: `9899b1c04dc9c15f38d03a4181ce442be0dd787ada3ffd8febd1753101f61af9`

## Source

- Scope: `routing-implementation-source-v1`
- Aggregate SHA-256: `ce592607e9eef6ffc2d3b2527659dc0c1271644b37db39eac360a52806f9d88e`
- Files: `67`
- Physical lines: `171085`
- Nonblank lines: `165424`

### Largest Python definitions

| Definition | File | AST span lines |
| --- | --- | ---: |
| `_PlaceAndRoutePcbWithPolicy` | `Compiler/Placement/PcbFlow.py` | 16947 |
| `RouteAuthoritativeResources` | `Compiler/Routing/AuthoritativePlanner.py` | 16002 |
| `PlacePcbGraph` | `Compiler/Placement/Pcb.py` | 5402 |
| `SolvePreparedPhysicalComponentPortFactorDomain` | `Compiler/Routing/AuthoritativePlanner.py` | 4126 |
| `PlanNegotiatedRouteTrees` | `Compiler/Routing/AuthoritativePlanner.py` | 3973 |
| `PreparePhysicalComponentPortFactorDomain` | `Compiler/Routing/AuthoritativePlanner.py` | 2697 |
| `ReserveClusterBoundaryLeases` | `Compiler/Routing/AuthoritativePlanner.py` | 2395 |
| `_SolveComponentRoutingProblemLegacy` | `Compiler/Routing/ComponentRouter.py` | 2352 |
| `SolveComponentRoutingProblemDynamic` | `Compiler/Routing/ComponentRouter.py` | 2221 |
| `_PlaceAndRoutePcbWithPolicy._TryPlacement` | `Compiler/Placement/PcbFlow.py` | 2202 |

## CLA4 failure

- Stage: `Placement`
- Reason: `PlacementOverlap`
- Detail: `no exact-legal placement candidate was generated`
- Runtime seconds: `16.376594`
- Timed out: `false`
- Deadline expired: `false`
- Remaining milliseconds: `101700`
- Detailed routing started: `false`

### Placement candidates

| Generator | Elapsed s | Claims | Conflicts | Signals |
| --- | ---: | ---: | ---: | --- |
| `row-beam` | 13.810852 | 8814 | 2 | NandNet0, Propagate0 |
| `row-beam-direct-only` | 2.488325 | 8808 | 2 | NandNet0, NandNet2 |

## Copied artifacts

| Snapshot path | Bytes | SHA-256 |
| --- | ---: | --- |
| [AcceptanceManifest.json](Artifacts/AcceptanceManifest.json) | 347237 | `787d8adff005466fc9da866dc930b1b7973365c96a3049f039ebcaabddfcadae` |
| [CarryLookaheadAdder4Run2.Nand.dot](Artifacts/CarryLookaheadAdder4Run2.Nand.dot) | 11398 | `dd06752150a1bad7972e3adb461aea2c56655395a41c1f3fbfccda153fdb1207` |
| [CarryLookaheadAdder4Run2.Nand.json](Artifacts/CarryLookaheadAdder4Run2.Nand.json) | 15019 | `5593b4536c985b3d99a9aa9ff20a94e6ddcb105741f21df21a694b1f980b424f` |
| [CarryLookaheadAdder4Run2.RoutingFailure.json](Artifacts/CarryLookaheadAdder4Run2.RoutingFailure.json) | 16261 | `c4f57aad994f168e47fb6165f6858bbb3898ec3b31507503cb394b7e95736ebb` |

This snapshot records a typed structural placement failure. It does not establish CLA4 routing acceptance.

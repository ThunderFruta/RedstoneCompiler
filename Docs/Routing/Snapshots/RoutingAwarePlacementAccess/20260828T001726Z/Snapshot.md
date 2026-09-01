# Routing design snapshot

- Snapshot ID: `20260828T001726Z-e4354645f99ae42d`
- Captured UTC: `2026-08-28T00:17:26Z`
- Captured local: `2026-08-27T20:17:26-04:00`
- Canonical evidence: `e4354645f99ae42d78f681688d93b21ca46ad7248c6d694eecc06f019e406465`
- Revision: `1681514368979f2cca1635b90b7f27062a966e33`
- Branch: `main`
- Dirty: `true`
- Status SHA-256: `1a3a9d51409b691ea50c7c41a4a198a490a6f291294c9ff931ca243fdc48dbef`

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
| [AcceptanceManifest.json](Artifacts/AcceptanceManifest.json) | 341369 | `f52bbef7aecba829fdb7c27da243035880008f1487bc12f8b8a5c154f599b91f` |
| [CarryLookaheadAdder4Run2.Nand.json](Artifacts/CarryLookaheadAdder4Run2.Nand.json) | 15019 | `5593b4536c985b3d99a9aa9ff20a94e6ddcb105741f21df21a694b1f980b424f` |
| [CarryLookaheadAdder4Run2.RoutingFailure.json](Artifacts/CarryLookaheadAdder4Run2.RoutingFailure.json) | 16144 | `b4c5b9c8d6f07e3df0b9a26a7c8e506392e3e7404ed6c152dbd9edbe188efb94` |

This snapshot records a typed structural placement failure. It does not establish CLA4 routing acceptance.

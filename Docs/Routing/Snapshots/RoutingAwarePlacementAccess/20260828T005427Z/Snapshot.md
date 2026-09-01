# Routing design snapshot

- Snapshot ID: `20260828T005427Z-cbf53696cb418ae4`
- Captured UTC: `2026-08-28T00:54:27Z`
- Captured local: `2026-08-27T20:54:27-04:00`
- Exact evidence SHA-256: `28167d900ccf6c8fe0b95eb0680a2d23539db9f27303621536f9173f5d560082`
- Portable semantic evidence SHA-256: `cbf53696cb418ae444b25423fe3d219d20dc7c367f901d2a3ee6d93d4a1d9a68`
- Revision: `1681514368979f2cca1635b90b7f27062a966e33`
- Branch: `main`
- Dirty: `true`
- Status SHA-256: `b368ddc4fa99d280949e1d7dc7bf1ed975ae71b76723fbddebe66154bdb7f859`

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

## Current runtime provenance

- Python: `CPython 3.12.3`
- Python executable: `/mnt/Projects/RedstoneCompiler/.venv/bin/python`
- Platform: `Linux-7.0.0-28-generic-x86_64-with-glibc2.39`
- Default policy: `physical-design-v16-reconvergent-access`
- Native extension: `RedstoneCompiler/RustRouting.cpython-312-x86_64-linux-gnu.so`
- Native SHA-256: `519bf9ebab4700539a93ee0718fc63069698b1dcbcc51cef399fc25d02447113`
- Benchmark input aggregate: `70788765155598b6751a296376968fa695540e93fdd96f798e54a2c5e22cb396`
- Tracked template aggregate: `69949bee51e6137894b983fee91c3d00ddc0f459d71903902b654aa3fe1e784e`

## Native acceptance-manifest evidence

- Manifest status: `FAILED`
- Accepted: `false`
- Policy: `physical-design-v16-reconvergent-access`
- Source provenance stable: `true`

| Cross-check | Result |
| --- | --- |
| `AuthoritativeCaseMatrixMatches` | `true` |
| `CurrentBenchmarkInputsMatch` | `true` |
| `CurrentBuildInputsMatch` | `true` |
| `CurrentDefaultPolicyMatches` | `true` |
| `CurrentNativeExtensionMatches` | `true` |
| `CurrentPhysicalTemplatesMatch` | `true` |
| `CurrentRoutingSourceMatches` | `true` |
| `FailureInputMatches` | `true` |
| `FailurePolicyMatches` | `true` |
| `FailureRevisionMatches` | `true` |

## CLA4 failure

- Stage: `Placement`
- Reason: `PlacementOverlap`
- Detail: `no exact-legal placement candidate was generated`
- Runtime seconds: `16.376594`
- Timed out: `false`
- Deadline expired: `false`
- Remaining milliseconds: `101700`
- Detailed routing started: `false`
- Success-artifact absence verified: `true`

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

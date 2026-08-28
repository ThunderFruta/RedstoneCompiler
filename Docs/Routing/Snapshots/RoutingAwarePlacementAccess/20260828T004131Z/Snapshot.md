# Routing design snapshot

- Snapshot ID: `20260828T004131Z-ef7006f0d411380b`
- Captured UTC: `2026-08-28T00:41:31Z`
- Captured local: `2026-08-27T20:41:31-04:00`
- Exact evidence SHA-256: `23fd874ccc015a9629feb54eec4aeda1a2c550ba44869467268f8efc27b3f5e4`
- Portable semantic evidence SHA-256: `ef7006f0d411380b6d581b37300f808de5d23e5a52f26695c9901e2511e7e30f`
- Revision: `1681514368979f2cca1635b90b7f27062a966e33`
- Branch: `main`
- Dirty: `true`
- Status SHA-256: `154087ed86b0fca9bf084d65d613be165573bb316359f09fd2e399c244f0a0d1`

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
| [AcceptanceManifest.json](Artifacts/AcceptanceManifest.json) | 347237 | `787d8adff005466fc9da866dc930b1b7973365c96a3049f039ebcaabddfcadae` |
| [CarryLookaheadAdder4Run2.Nand.dot](Artifacts/CarryLookaheadAdder4Run2.Nand.dot) | 11398 | `dd06752150a1bad7972e3adb461aea2c56655395a41c1f3fbfccda153fdb1207` |
| [CarryLookaheadAdder4Run2.Nand.json](Artifacts/CarryLookaheadAdder4Run2.Nand.json) | 15019 | `5593b4536c985b3d99a9aa9ff20a94e6ddcb105741f21df21a694b1f980b424f` |
| [CarryLookaheadAdder4Run2.RoutingFailure.json](Artifacts/CarryLookaheadAdder4Run2.RoutingFailure.json) | 16261 | `c4f57aad994f168e47fb6165f6858bbb3898ec3b31507503cb394b7e95736ebb` |

This snapshot records a typed structural placement failure. It does not establish CLA4 routing acceptance.

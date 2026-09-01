# Routing design snapshot

- Snapshot ID: `20260828T151609Z-2c2132084dda3215`
- Captured UTC: `2026-08-28T15:16:09Z`
- Captured local: `2026-08-28T11:16:09-04:00`
- Exact evidence SHA-256: `aebd93a82e8fce1e10fdf07c04d9c2ace735fd97f4f51fbba77122f1aafef081`
- Portable semantic evidence SHA-256: `2c2132084dda3215ab17b96b22e639e804ba0f2dd3d1ca3e89496866811f34df`
- Revision: `1681514368979f2cca1635b90b7f27062a966e33`
- Branch: `main`
- Dirty: `true`
- Status SHA-256: `a9fa30931eb872fb332c1add0b5dcd247016b461d7bf336c3a5b8f8dd74a2052`

## Source

- Scope: `routing-implementation-source-v1`
- Aggregate SHA-256: `057655f65674ce1c1f6e46c38cc6e6ac458ae0903b32406e7e5402da7f1169f9`
- Files: `217`
- Physical lines: `132941`
- Nonblank lines: `127342`

### Largest Python definitions

| Definition | File | AST span lines |
| --- | --- | ---: |
| `AuthoritativeRoutingState` | `Compiler/Routing/Authoritative/RunState.py` | 1077 |
| `_SolvePreparedPhysicalComponentPortFactorDomain` | `Compiler/Routing/Authoritative/PortSolving/Search.py` | 999 |
| `BuildPlacementAccessFabric` | `Compiler/Placement/Access/Fabric.py` | 976 |
| `OptimizeJointClusterPlacement` | `Compiler/Placement/Core/Search.py` | 966 |
| `IterPhysicalBoundaryPortAssignments` | `Compiler/Routing/Authoritative/ExteriorConnectors.py` | 957 |
| `SolveComponentInterfaceCsp` | `Compiler/Routing/Components/InterfacePlanning.py` | 938 |
| `RecordPhysicalComponentGlobalPlanNoGood` | `Compiler/Routing/Components/GlobalNoGoods.py` | 887 |
| `CompilePhysicalBoundaryMandatoryPortalPairRelation` | `Compiler/Routing/Interfaces/BoundaryRelations.py` | 871 |
| `BuildBoundedInterClusterRoutingChannel` | `Compiler/Placement/Core/Clusters.py` | 832 |
| `BuildBoundedInterClusterRoutingDeck` | `Compiler/Placement/Core/Clusters.py` | 789 |

## Current runtime provenance

- Python: `CPython 3.12.3`
- Python executable: `/mnt/Projects/RedstoneCompiler/.venv/bin/python`
- Platform: `Linux-7.0.0-28-generic-x86_64-with-glibc2.39`
- Default policy: `physical-design-v16-reconvergent-access`
- Native extension: `RedstoneCompiler/RustRouting.cpython-312-x86_64-linux-gnu.so`
- Native SHA-256: `9750ecb2752be302ecf789e1bbc739f19886a0a9529d3895144d2e39435c956e`
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
- Runtime seconds: `16.187578`
- Timed out: `false`
- Deadline expired: `false`
- Remaining milliseconds: `101888`
- Detailed routing started: `false`
- Success-artifact absence verified: `true`

### Placement candidates

| Generator | Elapsed s | Claims | Conflicts | Signals |
| --- | ---: | ---: | ---: | --- |
| `row-beam` | 13.599848 | 8814 | 2 | NandNet0, Propagate0 |
| `row-beam-direct-only` | 2.511667 | 8808 | 2 | NandNet0, NandNet2 |

## Copied artifacts

| Snapshot path | Bytes | SHA-256 |
| --- | ---: | --- |
| [AcceptanceManifest.json](Artifacts/AcceptanceManifest.json) | 415151 | `c5dc005b938dea3cdff8ba794ce315cd30ecc3946185cc1977e75256299ef8e3` |
| [CarryLookaheadAdder4Run1.Nand.json](Artifacts/CarryLookaheadAdder4Run1.Nand.json) | 15019 | `5593b4536c985b3d99a9aa9ff20a94e6ddcb105741f21df21a694b1f980b424f` |
| [CarryLookaheadAdder4Run1.RoutingFailure.json](Artifacts/CarryLookaheadAdder4Run1.RoutingFailure.json) | 16605 | `e2eaa1db52deedc27605327f1b9cbc1afa7deaff78e4decc0eb6566fd3bac149` |
| [Rca8PerformanceRerunAcceptanceManifest.json](Artifacts/Rca8PerformanceRerunAcceptanceManifest.json) | 405415 | `5f6f0c132fbcd029c608ffc8dca6b7a1397e1b74524086c399f01c25bac39622` |

This snapshot records a typed structural placement failure. It does not establish CLA4 routing acceptance.

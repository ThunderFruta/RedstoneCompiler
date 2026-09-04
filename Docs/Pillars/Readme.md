# Physical-design pillars and necessary requirements

This directory is the canonical catalog for the physical-design rewrite's ten
requested pillars and six necessary supporting requirements. The documents
define target behavior and acceptance conditions. They do not, by themselves,
claim that the behavior has been implemented or accepted.

The two groups have different roles:

- `R/` contains the requested product and architecture improvements.
- `N/` contains the correctness, execution, reuse, and acceptance requirements
  that every applicable pillar must obey.

## Requested pillars

| ID | Requirement | Primary owner | Records |
|---|---|---|---|
| [R1](R/R1/R1.md) | Delay expensive physical expansion | `PhysicalDesign/Routing/Global/` | [History](R/R1/CommitHistory.md) · [Notes](R/R1/Notes.md) |
| [R2](R/R2/R2.md) | Choose placement and routing space together | `PhysicalDesign/Orchestration/` | [History](R/R2/CommitHistory.md) · [Notes](R/R2/Notes.md) |
| [R3](R/R3/R3.md) | Generate folded and fully oriented alternatives early | `PhysicalDesign/Placement/` | [History](R/R3/CommitHistory.md) · [Notes](R/R3/Notes.md) |
| [R4](R/R4/R4.md) | Preserve global routing awareness throughout search | `PhysicalDesign/Routing/Global/` | [History](R/R4/CommitHistory.md) · [Notes](R/R4/Notes.md) |
| [R5](R/R5/R5.md) | Generalize by topology and reuse compatible work | `PhysicalDesign/Routing/Regions/` | [History](R/R5/CommitHistory.md) · [Notes](R/R5/Notes.md) |
| [R6](R/R6/R6.md) | Use persistent, priority-aware workers | `PhysicalDesign/Runtime/` | [History](R/R6/CommitHistory.md) · [Notes](R/R6/Notes.md) |
| [R7](R/R7/R7.md) | Keep policy in Python and native kernels narrow | Python physical-design owners and `Kernels/Routing/` | [History](R/R7/CommitHistory.md) · [Notes](R/R7/Notes.md) |
| [R8](R/R8/R8.md) | Retain detailed telemetry with concise terminal output | `App/` | [History](R/R8/CommitHistory.md) · [Notes](R/R8/Notes.md) |
| [R9](R/R9/R9.md) | Optimize routed connection quality and remove detours | `PhysicalDesign/Objectives/` and routing producers | [History](R/R9/CommitHistory.md) · [Notes](R/R9/Notes.md) |
| [R10](R/R10/R10.md) | Establish one shared pre-validation redstone model | `PhysicalDesign/Redstone/` | [History](R/R10/CommitHistory.md) · [Notes](R/R10/Notes.md) |

## Necessary requirements

| ID | Requirement | Primary owner | Records |
|---|---|---|---|
| [N1](N/N1/N1.md) | Explicit search outcomes and independent state axes | `PhysicalDesign/Contracts/` | [History](N/N1/CommitHistory.md) · [Notes](N/N1/Notes.md) |
| [N2](N/N2/N2.md) | One shared physical-rule contract | `PhysicalDesign/Redstone/` and `PhysicalDesign/Contracts/` | [History](N/N2/CommitHistory.md) · [Notes](N/N2/Notes.md) |
| [N3](N/N3/N3.md) | Verifiable reuse and coordinator-only commitment | `PhysicalDesign/Orchestration/` | [History](N/N3/CommitHistory.md) · [Notes](N/N3/Notes.md) |
| [N4](N/N4/N4.md) | Bounded scheduling, memory, cancellation, and shutdown | `PhysicalDesign/Runtime/` | [History](N/N4/CommitHistory.md) · [Notes](N/N4/Notes.md) |
| [N5](N/N5/N5.md) | Explicit, non-fail-fast physical acceptance | `Validation/` and `Tools/Routing/` | [History](N/N5/CommitHistory.md) · [Notes](N/N5/Notes.md) |
| [N6](N/N6/N6.md) | Selective stale-result salvage | `PhysicalDesign/Orchestration/` | [History](N/N6/CommitHistory.md) · [Notes](N/N6/Notes.md) |

## Governing dependency direction

```text
App / Tools
    -> Compilation pipeline
        -> Physical-design coordinator
            -> Placement, global routing, region routing, objectives
                -> Contracts, cells, redstone model, resources
                    -> Bounded native kernels

Final physical artifact
    -> Rendering
    -> Validation/Physical
        -> Validation/Mchprs
            -> Validation/Fabric
```

The coordinator is the only owner that commits accepted design state. Workers,
caches, native kernels, renderers, and validation adapters return information;
they do not mutate another stage's accepted state.

## Status language

Use these terms consistently:

- **Target:** specified here but not established by implementation evidence.
- **Implemented:** present in source and covered by focused tests.
- **Accepted:** verified through the applicable physical and runtime gates.
- **Inherited failure:** a pre-existing failure reproduced without regression.
- **Not-run:** a required check that was not executed; never equivalent to a
  pass.

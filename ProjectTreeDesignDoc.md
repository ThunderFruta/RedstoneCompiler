# Project Tree Design

## Purpose

Make the repository layout explicitly represent the compiler pipeline stages:
SV decoding, NAND normalization, placement, routing, and schematic output.
This implementation slice focuses on documentation, ownership boundaries, and
safe migration scaffolding; compiler behavior and generated artifact formats stay
unchanged.

## Scope of this implementation slice

- Preserve all existing Python package imports and CLI behavior.
- Preserve all existing generated outputs and file formats.
- Add a canonical documentation layout and migration index under `Docs/`.
- Do not perform import rewires, file moves, or runtime behavior changes in this
  step.

## Target root tree

```text
RedstoneCompiler/
├── Assets/
├── Cache/
│   ├── Frontend/
│   ├── Python/
│   ├── Rust/
│   ├── Tests/
│   └── Tools/
├── Docs/
│   ├── Architecture/
│   ├── Development/
│   ├── Formats/
│   ├── Guides/
│   ├── Routing/
│   ├── Testing/
│   ├── ProjectTree.md
│   └── Readme.md
├── Output/
├── RustRouting/
│   ├── Src/
│   └── target/
├── SchemEncoder/
├── SVDecoder/
├── Templates/
├── Tests/
├── Tools/
├── Scripts/
├── Compiler/
│   ├── Cells/
│   ├── Ir/
│   ├── Placement/
│   ├── Routing/
│   ├── Simulation/
│   ├── Synthesis/
│   ├── Pipeline.py
│   └── Main.py
├── AGENTS.md
├── Readme.md
├── RoutingDesignDoc.md
├── ProjectTreeDesignDoc.md
├── STYLE.md
├── Build/             # compatibility alias during migration
└── pyproject.toml
```

`SchemEncoder` is the corrected root for schematic/litematic encoding.

## Directory ownership

| Directory | Ownership |
|---|---|
| `SVDecoder/` | Frontend parser, elaborator, and IR adapter for parser outputs |
| `Compiler/Ir/` | normalized IR and logic/data contracts |
| `Compiler/Synthesis/` | NAND normalization and logic transforms |
| `Compiler/Placement/` | placement orchestration and geometry helpers |
| `Compiler/Routing/` | routing and global plan contracts |
| `Compiler/Cells/` | standard-cell macros and compatibility cell definitions |
| `Compiler/Simulation/` | physical simulation and verification helpers |
| `SchemEncoder/` | litematic/schem encoding and file serialization |
| `Templates/` | source-controlled cell and region templates |
| `RustRouting/` | Rust-assisted routing/search resource backend |
| `Tests/` | unit, integration, and scale checks |
| `Scripts/` | maintenance/dev utility scripts |
| `Tools/` | third-party helpers and local tooling |
| `Build/` | compatibility output location during transition |
| `Output/` | new user-facing output location |
| `Cache/` | disposable compiler runtime state |
| `Docs/` | architecture, migration, and operational documentation |

## Cache and output policy

All disposable, reproducible state moves toward `Cache/`:

```text
Cache/
├── Frontend/       # parser/elaboration work data
├── Python/         # __pycache__, bytecode, generated test cache
├── Rust/           # Rust target and native extension artifacts
├── Tests/          # test-generated state
└── Tools/          # temporary tool state
```

User-visible artifacts remain under `Output/`:

```text
Output/
└── FullAdder/
    ├── FullAdder.Nand.json
    ├── FullAdder.Nand.dot
    ├── FullAdder.PhysicalDesign.json
    ├── FullAdder.TruthTable.txt
    └── FullAdder.litematic
```

## Source migration map

| Current location | Target location | Constraint |
|---|---|---|
| `RedstoneCompiler/Frontend/` | `SVDecoder/` | provide old import compatibility |
| `RedstoneCompiler/Schem/` | `SchemEncoder/` | provide writer import compatibility |
| `RedstoneCompiler/Templates/` | `Templates/` | preserve template lookup behavior |
| `RedstoneCompiler/Cells/` | `Compiler/Cells/` | preserve cell contracts |
| `RedstoneCompiler/Ir/` | `Compiler/Ir/` | preserve IR contracts |
| `RedstoneCompiler/Placement/` | `Compiler/Placement/` | preserve placement policy |
| `RedstoneCompiler/Routing/` | `Compiler/Routing/` | preserve routing policy |
| `RedstoneCompiler/Simulation/` | `Compiler/Simulation/` | preserve simulation behavior |
| `RedstoneCompiler/Synthesis/` | `Compiler/Synthesis/` | preserve transform outputs |
| `RedstoneCompiler/Main.py` | `Compiler/Main.py` | retain root launcher compatibility |
| `RedstoneCompiler/Pipeline.py` | `Compiler/Pipeline.py` | retain legacy API compatibility |
| `Build/` | `Output/` | keep `Build/` compatibility alias |
| `RedstoneCompiler/.RedstoneWork` | `Cache/Frontend/` | update defaults only |
| `.pytest_cache/`, `__pycache__/`, `RustRouting/target/` | `Cache/` | never commit generated state |

## Compatibility rules

- Existing commands and `python RedstoneCompiler/Main.py` continue to work
  unchanged.
- Existing `RedstoneCompiler.*` import paths keep working via compatibility
  facades during migration.
- Existing templates, JSON contracts, and litematic output contracts do not change
  in this phase.
- No placement, routing, synthesis, or simulation algorithm rewrites are part of
  this migration slice.

## Documentation requirements

- All root directories have a short `Readme.md` and ownership statement.
- Detailed architecture and workflow documents remain in `Docs/`.
- Routing and output failures are documented with explicit evidence fields and next
  actions.
- Every migration change updates both `ProjectTreeDesignDoc.md` and
  `Docs/ProjectTree.md`.

## Migration sequence

### Phase 1 (implemented now)

- Add `Docs/` structure and directory docs.
- Add migration index and tree definition documentation.
- Add explicit notes in this design document for compatibility and scope.
- Keep behavior-compatible paths and defaults unchanged.

### Phase 2

- Add stable root entrypoint and package facades.
- Redirect defaults to `Cache/Frontend/` and `Output/` while preserving user
  selections and compatibility paths.

### Phase 3

- Move `Frontend` and `Schem` front-door directories to `SVDecoder` and
  `SchemEncoder` and add compatibility facades.

### Phase 4

- Move remaining compiler groups into `Compiler/` in ownership order: `Ir`,
  `Synthesis`, `Cells`, `Placement`, `Routing`, `Simulation`, then
  pipeline modules.

### Phase 5

- Retire compatibility shims after one stable cycle with both old and new paths
  validated.

## Verification checklist

- Import compatibility checks for both old and new paths.
- Parser, synthesis, routing, and writer smoke checks after each move.
- One end-to-end FullAdder artifact check.
- No generated litematic/JSON contract changes.
- One authoritative docs index update per migration phase.

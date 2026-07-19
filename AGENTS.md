# AGENTS

## Scope
- This repository is a Python scaffold for a SystemVerilog-to-Redstone compiler.
- Primary flow: parse HDL → NAND-only synthesis → placement → routing → `.schem 26.2` output.

## Runtime and editing expectations
- Work in-place inside `/mnt/Projects/RedstoneCompiler`.
- Avoid unrelated refactors.
- Keep changes incremental and compile-oriented.

## Naming contract (requested)
- Use PascalCase for source identifiers (classes, functions, methods, and public members).
- Keep package/module structure stable unless a structural change is required.
- Prefer explicit stage naming in function names and file flow orchestration.

## Authoring constraints
- Add new logic in the existing package modules.
- Keep Stage boundaries clear:
  - Frontend (SV parsing)
  - Synthesis (NAND normalization)
  - Placement
  - Routing
  - `.schem` writing

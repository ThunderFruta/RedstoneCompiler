# STYLE

## Naming
- Use PascalCase for all code identifiers that are edited in this codebase:
  - Classes
  - Functions
  - Methods
  - Variables and parameters
  - Constants names beyond module constants

## Structure
- Keep one public class/function per stage.
- Keep stage boundaries explicit and easy to trace in the pipeline.
- Prefer small pure functions over implicit side effects.

## Files
- Keep file layout split by compiler phase:
  - `frontend`
  - `synthesis`
  - `placement`
  - `routing`
  - `schem`
- Keep CLI in a single entrypoint file and call into pipeline orchestration only.

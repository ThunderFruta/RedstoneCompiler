# Compiler action hooks

`Compilation.Hooks` exposes opt-in observation across the compiler pipeline.
Supply a fresh `CompilerHooks` instance to `CompileSvToLitematic(Hooks=...)`.
The pipeline saves `<output stem>.CompilerHooks/` on completion or failure.
Each exact recorded hook/stage has one JSON file containing its events;
`Index.json` lists the files and the overall run outcome. Repeated invocations
of a stage share its file and retain their original global sequence numbers.
Without hooks, operations execute normally and no trace is written.

In the interactive root menu, select **4. Debugging**, then **1. Compile with
compiler hooks**. Enter comma-separated stage selectors, or press Enter to
record all instrumented stages. The submenu also diagnoses saved trace JSON,
reads existing routing CPU telemetry summaries, and runs the source-structure
advisory reviewer. Saved-trace and telemetry inspection leave the original
artifacts unchanged. **5. More options** retains defaults and Minecraft utilities;
**6. Exit** closes the root menu.

For CLI runs, `--compiler-hooks` selects all stages. Repeat
`--compiler-hook-stage physical --compiler-hook-stage validation` to select
those stage families; a selector alone also enables capture. CLI output
artifacts live beneath its reserved per-run directory, so inspect the
`.CompilerHooks/Index.json` beside that run's output or typed failure.
Normal mode retains the usual summary and success/failure artifacts without
action traces. Hook mode adds the selected observations under the same compiler
policy, deadlines, validation and exit behavior. Capture has bounded runtime
and storage overhead; observations do not make failed compilation successful.

```python
from pathlib import Path
from Compilation.Hooks import CompilerHooks
from Compilation.Pipeline import CompileSvToLitematic

Hooks = CompilerHooks(
    Stages=("physical", "validation"),
    MaxEvents=4096,
    MaxBytes=2_000_000,
    Callback=lambda Event: print(Event["Stage"], Event["Action"]),
)
CompileSvToLitematic(
    InputPath=Path("Assets/Examples/HalfAdder.sv"),
    OutputPath=Path("Output/Hooks/HalfAdder.litematic"),
    DiagramPath=Path("Output/Hooks/HalfAdder.Nand.json"),
    Hooks=Hooks,
)
```

An empty `Stages` tuple selects all instrumented stages. A selector matches
itself and dotted descendants: `physical` selects `physical.place_and_route`
and `physical.routing`; `frontend.parse` selects just the parser operation.
The pipeline covers frontend parsing, logic optimization, NAND lowering and
validation, diagram writing, placement/routing, physical NAND validation,
rendering, fixture construction, MCHPRS, Fabric and final artifact publication.
Routing stage callbacks add transitions beneath `physical.routing` and events
with the observed stage name. Combined placement/routing remains one operation;
its transitions provide finer observations without claiming separate completed
placement or routing outcomes.

Each event contains `Sequence`, `Stage`, `Action`, `ElapsedSeconds` and `Fields`.
Operation actions are `begin`, `finish` or `failed`; `finish` means the function
returned, so a backend returning a failed result still requires its authoritative
verdict check. Live callbacks receive detached JSON values on a bounded background queue,
never mutable compiler objects. Callback return values do not control compilation. Callback exceptions are counted, and recursive callback emission is dropped.
Compiler operations do not wait for callback delivery. A stalled listener can
leave pending callbacks; the saved document records `PendingCallbacks` and
`DroppedCallbacks`, and callback-error counts reflect the snapshot time.
`WaitForCallbacks(TimeoutSeconds=...)` provides an optional bounded caller-owned
wait. `Close()` ends delivery after the queue drains without waiting for listener
code; component-only callers should close their hooks explicitly. Callbacks must
not mutate compiler state through references captured in their own closures.

The trace retains at most `MaxEvents` and `MaxBytes` of serialized event payloads.
Shared run metadata appears only in the index. `MaxStorageBytes` independently
caps the exact combined bytes of the index and all stage files; its default
8,000,000 bytes matches the reader's default total-read limit. Publication is
rejected before writing when that storage cap is exceeded, preserving the
compiler outcome and reporting `Unavailable`. It does not silently reset event
caps or discard already retained events to make a bundle fit. Custom larger
storage budgets require a correspondingly larger `MaxFileBytes` when reading.
Selector count is bounded by `MaxEvents`, and selector length by 128 characters. Payloads
also have bounded nesting, node counts, string lengths and integer size; arbitrary
objects and nonfinite numbers are rejected. `DroppedEvents` records lost
observations, including invalid payloads. `CallbackErrors` records callback
failures. The saved trace records the run outcome and exception type separately
from the selected events, so truncation or filtering cannot imply success.
Publication is best effort. A private directory is opened before its final name
is exposed through an atomic no-replace reservation. A competing empty or
nonempty directory is never adopted or replaced; an unavailable reservation
primitive reports `Unavailable` without changing the compiler outcome.
Member writes, exclusive index publication and temporary cleanup use
retained directory descriptors. Directory identity is re-attested before index
publication and before reporting `Saved`, so a path swap cannot redirect writes
to a replacement directory or obtain a saved status. The index is published
only after all stage files.
An interrupted or failed write may leave an incomplete directory, which the
reader rejects. `Hooks.WriteError` reports unavailable publication without
replacing the compiler result or original exception. `Hooks.Publication`
provides a detached status snapshot: `Status` (`Saved`, `Unavailable`, `NotRun`),
`DirectoryPath`, `IndexPath`, `HookFileCount`, `EventCount`, `DroppedEvents`
and `WriteError`. A confirmed saved index is the publication signal.
Component callers can use `SaveHooks(Directory)` to publish the split layout.
The explicit `Save(Path)` aggregate helper remains for compatibility; normal
new compiler captures use only the split layout.

For additional taps in Python code, call `EmitCompilerAction(Stage, Action,
**Fields)` with small JSON values, or wrap a callable with
`RunCompilerOperation(Stage, Function, *Arguments, **Keywords)`. An explicit
`CaptureCompilerActions(Hooks)` scope supports isolated component runs and restores
any outer scope afterward. Context is local to the execution context; unrelated
threads and worker processes require explicit capture. Native instructions and
worker internals are not automatically traced. Do not pass compiler objects,
secrets or full search state as fields. This is an instrumented action trace,
not an instruction recorder or a replay engine.

After a run:

```bash
.venv/bin/python -m Compilation.Hooks Output/Hooks/HalfAdder.CompilerHooks
```

`ReadCompilerTrace` accepts a hook directory, `Index.json`, an indexed stage
file, or a historical aggregate `compiler-action-trace-v1` file. The new index
schema is `compiler-action-index-v1`; stage files use
`compiler-action-stage-v2`; older stage-v1 bundles remain readable. File names use a safe ASCII stage slug and full
SHA-256 of the stage name; the index maps each name back to its exact stage.
Each new stage file contains its stage name, events and a SHA-256 reference to
the shared index metadata. The reader verifies that reference and attaches the
run metadata for diagnosis. It also supports the earlier stage-v1 files that
stored full metadata.
Stages filtered out, not reached, or entirely dropped have no stage file;
selectors and loss counters remain in the index.

The reader enforces one total byte-read limit across the index and members
(each file is charged once, including stage-file entrypoints),
regular-file and symlink restrictions, name/hash/size/count binding, matching
metadata, and complete global sequence reconstruction. It rejects missing,
altered, unsafe, or inconsistent members. Stage-file diagnosis verifies the
whole inventory then restricts events to that stage, reporting
`CoverageScope=stage`; its overall outcome still describes the run, not a
claim that other stages were observed. A directory/index diagnosis reports
`CoverageScope=run`.
`DiagnoseCompilerTrace` returns the outcome, reported failure, failed operations,
last observed action, truncation and callback-error status. The reader never
executes trace content. Older outputs without a trace retain their existing
failure artifacts; their unrecorded internal actions cannot be reconstructed.
Hooks and diagnosis do not change routing policy, physical claims, required
validation, publication authority or acceptance verdicts.

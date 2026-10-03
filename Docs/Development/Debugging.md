# Routing debugging

Start from the typed failure and retained artifact, not from the last console
line. Record the exact command, interpreter, source state, policy, deadline,
and artifact path.

For negotiated-routing failures inspect, in order:

1. boundary escape matching and saturated cuts;
2. coarse overflow progression and history costs;
3. rerouted signals and retained/pruned branches;
4. active tiles, boundary touches, expansions, and cache deltas;
5. wire, support, required-air, electrical, and repeater claims;
6. placement feedback rounds and packed-area growth; and
7. final structural validation and Fabric-server status.

`BoundaryEscapeInfeasible` and congestion cuts should return contributing
clusters to placement. A stable nonzero overflow should expand or repair the
affected region. `RuntimeBudgetExceeded` is a bounded outcome, not evidence
that a larger timeout fixes topology.

Run focused unit tests before repeating a scale compile. Preserve the first
failed circuit's artifact and retain every planned acceptance outcome without fail-fast skipping. The
[failure catalog](../Routing/Active/FailureCatalog.md) maps each typed failure to its
required evidence and next owning stage.

## Failure presentation

New handled failures publish a source-bound `RoutingFailureReport.md` with a
versioned receipt. The Markdown report groups the typed failure, an ASCII
evidence-flow graph, input/configuration identities, captured conflict cells,
deadline/work details, downstream state and bounded diagnostics. Missing or
partial evidence stays explicit. It does not recompute routes or establish an
upstream root cause. Historical sealed HTML/v1 reports remain immutable
readback evidence; new runs generate no HTML.

Terminal summaries begin with `RESULT`, `TIME` and `PERF`. `STAGES` shows a
compact numbered chain such as `1 -> 2 -> 3X` and a stage legend. `X` marks the
reported failed stage. Routing timings aggregate repeated stages, so the chain
is labeled as first-observed summaries rather than a complete execution trace.
Absent stage history or performance observations remain unavailable. Full
stage timings, raw diagnostics and traceback stay in `Summary.txt`, `RawDump.txt`
and the telemetry files; the final terminal summary avoids a second diagnostic
dump. Compiler search, deadlines, validation and exit codes are unaffected.

## Normal compilation versus hooks

| Behavior | Normal compilation | Compilation with hooks |
|---|---|---|
| Compiler strategy, search policy and absolute deadlines | Selected policy | Same selected policy; hooks do not grant extra work or time |
| Correctness and physical validation | Required checks | Same required checks |
| Terminal result and typed failure | Standard RESULT/TIME/PERF/STAGES | Same result rules, plus a HOOKS publication locator |
| CPU telemetry | Available through the existing telemetry option | Independent of hooks; the same telemetry option still applies |
| Compiler action records | No hook bundle | Bounded events for all or selected instrumented stages |
| Saved hook output | None | One JSON per observed exact hook/stage and an Index.json for the run |
| Live event observers | None | Optional API callbacks; guided compilation saves files without streaming every event |

A hook file contains that stage's begin, finish, failed or transition events,
including repeated invocations and original global sequence numbers. The index
records the run outcome, selectors, counters and file identities. Unreached or
filtered stages have no event file; absence does not mean success. The terminal
summary names the saved index, or explicitly reports unavailable capture.
Historical aggregate traces remain readable through the saved-trace diagnosis
menu. Recording and storage add overhead within unchanged execution policies;
time-sensitive runs may reach an existing deadline sooner. Hooks are diagnostic
observations, not a different optimization strategy or an acceptance shortcut.

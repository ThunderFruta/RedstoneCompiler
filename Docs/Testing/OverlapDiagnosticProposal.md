# Review proposal: one isolated PR9 overlap diagnostic

Status: local proposal only. Publishing the diagnostic branch requires the
requested parent review. No hosted run, PR, or issue update accompanies this
proposal. Ownership is Telemetry-And-Acceptance; PR9 remains unchanged.

## Why a new route is needed

At PR9 source `097ba27a3b261b346ca53cc5cb65e6e149dd13fd`, the deterministic
workflow's dispatch/call events have no inputs. `RunChecks.py` accepts tiers,
expected commit and output location; its deterministic tier always executes
all of `Tests`. Its clean environment also removes ambient pytest overrides.
There is no existing scoped diagnostic entrypoint.

Hosted run 36936623514 failed after the 1200-second full-suite watchdog. The
one printed failure maps by collection ordinal to the overlap test, but the
retained output contains no assertion traceback. This proposal gathers that
missing evidence; it does not assume the cause of the failure.

## Proposed activation, after review

Review the four-file diff on local branch
`telemetry/overlap-diagnostic-097ba27-cloud-20261001`, based on Router
`9db4acfa1d00c07653dfe463d90c50e9e27d3b47`.
Publish its reviewed final commit once to that exact branch name. The new
workflow listens only to that branch's push event. Do not open a PR, dispatch
the deterministic workflow, retry the diagnostic, or push subsequent edits
as part of this one-run authorization. The existing deterministic push filters
exclude this branch; its PR trigger would run if a PR were opened.

The exact publication command, not executed during proposal preparation, is:

```bash
git push origin HEAD:refs/heads/telemetry/overlap-diagnostic-097ba27-cloud-20261001
```

A newly introduced `workflow_dispatch` entrypoint is not relied upon: that
would require default-branch registration. No permanent CI file is edited.
The workflow uses the existing ubuntu-24.04 runner, pinned action commits,
Python 3.12.14, Rust 1.96.0, hash-locked Python dependencies, and contents:read.
It requests no secrets, write permissions, shared caches, or privileged runner.

## Fixed invocation and evidence contract

The workflow checks out its diagnostic driver and a separate clean subject at
`097ba27a3b261b346ca53cc5cb65e6e149dd13fd`, tree
`d559bc6a6895cd7963310c35398e3cfd31d5f305`. The subject owns its virtualenv and
fresh native build. The driver runs:

```bash
subject/.venv/bin/python Tools/CI/DiagnoseOverlap.py
```

The driver admits no configurable source, command, node, or deadline. It runs
exactly once:

```text
Tests/Integration/test_deferred_local_routing_access_handoff.py::test_mandatory_selected_access_overlap_remains_typed_incomplete
```

Pytest uses verbose output, fixture setup display, long tracebacks, all phase
durations, and JUnit. Timed faulthandler dumps are disabled explicitly; the
production 10-second routing budget remains the unmodified test source.
The entire pytest process group has a 180-second external watchdog. On
completion, timeout, SIGTERM or SIGINT, the runner kills lingering group
members and waits for the direct child. Processes deliberately escaping the
process group are outside this mechanism; the runner does not claim containment
of hostile subprocesses.

Only fresh `Output/CI/overlap-public/` staging is uploaded, with seven-day retention.
Staging admits the explicit flat `EvidenceFiles` allowlist, rejects symlinks,
nonregular entries, unexpected files and directories, and indexes exactly the
copied bytes. If staging fails, upload does not run. Source-oracle output is
kept separately in `overlap-source-oracle-private/` and is not uploaded.
Credentials/environment dumps, caches and source trees are not allowed paths.
Necessary public tracebacks and build logs remain; there is no log-content
redaction. Expected evidence includes:

- Setup stdout/stderr, tool/dependency versions, setup exit and elapsed time.
- Native-build stdout/stderr, command, exit and elapsed time.
- Exact source/native/template provenance before and after pytest.
- Pytest stdout/stderr, command, exit, elapsed time and timeout flag.
- Immediately flushed/fsynced JSONL phase receipts, with node, setup/call/
  teardown duration, outcome, traceback and captured output. A later teardown
  hang cannot erase an already-reported assertion failure.
- JUnit when pytest completes; it is not promised after forced termination.
- Run receipt (or incomplete bootstrap receipt), driver commit/run/attempt,
  and a SHA-256 index of retained files.

A setup failure is a setup failure, not a test result. A watchdog timeout is
not a production deadline diagnosis. A call that never returns cannot yield
an assertion traceback, but completed phase records and verbose output survive.
Always-upload steps cannot guarantee delivery after runner loss or forced job
cancellation. No physical acceptance or performance-comparison claim follows
from this single diagnostic.

## Local verification and separate future scope

Eleven focused artifact-boundary cases passed, including rejected symlinks,
FIFOs, unexpected credential/environment files and cache/source directories,
and exact byte/index preservation. Six synthetic lifecycle checks passed: successful output; failed output/exit receipt;
timeout kills descendant heartbeat; normal parent exit cleans lingering
child; supervisor SIGTERM cleans the group; failed pytest call survives a
hanging teardown in the phase JSONL. These exercise real subprocesses and
pytest using the already existing cloud Python environment; no dependencies
were installed. The fixed subject overlap itself was not rerun. These checks
do not establish hosted readiness or diagnose PR9's hosted failure.

The prospective test plan, executable checks and results are retained under
`/workspace/overlap-diagnostic-task/`. This one-off branch is not proposed for
integration into Router or PR9.

Separately, permanent deterministic CI could add an observer-only pytest
plugin that immediately writes bounded per-phase failure receipts to its
existing evidence directory, then seals/uploads them through its current
path. That would address evidence loss on a later suite timeout. It needs its
own Telemetry change, independent tests for failure/teardown/timeout and output
bounds, and review; it must not change selection, outcomes, deadlines or routing.
This proposal implements only the diagnostic observer, not that permanent fix.

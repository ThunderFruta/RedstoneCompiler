# Commit-addressed continuous integration

## Scope and independent checks

Issue [#4](https://github.com/ThunderFruta/RedstoneCompiler/issues/4) is split into
three independent job results. A deterministic pass never establishes physical
acceptance or promotion readiness.

- **Python and Rust (no physical acceptance)**: clean exact-commit checkout,
  Rust format and locked release tests, a fresh release extension build,
  compile-all, structural/schema tests, complete test collection and deterministic
  pytest. Ambient scale/routing controls and Python import overrides are removed.
- **Java harness unit tests (no live Fabric)**: checksum-verified Gradle wrapper,
  harness `test build`, dependency report, Java and Gradle identity.
- **Seven-case physical acceptance**: separate manual workflow, unavailable by
  default. It uses the existing expanded, non-fail-fast acceptance harness, fresh
  output, canonical tracked templates, current-commit native and Java harness,
  authenticated Fabric startup and best-effort owned-server teardown. Missing
  prerequisites are failures, never successful physical checks.

The deterministic workflow runs on pull requests, pushes to `main` or
`Router-Refactor(R10-N5)`, and explicit manual dispatch. Feature-branch pushes
alone do not launch it. PR execution uses GitHub's merge commit; `Run.json` and
artifact names record the actual tested SHA. There is no `pull_request_target`,
write permission, supplied secret, shared dependency cache, or privileged runner
in the fast lane. Checkout does not persist its credential. Action references
are full reviewed commit SHAs.

## Dependency and native identity

The Linux x86_64 profile fixes Python 3.12.14, Rust 1.96.0 with rustfmt, Temurin
25.0.4.1+1, Gradle 9.5.1 and Fabric Loom 1.17.21. The Gradle wrapper verifies its
published distribution SHA-256. Minecraft 26.2, Fabric Loader 0.19.3 and JUnit
5.12.2 remain pinned in the project. Python build/test tooling and every
transitive Python dependency are exact-version, wheel-hash locked in
`Tools/CI/requirements.txt`; the project is built without fetching an unpinned
build-isolation environment. Cargo uses the tracked `Cargo.lock` and `--locked`,
including the pinned MCHPRS Git revision. No lockfile is regenerated in CI.

This is a version-pinned profile, not a hermetic toolchain: hosted OS images and
JDK/Python distribution availability can change, and Gradle's transitive Maven
artifacts do not yet have a checked-in dependency-verification checksum set.
Gradle's resolved dependency report is retained; its first successful clean
online run must be reviewed before claiming fully reproducible harness builds.
Do not manufacture verification metadata from a failed resolution or silently
restore the moving Loom snapshot. Future pin changes require explicit review
and a complete clean build.

The native build uses the repository's `Cache/Rust/target` layout explicitly.
The acceptance harness's existing source/native/template provenance functions
are reused. The actually imported checkout-local `.so` must have the same
SHA-256 as the just-built release library. Source must remain clean at the
expected commit, and before/after provenance must agree. Wrong native bytes,
wrong source or external templates fail the job before parity results can be
accepted. The receipt records dependency-file hashes, installed Python versions,
actual Rust/Java/Gradle versions, commands, exits, timings and selected tier.

CI sets `RC_TEMPLATE_ROOT` to this checkout's tracked `Assets/Templates`.
An explicit root must contain Input, Nand and Output litematics; invalid roots
fail instead of falling back. Without the override, existing local template
preference remains unchanged. The configured-child provenance probe imports
the current `Assets.Templates` namespace.

## Physical infrastructure admission: not provisioned by these workflows

Do not set `RC_PHYSICAL_RUNNER_READY` until an administrator has authorized and
verified every item below. No paid runner, credentials, EULA acceptance,
network/security setting, or GitHub runner grant is created by this change.
Standard hosted runners on this public repository are free under
[GitHub's billing policy](https://docs.github.com/en/billing/concepts/product-billing/github-actions);
no larger/paid hosted runner is selected. Artifact storage is quota-governed and
retained for only seven days. Physical infrastructure capacity/cost remains an
explicit administrator decision.

1. Review the exact Router integration commit and the workflow. Restrict the
   `redstone-physical` runner group **outside workflow YAML** to this repository's
   exact `physical-acceptance.yml` workflow on the protected
   `Router-Refactor(R10-N5)` ref. A YAML branch guard alone is not a security
   boundary: an untrusted branch could edit it. If the GitHub plan cannot enforce
   a workflow/ref restriction, do not attach this runner and leave the lane off.
2. Create the `physical-acceptance` environment with mandatory independent
   approval, no self-approval, and a deployment-branch restriction to that exact
   protected ref. Review source changes before allowing each run. Never grant
   fork/PR workflows access to the physical runner, its network or runtime.
3. Supply a single-job ephemeral Linux x86_64 VM, labels `self-hosted`, `linux`,
   `x64`, `redstone-fabric-ephemeral`, unprivileged user and no unrelated secrets,
   cloud identity, service account, private mounts, Docker socket, personal world
   or shared Fabric server. Destroy the VM and its disks after success, failure,
   timeout or cancellation. Job cleanup is only best effort; infrastructure
   destruction must not depend on repository code. No persistent runner is safe.
4. Preinstall rustup and normal build tools. Supply a fresh, stopped Fabric 26.2 /
   Loader 0.19.3 runtime at `/opt/redstone-fabric/runtime`, with verified launcher
   and server dependency JARs and an existing harness JAR. The runtime owner must
   already have accepted the Minecraft EULA (`eula=true`). There must be no
   legacy `/opt/redstone-fabric/build/libs/validation-server-harness-1.0.0.jar`.
   Use the supported manager's loopback-only server configuration and a dedicated
   disposable void world. Do not expose a system/user service shared with others.
   The job fails rather than accepting an EULA or downloading a server runtime.
5. Approve the infrastructure/cost boundary and set repository variable
   `RC_PHYSICAL_RUNNER_READY=ephemeral-v1`. Dispatch only the reviewed Router ref.
   Missing admission fails on a small hosted job before a physical runner is
   scheduled. A missing/offline approved runner remains queued; it is not a pass.

After admission, the physical workflow requires both clean hosted fast checks
for the exact commit before scheduling the protected physical job.
The physical job rebuilds both native and Java code, rejects an already-running
server, installs the just-built harness in the dedicated runtime, starts it
through the existing manager and waits for authenticated readiness. It then
runs all seven existing scheduled cases once. A failed case does not stop later
cases. Failure reports and any typed artifacts are retained. Missing Fabric
infrastructure stops before execution with `PhysicalAcceptance=not-run`.
There is no performance baseline in this workflow. Any future baseline comparison
must use the existing environment-compatibility and measurement-validity gates;
shared-host timing is not a stable performance reference.

## Evidence, privacy, retention and failure handling

Artifacts are named with tier, tested commit, workflow run ID and attempt, and
expire after seven days. Access follows the repository's existing GitHub Actions
artifact authorization; this change does not broaden sharing. Review repository
visibility and collaborator access before enabling physical uploads. Evidence is
restricted to disposable CI inputs, not private workstation material.

The upload path is only the fresh `Output/CI/<tier>` evidence directory. It holds
command receipts/logs, JUnit output, source/native/template provenance and an
`EvidenceIndex.json` hashing the uploaded subset. The physical collector allows
only `Summary.txt`, `RawDump.txt`, acceptance/archive/result manifests,
`SHA256SUMS`, `stdout.log`, `stderr.log`, and `.PhysicalDesign.json`,
`.RoutingFailure.json`, `.PhysicalFixture.json` from that invocation's fresh
acceptance directory. It refuses hidden paths, runtime/world/config directories,
symlinks and a subset exceeding 100 MiB. Unknown files are not uploaded.

The harness's `ArchiveManifest.json` and `SHA256SUMS` describe the **full original
archive**, which may include artifacts deliberately omitted from upload.
`EvidenceIndex.json` independently seals the published subset; do not claim the
subset contains every original archive file. No environment dump, tokens,
private configuration, server directory, world, build cache, downloaded JAR,
native binary or unrelated file belongs in an artifact. Ambient `RC_*`/`RCS_*`
variables are removed before the existing harness can record them, except the
controlled canonical template and dedicated runtime roots.

Ordinary command failure preserves its exit/log and makes the tier fail; a
native build/provenance failure prevents stale-native Python testing. Command logs stream directly to evidence files; setup failures retain a distinct
incomplete bootstrap receipt when an always-run step can execute. Upload
steps run after ordinary failures. Cancellation or runner loss may interrupt
publication and must be labeled incomplete; absence of an artifact is not a
successful check. Prior/historical results are never copied into new evidence.

## Local verification

From a clean committed checkout with the pinned tools and an isolated `.venv`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r Tools/CI/requirements.txt
source .venv/bin/activate
python Tools/CI/RunChecks.py --tier deterministic --expected-commit "$(git rev-parse HEAD)" --output Output/CI/local-deterministic
python Tools/CI/RunChecks.py --tier harness --expected-commit "$(git rev-parse HEAD)" --output Output/CI/local-harness
python -m pytest -q Tests/CI
```

Choose a new output name for every attempt. Never run the physical tier on a
personal/shared runtime. Its fixed path and admission requirements are deliberate.
Validate workflows with actionlint (1.7.7 was used initially) and `bash -n` for
all embedded run blocks. Unit coverage includes an actual injected pytest
failure, native-hash/source/template corruption, missing Fabric/EULA checks,
all-seven plan preservation and evidence symlink/privacy controls. Passing those
checks validates CI plumbing, not a new hosted CI pass or live Fabric result.

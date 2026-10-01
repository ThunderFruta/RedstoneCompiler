# Commit-addressed continuous integration

## Scope and independent checks

Issue [#4](https://github.com/ThunderFruta/RedstoneCompiler/issues/4) is split into
three independent job results. A deterministic pass never establishes physical
acceptance or promotion readiness.

- **Python and Rust (no physical acceptance)**: clean exact-commit checkout,
  Rust format and locked release tests, a fresh release extension build,
  compile-all, structural/schema tests, complete test collection and deterministic
  pytest. The independent source-to-NAND Yosys oracle is installed from the
  hash-locked CI requirements and required, so missing semantic proofs fail
  instead of silently skipping. Ambient scale/routing controls and Python import
  overrides are removed.
- **Java harness unit tests (no live Fabric)**: checksum-verified Gradle wrapper,
  harness `test build`, dependency report, Java and Gradle identity.
- **Physical admission report (no simulation)**: a hosted registration/manual
  workflow that records physical acceptance as blocked and not run. The existing
  seven-case engine remains available only for separately approved disposable
  infrastructure; no hosted physical execution or acceptance is established.

The deterministic workflow runs on pull requests, pushes to `main` or
`Router-Refactor(R10-N5)`, and explicit manual dispatch. Feature-branch pushes
alone do not launch it. PR execution uses GitHub's merge commit; `Run.json` and
artifact names record the actual tested SHA. There is no `pull_request_target`,
write permission, supplied secret, shared dependency cache, or privileged runner
in the fast lane. Checkout does not persist its credential. Action references
are full verified release commit SHAs: checkout 7.0.1, setup-python 7.0.0,
setup-java 6.0.1 and upload-artifact 7.0.1. Their Node 24 runtime requires
Actions Runner 2.327.1 or newer.

## Dependency and native identity

The Linux x86_64 profile fixes Python 3.12.14, Rust 1.96.0 with rustfmt, Temurin
25.0.4.1+1, Gradle 9.5.1 and Fabric Loom 1.17.21. The Gradle wrapper verifies its
published distribution SHA-256. Minecraft 26.2, Fabric Loader 0.19.3 and JUnit
5.12.2 remain pinned in the project. Python build/test tooling and every
transitive Python dependency are exact-version, wheel-hash locked in
`Tools/CI/requirements.txt`; the project is built without fetching an unpinned
build-isolation environment. Cargo uses the tracked `Cargo.lock` and `--locked`,
including the pinned MCHPRS Git revision. No lockfile is regenerated in CI.

The hosted Java selector is `25.0.4+101.0.LTS`, exactly the `version_data.semver`
published by the [Adoptium Linux x64 JDK GA metadata](https://api.adoptium.net/v3/assets/feature_releases/25/ga?architecture=x64&image_type=jdk&jvm_impl=hotspot&os=linux&vendor=eclipse)
for [Temurin `jdk-25.0.4.1+1`](https://github.com/adoptium/temurin25-binaries/releases/tag/jdk-25.0.4.1%2B1).
It selects that release, rather than a guessed conversion of its Java version.
The pinned [setup-java action](https://github.com/actions/setup-java/blob/de7274f081f381c8f8158605e0321c36c376e2e6/src/util.ts)
compares explicit SemVer build metadata exactly. Its raw four-field-plus-build
input `25.0.4.1+1` is invalid and caused the initial hosted setup failure.
`force-download: true` requires a fresh vendor download, whose authoritative
archive checksum the pinned action verifies. The inspected Linux x64 archive
SHA-256 is `dbb698396d478e7fa2b1e50f4103324b2a99b90569ee27c33f2261f9215cf41e`.
Before Gradle, `Tools/CI/JavaToolchain.py` requires the exact action resolution
and installed release-file identity: Eclipse Adoptium, Java `25.0.4.1`, runtime
`25.0.4.1+1-LTS`, and architecture `x86_64`. A wrong or missing identity fails
before harness tests can run. Local Ubuntu OpenJDK evidence does not establish
Temurin distribution parity; hosted success must be observed separately.

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

## Host-independent deterministic fixtures

The native worker pool keeps its existing policy: positive
`RC_ROUTING_THREADS` requests are capped by the operating system's available
parallelism; the default is at most eight workers. The Rust capacity contract
runs each configuration in a fresh process, compares against an independent OS
capacity observation, and observes distinct threads executing the pool's work.
Test-only per-item witnesses also observe the actual claim, exterior-connector,
and fabric-subtree computations, independently of their reported active-worker
telemetry; a serial batch advertising parallel capacity must fail.
Python batch checks still require exact reference results and full use of the
permitted shards. Their expected worker count follows that verified capacity,
not an assumption that every hosted runner has eight CPUs. The captured CLA4
fixture retains its 30-second gate and independently records real native batch
input sizes when checking active-worker and aggregate-work receipts.

Running-work deadline fixtures must establish application entry before testing
late completion, forced release, or ownership at a cleanup cutoff. A process
readiness signal alone does not establish that entry. The narrowly synchronized
fixtures use a shared test clock and explicit operation-entry/release barriers
around real spawned processes. Their original absolute work and cleanup
cutoffs remain fixed; they exercise the real cancellation, bounded transport,
result validation, process termination, reap, and resource release paths.
Independent wall-time watchdogs fail and recover a broken fixture; they never
extend the granted cutoffs or turn missing entry into a skipped/passing test.
These are deterministic temporal-contract tests, not startup-performance
measurements. Separate real-clock startup and deadline tests remain required.
No production Runtime deadline, cleanup, force, or publication policy changes
are implied by this fixture isolation.

## Physical admission and first workflow registration

This repository belongs to a personal GitHub account. Its current branches are
unprotected, and organization/enterprise runner groups are unavailable here.
The earlier organization-only runner-group scaffold did not provide a usable
physical lane. Repository variables, runner labels and YAML ref guards cannot
supply the missing external isolation or restrict a public repository's runner
against an edited workflow. No physical runner is configured or scheduled by
`physical-acceptance.yml`; setting a readiness variable cannot enable execution.

The workflow uses only a small `ubuntu-24.04` hosted reporting job. An exact
`Router-Refactor(R10-N5)` push requests a registration/reporting run that, if
executed, records
`Status=blocked`, `PhysicalAcceptance=not-run`, `Accepted=false` and
`HostedReportingOnly=true`. A successful registration job is only a reporting
result, never a physical acceptance pass. An accepted manual dispatch records the same
blocked receipt and exits nonzero. There is no checkout, reusable fast-lane call,
self-hosted job or simulation command in this scaffold. The deterministic
Python/Rust and Java workflows remain independently runnable.

GitHub documents both a default-branch prerequisite for `workflow_dispatch` and
CLI/API dispatch against another branch or tag after a workflow has run at least
once. This workflow is currently absent from default `main`. A Router push only
requests its hosted registration/reporting run; it does not establish that later
manual dispatch is available for this repository. After an approved exact Router
push, verify the actual run SHA and workflow registry. Only an independently
authorized attempt can establish whether dispatch by filename against Router is
accepted:

```sh
gh workflow run physical-acceptance.yml \
  --repo ThunderFruta/RedstoneCompiler --ref 'Router-Refactor(R10-N5)'
```

This command, if accepted, requests only the blocked admission report; it does
not request or grant physical infrastructure. Registry presence or a successful
Router push alone is not proof of dispatch eligibility. If GitHub rejects the
request, retain the concrete registration/dispatch error and keep issue #4 open.
Do not promote or change default `main` to work around it. Registering or
dispatching the workflow is a separate approved publication action, not a side
effect of local testing. See GitHub's
[workflow dispatch documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)
and [runner group scope](https://docs.github.com/en/actions/concepts/runners/runner-groups).

Issue #4 remains incomplete for an executed, accepted physical lane. A future
implementation needs an explicitly approved execution boundary compatible with
this account, external control of exact reviewed code admission and teardown,
and independent review before enabling any job. No runner, credential,
protection rule, environment grant, paid capacity, EULA acceptance or network
policy is created by this change. If that boundary cannot be enforced, leave
hosted physical acceptance unavailable. Do not attach this workstation, its
personal world or its shared Fabric runtime as a repository runner.

The existing `Tools/CI/RunChecks.py --tier physical` remains a separately gated
engine for a trusted disposable environment, not an enabled workflow. It requires
its fixed `/opt/redstone-fabric/runtime` root, a previously accepted EULA, a
trusted complete runtime-JAR inventory, a stopped dedicated runtime, a fresh
current-commit harness/native build and exact clean source. It does not provision
that environment. Its tests exercise synthetic controls, not live acceptance.

Actual physical acceptance still requires all seven expanded cases with the
existing non-fail-fast scheduler, canonical tracked templates, capacity-one
claims, exact MCHPRS truth tables, required live Fabric canaries and observed
settling, fresh retained artifacts, startup/native/harness/source provenance and
owned stopped cleanup. Missing infrastructure, typed failures, partial runs and
failed cases remain non-passing. Performance claims need the existing compatible
baseline and measurement-validity gates; shared-host timing is not a stable
performance reference. Local controlled acceptance on a disposable runtime is
separate from hosted CI and cannot establish that the hosted physical lane ran.

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
controlled canonical template/dedicated runtime roots and required independent
source-oracle executable settings.

Ordinary command failure preserves its exit/log and makes the tier fail; a
native build/provenance failure prevents stale-native Python testing. Command logs stream directly to evidence files; setup failures retain a distinct
incomplete bootstrap receipt when an always-run step can execute. The source
oracle writes its replayable HDL/IR/miter/proof scripts, hashes, counterexamples
and Yosys logs beneath the fresh deterministic evidence directory, so its
failures survive runner disposal. Every tier rechecks exact clean source at exit. Upload
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

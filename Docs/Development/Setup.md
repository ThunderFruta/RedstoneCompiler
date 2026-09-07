# Development setup

Use Python 3.12 or a compatible recent Python and a Rust toolchain for the
native router.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m compileall -q App Compilation Formats PhysicalDesign Validation Assets Tools RedstoneCompiler Tests
cargo test --manifest-path Kernels/Routing/Cargo.toml --release
./Validation/Fabric/ServerHarness/gradlew -p Validation/Fabric/ServerHarness test build
```

Run commands from the repository root. Generated user artifacts belong under
`Output/`; disposable frontend and test state belongs under `Cache/`. Do not
commit virtual environments, Rust build output, or transient `/tmp` evidence.

Before a scale compile, run the focused tests and confirm no other heavy
RedstoneCompiler process is active. See [Running tests](../Testing/RunningTests.md).

The Fabric harness uses Java 25 and the checked-in Gradle 9.5.1 wrapper.
A global Gradle installation is unnecessary. The first wrapper run downloads
the pinned distribution, verifies its SHA-256, and caches it under the Gradle
user home for reuse across worktrees. Network access is required until the
Gradle distribution and harness dependencies are cached. Check Java with
`java -version`; the wrapper does not install a JDK.

The managed worktree setup runs harness tests and builds the JAR through this
wrapper after the Python setup tests. Harness build output remains under
`Cache/Gradle/ServerHarness/Build/`. Building does not deploy the harness or
restart a Fabric server; deployment and authenticated readiness are separate
checks described in [Fabric validation](../Architecture/FabricServerValidation.md).

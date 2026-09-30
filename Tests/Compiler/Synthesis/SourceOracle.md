# Independent SystemVerilog-to-NAND oracle

This test-only gate feeds the original SystemVerilog source to an independent,
version-pinned Yosys frontend and SAT solver. The other side of the miter comes
from the real `ParseSvToNetlist -> OptimizeLogic -> ToNandOnly` pipeline. A small
exporter renders each resulting NAND and input/output boundary directly; it does
not evaluate source expressions or call the compiler's logic evaluator.

## Install and run

From the repository root, using its configured Python test environment:

```bash
python3 -m venv /tmp/rc-source-oracle
/tmp/rc-source-oracle/bin/pip install \
  -r Tests/Compiler/Synthesis/oracle-requirements.txt
RC_YOSYS=/tmp/rc-source-oracle/bin/yowasp-yosys \
RC_REQUIRE_SOURCE_ORACLE=1 \
python -m pytest -q Tests/Compiler/Synthesis/test_source_nand_oracle.py
```

The independent executable is pinned to `yowasp-yosys==0.69.0.0.post1233`,
Yosys 0.69, Git identity `9f75ca1f9`. All Python runtime dependencies are also
pinned in the requirements file. This is not a production compiler dependency.

With ordinary pytest, source SAT tests are explicitly skipped if the executable
is absent. Such a run does **not** establish source equivalence. The required
gate above changes missing-tool skips into failures. An installed but failing
or unexpected-version tool fails the gate. `RC_YOSYS` can name another executable
path, but the pinned version identity is still checked.

## Scope and outcomes

The corpus includes 32 deterministic generated acyclic sources, directed
precedence/nesting/alias/reconvergence/repeated-operand/multi-output/unused-signal
cases, explicit top-module selection, temporary-name collisions, externally
observed intermediate outputs, supported scalar qualifiers, split non-ANSI
port declarations, and all seven shipped examples including RCA8 and CLA4.
Expected input and output interfaces are checked at the parsed, optimized, and
NAND stages before the miter is built. SAT proves all two-state input
assignments, rather than a sampled truth table.

The runner reports exactly one of:

- `proved-equivalent`: zero exit status and an unambiguous completed SAT proof
- `counterexample`: completed SAT failure with a retained input assignment and
  unequal source/NAND outputs
- `unsupported`: an unpinned tool identity or unsupported NAND-export form
- `timeout`: SAT timeout or killed wall-time-exhausted subprocess
- `tool-failure`: launch error or nonzero process exit
- `unknown`: absent, conflicting, or malformed proof/model evidence

Only `proved-equivalent` passes a positive case. The intentional negative
control inverts a lowered output and must return a genuine counterexample.
Unit controls additionally exercise nonzero exits, missing executables, actual
subprocess timeouts, missing/malformed models, and conflicting evidence.

The SAT solver is bounded to 10 seconds; each proof subprocess has a 30-second
wall limit and its process group is killed on expiry. The version probe has a
120-second bound to allow a cold WebAssembly runtime compilation.

## Retained evidence

Each invocation creates a fresh UUID directory below `Output/SourceOracle/`.
Override the parent directory with `RC_SOURCE_ORACLE_EVIDENCE`. Artifacts are
ignored runtime output, not committed fixtures. Each proof directory retains:

- Original `source.sv`, parsed/optimized/NAND IR JSON, and their SHA-256 hashes
- Independently emitted `lowered.sv`, `miter.sv`, and replayable `proof.ys`
- Yosys stdout and stderr, plus the run's exact version-probe logs
- `result.json`: seed, top, exact tool version/command, process outcome/timing,
  timeout limit, artifact hashes, and proof classification
- `counterexample.json` and the decoded input/source-output/NAND-output values
  in `result.json` when a distinguishing SAT model exists

Replay one retained proof from its directory with the same pinned executable:

```bash
/tmp/rc-source-oracle/bin/yowasp-yosys -T -s proof.ys
```

A proof applies to that source and those IR hashes. It does not certify physical
routing, Minecraft behavior, unsupported HDL constructs, or four-state logic.

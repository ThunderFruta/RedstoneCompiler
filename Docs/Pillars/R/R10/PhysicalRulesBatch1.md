# Physical-Rules Batch1 fixture conformance

`PhysicalDesign.Redstone.FixtureValidation.CheckPhysicalFixture` provides one
expectation-free public query over the existing shared routing technology,
physical graphs, resource conflicts, repeater directions, and route-power
propagation. Results contain `Legal`, `Illegal`, or `Unknown`, exact normalized
fixture identity, source-bound model/technology identities, checked positions,
assumptions, and concrete reasons. Existing selected-access interfaces and
freshness checks are unchanged.

The checker reports dust edges in both directions, support requirements and
missing supports, stair headroom and blocked headroom, repeater input/output
positions, and within-fixture resource conflicts. These are static routing-model
facts. Blocked stair headroom removes an edge rather than automatically making
the fixture illegal. General wire-arm blockstate predictions and cross-net
ownership conflicts are outside this fixture schema.

Electrical output predictions are supported for a conditional route-transfer
query: every control declares a dust root in `routeInputs`, every output probes
a route wire/repeater, and the arrangement stays within the shared model's
supported flat repeater domain. The checker calls `PropagateRoutePower`, assumes
excitation enters only at those roots with power 0/15, and returns the required
root powers. The independent simulator must actually observe those powers
throughout the final stable interval. Missing observations or different powers
prevent success even if outputs happen to match. This does not add a general
lever, torch, device, or timing rule. Timing predictions explicitly remain
unavailable; settlement observations are a separate empirical check.

## Declarative cases

Each fixture directory has `Fixture.json`, one source file, and
`Expectations.json`. A source is a JSON template with the established
`Blocks: [{Position, State}]` physical representation, or a single-region
`.litematic` read by the existing codec. Litematic coordinates use that codec's
normalized region coordinates. Multiple regions are rejected. Source bytes are
SHA-256 bound; controls/probes and the normalized block map are bound separately.
The fixture file contains no expected behavior:

```json
{
  "schemaVersion": "physical-rules-fixture-v1",
  "source": {"kind": "template", "path": "Template.json", "sha256": "<source SHA-256>"},
  "inputs": [{"name": "A", "position": [0, 1, 0]}],
  "outputs": [{"name": "Y", "position": [2, 1, 0]}],
  "routeInputs": {"A": [1, 1, 0]}
}
```

`Expectations.json` uses schema `physical-rules-expectations-v1`, kind
`combinational`, and a nonempty `cases` list. Each case has a unique `id`, complete
Boolean `initialInputs` and `appliedInputs`, complete Boolean `expectedOutputs`,
integer `settlementTicks >= 0`, and integer `stabilityWindowTicks >= 1`. Integer
0/1 are not Boolean values. Optional `checkerStatus` and `physical` assertions
are independent of executor inputs. An omitted physical field differs from an
explicit empty list. Unknown keys, duplicate JSON keys, malformed positions,
incomplete vectors, and stateful sequences are configuration failures.

Add ordinary cases through fixture data and expectations only. Run discovery:

```bash
.venv/bin/python -m Validation.FixtureConformance.Runner \
  --fixtures Tests/Fixtures/PhysicalRulesBatch1 \
  --output Output/PhysicalRulesBatch1/<fresh-run-id>
```

`Loaders`, `Expectations`, `Observation`, `Comparison`, and `Runner` have separate
responsibilities. Neither checker nor native observer receives expected output
values or a logic artifact. Comparison binds the returned fixture/model identity
to independently supplied expected identities before assessing conformance.

## Native observation and timing

`ObserveMchprsFixture(RequestJson)` accepts `Fixture`, complete `InitialInputs`
and `AppliedInputs`, `HorizonTicks`, and `InitializationTicks`. It creates a new
world and compiler for each call, initializes the declared baseline, verifies
actual lever states, then applies the complete vector and verifies it again.
The pinned backend is `mchprs-redpiler-fe217210`; compiler options are explicitly
`optimize: false` and `io_only: false`, preserving observable dust nodes. Supported
probes are levers, lamps, dust, and repeaters. Unsupported blocks/properties or
probe kinds have explicit non-success results. The existing aggregate
`ValidateMchprsFixture` and dependency pins/build settings are unchanged.

Tick 0 is after complete input application and flush, before the first tick.
Every subsequent sample follows one `tick` and one `flush`. For deadline L and
window W, the horizon is H = L + W and the required sample count is H + 1.
W counts elapsed intervals, requiring W + 1 equal samples. Actual settlement is
the start of the final unchanged correct suffix through H; it must be at most L.
An earlier transient match cannot pass. Classification distinguishes passed,
late, transient, wrong, oscillating (changing wrong vectors), unobserved,
unsupported, backend error, identity mismatch, and root-assumption mismatch.
Finite observations do not prove indefinite stability.

Every run retains copied source and expectation bytes/hashes, normalized fixture,
per-case predictions/raw traces/results, model and native identities, aggregate
results, `Summary.txt`, and `RawDump.txt`. Discovery attempts every fixture and
all valid cases; malformed fixture/expectation files are recorded as configuration
failures without aborting other fixtures.

## Verification boundary

Batch1's controlled cases cover template/litematic identity transfer and opposite
repeater direction with mixed two-control baselines. Deliberate challenges cover
wrong expected values, switched identities, integer readbacks, transient and late
traces, missing observations, reordered fresh cases, unsupported probes, and a
powered output whose declared root has the wrong actual strength.

General torch/device truth, unrestricted repeater networks, model timing bounds,
stateful sequences, Fabric, scale, and full-router acceptance remain unproved.
This uncommitted candidate needs independent review and exact scope approval;
its artifacts are not an integrated dependency checkpoint.

# R10 notes

Working notes for [R10](R10.md). This file is non-normative; the requirement
file controls when the two disagree.

## Decisions

- None recorded.

## Open questions

- None recorded.

## Working notes

### 2026-09-06 first-wave access conformance and compatibility record

- **Source and dependency identities.** The bounded change started from
  `Physical-Rules` at `ffd8c76430eccb7f4ae1e06b4a777a4ed6ac3b9f`.  The
  Physical access checkpoint is `2f69160a85f1ac800243698aebec4f172164b9f8`;
  reviewed Joint references are `eb3ef79e2b53efd47bbd90d138fdeba2eb405e98`
  and `ea77a28afde1168d4243028f0a00a4ebb869549e`, each with that Physical
  checkpoint as merge base.  No commit, dependency integration, merge, or
  live-runtime action occurred.
- **Bounded compatibility slice.** `BuildNetRoutingProfiles` now has its
  default-false `RequireExplicitAccessWitness` mode: explicit callers must
  supply a witness, requested access length must equal witness access length,
  and explicit source/target paths stay whole; ordinary callers retain legacy
  prefix behavior.  `PlacementAccessFabric` now transports and serializes the
  paired default-empty `PinAccessDomainFingerprint` and
  `PinAccessWitnessFingerprint`.  Joint reviewed exactly these two producer
  changes for consumer compatibility and requested no companion change; that
  review used committed-reference behavior and reported Physical results, not
  Joint consumption of this uncommitted worktree.
- **Independent conformance coverage.** The catalog conformance cases use a
  local rigid-transform arithmetic oracle, literal mirror-only E/W direction
  challenges, controlled technology/catalog identity mutations, and public
  omitted/truncated-witness corruptions.  They establish transformed nonempty
  wire/support/electrical claims, preserved empty supported required-air scope,
  correct scoped identity effects, and rejection of the three witness
  corruptions.  Existing coverage already owns work-cap, complete/incomplete,
  preownership/foreign conflict, support obstruction, power propagation,
  rendering, and general-domain claims; no duplicate test was added.
- **Historical pre-freshness verification.** With the worktree `.venv/bin/python`, loaded native
  `RedstoneCompiler/RustRouting.cpython-312-x86_64-linux-gnu.so` SHA-256
  `c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`, and a
  fresh `PYTHONPYCACHEPREFIX`, the structural/schema gate passed 7 tests in
  2.84s; collection reported 1437 tests; and
  `.venv/bin/python -m pytest -q Tests` settled at **1433 passed, 4 skipped,
  257 subtests passed in 52.96s**.  The historical 13 failures are all
  resolved: 10 missing-profile-keyword failures by the explicit-profile
  correction and 3 missing fabric-fingerprint constructor failures by the
  paired fields.  There were no new or unexplained failures.  Full evidence:
  `Output/PhysicalRulesFinalVerification/20260906TFinal/`; independent oracle:
  `Output/PhysicalAccessConformance/20260906TIndependentOracle`; accepted
  focused re-review:
  `Output/PhysicalAccessConformance/20260906T004716ZIndependentReview`; API
  baseline and final compatibility records:
  `Output/AccessProfileOwnership/20260906T003729Z` and
  `Output/AccessProfileOwnership/20260906T004710Z`.
- **Limits retained at that review point.** No current supported access option has nonempty
  required-air, so nonempty required-air access-transform behavior remains
  unproved.  The E/W helper challenge is not a claim of production E/W access.
  For valid straight access, whole tuple and equal-prefix behavior cannot be
  distinguished because selection path and first leg have equal length.  Stale
  catalog/consumer identity handling and the full real five-stage handoff remained
  separate.  Native rebuild/parity, MCHPRS, Fabric, scale routing, and full
  R10/N2 acceptance were not run.  The later current-state review below
  supersedes this record's earlier commit-ready conclusion.

### 2026-09-06 selected-access current-state correction

This reviewed correction was first committed and pushed as historical
`dc95e349ca96470046d39c8513184be32cf3fbe9`, then recorded on the current
`Physical-Rules` history as same-parent checkpoint
`1d9e8995d0fed2ed1afa6f2eb6b20524aa8a89ab` (`Enforce current selected access
and add conformance coverage`). The seven production/test paths are
blob-identical between the two commits, which share parent `ffd8c764`; the
original nine-file binary diff is retained as SHA-256
`411ba33bf1edeea565d4aafeea99be2efd01e5ea403acf090ff7a34f8cde856e`.
This remains an exact Physical-owned dependency checkpoint for Joint planning,
not evidence that Joint has integrated it or that full routing acceptance has
occurred.

- **Observed stale acceptance.** New real-producer regressions retained at
  `Output/PhysicalRulesStaleAccess/20260906TRed/` first proved that an explicit
  profile accepted a valid old witness after a same-named target moved or its
  face changed at the same terminal, and that explicit fabric construction
  accepted a valid old witness/solve after current foreign ownership or routing
  technology changed.  The red run settled at **4 failed, 2 passed**; both
  unchanged-current controls passed.
- **Current identity enforcement.** The Physical access catalog now exposes one
  pure validator over its canonical placed-terminal binding traversal.  Explicit
  profiles require exact current signal, gate name/kind, role, pin, terminal,
  and face coverage, including no missing, extra, or duplicate selection.
  Explicit fabric construction requires agreement among the supplied technology,
  resource-graph technology, selected technology fingerprint, and the current
  placed-resource-model fingerprint including `FrozenNetWires`.  These checks
  compare identities only; they do not enumerate, regenerate, reselect, solve,
  salvage, or manufacture a routing outcome.
- **Focused verification.** With the same worktree interpreter and loaded native
  SHA-256 `c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`,
  the six current/stale regressions passed, all three affected files passed
  **61 tests**, all historical 13 compatibility nodes passed, the five focused
  incomplete-domain/solver checks passed, and the structural/schema gate passed
  **7 tests**.  Evidence is retained at
  `Output/PhysicalRulesStaleAccess/20260906TGreen/`.
- **Current disposition and limits.** The bounded current-state correction is
  commit-ready after settled current-snapshot verification and independent
  review.  The three affected test files passed **61 tests in 0.49s**; the
  structural/schema gate passed **7 tests in 2.10s**; collection reported
  **1442 tests**; and the full deterministic suite passed **1438 tests, 4
  skipped, and 257 subtests in 51.11s**.  JUnit confirms all historical 13
  compatibility nodes and all six current/stale controls passed.  Verification
  is retained at
  `Output/PhysicalRulesCompletionReview/20260906T014429Z/Verification/`, and
  the resolving independent review is retained at
  `Output/PhysicalRulesCompletionReview/20260906TFinalFixReview/`.  Joint's
  task `01a07418-c4c5-7290-ad60-4d9773df4c76`, review
  `01a0741b-5b17-7fe3-9ec0-c7d5f4cec0a1`, and final turn
  `01a07458-2765-73d1-8b7a-287997cea8ce` accepted the exact public APIs and
  behavior without a companion change or uncommitted-input consumption. The
  historical 1433-pass suite above predates this correction and is not relabeled
  as current. No Joint integration occurred. Nonempty required-air
  transformation, the full real five-stage handoff, native rebuild and parity,
  MCHPRS, Fabric, scale routing, and full R10/N2 acceptance remain separate or
  not-run.

### 2026-09-06 stale supplied resource-graph correction

- **Observed producer defect.** Starting from `Physical-Rules` checkpoint
  `1d9e8995d0fed2ed1afa6f2eb6b20524aa8a89ab`, a real selected source access
  claim supplied a position from its electrical influence scope. Adding that
  position to current `FrozenNetWires` while reusing the earlier resource graph
  allowed the old placed-resource-model fingerprint to survive and explicit
  fabric construction to report `Complete`. The independently retained red
  regression failed because neither public boundary raised.
- **Bounded correction and oracle.** The Physical catalog now normalizes the
  pre-owned nodes once and requires every such position to exist in the
  supplied resource graph's `ElectricalBlocks` before domain enumeration or
  model fingerprinting. The oracle is direct membership of the current frozen
  position in the current graph, derived from the selected claim rather than an
  expected hash. A stale supplied graph raises before it can create or validate
  a model identity; coherent current graphs preserve their existing identity
  and legal positive behavior. No selection, regeneration, solving, salvage,
  public export, or consumer-local legality rule was added.
- **Frozen-candidate verification.** The three affected access/profile files
  passed **62 tests in 0.86s**; placement/routing compile-all passed; the
  structural/schema gate passed **7 tests in 2.09s**; collection reported
  **1443 tests**; and one full deterministic run with
  `RC_RUN_SCALE_TESTS=0` passed **1439 tests, 4 skipped, and 257 subtests in
  52.42s**. The worktree interpreter loaded native SHA-256
  `c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`.
  Frozen evidence is retained at
  `Output/PhysicalRulesStaleGraphCorrection/20260906T131150Z/Verification/`.
- **Disposition and limits.** This bounded verified producer correction passed
  independent precommit review. No native source changed; native
  rebuild/parity, MCHPRS, Fabric, live validation, scale routing, the real
  five-stage consumer handoff, Joint integration, and full R10/N2 acceptance
  remain not-run or separate gates.

### 2026-09-06 current frontier

`f23293a18487cde6638c8b05be18ebd71afc0d30` is the current Physical-Rules
checkpoint atop the bounded catalog/realization/exact-claim/proof/domain slice
at `2f69160` and current terminal/resource/technology binding checks at
`1d9e899`. It closes the stale-supplied-resource-graph path by requiring each
selected-claim pre-owned position in `FrozenNetWires` to occur in the supplied
graph's `ElectricalBlocks`; coherent current graphs retain their legal result.
The retained focused result is 62 passed, and the retained deterministic suite
is 1,439 passed, 4 skipped, and 257 subtests. Evidence is
[the frozen verification record](/mnt/Projects/RedstoneCompiler-Worktrees/Physical-Rules/RedstoneCompiler/Output/PhysicalRulesStaleGraphCorrection/20260906T131150Z/Verification/Summary.txt).

Joint subsequently consumed this exact producer through `2902d1d` and retains
it at current Joint `64efbe1`; that is a consumer checkpoint, not new
Physical implementation. Nonempty required-air transformation, the broader
shared model, native parity, MCHPRS/Fabric, scale routing, and R10 acceptance
remain unproved or not run.

### 2026-09-06 supported stair-rule conformance candidate

- **Contract and independent oracle.** For lower dust `(0, 1, 0)` and upper
  dust `(1, 2, 0)`, literal coordinate arithmetic requires supports at
  `(0, 0, 0)` and `(1, 1, 0)` and clear headroom at `(0, 2, 0)`. The clear
  case must produce the exact two wire claims, two support claims, and one
  required-air claim; accept the local claim; connect the physical graph in
  both directions; and render the lower east state as `up` and upper west
  state as `side`. A solid block at `(0, 2, 0)` must remove the primitive,
  invalidate the formerly legal claim, disconnect the graph, and render the
  lower east state as `none`.
- **Distinct shared-consumer coverage.** Existing tests separately cover stair
  primitive claims, solid headroom obstruction, occupied upper-support
  solidity, and static/rendered wall-torch behavior. The new conformance test
  binds resource construction, exact claim validation, final physical
  connectivity, and rendered block state to one literal clear/solid oracle.
  This is a genuinely nonempty route-stair required-air claim, not selected-
  access-family required-air coverage. Production behavior is unchanged, so
  failing-before/passing-after production evidence is not applicable.
- **Source and verification.** The candidate starts from clean
  `Physical-Rules` baseline `b65847043c7ba354ea3835486df60840f9510cc2`.
  Independent pre-edit evidence at
  `Output/PhysicalRulesSharedContract/20260906T230415Z/Baseline/` records
  1,439 passed, 4 skipped, and 257 subtests; 7 structural/schema tests; and
  1,443 collected tests. Settled candidate verification at
  `Output/PhysicalRulesSharedContract/20260906T231840Z/Verification/` passed
  compile-all, the 17-test owning module, 7 structural/schema tests, collection
  of 1,444 tests, and the full deterministic suite at 1,440 passed, 4 skipped,
  and 257 subtests in 50.99 seconds. Supplemental placement/rendering and
  access-catalog coverage passed 69 tests and 14 subtests. Python 3.12.3 loaded
  native SHA-256
  `c086b46182a6bd3dd461536544fa99e9d4df887cbbf56460593a1a63f2f26f4d`;
  source and native identities remained stable.
- **Review, disposition, and limits.** Independent review at
  `Output/PhysicalRulesSharedContract/20260906T231148Z/Review/` resolved its
  one finding by requiring both blocked graph adjacencies to be absent, then
  passed the corrected node, owning module, independent oracle probe, and diff
  check with no unresolved in-scope technical finding. Test SHA-256 is
  `e64b4bdfa3a0db19cbfacd9a615d9aa844ff609c94dc5ecf52fa50cd8d93087a`.
  This three-file conformance-only candidate is commit-ready pending user
  approval; no production/API change or consumer dependency integration
  occurred. The wall-torch case remains an exact disagreement—resource
  primitive absent, final physical edge present, rendered `east=up` and
  `west=side`—outside the proved clear/solid scope pending a separately agreed
  shared occupancy contract. Native rebuild/parity, MCHPRS, Fabric, live or
  scale routing, the full consumer matrix, and R10 acceptance were not run or
  established.


### Batch1 declarative fixture candidate

The public fixture checker and separate data loaders, strict expectations, raw
MCHPRS observer, comparison, and reporting now support source-bound conditional
route-transfer conformance. Native observations independently verify complete
input baselines, applied vectors, root power, and every tick through the declared
settlement/stability horizon. See [Physical-Rules Batch1](PhysicalRulesBatch1.md) for the contract.

General device truth and model timing remain unavailable; no Fabric, scale, or
full-router acceptance is claimed. This is an uncommitted review candidate, not
an integrated checkpoint.

### 2026-09-07 state-aware dust-stair decision candidate

- **Shared decision.** `dust-stair-query-v1` returns separate geometry,
  headroom classification, and route-claim status with normalized semantic
  positions, deterministic input identity, stable reasons, and exact claim
  positions only for `Legal`. Resource construction admits only `Legal`, while
  final physical graphs and rendering project only `Connected` geometry. The
  identity retains complete relevant raw block states plus validity, and each
  resource graph deep-freezes its state context before populating caches.
- **Bounded behavior.** Supported clear air remains
  `Connected/Air/Legal`; solid headroom remains
  `Blocked/Solid/Illegal`. The exact source-qualified east-backed, lit W1 wall
  torch is `Connected/NonSolidElectrical/Unknown` with reason
  `electrical-headroom-ownership-unavailable`, no primitive or route claims,
  and verified lower `east=up` / upper `west=side` geometry. Control B remains
  the clear-air P0 decision. Underqualified or novel states fail closed.
- **Verification and status.** The complete deterministic suite passed 1,489
  tests with 4 skipped and 257 subtests; the structural/schema gate passed 7
  tests; and all seven declarative fixture cases passed. This is a frozen
  uncommitted candidate pending fresh independent review, not an integrated
  checkpoint or R10 acceptance.

The A/B evidence remains fixture-scoped: it does not establish ownership,
same-net compatibility, general device behavior, timing, or route legality.
Native rebuild/parity, live Fabric, scale routing, and full-router acceptance
were not run.

### 2026-09-07 current selected-access validation receipt candidate

`ValidateCurrentSelectedPlacementAccess` now re-attests supplied full selected
access witness and solve evidence against one immutable snapshot of current
placed terminals, resource graph, technology, and frozen wires. It returns a
typed non-authoritative receipt: only exact current terminal bindings,
technology, resource model, complete feasible domain evidence, and matching
solve identity return `Verified/Current`; stale relationships return typed
`Mismatch`, while incomplete, unsatisfiable, or insufficient domain evidence
returns typed `Unresolved`. It neither enumerates, selects, solves, routes, nor
regenerates access, and re-attests caller inputs before publication so an
in-call public-input change cannot publish Verified.

Focused receipt/catalog/channel tests (70), structural/schema tests (7), full
collection (1,508), and the deterministic suite (1,504 passed, 4 skipped, 257
subtests) are retained under
`Output/PhysicalCurrentSelectedAccessValidation/20260907T072013Z/`. This is a
Physical producer candidate only: no Joint consumer, dependency register,
native rebuild/parity, MCHPRS, Fabric, scale, acceptance, or R10 promotion claim
is made. A stored or decoded receipt never authorizes use without a fresh call
against the full source objects and current inputs.

### 2026-09-07 current selected-access receipt correction candidate

The receipt candidate now binds the complete finite canonical
`ResourceGraph.BlockStates` mapping into the versioned `pin-access-resource-model-v2`
identity used by both fresh witnesses and current validation. Non-finite floats,
opaque values, non-string state keys, invalid positions, and repeated per-signal
frozen-wire positions fail before any model or receipt identity is created.
Reordered unique frozen wires retain the same identity; one-shot frozen-wire
iterables fail rather than manufacturing a re-attestation.

A changed-during-validation receipt now requires a nonempty final observation
fingerprint, while every non-drift receipt forbids it. The earlier rejected
candidate remains unchanged at
`Output/PhysicalCurrentSelectedAccessValidation/20260907T072013Z/`; superseding
correction evidence is retained under
`Output/PhysicalCurrentSelectedAccessValidation/20260907T075607Z-Correction/`.

### 2026-09-07 current selected-access duplicate Mapping correction candidate

Frozen-wire normalization now rejects duplicate logical signal entries from a
general Mapping before any conversion to `dict`, model identity, or receipt.
Catalog and current validation consume the same unique-entry normalization path,
so a custom Mapping cannot silently discard an earlier signal entry. Unique
ordinary and custom mappings preserve the prior behavior; duplicate positions,
one-shot inputs, and reordered unique representations retain their separately
tested outcomes. The two earlier rejected roots remain immutable; the successor
evidence root is `Output/PhysicalCurrentSelectedAccessValidation/20260907T081834Z-Correction2/`.

### 2026-09-07 versioned placed-template routing-state candidate

Physical resource construction now has a single public
`BuildPlacedTemplateRoutingStateSnapshot(Placed, WorkCheck=None,
Technology=DefaultRedstoneRoutingTechnology)` producer traversal. It transforms
every explicit template `Blocks` entry with the public geometry/state transforms,
publishes deterministic occupancy plus complete transformed `BlockStates`, and
rejects duplicate identities/positions, malformed source, and observation drift
before graph publication. Explicit `minecraft:air` remains state evidence but
is excluded from occupancy and keep-outs; this is explicit-entry behavior, not
full-volume air completeness because template loading may filter file-air.

The producer captures the exact `PlacedGates` collection and membership, loaded
template mapping/domain, and each used template object, class, `Size`, and full
typed `Blocks` payload before its first derivation or work check. It derives only
from that immutable capture, then repeats the complete live attestation after
isolation and every work check immediately before publication. Collection or
mapping replacement, a gate mutation, and any later mutation of an already-used
template therefore reject instead of publishing a mixed resource graph. These
process-local identities are attestation-only and never enter portable graph or
receipt fingerprints.

Before capture, a template state must be exactly `Name` plus optional
`Properties`; unknown top-level fields are rejected rather than silently dropped
by the public transform. Template `Size` is exactly three positive integers and
each explicit local block is required to lie in its half-open volume. These
producer checks reject malformed/lossy input before any transformed state,
occupancy, graph, or identity publication.

When `Properties` is present, it is a nonempty exact dictionary whose keys and
values are exact strings. This is a placed-template source contract, not a
restriction on the broader JSON-like state freezer available for abstract graph
and cache inputs.

The transform-sensitive Minecraft `rotation` property is further constrained to
its canonical strings `0` through `15`: noncanonical strings such as `01` and
out-of-range/malformed values are rejected before the public transform could
normalize them. Valid canonical values continue to use the public rotation and
mirror oracle.

`RoutingResourceGraph` now defaults to immutable `routing-resource-graph-v3`.
The selected-access receipt accepts only v3 at initial and final observations;
custom graph tokens remain valid for abstract graphs but are not current receipt
inputs. This is a Physical producer dependency for R2/R3/R4/R9/N3 consumers;
it does not change Joint readiness, CommitRouting, rendering, or acceptance.

### 2026-09-07 supported clear dust-stair MCHPRS conformance candidate

`PhysicalRulesBatch1/SupportedStair` now fixes one P0 `+X/+Y` supported,
clear-headroom dust stair: a floor lever at `(0,1,0)` drives declared lower
route-root dust `(1,1,0)` over air `(1,2,0)` to upper dust `(2,2,0)`. The
fixture has no repeater, device, ownership, alternate-power, or timing-model
declaration. Its two fresh MCHPRS cases prove only stable logical `Y` after
off-to-on and on-to-off input application. The static checker independently
requires the literal bidirectional dust edges, supports, required air, and
empty missing/blocked/conflict sets. The native receipt proves complete input
readback, fresh world/compiler, fixed compiler options, per-tick logical output,
and lower route-root power; expectation-only mutation changes comparison but
not the checker prediction or raw observation.

Evidence is retained under `Output/PhysicalRulesBatch1Evidence/20260907T125818Z/`
and the complete data-only runner root
`Output/PhysicalRulesBatch1/20260907T130646Z/`: focused source/contracts
`40 passed`, real observer `5 passed`, runner `9/9` cases passed,
structural/schema `7 passed`, collection `1,570`, and deterministic tests
`1,566 passed, 4 skipped, 257 subtests`. Upper-dust analog power is **not
observed** by the existing native schema and is neither inferred nor asserted;
`0/15` applies only to the declared lower route root. This covers no blocked
N0 backend case, strength limit, fanout, ownership, general device/timing or
stateful behavior, Fabric, scale, router acceptance, or full R10 acceptance.

### 2026-09-07 blocked all-stone dust-stair conformance candidate

`PhysicalRulesBatch1/BlockedHeadroomStair` fixes the literal seven-block N0
boundary: four `minecraft:stone` blocks, the existing floor lever, and two
dust blocks, with stone at `(1,2,0)` blocking the lower-to-upper step. The
public stair decision is blocked/solid/illegal with no claim positions, while
the aggregate fixture checker remains `Legal` because it removes the blocked
edge and all required supports exist. Fresh native off-to-on and on-to-off
receipts observe false `Y` through ticks `0..7` and only declared lower-root
power (`15`/`0`); upper analog power is absent from the receipt and is not
inferred. This remains fixture-specific evidence, not general N0, Fabric,
scale, router-acceptance, or full R10 acceptance.

### 2026-09-07 dust signal-strength boundary conformance candidate

`PhysicalRulesBatch1/DustSignalStrengthAtLimit` and
`DustSignalStrengthBeyondLimit` pin the paired straight, stone-backed dust
boundary. The declared root at `(1,1,0)` has power `15` when `A` is applied;
F15 has fourteen dust edges and its endpoint remains Boolean true, while F16
has fifteen edges and its endpoint is Boolean false. Both applied-off cases
are false with root `0`. The static oracle derives this only from one-unit
attenuation per literal edge, and the native observer records fresh worlds,
compilers, complete input readback, ticks `0..7`, logical `Y`, and declared
root power. Expectation mutation cannot alter checker or raw-observer input.

This candidate makes no remote/upper analog, general timing, indefinite
stability, repeater, fanout, ownership, alternate-material, Fabric, scale,
router-acceptance, or full R10 claim. The prior four raw diagnostics establish
stability through ticks `0..16` for these exact layouts only; fixture timing
uses the established conservative `4 + 3` comparison horizon.

### 2026-09-09 placement-access domain completion candidate

- **Capability packet and source.** `Physical-Placement-Access-Domain-Completion-v1`
  starts from `Physical-Rules` commit
  `ea2e22d8f8743417691fdb25a642bb6427e8955f`, tree
  `de2e8e777b3f209f8d5e7554ce9fc12faab538df`. For an exact placed terminal,
  a domain is complete only when every enabled certified template/layer has a
  deterministic semantic attempt under one terminal, catalog, technology,
  resource-model, block-state, and frozen-wire identity. At least one legal
  attempt permits feasibility; zero legal options is candidate-local
  unsatisfiable only when every required attempt has a typed rejection.
- **Typed evidence and incomplete boundaries.** The versioned domain identity
  now retains enabled families, canonical per-pattern attempt identifiers,
  legal/rejected/deduplicated/not-evaluated/unknown status, closed reasons,
  exact option linkage, initial evaluation identity, and final drift identity.
  Work-cap or cooperatively reported deadline exhaustion leaves the unattempted
  patterns explicit and makes the solve incomplete. A raised outer typed
  deadline still propagates unchanged. Missing certified patterns, unknown
  physical decisions, or valid input mutation before publication cannot become
  a complete domain, conflict core, or `NoPinAccessPattern` proof.
- **Independent oracle and challenges.** The controlled one-terminal oracle
  fixes the three literal INPUT `Output0` pattern identities and layer zero,
  then independently checks one all-legal domain, an occupied-terminal domain
  with all three typed rejections, exhaustion before the third pattern,
  mutation at publication, reversed catalog enumeration, and removal of every
  certified terminal pattern. The last case specifically rejects the former
  aggregate shortcut that treated zero generated options as complete evidence.
- **Scope and limits.** No new pattern family, named circuit/signal behavior,
  Joint orchestration, recovery, bound increase, retry, worker, beam, or
  physical-legality relaxation is included. HalfAdder remains acceptance
  evidence only. This is a source-local uncommitted Physical producer candidate,
  not Joint integration, a Router checkpoint, full R10 acceptance, or promotion
  readiness. Native rebuild/parity, MCHPRS, Fabric, scale, and acceptance-matrix
  runs remain not-run because no native or backend behavior changed.
- **Verification evidence.** The required focused suite passed **154 tests**;
  the selected-access envelope consumer module passed **63 tests**; the
  structural/schema gate passed **7 tests**; compile-all passed; collection
  reported **2,346 tests**; and the final deterministic suite passed **2,341
  tests, 5 skipped, and 378 subtests** in 401.92 seconds. Fresh retained logs
  are under `Output/PhysicalAccessDomainCompletion/20260909T215543Z/`.

### 2026-09-09 placement-access completion review correction

The first candidate diff
`625794c5d66098e71e201ca593bc04d9135e5db33ed8fd9eb32a6bffaea8c050`
is preserved as rejected evidence. Independent review proved that family-set
coverage and aggregate option counts did not establish the exact certified
template/layer universe, permitted many-to-one legal links and global-cap
forgeries, and omitted the stable final input observation.

- **Corrected Physical contract.** Every terminal now retains an ordered exact
  required-pattern manifest with template ID, family, template fingerprint,
  layer, and dependency-bound attempt ID. Attempts must match it one-for-one;
  each legal attempt must map uniquely to a retained option with identical
  template/layer/catalog/technology/resource identity. Complete problem evidence
  validates total evaluated work against the one shared original generation
  cap. Stable results retain equal nonempty initial/final observations; drift
  retains unequal observations and remains incomplete.
- **Current Physical re-attestation.** Current validation recomputes the exact
  manifest and evaluation input from current placed terminals, catalog,
  technology, resource model, block states, and frozen wires before accepting
  either feasible or unsatisfiable evidence. Same-family truncation and
  catalog-absent invented requirements therefore produce typed mismatch and
  cannot reach the current selected-access envelope's `Ready`, `Unsatisfiable`,
  or `NoPinAccessPattern` outcomes.
- **Remaining dependency.** A fully self-consistent portable record that changes
  its own families, catalog version, generation cap, or assignment cap can be
  compared with the *live* policy only when the Joint-owned current-envelope
  and final-publication callers supply those controls explicitly. That
  producer/consumer contract is pending parent routing. Until it is integrated
  and reviewed, this repair is not capability-proven or commit-ready even when
  its Physical checks pass.
- **Repair evidence.** Red evidence, pre-verification source/test hashes, and
  subsequent checks are retained under
  `Output/PhysicalAccessDomainCompletionRepair/20260909T230914Z/`. The rejected
  first-candidate root remains unchanged.
- **Provisional verification.** The repaired Physical suite passed **160
  tests**, the selected-envelope consumer module passed **65 tests**, the
  structural/schema gate passed **7 tests**, compile-all passed, collection
  reported **2,354 tests**, and the deterministic suite passed **2,349 tests,
  5 skipped, and 378 subtests** in 326.09 seconds. These checks establish the
  independent Physical slice only; they do not discharge the live-policy
  consumer dependency above.

### 2026-09-10 evaluation-controls producer migration

`PlacementAccessEvaluationControls` is the frozen Physical record for the
current policy's normalized enabled families, catalog version, exact positive
generation cap, and exact positive assignment cap. Its deterministic controls
fingerprint is retained in the v2 current-validation input identity.

The existing `ValidateCurrentSelectedPlacementAccess` entry point accepts
keyword-only `CurrentControls=None` for Python call compatibility, but the
compatibility path has no authority: omission returns typed
`Unresolved/EvaluationControlsMissing` before feasible, incomplete, or
unsatisfiable classification. Supplied controls must be the exact frozen type
and must match every domain, the solve assignment cap, and the recomputed
current catalog manifest; mismatch is typed
`Mismatch/EvaluationControlsMismatch`. No validation path regenerates access or
changes a solve result.

The Joint-owned current-envelope and final-publication callers have not yet
migrated in this worktree, so their existing tests and the complete suite are
expected blocked rather than relabeled green. Combined producer/consumer
verification is outside this step and remains not-run.

The independently valid expanded producer suite passed **181 tests with 1
Joint-caller test deselected**; the structural/schema gate passed **7 tests**;
compile-all passed; and collection reported **2,376 tests**. Two explicit
unchanged-caller probes failed with `EvaluationControlsMissing`, one at the
current-envelope boundary and one at final publication. The complete suite was
not run because it would repeat those known absent-caller failures and the user
did not authorize combined-source verification.

### 2026-09-12 immutable rejected pin-access evidence candidate

The Physical producer now retains one immutable explanation for the existing
first decisive catalog rejection. It binds the unchanged semantic attempt ID
to the exact evaluation-input fingerprint; transformed terminal, face, bridge,
leg, track and typed block roles; the proposed claims available at that
predicate; exact typed proposed/conflicting resource slices; and only owners
and provenance directly supplied at that decision boundary. The existing
foreign-static ownership index supplies a conflicting signal, but not its
originating gate/static role or frozen node. The producer retains that known
signal, marks provenance `Partial`/`Unavailable`, and performs no post-rejection
placed-gate or frozen-node traversal to reconstruct it. Unknown occupancy
ownership and facts unavailable before claim construction remain explicit
`Partial`; a missing record is `Unavailable`. Neither state is reported as a
complete explanation.

The admission predicate order, first rejection, catalog/templates, technology,
resource-model identity, control/input identities, attempt IDs, option
fingerprints, legal-option order, work counts, budgets, domain completeness and
solve classification are unchanged. Rejected geometry is evidence only and is
never inserted into the selectable option tuple. Pairwise option
`BlockingResources` retain their old meaning; rejected-attempt resources are a
separate core projection with a separate explanation status.

The evidence-bearing attempt/domain/problem/core/witness/solve/frozen-contract
serializers are deliberately versioned rather than defaulting historical
records into invented explanations. Table-driven current/historical decoder
coverage spans attempt, domain, conflict core, selected witness, solve result,
and frozen contract and proves rejected historical records remain rejected
without payload enrichment. Joint still owns envelope/schema consumption and
feedback, and Telemetry still owns artifact projection. The current Physical
worktree therefore proves only the producer contract; it does not reproduce or
repair the seven-circuit benchmark, validate the physical rule independently,
enable another pattern, relax legality, or establish full R10 acceptance.

Independent evidence coverage passes 12 tests, including an adversarial
post-decision traversal guard; the focused producer set passes 193 tests with
the known Joint finalization caller deselected; structural/schema passes 7
tests. Two exact baseline/candidate runs retain 300 raw wall and CPU samples per
fixture per run. Their pooled 600-sample comparison preserves every required
identity/outcome, remains below 4 KiB per attempt, and passes the unchanged 5%
median-wall guard for all fixtures. The dense 32-rejection case is 4.748%
faster than exact baseline after memoizing immutable template fingerprints and
reusing its already-built attempt ID.

The exact unfiltered deterministic suite remains a blocking failure rather than
a claimed pass: 2,328 passed, 55 failed, 5 skipped, and 378 subtests for
`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q Tests`. Exact-parent
comparison reproduces every failing node ID, including each of the prior eight
replay/telemetry cases, with zero candidate-only failure IDs. That proves
inheritance but does not satisfy the authorized P1 green-suite condition. The
repaired packet is under
`Output/PhysicalRules/RejectionEvidence/20260912T225854Z-58e45c4-repaired-candidate/`.
This candidate remains uncommitted, is not commit-ready, and requires fresh
independent review after the full-suite blocker is resolved.

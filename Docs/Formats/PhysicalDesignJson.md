# Physical design JSON

`<Name>.PhysicalDesign.json` is the authoritative successful-run evidence
envelope. It is published transactionally only after final claim validation
and litematic serialization. It is not Fabric-server acceptance by itself.

Important sections include:

- `Strategy`, `Policy`, and `Technology` for the selected behavior;
- `SourceState`, `Environment`, and `Reproduction` for provenance;
- `PlanningContracts`, `GlobalGuidePlanning`, and `NegotiatedRouting`;
- `RoutingResourceGraph` cache and graph statistics;
- `RunSummary` for dimensions, runtime, route metrics, and Fabric-server status;
- `FinalValidation` for conflict and unresolved-claim truth; and
- `BlockComposition` for exact material and provenance counts.

Routing acceptance requires `FallbackUsed=false`, zero final conflicts, zero
unresolved claims, and deterministic fingerprints across repeated runs. Full
behavioral acceptance additionally requires an authoritative Fabric-server
result; only `FabricServerValidation.Status=passed` is functionally accepted.
Fields may grow with a policy version; readers should ignore unknown fields and
must not infer server acceptance from file presence alone.

Failed routing writes `<Name>.RoutingFailure.json` instead. Its typed failure,
partial work, cuts, deadlines, and reproduction data are diagnostic evidence,
not a successful physical design.

## Automatic local routing-failure report

The argument and guided compiler CLI now attempts one
`Runs/<run-id>/RoutingFailureReport.html` after a handled failure reaches the
existing persisted failure-artifact path. The file is self-contained HTML with
escaped text and a compact XYZ/claim-role table. It contains no JavaScript,
external assets, links derived from diagnostics, browser launch, or server.
`Summary.txt`, concise terminal output, and `RawDump.txt` name the report;
RawDump's artifact inventory hashes the HTML using the ordinary reporting
implementation. A publication error is recorded as `Unavailable` with its
exception type while preserving the original compiler failure and exit code.
Success and cancellation do not publish a failure report. Direct callers of
`CompileSvToLitematic` that do not use CLI run reporting do not receive HTML.

The reader consumes only the persisted `routing-failure-v1` artifact, with a
nonempty typed stage/reason. It displays affected nets/resources/locations,
deadline state, and suggested repair actions. Suggestions are not evidence that
an action ran. It reuses the source inventory's path, size, and SHA-256, and
verifies the bounded bytes against that hash before interpreting them. A missing,
changed, symlinked, nonlocal, or oversized source makes publication unavailable.
An artifact exceeding 4 MiB fails closed with `ArtifactReadLimit`; it is not
interpreted or rehashed by the reader. Malformed JSON and unsupported schema
versions within the byte cap receive a metadata-only report.

Within `Failure.Diagnostics`, a bounded traversal locates keys named
`SelfClaimConflictEvidence`. The supported version is
`materialization-self-conflict-evidence-v1`, scope
`FirstDecisiveMaterializationSelfClaimPredicate`, predicate
`FindSelfClaimConflicts`. It projects captured signal, conflict/count/coverage
facts, XYZ cells and claim roles, node count, the four claim counts, capture
status and omissions. It validates the fields it interprets, including count
and coverage consistency and the v1 unavailable-provenance/not-captured-identity
sentinels. Contradictory interpreted facts are `Malformed`; other schema
versions are `Unsupported`. No compatibility with future versions is implied.
The reader never imports the producer or rebuilds route geometry or claims.

Evidence labels are deliberately separate:

- `Available`/`Partial` describes the supported captured cell record; its own
  `CaptureStatus` is shown. A complete predicate capture is not a full scene.
- Physical scene context remains partial when captured cells exist. Surrounding
  components, contributor provenance, and evaluation identities are not supplied.
- `Unavailable`, `Malformed`, and `Unsupported` do not imply an empty physical
  world or prove that no conflict exists.
- The cell table is derived formatting of observed rejection facts, never a
  proven upstream root cause or global impossibility proof.
- Physical publication, MCHPRS, and Fabric are `not-run` when routing prevented
  them. `MchprsValidation` and `FabricFinalCheck` envelopes instead say their
  later-phase state is unavailable in this bounded reader; they are not
  relabeled as routing failures that prevented validation.

Structural safety caps are 4 MiB input parsing, 8,192 discovery values, depth
24, eight evidence records, 24,000 source text characters, 512 characters per
string, 24 entries and depth four per displayed field, and a 256 KiB final HTML
ceiling. Search and display truncation are explicit. These are implementation
resource caps, not a measured latency SLA. The run inventory's existing
recursive enumeration/hashing and normal provenance/report work remain outside
these new reader caps. No output directory is copied. Source node/claim arrays
are not expanded or displayed. No report can be guaranteed after an unsupervised
kill or when the run directory cannot be written.

The Joint capture producer is not yet integrated into this Telemetry checkout.
Ordinary current failures can therefore receive useful typed reports without
compatible spatial evidence. An authenticated retained Joint capture in a
separately declared literal failure envelope tests display compatibility; it
does not prove end-to-end producer integration or physical routing acceptance.

## Sealed report receipts and archive readback

Each new HTML publication also writes `RoutingFailureReport.receipt.json`.
Receipt schema `routing-failure-report-receipt-v1` contains exactly the source
and report byte identities needed for relocation:

```json
{
  "SchemaVersion": "routing-failure-report-receipt-v1",
  "Source": {"Name": "Design.RoutingFailure.json", "SizeBytes": 123, "Sha256": "<64 lowercase hex characters>"},
  "Report": {"Name": "RoutingFailureReport.html", "SizeBytes": 456, "Sha256": "<64 lowercase hex characters>"}
}
```

The names are sibling basenames, not paths or URLs to open. The receipt is
published last. HTML, receipt and ordinary text reports are written using
retained non-following directory descriptors. The publisher verifies that its
recorded directory still names the same object before announcing publication;
ordinary reporting then safely rechecks the retained failure bytes. Existing
pairs are validated instead of overwritten or regenerated. A report bound to
an older/different failure is rejected.

`App/RoutingFailureArtifacts.py` owns the shared contract. It validates exact
byte lengths/hashes, the source binding, a unique listing, the fixed inert HTML
grammar/CSP/stylesheet, and safe file observations. Receipt parsing is limited
to 4 KiB; HTML remains limited to 256 KiB and source rechecks to 4 MiB. Membership
discovery inspects directory names through retained descriptors, with a 50,000
entry and depth-32 cap; it reads no file contents and follows no links.

Ordinary reports retain both artifact identities and a separate availability
record. Artifact availability (`Available`, `Unavailable`, `Malformed`,
`Rejected`, `NotRun`) is distinct from scene/capture completeness. A sound report
can truthfully display unavailable or unsupported scene evidence. A report I/O
or validation problem never grants acceptance or replaces the typed routing
failure. Generic artifact inventory does not grant report authority: the run
reporter inserts only the specifically validated report pair.

Acceptance evaluation observes the pair beside the exact selected direct or
nested failure. It retains `RoutingFailureReport` and
`RoutingFailureReportReceipt` artifact records plus the separate report state.
Report availability does not participate in the routing acceptance verdict.
A successful run requires no report. Extra pairs or report-shaped listings at
other locations are rejected as case report evidence. Direct stale report
members are cleared with the other exact prior case outputs at run startup;
older nested runs are not deleted or silently selected.

The archive publisher's existing safe mirror copies regular run files. It
projects report validity from the same immutable bytes used for the archive
inventory and `SHA256SUMS`; a physically retained malformed/unlisted report is
raw diagnostic material, not accepted report evidence. Sealed readback rejects
missing, altered, unsafe, duplicate or unlisted report members. Whole-run
projections reject pairs without one selected source or additional unclaimed
pairs, while retaining the original failure/acceptance classification.

Routing-design snapshots automatically include only the validated pair for the
selected failure, avoiding basename collisions with other acceptance cases.
Other cases retain their availability in the acceptance summary. Snapshot staging
copies retained observations through directory descriptors, verifies the copy,
and includes HTML and receipt in the snapshot seal. `ReadRoutingDesignSnapshot`
verifies the seal and source binding after relocation. No reader regenerates
HTML or adds present-day spatial data to old evidence. Historical archives with
no report remain unavailable; an older HTML without a receipt cannot be upgraded
to trusted report evidence automatically.

For sealed acceptance readback, first use
`Tools.Routing.CaptureRoutingDesignSnapshot.BuildSealedArchiveEvidence`, then
project `App.BenchmarkArchive.BuildArchiveRunSurface` with the retained member
observations (each supplies Data, SizeBytes and Sha256). For a snapshot bundle,
use `Tools.Routing.CaptureRoutingDesignSnapshot.ReadRoutingDesignSnapshot`;
its `RoutingFailureReport` result is the independently validated availability,
separate from the original `Snapshot` document. `sha256sum -c SHA256SUMS` is a
useful byte check but alone does not detect newly added unlisted report files or
validate a report's source binding.

The checksum chain detects changes relative to a trusted retained seal. It is
not a digital signature against replacement of the files, receipt and every
checksum together. No routing, reuse, cache or accepted-state authority is
introduced by the receipt or by successful integrity validation.

The v1 receipt grammar, CSP and stylesheet are a retained reading contract. A
future viewer format must introduce a new receipt version and preserve v1
readback; changing the current viewer must not reinterpret or regenerate old
report bytes. The archive seal authenticates retained byte identities relative
to the trusted checksum reference, independently of the current renderer.

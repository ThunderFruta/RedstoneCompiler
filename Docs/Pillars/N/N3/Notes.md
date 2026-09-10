# N3 notes

Working notes for [N3](N3.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- Only the physical-design coordinator may commit accepted design state.

## Open questions

- None recorded.

## Working notes

### 2026-09-06 current frontier

Current Joint `64efbe1` proves the bounded public two-NAND fanout keeps its
selected access witness and current Physical identities immutable through
placement, access fabric, raw assignment, detailed routing, compaction, and
coordinator publication. Stale selected access is rejected before later-stage
work; no worker or cache receives independent accepted-state authority.

This is a controlled N3 path only. Reusable subclaim manifests, cross-worker
deterministic commitment, selective salvage, and global N3 acceptance remain
unproved or not run. See the [bounded R2 evidence](../../R/R2/Notes.md#2026-09-06-current-bounded-frontier).

### Candidate-specific preparation-result candidate

The current uncommitted Joint candidate adds immutable candidate preparation
results at the lazy pre-route boundary.  Complete feasible, complete failed,
and incomplete members all retain exact candidate-input, portfolio, work-cap,
and caller-owned deadline identity.  Only the result matching the selected
candidate and those exact controls can supply the frozen track preparation;
ambient or previous-candidate materialization cannot substitute for it.

The candidate input is a canonical manifest over the current placement core,
retention, selected solve/witness binding, policy, technology/resource model,
routing envelope, fabric descriptor, portfolio mode and objective inputs.  It
is re-attested before materialization, cached return and selected consumption.
Result construction requires exact Boolean/integer semantics and deeply owns
the manifest and preparation payload, so mutation of caller containers cannot
change its serialization or fingerprint.  A public single-NAND regression now
proves the actual producer-to-Setup success path and exact-input, work-control
and missing-result failure paths.

Canonical authority uses explicit map, ordered-sequence, set, scalar and null
tags.  It preserves empty-container kind, repeated pair entries, nested shape
and full-canonical set ordering; it never guesses that a sequence of pairs is a
dictionary.  `CompleteFeasible` is admitted only after direct enumeration shows
one exact allowed ordinary or local-claim choice for every required raw-domain
signal.  Missing, partial, duplicate, multi-choice, unknown and coercible
selection identities fail before candidate-result construction.

Outer-portfolio exhaustiveness remains a separate fact from the completeness
of any candidate result.  This hardens deterministic result transport and the
coordinator-owned selection boundary only.  It does not add worker commitment,
reuse certificates, selective salvage, Physical rules, or global N3
acceptance.

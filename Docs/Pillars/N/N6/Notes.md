# N6 notes

Working notes for [N6](N6.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- Every old-snapshot result is stale by default.
- Salvage is claim-specific and requires a coordinator-issued reuse
  certificate followed by current-snapshot revalidation.
- Demotion preserves a live or resumable review task; termination ends that
  task instance and does not classify the stale claim.

## Open questions

- None recorded.

## Working notes

- Reconciliation `0cbb17916fe3239adee04762c0fddd7cea3d2a4c` documents why legacy
  R1-associated history is not selective salvage. Current tip
  `5cd445621d492e1b62fa72d63c3d021945edbc8a` is outcome-first test cleanup;
  neither commit is an N6 implementation, test, or Router-consumed checkpoint.
- Strict rejection of stale results remains an ordinary direct-identity boundary.
  It does not prove a lazy expansion, topology-reuse, certificate, or
  revalidation path.

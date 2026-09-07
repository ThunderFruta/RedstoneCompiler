# N6 commit history

This file records commits intentionally attributed to [N6](N6.md). A commit is
listed only when its scope and verification are known.

| Commit | Date | Relationship to N6 |
|---|---|---|
| Joint checkpoint `ea77a28`, consuming Physical checkpoint `2f69160` | 2026-09-05 | External Stage-1 behavior rejects absent, stale, or inconsistent identities instead of regenerating access or entering fallback. Reject-all-stale behavior is not selective salvage and does not implement N6. |
| `0cbb179` — Record reuse and salvage history | 2026-09-05 | Reconciles legacy R1-associated work and records non-attribution; it does not implement claim-specific dependency manifests, certificates, or revalidation. Current owner tip `5cd4456` is outcome-first test cleanup and adds no N6 checkpoint. |

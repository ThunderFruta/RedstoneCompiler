# R8 commit history

This file records commits intentionally attributed to [R8](R8.md). A commit is
listed only when its scope and verification are known.

| Commit | Date | Relationship to R8 |
|---|---|---|
| `e274e79` — Add versioned snapshots and validated artifact reads | 2026-09-07 | Current committed Telemetry checkpoint. Consumed exactly by Router `7c7b52b` (`Integrate versioned snapshots and validated artifact reads`); canonical dependency-register closure `ee4c588` records the `e274e79` producer / `7c7b52b` consumer relationship. Reporting/archive scope is capability-proven, not complete R8 lifecycle coverage or promotion readiness. |
| `5ecdcec` — Implement routing telemetry and acceptance evidence | 2026-09-05 | Squashed R8/N5 checkpoint covering process-timeout capture, snapshot identities, concise/raw reports, source identity, copied evidence, and archive checksums. This does not establish complete lifecycle telemetry or production acceptance. |
| `502cc8f` — Refocus active tests on observable outcomes | 2026-09-05 | Pre-repair Telemetry checkpoint consumed by audited Joint `c6d6a81`. It preserves complete strategy/policy receipts and independent archive/snapshot consistency coverage, but is superseded as the branch tip by the reviewed reporting repair below. |
| `b8c1d4f` — Preserve uncertainty and strict fallback evidence in reports | 2026-09-06 | Prior committed Telemetry R8/N5 repair, parented by exact audited Joint `c6d6a81`. It makes archive stability tri-state with newest-observation authority and preserves strict public no-fallback/snapshot evidence. Its source and tests were consumed unchanged by Joint `64efbe1` and Router `bd3b934`; this does not claim production acceptance. |

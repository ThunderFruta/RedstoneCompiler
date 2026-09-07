# N5 commit history

This file records commits intentionally attributed to [N5](N5.md). A commit is
listed only when its scope and verification are known.

| Commit | Date | Relationship to N5 |
|---|---|---|
| `e274e79` — Add versioned snapshots and validated artifact reads | 2026-09-07 | Current committed Telemetry checkpoint. Consumed exactly by Router `7c7b52b` (`Integrate versioned snapshots and validated artifact reads`); canonical dependency-register closure `ee4c588` records the `e274e79` producer / `7c7b52b` consumer relationship. Reporting/archive scope is capability-proven, while full N5 physical acceptance and promotion readiness remain unproven. |
| `5ecdcec` — Implement routing telemetry and acceptance evidence | 2026-09-05 | Squashed Telemetry checkpoint covering routing-strategy harness integration, typed snapshots and process-timeout meaning, archive publication, checksums, and retained interruption evidence. Recorded Stage-1 outcomes remain tied to pre-split source `2024d7d`; the current Physical-to-Joint behavior is merge commit `2902d1d` over exact Physical `f23293a`. |
| `502cc8f` — Refocus active tests on observable outcomes | 2026-09-05 | Pre-repair Telemetry checkpoint consumed by audited Joint `c6d6a81`; its complete v17 strategy/policy receipts and archive/snapshot coverage were the basis for the later reporting repair. |
| `b8c1d4f` — Preserve uncertainty and strict fallback evidence in reports | 2026-09-06 | Prior committed Telemetry R8/N5 repair, parented by audited Joint `c6d6a81`. It fixes tri-state archive authority and strict public fallback/snapshot evidence; Joint `64efbe1` and Router `bd3b934` consume the reviewed producer unchanged. This remains bounded reporting evidence, not physical acceptance. |

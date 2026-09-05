# R8 commit history

This file records commits intentionally attributed to [R8](R8.md). A commit is
listed only when its scope and verification are known.

| Commit | Date | Relationship to R8 |
|---|---|---|
| `50555cc` — Harden routing evidence snapshots | 2026-09-05 | Own process-timeout capture and snapshot identity checks independently of R2 implementation. This does not establish complete lifecycle telemetry or acceptance. |
| `4cdc235` — Extract benchmark archives into telemetry bucket | 2026-09-05 | Reclassified from legacy R1 archive/reporting slice `14646a9`. Retain concise and raw reports, source identity, copied evidence, and archive checksums. |

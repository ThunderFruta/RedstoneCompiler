# Freerouting upstream

This directory holds the independently developed Freerouting PCB autorouter
used by `Scripts/Routing/RunFreeroutingBenchmark.py` as an external routing baseline.

- Project: <https://github.com/freerouting/freerouting>
- Release: `v2.3.0` (2026-08-07)
- Tag commit: `2d4de019aa89e9fa3dc1dc44e09bf509760cafc1`
- Asset: `Upstream/freerouting-2.3.0.jar`
- Asset size: `62,995,156` bytes
- SHA-256: `3cf18d608437740bc497db6b8ef5888e2e60a08de0def20691d1bad0c0e0ee24`
- License: GPL-3.0; the unmodified upstream license is preserved as
  `LICENSE-GPL-3.0`.

The JAR is intentionally ignored rather than committed. Install or repair the
pinned asset with:

```bash
mkdir -p Tools/ExternalRouters/Freerouting/Upstream
curl --fail --location --retry 3 --output Tools/ExternalRouters/Freerouting/Upstream/freerouting-2.3.0.jar https://github.com/freerouting/freerouting/releases/download/v2.3.0/freerouting-2.3.0.jar
sha256sum Tools/ExternalRouters/Freerouting/Upstream/freerouting-2.3.0.jar
```

Freerouting consumes Specctra DSN and emits SES plus JSON DRC evidence. The
benchmark adapter preserves the compiler's NAND hypergraph, but supplies a
deterministic synthetic PCB placement and PCB design rules. A clean external
result is therefore an abstract topology/capacity result, not proof of legal
Redstone placement, powered connectivity, support, required air, repeater
strength, materialization, or Minecraft truth-table behavior.

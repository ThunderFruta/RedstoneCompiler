# RedstoneCompiler Fabric harness

This is the sole runtime mod required by the compiler's local validation
server. It is server-only and deliberately has no Fabric API, Litematica,
WorldEdit, Carpet, or client dependency.

Build it with Gradle 9.5.1 and Java 25:

```sh
gradle build
```

Place the Fabric 26.2 server launcher at
`$RC_FABRIC_SERVER_ROOT/fabric-server-launch.jar` and accept the Minecraft
EULA there. When this project has been built, the compiler automatically copies
its JAR to `$RC_FABRIC_SERVER_ROOT/mods/redstonecompiler-harness.jar`, then
writes the private loopback token and 1,000-TPS configuration before launch.

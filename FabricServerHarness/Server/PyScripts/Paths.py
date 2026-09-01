"""Canonical locations for the ignored local Fabric server runtime."""

from __future__ import annotations

from pathlib import Path


RuntimeRoot = Path(__file__).resolve().parent.parent
HarnessRoot = RuntimeRoot.parent
RepositoryRoot = HarnessRoot.parent
LauncherPath = RuntimeRoot / "fabric-server-launch.jar"
HarnessJarPath = RuntimeRoot / "mods" / "redstonecompiler-harness.jar"
BuiltHarnessJarPath = (
    HarnessRoot / "build" / "libs" / "redstonecompiler-harness-1.0.0.jar"
)
HarnessConfigurationPath = (
    RuntimeRoot / "config" / "redstonecompiler-harness.json"
)
EulaPath = RuntimeRoot / "eula.txt"
ServerPropertiesPath = RuntimeRoot / "server.properties"
WorldPath = RuntimeRoot / "world"
FixturesPath = RuntimeRoot / "fixtures"
ManagerStatePath = RuntimeRoot / "redstonecompiler-server-manager.json"
ManagerLogPath = RuntimeRoot / "logs" / "redstonecompiler-server-manager.log"


def GetServerRoot() -> Path:
    """Return the one canonical local Fabric server runtime directory."""
    return RuntimeRoot

"""Fail-closed provenance and narrow CI evidence collection."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil


EvidenceNames = frozenset({
    "Summary.txt", "RawDump.txt", "AcceptanceManifest.json",
    "ArchiveManifest.json", "BenchmarkResult.json", "SHA256SUMS",
    "stdout.log", "stderr.log",
})
EvidenceSuffixes = (
    ".PhysicalDesign.json", ".RoutingFailure.json", ".PhysicalFixture.json",
)
ForbiddenParts = frozenset({
    "Runtime", "Server", "world", "world_nether", "world_the_end", "config",
    "secrets", "mods", "libraries", "logs",
})


def FileHash(PathValue: Path) -> str:
    """Hash file contents without recording environment or credentials."""
    return sha256(PathValue.read_bytes()).hexdigest()


def WriteJson(PathValue: Path, Value: object) -> None:
    PathValue.parent.mkdir(parents=True, exist_ok=True)
    PathValue.write_text(json.dumps(Value, indent=2, sort_keys=True) + "\n")


def ValidateProvenance(
    Provenance: dict, RepositoryRoot: Path, BuiltNative: Path, ExpectedCommit: str,
) -> None:
    """Require clean exact source, the just-built imported native, tracked templates."""
    Git = Provenance["Git"]
    if Git.get("Revision") != ExpectedCommit or Git.get("Dirty") is not False:
        raise ValueError("CI requires the exact expected commit and a clean checkout")
    Native = Provenance["NativeExtension"]
    NativePath = (RepositoryRoot / str(Native.get("Path", ""))).resolve()
    if (
        Native.get("Loaded") is not True
        or Native.get("WithinRepository") is not True
        or not NativePath.is_relative_to(RepositoryRoot / "RedstoneCompiler")
        or not BuiltNative.is_file()
        or not NativePath.is_file()
        or Native.get("Sha256") != FileHash(BuiltNative)
        or FileHash(NativePath) != FileHash(BuiltNative)
    ):
        raise ValueError("imported native extension does not match this checkout's build")
    Templates = Provenance["PhysicalTemplates"]
    if Templates.get("ResolutionSource") != "configured-child":
        raise ValueError("configured Python did not resolve the template catalog")
    for Name in ("Input", "Nand", "Output"):
        Record = Templates.get("Templates", {}).get(Name, {})
        ExpectedPath = RepositoryRoot / "Assets/Templates" / f"{Name}.litematic"
        if (
            Record.get("WithinRepository") is not True
            or Record.get("Path") != ExpectedPath.relative_to(RepositoryRoot).as_posix()
            or Record.get("Sha256") != FileHash(ExpectedPath)
        ):
            raise ValueError("CI must use the canonical tracked template files")


def FabricPrerequisites(RuntimeRoot: Path) -> dict:
    """Inspect an already-authorized runtime; never create it or accept its EULA."""
    if RuntimeRoot.is_symlink() or any(Parent.is_symlink() for Parent in RuntimeRoot.parents):
        raise ValueError("Fabric runtime must not traverse symlinks")
    RuntimeRoot = RuntimeRoot.resolve()
    Required = {
        "Launcher": RuntimeRoot / "fabric-server-launch.jar",
        "Harness": RuntimeRoot / "mods/redstonecompiler-harness.jar",
    }
    for Name, PathValue in Required.items():
        if not PathValue.is_file() or PathValue.is_symlink() or any(Parent.is_symlink() for Parent in PathValue.parents):
            raise ValueError(f"missing preprovisioned Fabric prerequisite: {Name}")
    Eula = RuntimeRoot / "eula.txt"
    if not Eula.is_file() or Eula.is_symlink():
        raise ValueError("Minecraft EULA has not been accepted by the runtime owner")
    Values = [Line.strip().lower() for Line in Eula.read_text().splitlines()]
    if "eula=true" not in Values:
        raise ValueError("Minecraft EULA has not been accepted by the runtime owner")
    LockPath = RuntimeRoot.parent / "runtime-lock.json"
    if not LockPath.is_file() or LockPath.is_symlink():
        raise ValueError("missing administrator-verified Fabric runtime inventory")
    Lock = json.loads(LockPath.read_text())
    if not isinstance(Lock, dict) or (
        Lock.get("ProvisioningValidated") is not True
        or Lock.get("MinecraftVersion") != "26.2"
        or Lock.get("FabricLoaderVersion") != "0.19.3"
    ):
        raise ValueError("Fabric inventory requires validated fixed runtime versions")
    ExpectedJars = Lock.get("Jars")
    if not isinstance(ExpectedJars, dict) or len(ExpectedJars) < 2 or "fabric-server-launch.jar" not in ExpectedJars:
        raise ValueError("Fabric inventory is incomplete")
    if any(PathValue.is_symlink() for PathValue in RuntimeRoot.rglob("*")):
        raise ValueError("dedicated Fabric runtime must not contain symlinks")
    ActualJars = {}
    for Jar in sorted(RuntimeRoot.rglob("*.jar")):
        if Jar.is_symlink() or any(Parent.is_symlink() for Parent in Jar.parents):
            raise ValueError("Fabric dependency JARs must not traverse symlinks")
        Relative = Jar.relative_to(RuntimeRoot).as_posix()
        if Relative == "mods/redstonecompiler-harness.jar":
            continue  # The current-commit harness has its own separately verified identity.
        if not Jar.is_file():
            raise ValueError("Fabric dependency JAR is not a regular file")
        ActualJars[Relative] = FileHash(Jar)
    if ActualJars != ExpectedJars:
        raise ValueError("Fabric dependency JARs differ from the provisioned inventory")
    return {
        **{Name: {"Sha256": FileHash(PathValue)} for Name, PathValue in Required.items()},
        "RuntimeLockSha256": FileHash(LockPath), "RuntimeJars": ActualJars,
    }


def CollectAcceptanceEvidence(Source: Path, Destination: Path) -> list[str]:
    """Copy only bounded, allowlisted run evidence, refusing symlink traversal."""
    if Source.is_symlink() or any(Parent.is_symlink() for Parent in Source.parents):
        raise ValueError("acceptance evidence source must not traverse symlinks")
    Source = Source.resolve()
    Copied = []
    TotalBytes = 0
    if not Source.is_dir():
        return Copied
    for PathValue in sorted(Source.rglob("*")):
        Relative = PathValue.relative_to(Source)
        if (
            any(Part.startswith(".") or Part in ForbiddenParts for Part in Relative.parts)
            or PathValue.is_symlink()
            or any(Parent.is_symlink() for Parent in PathValue.parents if Parent != Source)
            or not PathValue.is_file()
            or not PathValue.resolve().is_relative_to(Source)
            or not (
                PathValue.name in EvidenceNames
                or PathValue.name.endswith(EvidenceSuffixes)
            )
        ):
            continue
        TotalBytes += PathValue.stat().st_size
        if TotalBytes > 100 * 1024 * 1024:
            raise ValueError("allowlisted acceptance evidence exceeds 100 MiB")
        Target = Destination / Relative
        Target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PathValue, Target)
        Copied.append(Relative.as_posix())
    return Copied


def SealEvidence(Root: Path) -> None:
    """Checksum the published subset, independently from the full harness archive."""
    Files = {
        PathValue.relative_to(Root).as_posix(): FileHash(PathValue)
        for PathValue in sorted(Root.rglob("*"))
        if PathValue.is_file() and not PathValue.is_symlink()
        and PathValue.name != "EvidenceIndex.json"
    }
    WriteJson(Root / "EvidenceIndex.json", {"Files": Files})

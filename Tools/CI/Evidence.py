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
    RuntimeRoot = RuntimeRoot.resolve()
    Required = {
        "Launcher": RuntimeRoot / "fabric-server-launch.jar",
        "Harness": RuntimeRoot / "mods/redstonecompiler-harness.jar",
    }
    for Name, PathValue in Required.items():
        if not PathValue.is_file() or PathValue.is_symlink():
            raise ValueError(f"missing preprovisioned Fabric prerequisite: {Name}")
    Eula = RuntimeRoot / "eula.txt"
    if not Eula.is_file() or Eula.is_symlink():
        raise ValueError("Minecraft EULA has not been accepted by the runtime owner")
    Values = [Line.strip().lower() for Line in Eula.read_text().splitlines()]
    if "eula=true" not in Values:
        raise ValueError("Minecraft EULA has not been accepted by the runtime owner")
    return {Name: {"Sha256": FileHash(PathValue)} for Name, PathValue in Required.items()}


def CollectAcceptanceEvidence(Source: Path, Destination: Path) -> list[str]:
    """Copy only bounded, allowlisted run evidence, refusing symlink traversal."""
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

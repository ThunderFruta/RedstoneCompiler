"""Packaging boundary checks for the native routing extension and stub."""

from pathlib import Path

import RedstoneCompiler.RustRouting as RustRouting


RepositoryRoot = Path(__file__).resolve().parents[2]


def test_native_extension_and_editor_stub_share_the_package_boundary() -> None:
    ModulePath = Path(RustRouting.__file__).resolve()
    StubPath = RepositoryRoot / "RedstoneCompiler/RustRouting.pyi"

    assert ModulePath.parent == StubPath.parent
    assert ModulePath.suffix == ".so"
    assert StubPath.is_file()
    Stub = StubPath.read_text(encoding="utf-8")
    assert "def __getattr__(Name: str) -> Any" in Stub
    for Export in (
        "ValidateMchprsFixture",
        "RoutingContext",
        "SolveLeaseDomainsBounded",
    ):
        assert hasattr(RustRouting, Export), Export

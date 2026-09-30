"""Independent official-release facts and pre-Gradle Java identity failures."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from Tools.CI.JavaToolchain import ValidateTemurinIdentity


# Authoritative Linux x64 JDK metadata, not values derived from the checker:
# https://api.adoptium.net/v3/assets/feature_releases/25/ga
# https://github.com/adoptium/temurin25-binaries/releases/tag/jdk-25.0.4.1%2B1
OfficialSelector = "25.0.4+101.0.LTS"
OfficialIdentity = {
    "IMPLEMENTOR": "Eclipse Adoptium",
    "JAVA_VERSION": "25.0.4.1",
    "JAVA_RUNTIME_VERSION": "25.0.4.1+1-LTS",
    "OS_ARCH": "x86_64",
}
Root = Path(__file__).resolve().parents[2]


def ReleaseText(Identity):
    return "\n".join(f"{Key}={json.dumps(Value)}" for Key, Value in Identity.items()) + "\n"


def test_exact_vendor_metadata_selector_and_installed_identity_pass():
    Result = ValidateTemurinIdentity(ReleaseText(OfficialIdentity), OfficialSelector)
    assert Result == {"Selector": OfficialSelector, **OfficialIdentity}


@pytest.mark.parametrize("Selector", ["25.0.4.1+1", "25", "25.0.4+1", "25.0.4+7.0.LTS", ""])
def test_wrong_or_floating_action_resolution_is_rejected(Selector):
    with pytest.raises(ValueError):
        ValidateTemurinIdentity(ReleaseText(OfficialIdentity), Selector)


@pytest.mark.parametrize("Key,Value", [
    ("IMPLEMENTOR", "Ubuntu"),
    ("JAVA_VERSION", "25.0.4"),
    ("JAVA_RUNTIME_VERSION", "25.0.4.1+2-LTS"),
    ("OS_ARCH", "aarch64"),
])
def test_wrong_installed_vendor_version_build_or_architecture_is_rejected(Key, Value):
    Identity = {**OfficialIdentity, Key: Value}
    with pytest.raises(ValueError):
        ValidateTemurinIdentity(ReleaseText(Identity), OfficialSelector)


@pytest.mark.parametrize("Key", sorted(OfficialIdentity))
def test_missing_release_fact_cannot_pass(Key):
    Identity = {Name: Value for Name, Value in OfficialIdentity.items() if Name != Key}
    with pytest.raises(ValueError):
        ValidateTemurinIdentity(ReleaseText(Identity), OfficialSelector)


def test_duplicate_release_fact_cannot_hide_another_identity():
    Text = ReleaseText(OfficialIdentity) + 'IMPLEMENTOR="Ubuntu"\n'
    with pytest.raises(ValueError):
        ValidateTemurinIdentity(Text, OfficialSelector)


@pytest.mark.parametrize("Valid", [True, False])
def test_public_attestation_command_fails_before_gradle_for_wrong_identity(tmp_path, Valid):
    Identity = {**OfficialIdentity, "IMPLEMENTOR": "Eclipse Adoptium" if Valid else "Ubuntu"}
    (tmp_path / "release").write_text(ReleaseText(Identity))
    Environment = {**os.environ, "JAVA_HOME": str(tmp_path), "RC_SETUP_JAVA_VERSION": OfficialSelector}
    Result = subprocess.run([sys.executable, "Tools/CI/JavaToolchain.py"], cwd=Root,
                            env=Environment, capture_output=True, text=True)
    assert (Result.returncode == 0) is Valid
    if Valid:
        assert json.loads(Result.stdout) == {"Selector": OfficialSelector, **OfficialIdentity}


def test_workflow_requests_official_exact_semver_and_attests_before_gradle():
    Workflow = (Root / ".github/workflows/deterministic.yml").read_text()
    Harness = Workflow.split("  harness:\n", 1)[1]
    Selector = re.search(r"java-version: '([^']+)'", Harness).group(1)
    # SemVer core is exactly three numeric components; build identity is explicit.
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\+[0-9]+\.[0-9]+\.LTS", Selector)
    assert Selector == OfficialSelector
    assert "distribution: temurin" in Harness and "force-download: true" in Harness
    assert "id: java" in Harness
    assert "RC_SETUP_JAVA_VERSION: ${{ steps.java.outputs.version }}" in Harness
    Attestation = Harness.index("run: python Tools/CI/JavaToolchain.py")
    Gradle = Harness.index("run: python Tools/CI/RunChecks.py --tier harness")
    assert Attestation < Gradle
    assert "continue-on-error" not in Harness

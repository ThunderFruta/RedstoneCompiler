"""Fail closed on the hosted harness's exact vendor and Java release identity."""

import json
import os
from pathlib import Path


ExpectedSelector = "25.0.4+101.0.LTS"
ExpectedRelease = {
    "IMPLEMENTOR": "Eclipse Adoptium",
    "JAVA_VERSION": "25.0.4.1",
    "JAVA_RUNTIME_VERSION": "25.0.4.1+1-LTS",
    "OS_ARCH": "x86_64",
}


def ValidateTemurinIdentity(ReleaseText: str, ResolvedVersion: str) -> dict:
    """Require both the action resolution and installed JDK release facts."""
    if ResolvedVersion != ExpectedSelector:
        raise ValueError("setup-java did not resolve the pinned Temurin build")
    Identity = {}
    for Line in ReleaseText.splitlines():
        Key, Separator, Value = Line.partition("=")
        if Separator and Key in ExpectedRelease:
            if Key in Identity:
                raise ValueError("Duplicate JDK release identity field")
            Identity[Key] = json.loads(Value)
    if Identity != ExpectedRelease:
        raise ValueError("Installed JDK release identity does not match pinned Temurin")
    return {"Selector": ResolvedVersion, **Identity}


def Main() -> int:
    JavaHome = os.environ.get("JAVA_HOME")
    if not JavaHome:
        raise ValueError("JAVA_HOME is required for Temurin attestation")
    Identity = ValidateTemurinIdentity(
        (Path(JavaHome) / "release").read_text(),
        os.environ.get("RC_SETUP_JAVA_VERSION", ""),
    )
    print(json.dumps(Identity, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(Main())

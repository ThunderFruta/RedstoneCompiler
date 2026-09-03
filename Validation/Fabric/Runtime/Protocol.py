"""Authenticated loopback requests for the local Fabric harness."""

from __future__ import annotations

import json
import socket
from time import monotonic, sleep
from typing import Any

from .Paths import HarnessConfigurationPath


def ReadHarnessConfiguration() -> dict[str, object]:
    """Read the runtime-only token and control endpoint configuration."""
    Configuration = json.loads(HarnessConfigurationPath.read_text(encoding="utf-8"))
    if not isinstance(Configuration, dict):
        raise RuntimeError("invalid harness control configuration")
    if not isinstance(Configuration.get("Token"), str):
        raise RuntimeError("harness control configuration has no token")
    if not isinstance(Configuration.get("Port"), int):
        raise RuntimeError("harness control configuration has no port")
    return Configuration


def SendRequest(
    Request: dict[str, object],
    *,
    TimeoutSeconds: float = 10.0,
) -> dict[str, Any]:
    """Send one token-authenticated JSON-line request to the harness."""
    Configuration = ReadHarnessConfiguration()
    with socket.create_connection(
        ("127.0.0.1", int(Configuration["Port"])),
        timeout=TimeoutSeconds,
    ) as Connection:
        Stream = Connection.makefile("rwb")
        Stream.write(json.dumps({
            "Token": Configuration["Token"],
            **Request,
        }, sort_keys=True).encode("utf-8") + b"\n")
        Stream.flush()
        Response = Stream.readline()
    if not Response:
        raise RuntimeError("harness closed the control connection")
    Parsed = json.loads(Response.decode("utf-8"))
    if not isinstance(Parsed, dict):
        raise RuntimeError("harness returned a non-object response")
    return Parsed


def WaitForReady(TimeoutSeconds: float) -> dict[str, Any]:
    """Wait until the started server can execute an authenticated read."""
    Deadline = monotonic() + TimeoutSeconds
    LastError: Exception | None = None
    while monotonic() < Deadline:
        try:
            Response = SendRequest({
                "Action": "WorldReadBlocks",
                "Positions": [],
            }, TimeoutSeconds=2.0)
        except (OSError, ValueError, RuntimeError) as Error:
            LastError = Error
        else:
            if Response.get("Status") == "observed":
                return Response
            if Response.get("Error") == "unauthenticated-control-request":
                raise RuntimeError(
                    "Fabric control token was rejected; another server may own the port",
                )
            LastError = RuntimeError(
                "Fabric harness is not ready: " + json.dumps(Response, sort_keys=True),
            )
        sleep(0.2)
    raise RuntimeError(f"Fabric harness did not become ready: {LastError}")

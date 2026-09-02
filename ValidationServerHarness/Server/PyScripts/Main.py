"""Command interface for the canonical local Fabric server runtime."""

from __future__ import annotations

import argparse
import json
import sys

from Process import (
    ClearServerWorld,
    CurrentStatus,
    StartServer,
    StopServer,
)


def BuildParser() -> argparse.ArgumentParser:
    """Build the explicit lifecycle command parser."""
    Parser = argparse.ArgumentParser(
        description="Start and manage the canonical RedstoneCompiler validation server.",
    )
    Parser.add_argument(
        "Action",
        choices=("start", "stop", "status", "clear"),
        nargs="?",
        default="status",
    )
    return Parser


def Main(Arguments: list[str] | None = None) -> int:
    """Execute one lifecycle action against the fixed canonical runtime root."""
    Parsed = BuildParser().parse_args(
        sys.argv[1:] if Arguments is None else Arguments,
    )
    try:
        if Parsed.Action == "start":
            Result = StartServer()
        elif Parsed.Action == "stop":
            Result = StopServer()
        elif Parsed.Action == "clear":
            Result = ClearServerWorld()
        else:
            Result = CurrentStatus()
    except (OSError, RuntimeError, ValueError) as Error:
        print(f"Fabric server manager failed: {Error}", file=sys.stderr)
        return 1
    print(json.dumps(Result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(Main())

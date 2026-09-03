#!/usr/bin/env python3
"""Start, stop, inspect, or live-clear the canonical validation server."""

from __future__ import annotations

import os
from pathlib import Path
import sys


RepositoryRoot = Path(__file__).resolve().parents[2]
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))

from Validation.Fabric import ResolveFabricServerRoot


ServerRoot = ResolveFabricServerRoot()
RuntimeScripts = RepositoryRoot / "Validation/Fabric/Runtime"
RuntimeMain = RuntimeScripts / "Main.py"


def GuidedArguments() -> list[str]:
    """Ask for one explicit Fabric server lifecycle action."""
    print("RedstoneCompiler validation server")
    print(
        "1) Start or repair server\n"
        "2) Stop server\n"
        "3) Show status\n"
        "4) Clear every persisted non-air simulation block (keeps server running)\n"
        "0) Exit",
    )
    Choice = input("Choose an action [3]: ").strip() or "3"
    if Choice == "4":
        Confirmation = input(
            "This clears every non-air block in saved simulation chunks while "
            "the server stays running. "
            "Type CLEAR to continue: ",
        ).strip()
        if Confirmation != "CLEAR":
            raise EOFError
        return ["clear"]
    Actions = {
        "1": ["start"],
        "2": ["stop"],
        "3": ["status"],
    }
    if Choice == "0":
        raise EOFError
    if Choice not in Actions:
        raise ValueError("choose 0, 1, 2, 3, or 4")
    return Actions[Choice]


def Main(Arguments: list[str] | None = None) -> int:
    """Dispatch the one Fabric lifecycle command to its runtime modules."""
    if not RuntimeMain.is_file():
        print(
            f"Fabric server runtime manager is missing: {RuntimeMain}",
            file=sys.stderr,
        )
        return 1
    os.environ["RC_FABRIC_SERVER_ROOT"] = str(ServerRoot)
    sys.path.insert(0, str(RuntimeScripts))
    from Validation.Fabric.Runtime.Main import Main as RuntimeEntryPoint

    RawArguments = list(sys.argv[1:] if Arguments is None else Arguments)
    if not RawArguments:
        try:
            RawArguments = GuidedArguments()
        except (EOFError, KeyboardInterrupt):
            print("No action selected. Run with --help for explicit commands.")
            return 2
        except ValueError as Error:
            print(f"Invalid Fabric server action: {Error}", file=sys.stderr)
            return 2
    return RuntimeEntryPoint(RawArguments)


if __name__ == "__main__":
    raise SystemExit(Main())

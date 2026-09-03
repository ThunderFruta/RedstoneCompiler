#!/usr/bin/env python3
"""Run authenticated Minecraft commands against the canonical Fabric server."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Callable


RepositoryRoot = Path(__file__).resolve().parents[2]
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))

from Validation.Fabric import FabricServerConfiguration, FabricServerSupervisor, ResolveFabricServerRoot


def BuildParser() -> argparse.ArgumentParser:
    """Build the command-console argument parser."""
    Parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Start the server first with ControlFabricServer.py start. Omit "
            "--command for an interactive console. In that console, use "
            ":help or :quit; every other non-empty line is sent to Minecraft."
        ),
    )
    Parser.add_argument(
        "--server-root",
        type=Path,
        default=ResolveFabricServerRoot(),
        help=(
            "running Fabric server directory "
            f"(default: {ResolveFabricServerRoot()})"
        ),
    )
    Parser.add_argument(
        "--command",
        metavar="COMMAND",
        help="run exactly one Minecraft command and exit",
    )
    return Parser


def NormalizeConsoleCommand(Command: str) -> str:
    """Validate one single-line Minecraft command before dispatch."""
    Normalized = Command.strip()
    if not Normalized:
        raise ValueError("a Minecraft command is required")
    if "\n" in Normalized or "\r" in Normalized:
        raise ValueError("Minecraft commands must be one line")
    return Normalized


def ExecuteConsoleCommand(
    Supervisor: FabricServerSupervisor,
    Command: str,
) -> dict[str, object]:
    """Send one privileged command through the authenticated harness channel."""
    Normalized = NormalizeConsoleCommand(Command)
    Result = Supervisor.ControlRunningServer(
        Action="WorldRunCommand",
        Command=Normalized,
    )
    return {
        "Command": Normalized,
        "Status": Result.Status,
        "RuntimeSeconds": Result.RuntimeSeconds,
        "Diagnostics": Result.Diagnostics,
    }


def RunInteractiveConsole(
    Supervisor: FabricServerSupervisor,
    ReadLine: Callable[[str], str] = input,
) -> int:
    """Run a human-facing command loop without attaching to Java stdin."""
    print("RedstoneCompiler Fabric command console")
    print("Enter a Minecraft command. Use :help for console help or :quit to exit.")
    while True:
        try:
            Command = ReadLine("fabric> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if Command.strip() in {":quit", ":exit"}:
            return 0
        if Command.strip() == ":help":
            print(
                "Commands are sent with server console permissions. "
                "Use /command or command; :quit exits this console.",
            )
            continue
        if not Command.strip():
            continue
        try:
            Report = ExecuteConsoleCommand(Supervisor, Command)
        except ValueError as Error:
            print(f"Invalid Minecraft command: {Error}", file=sys.stderr)
            continue
        print(Report)


def Main(Arguments: list[str] | None = None) -> int:
    """Run one command or open the authenticated interactive command console."""
    Parsed = BuildParser().parse_args(
        sys.argv[1:] if Arguments is None else Arguments,
    )
    Supervisor = FabricServerSupervisor(
        FabricServerConfiguration(Root=Parsed.server_root.expanduser().resolve()),
    )
    if Parsed.command is None:
        return RunInteractiveConsole(Supervisor)
    try:
        Report = ExecuteConsoleCommand(Supervisor, Parsed.command)
    except ValueError as Error:
        print(f"Invalid Minecraft command: {Error}", file=sys.stderr)
        return 2
    print(Report)
    return 0 if Report["Status"] == "command-complete" else 1


if __name__ == "__main__":
    raise SystemExit(Main())

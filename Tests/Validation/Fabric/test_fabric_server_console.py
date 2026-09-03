"""Tests for the authenticated interactive Fabric command console."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from Tools.Fabric import ConsoleFabricServer


class FabricServerConsoleTests(unittest.TestCase):
    def testConsoleRejectsBlankAndMultilineCommands(self) -> None:
        with self.assertRaisesRegex(ValueError, "command is required"):
            ConsoleFabricServer.NormalizeConsoleCommand("   ")
        with self.assertRaisesRegex(ValueError, "one line"):
            ConsoleFabricServer.NormalizeConsoleCommand("say one\nsay two")

    def testConsoleExecutesOneCommandThroughTheAuthenticatedBoundary(self) -> None:
        Supervisor = Mock()
        Supervisor.ControlRunningServer.return_value = SimpleNamespace(
            Status="command-complete",
            RuntimeSeconds=0.125,
            Diagnostics={"CommandExecuted": True},
        )

        Report = ConsoleFabricServer.ExecuteConsoleCommand(
            Supervisor,
            "/say hello",
        )

        Supervisor.ControlRunningServer.assert_called_once_with(
            Action="WorldRunCommand",
            Command="/say hello",
        )
        self.assertEqual(Report["Status"], "command-complete")
        self.assertEqual(Report["Diagnostics"], {"CommandExecuted": True})

    def testConsoleOneShotUsesTheRequestedServerRoot(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            Output = StringIO()
            with patch(
                "Tools.Fabric.ConsoleFabricServer.FabricServerSupervisor",
            ) as SupervisorConstructor, redirect_stdout(Output):
                SupervisorConstructor.return_value.ControlRunningServer.return_value = (
                    SimpleNamespace(
                        Status="command-complete",
                        RuntimeSeconds=0.25,
                        Diagnostics={},
                    )
                )
                ExitCode = ConsoleFabricServer.Main([
                    "--server-root",
                    str(Root),
                    "--command",
                    "time query daytime",
                ])

        Configuration = SupervisorConstructor.call_args.args[0]
        self.assertEqual(Configuration.Root, Root.resolve())
        self.assertEqual(ExitCode, 0)
        self.assertIn("'Command': 'time query daytime'", Output.getvalue())

    def testInteractiveConsoleLeavesAfterItsMetaCommand(self) -> None:
        Supervisor = Mock()
        Supervisor.ControlRunningServer.return_value = SimpleNamespace(
            Status="command-complete",
            RuntimeSeconds=0.125,
            Diagnostics={},
        )
        Commands = iter(["/say hello", ":quit"])
        Output = StringIO()

        with redirect_stdout(Output):
            ExitCode = ConsoleFabricServer.RunInteractiveConsole(
                Supervisor,
                lambda Prompt: next(Commands),
            )

        self.assertEqual(ExitCode, 0)
        Supervisor.ControlRunningServer.assert_called_once_with(
            Action="WorldRunCommand",
            Command="/say hello",
        )
        self.assertIn("Fabric command console", Output.getvalue())

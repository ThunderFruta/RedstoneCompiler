from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from Compiler.Main import ParsePromptPath, RunPytest


class MainPathTests(unittest.TestCase):
    def testParsePromptPathAcceptsQuotedAbsolutePath(self) -> None:
        Expected = Path("/mnt/Projects/RedstoneCompiler/Examples/RippleCarryAdder4.sv")

        self.assertEqual(ParsePromptPath(f"'{Expected}'"), Expected)
        self.assertEqual(ParsePromptPath(f'"{Expected}"'), Expected)

    def testParsePromptPathPreservesUnquotedPath(self) -> None:
        Expected = Path("Examples/FullAdder.sv")

        self.assertEqual(ParsePromptPath(str(Expected)), Expected)

    @patch("Compiler.Main.subprocess.run")
    def testRunPytestUsesActiveInterpreterAndRepositoryRoot(self, Run) -> None:
        Run.return_value.returncode = 0

        self.assertEqual(RunPytest(), 0)
        Run.assert_called_once_with(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "Tests",
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=False,
        )

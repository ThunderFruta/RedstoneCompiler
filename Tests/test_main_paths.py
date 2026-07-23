from pathlib import Path
import unittest

from Compiler.Main import ParsePromptPath


class MainPathTests(unittest.TestCase):
    def testParsePromptPathAcceptsQuotedAbsolutePath(self) -> None:
        Expected = Path("/mnt/Projects/RedstoneCompiler/Examples/RippleCarryAdder4.sv")

        self.assertEqual(ParsePromptPath(f"'{Expected}'"), Expected)
        self.assertEqual(ParsePromptPath(f'"{Expected}"'), Expected)

    def testParsePromptPathPreservesUnquotedPath(self) -> None:
        Expected = Path("Examples/FullAdder.sv")

        self.assertEqual(ParsePromptPath(str(Expected)), Expected)

#!/usr/bin/env python3
"""
Example invocation script for the scaffold.
"""

from Compiler.Main import Main


def Run() -> int:
    return Main(
        [
            "--input",
            "Examples/FullAdder.sv",
            "--output",
            "Output/FullAdder/FullAdder.litematic",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(Run())

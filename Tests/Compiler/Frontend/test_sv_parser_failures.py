"""Negative contracts for unsupported or malformed SystemVerilog input."""

from pathlib import Path

import pytest

from Compiler.Frontend.Sv import ParseSvToNetlist


@pytest.mark.parametrize(
    ("Source", "TopModule", "Message"),
    (
        (
            "module Top(input logic [3:] a, output logic y); "
            "assign y = a[0]; endmodule",
            None,
            "Only scalar signals are supported",
        ),
        (
            "module Top(input logic a, output logic y); "
            "always_comb y = a; endmodule",
            None,
            "Output ports are not assigned: y",
        ),
        (
            "module Top(input logic a, b, output logic y); "
            "assign y = a; assign y = b; endmodule",
            None,
            "Signal has multiple continuous assignments: y",
        ),
        (
            "module Top(input logic a, output logic y); "
            "assign y = a; endmodule",
            "Missing",
            "Top module 'Missing' was not found",
        ),
    ),
)
def test_invalid_systemverilog_fails_closed(
    tmp_path: Path,
    Source: str,
    TopModule: str | None,
    Message: str,
) -> None:
    SourcePath = tmp_path / "Invalid.sv"
    SourcePath.write_text(Source, encoding="utf-8")

    with pytest.raises(ValueError, match=Message):
        ParseSvToNetlist(
            InputPath=SourcePath,
            TopModule=TopModule,
            Workdir=tmp_path / "Frontend",
        )

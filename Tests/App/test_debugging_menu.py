"""Guided debugging actions follow the public menu and compiler flag contracts."""

from pathlib import Path

import pytest

import App.Main as RootMain
from App import CompilerCli
from Compilation.Hooks import CompilerHooks


def Answers(monkeypatch, Values):
    Responses = iter(Values)
    monkeypatch.setattr('builtins.input', lambda Prompt: next(Responses))


def test_root_debugging_and_more_options_navigation(monkeypatch, capsys):
    Answers(monkeypatch, ['4', '5', '5', '5', '6'])
    Defaults = dict(CompilerCli.BuiltInDefaults)
    Result, Returned = RootMain.GuidedMenu(Defaults, Path('/tmp/unused-defaults.json'))
    assert Result is None
    assert Returned == Defaults
    Output = capsys.readouterr().out
    for Label in ['4. Debugging', '5. More options', '6. Exit',
                  '1. Compile with compiler hooks', 'Configure defaults']:
        assert Label in Output


@pytest.mark.parametrize('Selection,Expected', [('', []), ('physical,validation', ['physical', 'validation'])])
def test_debug_compile_forwards_stage_selection_to_public_cli(monkeypatch, tmp_path, Selection, Expected):
    Source = tmp_path / 'Design.sv'
    Answers(monkeypatch, ['4', '1', str(Source), '', str(tmp_path), 'Design', 'n', Selection])
    Arguments, _ = RootMain.GuidedMenu(dict(CompilerCli.BuiltInDefaults), tmp_path / 'defaults.json')
    Parsed = RootMain.BuildParser().parse_args(Arguments)
    assert Parsed.input == Source
    assert Parsed.compiler_hook_stage == Expected
    assert Parsed.compiler_hooks is (not Expected)
    assert '--push' not in Arguments


def test_saved_trace_diagnosis_and_telemetry_inspection_are_read_only(monkeypatch, tmp_path, capsys):
    Trace = tmp_path / 'Trace.json'
    Hooks = CompilerHooks()
    Hooks.Outcome = 'failed'
    Hooks.Emit('frontend.parse', 'failed', ErrorType='ValueError')
    Hooks.Save(Trace)
    Hooks.Close()
    Telemetry = tmp_path / 'RoutingTelemetry.txt'
    Telemetry.write_text('CPU summary: retained observation\n')
    Before = {File.name: File.read_bytes() for File in [Trace, Telemetry]}
    Answers(monkeypatch, ['2', str(Trace), '3', str(tmp_path), '5'])
    assert RootMain.DebuggingMenu({}, tmp_path / 'defaults.json') is None
    Output = capsys.readouterr().out
    assert 'frontend.parse' in Output
    assert '"Outcome": "failed"' in Output
    assert 'CPU summary: retained observation' in Output
    assert Before == {File.name: File.read_bytes() for File in [Trace, Telemetry]}


def test_bad_trace_and_missing_telemetry_return_to_debugging_menu(monkeypatch, tmp_path, capsys):
    Trace = tmp_path / 'Bad.json'
    Trace.write_text('{}')
    Answers(monkeypatch, ['2', str(Trace), '3', str(tmp_path), 'unrecognized', '5'])
    assert RootMain.DebuggingMenu({}, tmp_path / 'defaults.json') is None
    Output = capsys.readouterr().out
    assert Output.count('Debugging tool failed:') == 2
    assert 'Unknown menu option: unrecognized' in Output


def test_source_review_uses_existing_public_reviewer(monkeypatch, tmp_path, capsys):
    Answers(monkeypatch, ['4', '5'])
    assert RootMain.DebuggingMenu({}, tmp_path / 'defaults.json') is None
    Output = capsys.readouterr().out
    assert 'advisory' in Output.lower()
    assert 'LARGEST PYTHON DEFINITIONS' in Output

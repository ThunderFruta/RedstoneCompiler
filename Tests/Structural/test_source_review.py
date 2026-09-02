"""Advisory source-review command contracts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


RepositoryRoot = Path(__file__).resolve().parents[2]
ScriptPath = RepositoryRoot / "Scripts/Routing/ReviewSourceStructure.py"
Spec = importlib.util.spec_from_file_location("ReviewSourceStructure", ScriptPath)
assert Spec is not None and Spec.loader is not None
ReviewModule = importlib.util.module_from_spec(Spec)
sys.modules[Spec.name] = ReviewModule
Spec.loader.exec_module(ReviewModule)


def test_source_review_is_advisory_and_deterministic(capsys) -> None:
    First = ReviewModule.BuildSourceReview(RepositoryRoot)
    Second = ReviewModule.BuildSourceReview(RepositoryRoot)

    assert First == Second
    assert First["Status"] == "advisory"
    assert First["ReviewTargets"]["PythonDefinitionSpanLines"] == 1_000
    assert First["Ownership"]
    assert First["LargestFiles"]
    assert First["LargestPythonDefinitions"]
    assert "Passed" not in First
    assert "Violations" not in First

    assert ReviewModule.Main(("--format", "json")) == 0
    Output = json.loads(capsys.readouterr().out)
    assert Output == First

"""CPU accounting and durable observation contracts, without performance thresholds."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from App.Telemetry import AwaitTelemetryTask, EmitTelemetryEvent, RunTelemetryTask, TelemetryWork
from App.TelemetryObserver import MeasureSample, ReadProcessTree, ReadStat, SummarizeTelemetry


def Stat(Ticks=0, ChildTicks=0, Name="python", Start=1):
    return {"Ticks": Ticks, "ChildTicks": ChildTicks, "Name": Name, "Start": Start, "State": "R"}


def Snapshot(Processes, Threads=None):
    return {"Processes": Processes, "Threads": Threads or {}, "ReadErrors": 0}


def test_cpu_reaping_transfers_child_time_without_double_counting():
    Before = Snapshot({10: Stat(100), 20: Stat(40)})
    After = Snapshot({10: Stat(110, 50)})
    Result = MeasureSample(Before, After, 1, 10, 100)
    assert Result["CpuSeconds"] == pytest.approx(0.2)
    assert Result["ParentCpuSeconds"] == pytest.approx(0.1)
    assert Result["ChildCpuSeconds"] == pytest.approx(0.1)


def test_reaping_read_race_does_not_add_the_same_cpu_twice():
    Before = Snapshot({10: Stat(100), 20: Stat(40)})
    During = Snapshot({10: Stat(105)})
    assert MeasureSample(Before, During, 1, 10, 100)["CpuSeconds"] == 0
    After = Snapshot({10: Stat(110, 50)})
    assert MeasureSample(During, After, 1, 10, 100)["CpuSeconds"] == pytest.approx(0.2)


def test_native_thread_count_does_not_imply_cpu_activity():
    Main, Native1, Native2 = (10, 1, 10, 1), (10, 1, 11, 2), (10, 1, 12, 3)
    Before = Snapshot({10: Stat(100)}, {Main: Stat(80), Native1: Stat(10, Name="redstone-router"), Native2: Stat(10, Name="redstone-router")})
    After = Snapshot({10: Stat(170)}, {Main: Stat(130), Native1: Stat(30, Name="redstone-router"), Native2: Stat(10, Name="redstone-router")})
    Result = MeasureSample(Before, After, 0.5, 10, 100)
    assert Result["AverageCores"] == pytest.approx(1.4)
    assert Result["NativeThreads"] == 2
    assert Result["BusyNativeThreads"] == 1
    assert Result["NativeCpuSeconds"] == pytest.approx(0.2)
    assert Result["MainThreadCpuSeconds"] == pytest.approx(0.5)


def WriteStat(PathValue, Pid, Parent, Ticks, Name="worker (one)"):
    Fields = ["0"] * 40
    Fields[0], Fields[1], Fields[11], Fields[12], Fields[13], Fields[14], Fields[19] = "R", str(Parent), str(Ticks), "0", "0", "0", "1"
    PathValue.parent.mkdir(parents=True, exist_ok=True)
    PathValue.write_text(f"{Pid} ({Name}) " + " ".join(Fields))


def test_process_tree_excludes_observer_and_preserves_parenthesized_names(tmp_path):
    for Pid, Parent, Children in ((10, 1, "20 30"), (20, 10, ""), (30, 10, ""), (99, 1, "")):
        WriteStat(tmp_path / str(Pid) / "stat", Pid, Parent, 10)
        Task = tmp_path / str(Pid) / "task" / str(Pid)
        WriteStat(Task / "stat", Pid, Parent, 10)
        (Task / "children").write_text(Children)
    Tree = ReadProcessTree(10, 30, tmp_path)
    assert set(Tree["Processes"]) == {10, 20}
    assert ReadStat(tmp_path / "10/stat")["Name"] == "worker (one)"


def test_task_wrapper_and_wait_preserve_values_and_errors(tmp_path, monkeypatch):
    Events = tmp_path / "events.jsonl"
    Events.touch()
    monkeypatch.setenv("RC_ROUTING_TELEMETRY_EVENTS", str(Events))
    assert RunTelemetryTask("ok", lambda: 42) == 42
    def Fail():
        raise ValueError("original error")
    with pytest.raises(ValueError, match="original error"):
        RunTelemetryTask("bad", Fail)
    with pytest.raises(ValueError, match="original error"):
        AwaitTelemetryTask("bad", SimpleNamespace(result=lambda **_: Fail()), 1)
    with TelemetryWork("sample"):
        pass
    Records = [json.loads(Line) for Line in Events.read_text().splitlines()]
    assert [R["State"] for R in Records if R["Kind"] == "task"] == ["started", "completed", "started", "failed"]
    assert [R["Action"] for R in Records if R["Kind"] == "wait"] == ["begin", "end"]


def test_unavailable_journal_cannot_fail_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_ROUTING_TELEMETRY_EVENTS", str(tmp_path / "absent/events"))
    EmitTelemetryEvent("sample", Value=object())
    assert RunTelemetryTask("ok", lambda: 17) == 17


def test_partial_summary_recovers_samples_without_fabricating_completion(tmp_path):
    Row = MeasureSample(Snapshot({10: Stat(0)}), Snapshot({10: Stat(140)}), 1, 10, 100)
    Row.update(Stage="placement", Phase="Routing", Work="cluster layouts")
    (tmp_path / "RoutingTelemetry.samples.jsonl").write_text(json.dumps(Row) + '\n{"truncated":')
    (tmp_path / "RoutingTelemetry.events.jsonl").write_text(json.dumps({"Kind": "stage", "At": 1, "Pid": 10}) + "\n")
    Result = SummarizeTelemetry(tmp_path)
    assert Result["Status"] == "partial"
    assert Result["SampleCount"] == 1
    assert Result["AverageCores"] == pytest.approx(1.4)
    assert Result["Work"][0]["NearSingleCoreWallSeconds"] == 1
    assert not any(Stage["Phase"] == "Validation" for Stage in Result["Stages"])

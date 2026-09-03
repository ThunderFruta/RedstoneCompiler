"""Linux process-tree CPU sampling in a separate interpreter, outside the GIL."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
from time import monotonic, sleep


def ReadStat(PathValue: Path) -> dict[str, object]:
    """Read Linux stat without splitting a parenthesized command name on spaces."""
    Raw = PathValue.read_text()
    Left, Right = Raw.index("("), Raw.rindex(")")
    Fields = Raw[Right + 2:].split()
    return {
        "Name": Raw[Left + 1:Right], "State": Fields[0],
        "Parent": int(Fields[1]), "Ticks": int(Fields[11]) + int(Fields[12]),
        "ChildTicks": int(Fields[13]) + int(Fields[14]),
        "Start": int(Fields[19]),
    }


def ReadProcessTree(RootPid: int, ExcludedPid: int, ProcRoot: Path = Path("/proc")) -> dict:
    """Read only the target's descendants; exclude this observer and its CPU."""
    Pending = [RootPid]
    Processes = {}
    Threads = {}
    Errors = 0
    while Pending:
        Pid = Pending.pop()
        if Pid in Processes or Pid == ExcludedPid:
            continue
        try:
            Stat = ReadStat(ProcRoot / str(Pid) / "stat")
            Processes[Pid] = Stat
            for Task in (ProcRoot / str(Pid) / "task").iterdir():
                try:
                    ThreadStat = ReadStat(Task / "stat")
                    Tid = int(Task.name)
                    Threads[(Pid, Stat["Start"], Tid, ThreadStat["Start"])] = ThreadStat
                    Pending.extend(int(Value) for Value in (Task / "children").read_text().split())
                except (OSError, ValueError, IndexError):
                    Errors += 1
        except (OSError, ValueError, IndexError):
            Errors += 1
    return {"Processes": Processes, "Threads": Threads, "ReadErrors": Errors}


def MeasureSample(Previous: dict, Current: dict, Wall: float, RootPid: int, Hertz: int) -> dict:
    """Measure CPU deltas; child reaping transfers CPU rather than adding it twice."""
    def Total(Snapshot):
        return sum(int(P["Ticks"]) + int(P["ChildTicks"]) for P in Snapshot["Processes"].values())

    Accounted = Previous.get("AccountedTicks", Total(Previous))
    Current["AccountedTicks"] = max(Accounted, Total(Current))
    Cpu = max(0, Total(Current) - Accounted) / Hertz
    Root = Current["Processes"].get(RootPid, {})
    OldRoot = Previous["Processes"].get(RootPid, {})
    ParentCpu = max(0, int(Root.get("Ticks", 0)) - int(OldRoot.get("Ticks", 0))) / Hertz
    MainCpu = NativeCpu = 0.0
    BusyNative = BusyOther = 0
    BusyProcesses = set()
    ThreadStates = Counter()
    NativeThreads = 0
    for Key, Thread in Current["Threads"].items():
        Pid, _Start, Tid, _ThreadStart = Key
        PreviousThread = Previous["Threads"].get(Key)
        Delta = max(0, int(Thread["Ticks"]) - int(PreviousThread["Ticks"]) if PreviousThread else int(Thread["Ticks"])) / Hertz
        Native = str(Thread["Name"]).startswith("redstone-route")
        NativeThreads += int(Native)
        ThreadStates[str(Thread["State"])] += 1
        if Pid == RootPid and Tid == RootPid:
            MainCpu += Delta
        if Native:
            NativeCpu += Delta
            BusyNative += int(Delta > 0)
        else:
            BusyOther += int(Delta > 0)
        if Delta > 0:
            BusyProcesses.add(Pid)
    return {
        "WallSeconds": Wall, "CpuSeconds": Cpu, "AverageCores": Cpu / Wall if Wall else 0,
        "ParentCpuSeconds": ParentCpu, "ChildCpuSeconds": max(0.0, Cpu - ParentCpu),
        "MainThreadCpuSeconds": MainCpu, "NativeCpuSeconds": NativeCpu,
        "Processes": len(Current["Processes"]), "BusyProcesses": len(BusyProcesses),
        "Threads": len(Current["Threads"]), "NativeThreads": NativeThreads,
        "BusyNativeThreads": BusyNative, "BusyOtherThreads": BusyOther,
        "ThreadStates": dict(ThreadStates), "ReadErrors": Current["ReadErrors"],
    }


def ReadRecords(PathValue: Path) -> list[dict]:
    """Recover complete journal records even after an externally killed run."""
    if not PathValue.exists():
        return []
    Records = []
    for Line in PathValue.read_text().splitlines():
        try:
            Records.append(json.loads(Line))
        except ValueError:
            continue
    return Records


def SummarizeTelemetry(Directory: Path) -> dict:
    """Aggregate sampled observations without representing missing work as zero."""
    Samples = ReadRecords(Directory / "RoutingTelemetry.samples.jsonl")
    Events = ReadRecords(Directory / "RoutingTelemetry.events.jsonl")
    MetadataPath = Directory / "RoutingTelemetry.meta.json"
    Metadata = json.loads(MetadataPath.read_text()) if MetadataPath.exists() else {}
    Stages = {}
    Work = {}
    for Sample in Samples:
        for Mapping, Key in ((Stages, Sample["Stage"]), (Work, Sample.get("Work") or Sample["Stage"])):
            Entry = Mapping.setdefault(Key, {
                "Name": Key, "Phase": Sample["Phase"], "Samples": 0, "WallSeconds": 0.0,
                "CpuSeconds": 0.0, "ParentCpuSeconds": 0.0, "ChildCpuSeconds": 0.0,
                "MainThreadCpuSeconds": 0.0, "NativeCpuSeconds": 0.0,
                "NearSingleCoreWallSeconds": 0.0, "LowCpuWallSeconds": 0.0,
                "PeakBusyNativeThreads": 0, "PeakBusyProcesses": 0, "PeakThreads": 0,
            })
            Entry["Samples"] += 1
            for Field in ("WallSeconds", "CpuSeconds", "ParentCpuSeconds", "ChildCpuSeconds", "MainThreadCpuSeconds", "NativeCpuSeconds"):
                Entry[Field] += Sample[Field]
            if 0.5 <= Sample["AverageCores"] <= 1.5:
                Entry["NearSingleCoreWallSeconds"] += Sample["WallSeconds"]
            if Sample["AverageCores"] < 0.5:
                Entry["LowCpuWallSeconds"] += Sample["WallSeconds"]
            for Destination, Source in (("PeakBusyNativeThreads", "BusyNativeThreads"), ("PeakBusyProcesses", "BusyProcesses"), ("PeakThreads", "Threads")):
                Entry[Destination] = max(Entry[Destination], Sample[Source])
    for Entry in (*Stages.values(), *Work.values()):
        Entry["AverageCores"] = Entry["CpuSeconds"] / Entry["WallSeconds"] if Entry["WallSeconds"] else 0
    TaskStates = {}
    TaskCounts = Counter()
    Waits = {}
    WaitSeconds = 0.0
    QueuedAt = {}
    QueueSeconds = 0.0
    for Event in Events:
        if Event["Kind"] == "task":
            TaskStates[str(Event["Task"])] = str(Event["State"])
            TaskCounts[str(Event["State"])] += 1
            if Event["State"] == "queued":
                QueuedAt[Event["Task"]] = Event["At"]
            elif Event["State"] == "started" and Event["Task"] in QueuedAt:
                QueueSeconds += max(0.0, Event["At"] - QueuedAt.pop(Event["Task"]))
        if Event["Kind"] == "wait":
            Key = (Event["Pid"], Event["Task"])
            if Event["Action"] == "begin":
                Waits[Key] = Event["At"]
            elif Key in Waits:
                WaitSeconds += max(0.0, Event["At"] - Waits.pop(Key))
    TotalWall = sum(S["WallSeconds"] for S in Samples)
    TotalCpu = sum(S["CpuSeconds"] for S in Samples)
    Complete = any(E["Kind"] == "stop" for E in Events)
    Summary = {
        "SchemaVersion": "routing-cpu-telemetry-v1", "Status": "complete" if Complete else "partial",
        "Metadata": Metadata, "SampleCount": len(Samples),
        "SampledWallSeconds": TotalWall, "SampledCpuSeconds": TotalCpu,
        "AverageCores": TotalCpu / TotalWall if TotalWall else None,
        "PeakSampleCores": max((S["AverageCores"] for S in Samples), default=None),
        "MaximumSampleGapSeconds": max((S["WallSeconds"] for S in Samples), default=None),
        "ReadErrors": sum(S["ReadErrors"] for S in Samples),
        "Stages": list(Stages.values()), "Work": list(Work.values()),
        "InstrumentedTasks": {
            "Events": dict(TaskCounts), "FinalStates": dict(Counter(TaskStates.values())),
            "CompletedWaitSeconds": WaitSeconds, "QueueAndStartupSeconds": QueueSeconds,
            "PeakQueued": max((S.get("Tasks", {}).get("queued", 0) for S in Samples), default=0),
            "PeakRunning": max((S.get("Tasks", {}).get("started", 0) for S in Samples), default=0),
        },
        "Coverage": {
            "Cpu": "compiler process and live/reaped descendants; observer excluded; external Fabric service excluded",
            "BusyThreads": "threads with positive CPU tick deltas in a sample, not instantaneous simultaneous execution",
            "Stages": "intervals crossing stage/work boundaries are marked mixed rather than assigned to one stage",
            "Tasks": "symbolic unary proof pool only; uninstrumented queues are not inferred",
            "Stacks": "GIL-safe main-thread Python snapshots requested every second; held-GIL native calls can delay snapshots; no native backtrace",
            "Limits": "short-lived thread activity and final CPU before forced kill can be missed",
        },
    }
    (Directory / "RoutingTelemetry.json").write_text(json.dumps(Summary, indent=2) + "\n")
    Lines = [f"RESULT: {'SUCCESS' if Complete else 'PARTIAL'} — routing telemetry", f"TIME: sampled wall={TotalWall:.3f}s cpu={TotalCpu:.3f}s", f"CPU: average_cores={TotalCpu / TotalWall if TotalWall else 0:.2f}"]
    for Entry in sorted(Work.values(), key=lambda Value: -Value["WallSeconds"]):
        Lines.append(f"  {Entry['Name']}: wall={Entry['WallSeconds']:.3f}s average_cores={Entry['AverageCores']:.2f} main_cpu={Entry['MainThreadCpuSeconds']:.3f}s native_cpu={Entry['NativeCpuSeconds']:.3f}s child_cpu={Entry['ChildCpuSeconds']:.3f}s busy_native_peak={Entry['PeakBusyNativeThreads']}")
    Lines.append(f"OUTPUT: {Directory / 'RoutingTelemetry.json'}")
    (Directory / "RoutingTelemetry.txt").write_text("\n".join(Lines) + "\n")
    return Summary


def Observe(RootPid: int, Directory: Path, Interval: float = 0.25) -> None:
    """Run until the parent finishes or disappears; flush every observation."""
    ObserverPid = os.getpid()
    Hertz = os.sysconf("SC_CLK_TCK")
    Previous = ReadProcessTree(RootPid, ObserverPid)
    RootStart = Previous["Processes"].get(RootPid, {}).get("Start")
    Started = Last = monotonic()
    ObserverCpu = os.times()
    Stage, Phase = "frontend", "Compile"
    WorkStack = []
    TaskStates = {}
    Metadata = {
        "RootPid": RootPid, "ObserverPid": ObserverPid, "IntervalSeconds": Interval,
        "ClockTicksPerSecond": Hertz, "LogicalCpus": os.cpu_count(),
        "CpuAffinity": sorted(os.sched_getaffinity(RootPid)),
        "ConfiguredRoutingThreads": os.environ.get("RC_ROUTING_THREADS", "auto"),
    }
    (Directory / "RoutingTelemetry.meta.json").write_text(json.dumps(Metadata) + "\n")
    (Directory / "RoutingTelemetry.ready").touch()
    with (Directory / "RoutingTelemetry.events.jsonl").open() as EventFile, (Directory / "RoutingTelemetry.samples.jsonl").open("w", buffering=1) as Output:
        Stop = False
        while not Stop:
            sleep(Interval)
            StagesSeen = {Stage}
            PhasesSeen = {Phase}
            WorkChanged = False
            for Line in EventFile:
                try:
                    Event = json.loads(Line)
                except ValueError:
                    continue
                if Event["Kind"] == "task":
                    TaskStates[Event["Task"]] = Event["State"]
                if Event["Pid"] != RootPid:
                    continue
                if Event["Kind"] == "stage":
                    Stage, Phase = Event["Stage"], Event["Phase"]
                    StagesSeen.add(Stage)
                    PhasesSeen.add(Phase)
                    WorkStack.clear()
                elif Event["Kind"] == "work":
                    WorkChanged = True
                    if Event["Action"] == "begin":
                        WorkStack.append(Event["Name"])
                    elif WorkStack:
                        WorkStack.pop()
                elif Event["Kind"] == "stop":
                    Stop = True
            Current = ReadProcessTree(RootPid, ObserverPid)
            Root = Current["Processes"].get(RootPid)
            if Root is None or Root["Start"] != RootStart or Root["State"] == "Z":
                break
            Now = monotonic()
            Sample = MeasureSample(Previous, Current, Now - Last, RootPid, Hertz)
            MixedStage = len(StagesSeen) > 1
            Sample.update({
                "At": Now, "ElapsedSeconds": Now - Started,
                "Stage": "mixed stage interval" if MixedStage else Stage,
                "Phase": "Mixed" if len(PhasesSeen) > 1 else Phase,
                "Work": "mixed work interval" if WorkChanged and not MixedStage else WorkStack[-1] if WorkStack and not MixedStage else "",
                "ObservedStages": sorted(StagesSeen),
                "Tasks": dict(Counter(TaskStates.values())),
            })
            Output.write(json.dumps(Sample, separators=(",", ":")) + "\n")
            Previous, Last = Current, Now
    Times = os.times()
    Metadata["ObserverCpuSeconds"] = Times.user + Times.system - ObserverCpu.user - ObserverCpu.system
    (Directory / "RoutingTelemetry.meta.json").write_text(json.dumps(Metadata) + "\n")
    SummarizeTelemetry(Directory)


def Main() -> None:
    Parser = argparse.ArgumentParser(description=__doc__)
    Parser.add_argument("--observe", type=int)
    Parser.add_argument("--directory", type=Path, required=True)
    Arguments = Parser.parse_args()
    if Arguments.observe is not None:
        Observe(Arguments.observe, Arguments.directory)
    else:
        SummarizeTelemetry(Arguments.directory)


if __name__ == "__main__":
    Main()

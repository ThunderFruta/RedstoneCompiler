"""File-backed routing observation, independent of routing decisions and deadlines."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread, main_thread
import traceback
from time import monotonic, sleep
from typing import Any, Callable, Iterator


EventEnvironment = "RC_ROUTING_TELEMETRY_EVENTS"


def EmitTelemetryEvent(Kind: str, **Fields: object) -> None:
    """Append one small atomic event; unavailable observation never fails routing."""
    Destination = os.environ.get(EventEnvironment)
    if not Destination:
        return
    Record = {
        "Kind": Kind, "At": monotonic(), "Pid": os.getpid(), **Fields,
    }
    try:
        Payload = (json.dumps(Record, separators=(",", ":")) + "\n").encode()
        Descriptor = os.open(Destination, os.O_APPEND | os.O_WRONLY)
        try:
            os.write(Descriptor, Payload)
        finally:
            os.close(Descriptor)
    except (OSError, TypeError, ValueError):
        pass


@contextmanager
def TelemetryWork(Name: str) -> Iterator[None]:
    """Annotate a bounded operation without changing its return/error behavior."""
    EmitTelemetryEvent("work", Name=Name, Action="begin")
    try:
        yield
    finally:
        EmitTelemetryEvent("work", Name=Name, Action="end")


def RunTelemetryTask(Task: str, Function: Callable[..., Any], *Arguments: Any) -> Any:
    """Observe a spawned task without changing its result or exception."""
    EmitTelemetryEvent("task", Task=Task, State="started")
    try:
        Result = Function(*Arguments)
    except BaseException:
        EmitTelemetryEvent("task", Task=Task, State="failed")
        raise
    Complete = not (isinstance(Result, tuple) and len(Result) > 2 and isinstance(Result[2], dict) and Result[2].get("Complete") is False)
    EmitTelemetryEvent("task", Task=Task, State="completed" if Complete else "incomplete")
    return Result


def AwaitTelemetryTask(Task: str, Future: Any, Timeout: float | None) -> Any:
    """Measure the actual coordinator wait, including timeout/error paths."""
    EmitTelemetryEvent("wait", Task=Task, Action="begin")
    try:
        return Future.result(timeout=Timeout)
    finally:
        EmitTelemetryEvent("wait", Task=Task, Action="end")


class RoutingTelemetry:
    """Own a separate CPU observer and a GIL-safe Python stack sampler."""

    def __init__(self, Directory: Path, Enabled: bool = False) -> None:
        self.Directory = Directory.resolve()
        self.Enabled = Enabled
        self.Process: subprocess.Popen | None = None
        self.PreviousEvents = os.environ.get(EventEnvironment)
        self.StackFile = None
        self.StackStop = Event()
        self.StackThread: Thread | None = None
        self.Error = ""

    def SampleStacks(self) -> None:
        """Inspect Python frames under the GIL; never traverse frames asynchronously."""
        while not self.StackStop.wait(1.0):
            Frame = sys._current_frames().get(main_thread().ident)
            if Frame is not None and self.StackFile is not None:
                self.StackFile.write(f"Sample monotonic={monotonic():.6f}\n")
                self.StackFile.write("".join(traceback.format_stack(Frame, limit=24)))
                self.StackFile.flush()
            del Frame

    def Start(self) -> None:
        if not self.Enabled:
            return
        try:
            self.Directory.mkdir(parents=True, exist_ok=True)
            Events = self.Directory / "RoutingTelemetry.events.jsonl"
            Events.write_text("")
            os.environ[EventEnvironment] = str(Events)
            self.Process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("TelemetryObserver.py")),
                 "--observe", str(os.getpid()), "--directory", str(self.Directory)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            Ready = self.Directory / "RoutingTelemetry.ready"
            Until = monotonic() + 3.0
            while not Ready.exists() and monotonic() < Until:
                if self.Process.poll() is not None:
                    raise RuntimeError("telemetry observer exited during startup")
                sleep(0.01)
            if not Ready.exists():
                raise RuntimeError("telemetry observer did not become ready")
            self.StackFile = (self.Directory / "RoutingTelemetry.stacks.txt").open("w")
            self.StackThread = Thread(target=self.SampleStacks, name="redstone-stack-telemetry", daemon=True)
            self.StackThread.start()
            EmitTelemetryEvent("stage", Stage="frontend", Phase="Compile")
        except (OSError, RuntimeError) as Error:
            self.Error = str(Error)
            self.Finish()

    def Stage(self, Stage: str, Phase: str = "Routing") -> None:
        if self.Enabled:
            EmitTelemetryEvent("stage", Stage=Stage, Phase=Phase)

    def Finish(self) -> dict[str, object] | None:
        if not self.Enabled:
            return None
        if self.StackFile is not None:
            self.StackStop.set()
            if self.StackThread is not None:
                self.StackThread.join()
            self.StackFile.close()
            self.StackFile = None
        if self.Process is not None:
            EmitTelemetryEvent("stop")
            try:
                self.Process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.Process.terminate()
                self.Process.wait(timeout=3.0)
            self.Process = None
        if self.PreviousEvents is None:
            os.environ.pop(EventEnvironment, None)
        else:
            os.environ[EventEnvironment] = self.PreviousEvents
        SummaryPath = self.Directory / "RoutingTelemetry.json"
        if SummaryPath.is_file():
            try:
                return json.loads(SummaryPath.read_text())
            except (OSError, ValueError):
                pass
        return {"Status": "unavailable", "Error": self.Error or "observer produced no summary"}

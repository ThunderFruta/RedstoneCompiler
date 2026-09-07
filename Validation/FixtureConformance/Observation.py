"""Expectation-free raw observation adapter; no fallback to aggregate validation."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from PhysicalDesign.Redstone.FixtureValidation import FixtureIdentity
from .Expectations import Vector, TickCount
from .Loaders import LoadedFixture


def ObserveCase(Fixture: LoadedFixture, InitialInputs: dict[str, bool], AppliedInputs: dict[str, bool], HorizonTicks: int) -> dict[str, Any]:
    if FixtureIdentity(Fixture.Document) != Fixture.Sha256:
        raise ValueError("stale-fixture-identity")
    Names = {Port["Name"] for Port in Fixture.Document["Inputs"]}
    Request = {"Fixture": Fixture.Document,
               "InitialInputs": Vector(InitialInputs, Names), "AppliedInputs": Vector(AppliedInputs, Names),
               "HorizonTicks": TickCount(HorizonTicks), "InitializationTicks": 100}
    try:
        import RedstoneCompiler.RustRouting as Native
        NativePath = Path(Native.__file__).resolve()
        Identity = {"Path": str(NativePath), "Sha256": sha256(NativePath.read_bytes()).hexdigest()}
        Function = getattr(Native, "ObserveMchprsFixture", None)
        if Function is None:
            return {"Status": "unsupported", "Reason": "native-observer-unavailable", "Native": Identity, "FixtureSha256": Fixture.Sha256}
        Result = json.loads(Function(json.dumps(Request, sort_keys=True)))
        Result["Native"] = Identity
        Result["FixtureSha256"] = Fixture.Sha256
        return Result
    except Exception as Error:
        return {"Status": "backend-error", "Reason": str(Error), "FixtureSha256": Fixture.Sha256}

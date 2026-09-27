"""Bounded, inert HTML projections of persisted routing-failure evidence.

This observer has no routing imports and grants no physical-design authority.
The run reporter supplies its existing inventory identity; parsing is bounded
separately from that inventory's pre-existing file hashing cost.
"""

from __future__ import annotations

from hashlib import sha256
from html import escape
from itertools import islice
import json
import os
from pathlib import Path
import stat
from time import perf_counter
from typing import Mapping

from App.RoutingFailureArtifacts import (BuildReportReceipt, ReceiptName, ReportName,
    ReportStyle, ValidateReportPair, FileIdentity, ObserveReportPair,
    OpenEvidenceDirectory, WriteEvidenceMember, VerifyEvidenceDirectory)


MaximumArtifactBytes = 4 * 1024 * 1024
MaximumSearchNodes = 8192
MaximumSearchDepth = 24
MaximumEvidenceRecords = 8
MaximumTextCharacters = 24000
MaximumReportBytes = 256 * 1024
EvidenceVersion = "materialization-self-conflict-evidence-v1"


class ArtifactReadLimit(ValueError):
    """The source cannot be authenticated within the report reader's byte cap."""


class _Display:
    """One shared text budget, including paths and diagnostic field labels."""

    def __init__(self) -> None:
        self.Remaining = MaximumTextCharacters
        self.Truncated = False

    def Text(self, Value: object) -> str:
        Text = str(Value)
        Limit = min(512, self.Remaining)
        Kept = Text[:Limit]
        self.Remaining -= len(Kept)
        if len(Text) > Limit:
            self.Truncated = True
            Kept += " [display truncated]"
        return escape(Kept, quote=True)

    def Value(self, Value: object, Depth: int = 0) -> str:
        if self.Remaining <= 0:
            self.Truncated = True
            return "[display budget exhausted]"
        if isinstance(Value, (dict, list)):
            if Depth >= 4:
                self.Truncated = True
                return "[display depth limit]"
            Items = Value.items() if isinstance(Value, dict) else enumerate(Value)
            Parts = []
            for Key, Item in islice(Items, 24):
                Parts.append(self.Text(Key) + ": " + self.Value(Item, Depth + 1))
                if self.Remaining <= 0:
                    break
            if len(Value) > len(Parts):
                self.Truncated = True
                Parts.append("[additional entries omitted]")
            return "\n".join(Parts) or "(empty)"
        return self.Text(Value if Value is not None else "unavailable")

    def Field(self, Name: str, Value: object) -> str:
        return f"<dt>{self.Text(Name)}</dt><dd><pre>{self.Value(Value)}</pre></dd>"


def _FindEvidence(Value: object) -> tuple[list[tuple[str, object]], bool]:
    """Visit a bounded prefix in persisted order, never traverse geometry anew."""
    Found: list[tuple[str, object]] = []
    Remaining = MaximumSearchNodes
    Limited = False

    def Visit(Current: object, Location: str, Depth: int) -> None:
        nonlocal Remaining, Limited
        if Remaining <= 0 or Depth > MaximumSearchDepth:
            Limited = True
            return
        Remaining -= 1
        if isinstance(Current, dict):
            for Key, Item in Current.items():
                if Remaining <= 0 or len(Found) >= MaximumEvidenceRecords:
                    Limited = True
                    break
                Child = f"{Location}/{str(Key)[:80]}"[-512:]
                if Key == "SelfClaimConflictEvidence":
                    Found.append((Child, Item))
                    Remaining -= 1
                else:
                    Visit(Item, Child, Depth + 1)
        elif isinstance(Current, list):
            for Index, Item in enumerate(Current):
                if Remaining <= 0 or len(Found) >= MaximumEvidenceRecords:
                    Limited = True
                    break
                Visit(Item, f"{Location}/{Index}"[-512:], Depth + 1)

    Visit(Value, "", 0)
    return Found, Limited


def _EvidenceState(Value: object) -> str:
    if not isinstance(Value, dict):
        return "Malformed"
    if not isinstance(Value.get("SchemaVersion"), str):
        return "Malformed"
    if Value["SchemaVersion"] != EvidenceVersion:
        return "Unsupported"
    if Value.get("Scope") != "FirstDecisiveMaterializationSelfClaimPredicate":
        return "Malformed"
    if Value.get("CaptureStatus") == "Unavailable":
        return "Unavailable"
    if Value.get("CaptureStatus") not in ("Complete", "Partial"):
        return "Malformed"
    if (Value.get("Predicate") != "FindSelfClaimConflicts"
            or Value.get("ContributorProvenance") != "Unavailable"
            or Value.get("EvaluationIdentity") != "NotCaptured"):
        return "Malformed"
    Counts = ("ObservedConflictCount", "RetainedConflictCount", "NodeCount")
    if any(type(Value.get(Key)) is not int or Value[Key] < 0 for Key in Counts):
        return "Malformed"
    Conflicts = Value.get("Conflicts")
    Omissions = Value.get("Omissions")
    ClaimCounts = Value.get("ClaimCounts")
    if (not isinstance(Value.get("Signal"), str) or not Value["Signal"]
            or not isinstance(Conflicts, list) or len(Conflicts) > 16
            or not isinstance(Omissions, list) or len(Omissions) > 24
            or not all(isinstance(Item, str) for Item in Omissions)
            or not isinstance(ClaimCounts, dict)
            or set(ClaimCounts) != {"Wire", "Support", "Air", "Electrical"}
            or not all(Key in ("Wire", "Support", "Air", "Electrical")
                       and type(Count) is int and Count >= 0
                       for Key, Count in ClaimCounts.items())
            or Value["RetainedConflictCount"] != len(Conflicts)
            or Value["ObservedConflictCount"] < max(1, len(Conflicts))):
        return "Malformed"
    Coverage = ("Complete" if len(Conflicts) == Value["ObservedConflictCount"]
                else "Partial" if Conflicts else "Unavailable")
    if Value.get("ConflictCoverage") != Coverage:
        return "Malformed"
    Seen = set()
    for Conflict in Conflicts:
        if not isinstance(Conflict, dict):
            return "Malformed"
        Position = Conflict.get("Position")
        Roles = Conflict.get("ClaimKinds")
        if (not isinstance(Position, list) or len(Position) != 3
                or not all(type(Axis) is int for Axis in Position)
                or not isinstance(Roles, list) or not 2 <= len(Roles) <= 3
                or not all(Role in ("Wire", "Support", "Air") for Role in Roles)
                or len(set(Roles)) != len(Roles)
                or Conflict.get("Kind") not in ("Support", "Air")
                or Conflict["Kind"] not in Roles
                or (Conflict["Kind"] == "Air" and set(Roles) != {"Air", "Wire"})):
            return "Malformed"
        Key = (Conflict["Kind"], tuple(Position))
        if Key in Seen:
            return "Malformed"
        Seen.add(Key)
    if Value["CaptureStatus"] == "Partial" and not Omissions:
        return "Malformed"
    if Value["CaptureStatus"] == "Complete" and (
            Omissions or Value["ObservedConflictCount"] != len(Conflicts)):
        return "Malformed"
    return "Partial" if Value["CaptureStatus"] == "Partial" else "Available"


def _Render(Source: Path, Identity: Mapping[str, object], Payload: object,
            SourceState: str) -> str:
    Display = _Display()
    Parts = ["<h1>Routing failure report</h1>",
             "<p>Diagnostic evidence only. This report does not grant routing, "
             "reuse, cache, or accepted-state authority.</p><h2>Source artifact</h2><dl>"]
    for Name, Value in (("Path", str(Source)), ("SHA-256", Identity.get("Sha256")),
                        ("Bytes", Identity.get("Bytes")), ("Reader state", SourceState)):
        Parts.append(Display.Field(Name, Value))
    Parts.append("</dl>")
    Supported = (SourceState == "Available" and isinstance(Payload, dict)
                 and Payload.get("SchemaVersion") == "routing-failure-v1"
                 and isinstance(Payload.get("Failure"), dict))
    Failure = Payload["Failure"] if Supported else {}
    Parts.append("<h2>Typed failure</h2><dl>")
    for Key in ("Stage", "Reason", "Detail", "AffectedNets", "Resources", "Locations"):
        Parts.append(Display.Field(Key, Failure.get(Key)))
    Parts.append(Display.Field("Suggested repair actions (not proof of execution)",
                               Failure.get("RepairActions")))
    if Supported:
        Parts.append(Display.Field("Affected", Payload.get("Affected")))
        Diagnostics = Failure.get("Diagnostics")
        Deadline = Payload.get("Deadline")
        if Deadline is None and isinstance(Diagnostics, dict):
            Deadline = Diagnostics.get("Deadline")
        Parts.append(Display.Field("Deadline", Deadline))
    Parts.append("</dl><h2>Spatial evidence</h2>")
    Records, Limited = _FindEvidence(Failure.get("Diagnostics", {})) if Supported else ([], False)
    if not Records:
        State = "Unavailable" if Supported else SourceState
        Parts.append("<dl>" + Display.Field("Spatial evidence state", State) + "</dl>")
        Parts.append("<p>No supported spatial evidence was found in the inspected "
                     "portion. Missing coordinates do not mean no conflict exists.</p>")
    for Location, Record in Records:
        State = _EvidenceState(Record)
        Parts.append("<article><dl>")
        Parts.append(Display.Field("Evidence location (bounded display)", Location))
        Parts.append(Display.Field("Captured cell evidence state", State))
        SceneState = ("Partial" if State in ("Available", "Partial")
                      and Record["Conflicts"] else "Unavailable")
        Parts.append(Display.Field("Physical scene state", SceneState))
        Parts.append("</dl><p>Context, contributor provenance and evaluation identity "
                     "are not supplied by this viewer.</p><dl>")
        if isinstance(Record, dict):
            for Key in ("SchemaVersion", "CaptureStatus", "Omissions"):
                Parts.append(Display.Field(Key, Record.get(Key)))
        if State in ("Available", "Partial"):
            for Key in ("Signal", "ObservedConflictCount", "RetainedConflictCount",
                        "ConflictCoverage", "NodeCount", "ClaimCounts",
                        "ContributorProvenance", "EvaluationIdentity"):
                Parts.append(Display.Field(Key, Record.get(Key)))
            Parts.append("</dl><p>Observed physical rejection at the deciding predicate; "
                         "not proof that no other route exists and not a proven upstream "
                         "root cause. A Support/Air conflict alone does not identify a "
                         "native-routing bug.</p><h3>Derived display of captured cells</h3>"
                         "<table><tr><th>XYZ</th><th>Resource</th><th>Claim roles</th></tr>")
            for Conflict in Record["Conflicts"]:
                Position = "(" + ", ".join(str(Axis) for Axis in Conflict["Position"]) + ")"
                Parts.append("<tr><td>" + Display.Text(Position) + "</td><td>"
                             + Display.Text(Conflict["Kind"]) + "</td><td>"
                             + Display.Text(", ".join(Conflict["ClaimKinds"])) + "</td></tr>")
            Parts.append("</table>")
        else:
            Parts.append("</dl><p>Conflict cells are not interpreted for this record.</p>")
        Parts.append("</article>")
    Parts.append("<p>Complete capture describes the recorded predicate snapshot only, "
                 "not a complete physical scene or complete search. A fixed-domain "
                 "rejection is not global circuit impossibility.</p>")
    Parts.append("<h2>Downstream state</h2><dl>")
    Stage = Failure.get("Stage")
    # The envelope is also used for later validation failures. Never relabel
    # those observed executions as phases prevented by routing.
    Later = Stage in ("MchprsValidation", "FabricFinalCheck")
    for Name in ("Physical publication", "MCHPRS", "Fabric"):
        State = ("not-run — routing failure prevented this phase" if Supported and not Later
                 else "unavailable — this is a later validation failure; consult source artifact"
                 if Later else "unavailable — failure stage could not be read")
        Parts.append(Display.Field(Name, State))
    Parts.append("</dl><h2>Reader limits</h2><p>Parsing: 4 MiB; evidence discovery: "
                 "8192 values, depth 24, 8 records; display: 24000 source characters, "
                 "512 per string, 24 entries per field, depth 4. Node/claim cell arrays "
                 "are not displayed. No route or physical claims were recomputed.</p>")
    Parts.append(f"<p>Evidence discovery limited: {Limited}. Display truncated: {Display.Truncated}.</p>")
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Routing failure report</title><style>""" + ReportStyle + """</style></head><body>""" + "".join(Parts) + "</body></html>\n"


def PublishRoutingFailureReport(*, RunDirectory: Path, FailurePath: Path,
                                SourceIdentity: Mapping[str, object]) -> dict[str, object]:
    """Best effort publication; conventional failures never replace routing failure.

    Source bytes must match the run inventory. Unsupported/malformed artifacts
    get a metadata report. Oversize input fails closed without extra unbounded
    hashing. Cancellation exceptions propagate.
    """
    Started = perf_counter()
    DirectoryDescriptors: list[int] = []
    try:
        Directory = RunDirectory.resolve()
        Source = FailurePath.parent.resolve() / FailurePath.name
        if Source.parent != Directory or Source.is_symlink():
            raise ValueError("failure artifact must be a regular run-local file")
        if str(Source) != SourceIdentity.get("Path"):
            raise ValueError("failure artifact inventory identity missing")
        DirectoryDescriptors = OpenEvidenceDirectory(Directory)
        DirectoryDescriptor = DirectoryDescriptors[-1]
        Flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        Descriptor = os.open(Source.name, Flags, dir_fd=DirectoryDescriptor)
        with os.fdopen(Descriptor, "rb") as Stream:
            Info = os.fstat(Stream.fileno())
            if not stat.S_ISREG(Info.st_mode) or Info.st_size != SourceIdentity.get("Bytes"):
                raise ValueError("failure artifact identity changed")
            if Info.st_size > MaximumArtifactBytes:
                raise ArtifactReadLimit("failure artifact exceeds bounded reader size")
            Raw = Stream.read(MaximumArtifactBytes + 1)
        if len(Raw) > MaximumArtifactBytes:
            raise ArtifactReadLimit("failure artifact grew beyond reader size")
        if sha256(Raw).hexdigest() != SourceIdentity.get("Sha256"):
            raise ValueError("failure artifact hash changed")
        if os.path.lexists(Directory / ReportName) or os.path.lexists(Directory / ReceiptName):
            Existing = ObserveReportPair(Source, Raw)
            if Existing["Status"] != "Available":
                return {"Status": Existing["Status"], "ErrorType": Existing["Reason"],
                        "WallSeconds": perf_counter() - Started, "Bytes": 0}
            VerifyEvidenceDirectory(Directory, DirectoryDescriptor)
            return {"Status": "Published", "Path": str(Directory / ReportName),
                    "Bytes": Existing["Report"]["SizeBytes"], "Sha256": Existing["Report"]["Sha256"],
                    "Source": Existing["Source"], "Receipt": Existing["Receipt"],
                    "WallSeconds": perf_counter() - Started}
        Payload = None
        State = "Unsupported — artifact exceeds bounded reader size"
        if len(Raw) <= MaximumArtifactBytes:
            try:
                Payload = json.loads(Raw)
                State = "Available"
                if not isinstance(Payload, dict) or not isinstance(Payload.get("Failure"), dict):
                    State = "Malformed"
                elif Payload.get("SchemaVersion") != "routing-failure-v1":
                    State = "Unsupported"
                elif any(not isinstance(Payload["Failure"].get(Key), str)
                         or not Payload["Failure"][Key] for Key in ("Stage", "Reason")):
                    State = "Malformed"
            except (ValueError, UnicodeError, RecursionError):
                State = "Malformed"
        Content = _Render(Source, SourceIdentity, Payload, State).encode("utf-8")
        if len(Content) > MaximumReportBytes:
            raise ValueError("report byte limit exceeded")
        Receipt = BuildReportReceipt(Source.name, Raw, Content)
        Validation = ValidateReportPair(FailureName=Source.name, FailureData=Raw,
                                        ReportData=Content, ReceiptData=Receipt)
        if Validation["Status"] != "Available":
            raise ValueError("generated report failed its inert artifact contract")
        # Publish the receipt last: it commits the content/source binding.
        for Name, Data in ((ReportName, Content), (ReceiptName, Receipt)):
            WriteEvidenceMember(DirectoryDescriptor, Name, Data)
        VerifyEvidenceDirectory(Directory, DirectoryDescriptor)
        return {"Status": "Published", "Path": str(Directory / ReportName),
                "Bytes": len(Content), "Sha256": FileIdentity(ReportName, Content)["Sha256"],
                "Source": Validation["Source"], "Receipt": Validation["Receipt"],
                "WallSeconds": perf_counter() - Started}
    except Exception as Error:
        return {"Status": "Unavailable", "ErrorType": type(Error).__name__,
                "WallSeconds": perf_counter() - Started, "Bytes": 0}
    finally:
        for Descriptor in reversed(DirectoryDescriptors):
            os.close(Descriptor)

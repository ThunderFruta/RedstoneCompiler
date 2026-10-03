"""Bounded Markdown UI for retained failure evidence; never recompute a claim."""
from __future__ import annotations

from html import escape
from itertools import islice
import json
from pathlib import Path
from typing import Mapping
import unicodedata

MaximumSearchNodes = 8192
MaximumSearchDepth = 24
MaximumEvidenceRecords = 8
MaximumTextCharacters = 24000
EvidenceVersion = "materialization-self-conflict-evidence-v1"


class _Display:
    """One bounded display budget; untrusted text stays inside literal fences."""
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
        Kept = "".join(json.dumps(Char, ensure_ascii=True)[1:-1]
                       if unicodedata.category(Char) in ("Cc", "Cf", "Cs") else Char
                       for Char in Kept)
        return escape(Kept, quote=False).replace("`", "\\u0060")

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
                Label = self.Text(Key)
                Content = self.Value(Item, Depth + 1)
                Parts.append(Label + (":\n" + "\n".join("  " + Line for Line in Content.split("\n"))
                                      if isinstance(Item, (dict, list)) else ": " + Content))
                if self.Remaining <= 0:
                    break
            if len(Value) > len(Parts):
                self.Truncated = True
                Parts.append("[additional entries omitted]")
            return "\n".join(Parts) or "(empty)"
        return self.Text(Value if Value is not None else "unavailable")

    def Field(self, Name: str, Value: object) -> str:
        return LiteralBlock(self.Text(Name) + ": " + self.Value(Value))

    def Fields(self, Values) -> str:
        """Keep one evidence group in one readable literal block."""
        Parts = []
        for Name, Value in Values:
            Label, Content = self.Text(Name), self.Value(Value)
            Parts.append(Label + (":\n" + "\n".join("  " + Line for Line in Content.split("\n"))
                                  if isinstance(Value, (dict, list)) and Value else ": " + Content))
        return LiteralBlock("\n".join(Parts))

    def Table(self, Values) -> str:
        Rows = ["| Field | Value |", "| :--- | :--- |"]
        for Name, Value in Values:
            Text = self.Text(Value if Value is not None else "unavailable")
            # A raw pipe splits a GFM table even inside an inline code span.
            Text = Text.replace("\\", "\\u005c").replace("|", "\\u007c")
            Rows.append("| " + Name + " | `" + Text + "` |")
        return "\n".join(Rows) + "\n\n"


def LiteralBlock(Text: str) -> str:
    return "```text\n" + Text + "\n```\n\n"


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


def RenderRoutingFailureMarkdown(Source: Path, Identity: Mapping[str, object], Payload: object,
                                 SourceState: str) -> str:
    """Compact presentation of recorded relationships, without inferred execution."""
    Display = _Display()
    Supported = (SourceState == "Available" and isinstance(Payload, dict)
                 and Payload.get("SchemaVersion") == "routing-failure-v1"
                 and isinstance(Payload.get("Failure"), dict))
    Failure = Payload["Failure"] if Supported else {}
    Diagnostics = Failure.get("Diagnostics")
    Diagnostics = Diagnostics if isinstance(Diagnostics, dict) else {}
    Parts = ["# Routing failure report\n\n", "## Failure at a glance\n\n",
             Display.Table((("Stage", Failure.get("Stage")), ("Reason", Failure.get("Reason")))),
             Display.Field("Detail", Failure.get("Detail")), "## Data-flow graph\n\n"]
    if Supported:
        Graph = ["Retained input / configuration", "|", "v",
                 Display.Text(Failure.get("Stage")), "|",
                 "X " + Display.Text(Failure.get("Reason"))]
        Nets = Failure.get("AffectedNets")
        if isinstance(Nets, list) and Nets:
            for Net in Nets[:24]:
                Graph.append("Affected signal: " + Display.Text(Net) + " --> failure")
            if len(Nets) > 24:
                Display.Truncated = True
                Graph.append("Additional affected signals omitted")
        Parts.append(LiteralBlock("\n".join(Graph)))
    else:
        Parts.append(Display.Field("Graph evidence", "unavailable: unsupported or malformed source"))
    Parts.append("Arrows show recorded evidence relationships, not successful execution of upstream stages.\n\n")
    Parts.append("## Evidence status\n\n")
    Nets = Failure.get("AffectedNets")
    Parts.append(Display.Table((("Reader state", SourceState),
        ("Affected signal relationships", "Recorded" if isinstance(Nets, list) and Nets else "Unavailable"),
        ("Circuit connectivity", "Not captured"))))
    Parts.append("## Source artifact\n\n")
    Parts.append(Display.Fields((("Artifact", Source.name), ("Path", str(Source)),
                                ("SHA-256", Identity.get("Sha256")), ("Bytes", Identity.get("Bytes")))))
    Parts.append("## Failure details\n\n")
    Values = [(Key, Failure.get(Key)) for Key in ("AffectedNets", "Resources", "Locations")]
    Values.append(("Suggested repair actions (not proof of execution)", Failure.get("RepairActions")))
    if Supported:
        Values.extend((("Affected", Payload.get("Affected")),
                       ("Deadline", Payload.get("Deadline", Diagnostics.get("Deadline"))),
                       ("Effective controls", Payload.get("EffectiveControls")),
                       ("Native work", Payload.get("NativeWork")),
                       ("Stage timings", Payload.get("StageTimingsSeconds"))))
    Parts.append(Display.Fields(Values))
    Parts.append("## Spatial evidence\n\n")
    Records, Limited = _FindEvidence(Diagnostics) if Supported else ([], False)
    if not Records:
        Parts.append(Display.Field("Spatial evidence state", "Unavailable" if Supported else SourceState))
        Parts.append("No supported spatial evidence was found. Missing coordinates do not mean no conflict exists.\n\n")
    for Index, (Location, Record) in enumerate(Records, 1):
        State = _EvidenceState(Record)
        Parts.append(f"### Capture {Index}\n\n")
        Values = [("Evidence location (bounded display)", Location), ("Captured cell evidence state", State),
                  ("Physical scene state", "Partial" if State in ("Available", "Partial") and Record["Conflicts"] else "Unavailable")]
        if isinstance(Record, dict):
            Values.extend((Key, Record.get(Key)) for Key in ("SchemaVersion", "CaptureStatus", "Omissions"))
        if State in ("Available", "Partial"):
            Values.extend((Key, Record.get(Key)) for Key in ("Signal", "ObservedConflictCount", "RetainedConflictCount",
                "ConflictCoverage", "NodeCount", "ClaimCounts", "ContributorProvenance", "EvaluationIdentity"))
        Parts.append(Display.Fields(Values))
        if State in ("Available", "Partial"):
            Parts.append("Observed physical rejection at the deciding predicate; not proof that no other route exists "
                         "and not a proven upstream root cause. A Support/Air conflict alone does not identify a native-routing bug.\n\n")
            Cells = []
            for Conflict in Record["Conflicts"]:
                Position = "(" + ", ".join(str(Axis) for Axis in Conflict["Position"]) + ")"
                Cells.append("Cell " + Position + "\n  Resource: " + Conflict["Kind"] +
                             "\n  Conflicting claim roles: " + ", ".join(Conflict["ClaimKinds"]))
            if Cells:
                Parts.append(LiteralBlock("\n\n".join(Cells)))
        else:
            Parts.append("Conflict cells are not interpreted for this record.\n\n")
    Parts.append("## Downstream state\n\n")
    Later = Failure.get("Stage") in ("MchprsValidation", "FabricFinalCheck")
    Values = []
    for Name in ("Physical publication", "MCHPRS", "Fabric"):
        State = ("not-run - routing failure prevented this phase" if Supported and not Later
                 else "unavailable - later validation failure; consult source artifact" if Later
                 else "unavailable - failure stage could not be read")
        Values.append((Name, State))
    Parts.append(Display.Fields(Values))
    if Supported:
        Parts.append("## Inputs and configuration\n\n")
        Parts.append(Display.Fields((Key, Payload.get(Key)) for Key in
                                    ("Reproduction", "Strategy", "Policy", "Technology", "Fingerprints")))
    Parts.append("## Retained diagnostics\n\n")
    Parts.append(Display.Field("Diagnostics", Diagnostics if Supported else None))
    Parts.append("## Coverage and limits\n\n")
    Parts.append("Diagnostic evidence only. No routing, reuse, cache or accepted-state authority. "
                 "Complete capture describes the recorded predicate snapshot only, not a complete physical scene or complete search. "
                 "A fixed-domain rejection is not global circuit impossibility.\n\n")
    Parts.append("Parsing: 4 MiB; evidence discovery: 8192 values, depth 24, 8 records; display: 24000 source characters, "
                 "512 per string, 24 entries per field, depth 4. No route or physical claims were recomputed.\n\n")
    Parts.append(f"Evidence discovery limited: {Limited}. Display truncated: {Display.Truncated}.\n")
    return "".join(Parts)
